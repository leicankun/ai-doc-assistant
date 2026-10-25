"""RAG问答接口。"""
import sqlite3
import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from openai import APIError, APITimeoutError
from storage.database import get_connection
from config import DB_PATH
from starlette.concurrency import run_in_threadpool
from schemas import SearchRequest
from services.llm import answer_with_chunks
from routers.search import search_document
import json
from contextlib import aclosing, closing

from fastapi import Request
from fastapi.responses import StreamingResponse

from services.llm import stream_answer_with_chunks

from schemas import RagChatRequest
from services.rag_chat import rewrite_question
from config import RAG_HISTORY_TURNS

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


# 多轮 RAG：读取历史、改写追问、重新检索、回答并保存引用。
@router.post("/{document_id}/ask-rag/chat")
def ask_document_rag_chat(
    document_id: int,
    request: RagChatRequest,
    connection: sqlite3.Connection = Depends(get_connection),
):
    session = connection.execute(
        """
        SELECT id FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (request.session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    if not os.getenv("DEEPSEEK_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="服务尚未配置回答模型 API Key",
        )

    # 读取最近 5 轮，再恢复为从旧到新的顺序。
    rows = connection.execute(
        """
        SELECT question, answer
        FROM rag_turns
        WHERE document_id = ? AND session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (document_id, request.session_id,RAG_HISTORY_TURNS),
    ).fetchall()

    history = [dict(row) for row in reversed(rows)]

    try:
        retrieval_question = rewrite_question(
            request.question,
            history,
        )

        # 使用明确的检索问题，每轮重新查资料。
        search_result = search_document(
            document_id=document_id,
            request=SearchRequest(
                question=retrieval_question,
                mode=request.mode,
                top_k=request.top_k,
            ),
            connection=connection,
        )

        result = answer_with_chunks(
            question=request.question,
            chunks=search_result["items"],
            history=history,
        )

    except HTTPException:
        # 保留检索接口的 404、409 等错误。
        raise
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="模型调用超时，请稍后重试",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="模型调用失败，请稍后重试",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("多轮 RAG 失败，document_id=%s", document_id)
        raise HTTPException(
            status_code=500,
            detail="多轮 RAG 失败，请查看后端日志",
        ) from exc

    # 只有成功生成并通过引用编号校验，才保存本轮。
    try:
        cursor = connection.execute(
            """
            INSERT INTO rag_turns (
                document_id, session_id, question,
                retrieval_question, answer, citations_json, mode
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                request.session_id,
                request.question,
                retrieval_question,
                result["answer"],
                json.dumps(result["citations"], ensure_ascii=False),
                request.mode,
            ),
        )
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="回答已生成，但历史保存失败",
        ) from exc

    return {
        "id": cursor.lastrowid,
        "document_id": document_id,
        "session_id": request.session_id,
        "question": request.question,
        "retrieval_question": retrieval_question,
        "mode": request.mode,
        "answer": result["answer"],
        "citations": result["citations"],
        "retrieved_chunks": search_result["total"],
    }


# 查询指定会话的 RAG 历史，包含每轮回答对应的引用。
@router.get("/{document_id}/sessions/{session_id}/rag-history")
def get_rag_history(
    document_id: int,
    session_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    session = connection.execute(
        """
        SELECT id FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    rows = connection.execute(
        """
        SELECT * FROM rag_turns
        WHERE document_id = ? AND session_id = ?
        ORDER BY id ASC
        """,
        (document_id, session_id),
    ).fetchall()

    items = []

    for row in rows:
        item = dict(row)
        item["citations"] = json.loads(item.pop("citations_json"))
        items.append(item)

    return {"items": items, "total": len(items)}




# 多轮流式 RAG：引用校验通过后保存本轮，停止或生成失败不保存。
@router.post("/{document_id}/ask-rag/chat/stream")
def ask_document_rag_chat_stream(
    document_id: int,
    request: RagChatRequest,
    http_request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
):
    session = connection.execute(
        """
        SELECT id FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (request.session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    if not os.getenv("DEEPSEEK_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="服务尚未配置回答模型 API Key",
        )

    # 读取最近 5 轮，再恢复为从旧到新的顺序。
    rows = connection.execute(
        """
        SELECT question, answer
        FROM rag_turns
        WHERE document_id = ? AND session_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (document_id, request.session_id,RAG_HISTORY_TURNS),
    ).fetchall()

    history = [dict(row) for row in reversed(rows)]

    try:
        retrieval_question = rewrite_question(
            request.question,
            history,
        )

        # 使用明确的检索问题，每轮重新查资料。
        search_result = search_document(
            document_id=document_id,
            request=SearchRequest(
                question=retrieval_question,
                mode=request.mode,
                top_k=request.top_k,
            ),
            connection=connection,
        )

    except HTTPException:
        # 保留检索接口的 404、409 等错误。
        raise
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="模型调用超时，请稍后重试",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="模型调用失败，请稍后重试",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("多轮 RAG 失败，document_id=%s", document_id)
        raise HTTPException(
            status_code=500,
            detail="多轮 RAG 失败，请查看后端日志",
        ) from exc


    def save_turn(answer, citations):
        with closing(sqlite3.connect(DB_PATH)) as save, save:
            cursor = save.execute(
                """INSERT INTO rag_turns(document_id,session_id,question,retrieval_question,answer,citations_json,mode)
                VALUES(?,?,?,?,?,?,?)""",
                (document_id,request.session_id,request.question,retrieval_question,
                 answer,json.dumps(citations,ensure_ascii=False),request.mode),
            )
            return cursor.lastrowid

    def encode(event):
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def generate():
        parts = []
        try:
            if await http_request.is_disconnected():
                return
            yield encode({"type":"meta", "retrieval_question":retrieval_question})
            async with aclosing(stream_answer_with_chunks(
                request.question, search_result["items"], history=history
            )) as events:
                async for event in events:
                    if await http_request.is_disconnected():
                        return
                    if event["type"] == "delta":
                        parts.append(event["content"])
                        yield encode(event)
                    elif event["type"] == "done":
                        answer = "".join(parts)
                        turn_id = await run_in_threadpool(save_turn, answer, event["citations"])
                        yield encode({**event, "id":turn_id, "document_id":document_id,
                            "session_id":request.session_id, "question":request.question,
                            "retrieval_question":retrieval_question, "answer":answer,
                            "mode":request.mode, "retrieved_chunks":search_result["total"]})
        except APITimeoutError:
            yield encode({"type":"error", "message":"模型回答超时，请稍后重试"})
        except APIError:
            yield encode({"type":"error", "message":"模型调用失败，请稍后重试"})
        except ValueError as exc:
            yield encode({"type":"error", "message":str(exc)})
        except sqlite3.Error:
            logger.exception("RAG history save failed")
            yield encode({"type":"error", "message":"回答已生成，但历史保存失败"})
        except Exception:
            logger.exception("RAG stream failed")
            yield encode({"type":"error", "message":"生成失败，请查看后端日志"})

    return StreamingResponse(generate(),media_type="text/event-stream",
        headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})
