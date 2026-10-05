"""全文问答接口。"""
import sqlite3
import json
from contextlib import aclosing
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import APIError, APITimeoutError
from config import DB_PATH
from database import get_connection
from schemas import QuestionRequest
from llm import answer_question, stream_answer_question
from routers.document_management import get_document_content

router = APIRouter(prefix="/documents", tags=["全文问答"])


# 使用文档全文和当前会话最近 5 轮历史生成完整回答，并保存本轮问答。
@router.post("/{document_id}/ask")
def ask_document(
    document_id: int,
    request: QuestionRequest,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 复用之前写好的函数，查询文档并读取正文
    document = get_document_content(document_id, connection)
    content = document["content"]

    # 确认会话存在，并且属于当前文档
    session = connection.execute(
    """
    SELECT id
    FROM document_sessions
    WHERE id = ? AND document_id = ?
    """,
    (request.session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )
    if not content.strip():
        raise HTTPException(status_code=422, detail="文档内容为空")

    # 第一版先处理短文档
    if len(content) > 20000:
        raise HTTPException(
            status_code=422,
            detail="当前仅支持不超过 20000 个字符的文档",
        )

    # 查询当前会话最近 5 轮问答
    history_rows = connection.execute(
        """
        SELECT question, answer
        FROM question_answers
        WHERE document_id = ? AND session_id = ?
        ORDER BY id DESC
        LIMIT 5
        """,
        (document_id, request.session_id),
    ).fetchall()

    # 将最近 5 轮恢复为从旧到新的顺序
    history = []

    for row in reversed(history_rows):
        history.append({
            "question": row["question"],
            "answer": row["answer"],
        })

    try:
        answer = answer_question(content, request.question,history)
    except APITimeoutError:
        raise HTTPException(status_code=504, detail="模型回答超时，请稍后重试")
    except APIError:
        raise HTTPException(status_code=502, detail="模型调用失败，请检查模型服务配置")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="服务尚未配置模型 API Key")

    if not answer:
        raise HTTPException(status_code=502, detail="模型没有返回回答")

    # 将这次问答保存到数据库
    try:
        connection.execute(
            """
            INSERT INTO question_answers (document_id,session_id, question, answer)
            VALUES (?, ?, ?,?)
            """,
            (document_id,request.session_id, request.question, answer),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="模型已回答，但问答记录保存失败",
        )

    return {
        "document_id": document_id,
        "session_id": request.session_id,
        "question": request.question,
        "answer": answer,
    }


# 使用文档全文和最近 5 轮历史，通过 SSE 流式返回回答，正常完成后保存历史。
@router.post("/{document_id}/ask/stream")
def ask_document_stream(
    document_id: int,
    request: QuestionRequest,
    http_request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 读取 Markdown 或 PDF 正文
    document = get_document_content(document_id, connection)
    content = document["content"]

    # 2. 检查会话是否属于当前文档
    session = connection.execute(
        """
        SELECT id
        FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (request.session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    if not content.strip():
        raise HTTPException(
            status_code=422,
            detail="文档内容为空",
        )

    if len(content) > 20000:
        raise HTTPException(
            status_code=422,
            detail="当前仅支持不超过 20000 个字符的文档",
        )

    # 3. 查询最近 5 轮历史
    rows = connection.execute(
        """
        SELECT question, answer
        FROM question_answers
        WHERE document_id = ? AND session_id = ?
        ORDER BY id DESC
        LIMIT 5
        """,
        (document_id, request.session_id),
    ).fetchall()

    history = [
        {
            "question": row["question"],
            "answer": row["answer"],
        }
        for row in reversed(rows)
    ]

    # 将事件编码为 SSE 格式；空行表示一条事件结束
    def make_event(data):
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    # 4. 逐段生成响应
    async def generate():
        parts = []

        try:
            # aclosing 确保提前退出时也关闭内部生成器
            async with aclosing(
                stream_answer_question(
                    content,
                    request.question,
                    history,
                )
            ) as text_stream:
                async for text in text_stream:
                    # 客户端已经断开，就停止处理
                    if await http_request.is_disconnected():
                        return

                    parts.append(text)

                    yield make_event({
                        "type": "delta",
                        "content": text,
                    })

            if await http_request.is_disconnected():
                return

            answer = "".join(parts)

            if not answer.strip():
                yield make_event({
                    "type": "error",
                    "message": "模型没有返回回答",
                })
                return

            # 5. 正常生成结束后，使用独立连接保存完整问答
            # 不依赖流式响应期间原连接的生命周期
            save_connection = sqlite3.connect(DB_PATH)

            try:
                save_connection.execute(
                    """
                    INSERT INTO question_answers
                        (document_id, session_id, question, answer)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        request.session_id,
                        request.question,
                        answer,
                    ),
                )
                save_connection.commit()

            except sqlite3.Error:
                save_connection.rollback()
                raise

            finally:
                save_connection.close()

            # 6. 保存成功后通知前端结束
            yield make_event({
                "type": "done",
                "document_id": document_id,
                "session_id": request.session_id,
            })

        except APITimeoutError:
            yield make_event({
                "type": "error",
                "message": "模型回答超时，请稍后重试",
            })

        except APIError:
            yield make_event({
                "type": "error",
                "message": "模型调用失败，请稍后重试",
            })

        except RuntimeError:
            yield make_event({
                "type": "error",
                "message": "服务尚未配置模型 API Key",
            })

        except ValueError as error:
            yield make_event({
                "type": "error",
                "message": str(error),
            })

        except sqlite3.Error:
            yield make_event({
                "type": "error",
                "message": "回答已生成，但历史记录保存失败",
            })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
