"""RAG问答接口。"""
import sqlite3
import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from openai import APIError, APITimeoutError
from database import get_connection
from schemas import SearchRequest
from llm import answer_with_chunks
from routers.search import search_document
import json
from contextlib import aclosing

from fastapi import Request
from fastapi.responses import StreamingResponse

from llm import stream_answer_with_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["RAG问答"])


# 先检索相关分块，再生成带引用的单轮回答；不读取或保存会话历史。
@router.post("/{document_id}/ask-rag")
def ask_document_rag(
    document_id: int,
    request: SearchRequest,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 复用已有检索接口的处理逻辑。
    # 普通 Python 函数调用不会自动注入依赖，
    # 所以这里明确传入 connection。
    search_result = search_document(
        document_id=document_id,
        request=request,
        connection=connection,
    )
    chunks = search_result["items"]

    # 2. 有资料需要调用模型时，先检查配置。
    if chunks and not os.getenv("DEEPSEEK_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="服务尚未配置回答模型 API Key",
        )

    # 3. 将检索分块交给回答模型。
    try:
        result = answer_with_chunks(
            question=request.question,
            chunks=chunks,
        )
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="模型回答超时，请稍后重试",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="回答模型调用失败，请检查模型服务配置",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception(
            "RAG 回答失败，document_id=%s",
            document_id,
        )
        raise HTTPException(
            status_code=500,
            detail="RAG 回答失败，请查看后端日志",
        ) from exc

    return {
        "document_id": document_id,
        "question": request.question,
        "mode": request.mode,
        "answer": result["answer"],
        "citations": result["citations"],
        "retrieved_chunks": len(chunks),
    }



# 先检索资料，再通过 SSE 流式生成回答，结束后返回引用。
@router.post("/{document_id}/ask-rag/stream")
def ask_document_rag_stream(
    document_id: int,
    request: SearchRequest,
    http_request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 先完成检索。
    # 检索失败时，还未开始 SSE，可以正常返回 HTTP 错误。
    search_result = search_document(
        document_id=document_id,
        request=request,
        connection=connection,
    )
    chunks = search_result["items"]

    if chunks and not os.getenv("DEEPSEEK_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="服务尚未配置回答模型 API Key",
        )

    # 2. 将字典编码为 SSE 事件。
    def make_event(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 3. 流式生成并发送事件。
    async def generate():
        try:
            if await http_request.is_disconnected():
                return

            # 提前退出时，也会关闭内部异步生成器和模型连接。
            async with aclosing(
                stream_answer_with_chunks(
                    question=request.question,
                    chunks=chunks,
                )
            ) as events:
                async for event in events:
                    if await http_request.is_disconnected():
                        return

                    if event["type"] == "done":
                        event = {
                            **event,
                            "document_id": document_id,
                            "question": request.question,
                            "mode": request.mode,
                            "retrieved_chunks": len(chunks),
                        }

                    yield make_event(event)

        except APITimeoutError:
            yield make_event({
                "type": "error",
                "message": "模型回答超时，请稍后重试",
            })

        except APIError:
            yield make_event({
                "type": "error",
                "message": "回答模型调用失败，请稍后重试",
            })

        except ValueError as exc:
            yield make_event({
                "type": "error",
                "message": str(exc),
            })

        except Exception:
            logger.exception(
                "RAG 流式回答失败，document_id=%s",
                document_id,
            )
            yield make_event({
                "type": "error",
                "message": "RAG 回答失败，请查看后端日志",
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
