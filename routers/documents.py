import sqlite3
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from config import UPLOAD_DIR
from database import get_connection
from schemas import Document, QuestionRequest,SessionCreate
from llm import answer_question
from openai import APIError, APITimeoutError
from pathlib import Path
from io import BytesIO
from pypdf import PdfReader
from pypdf.errors import PyPdfError
import json
from contextlib import aclosing
from fastapi import Request
from fastapi.responses import StreamingResponse
from config import DB_PATH
from llm import stream_answer_question
from chunking import split_text

router = APIRouter(
    prefix="/documents",
    tags=["文档"],
)


# GET /documents：返回文档信息列表
@router.get("")
def list_documents(
    connection: sqlite3.Connection = Depends(get_connection),
):
    query_cursor = connection.execute(
        "SELECT id, title, file_type FROM documents ORDER BY id"
        )
    rows = query_cursor.fetchall()
    items = []
    for row in rows:
        items.append(dict(row))
    return{
        "items":items,
        "total":len(items),
    }


# GET /documents：返回指定id文档信息列表
@router.get("/{document_id}")
def get_document(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
    ):
    query_cursor = connection.execute(
        "SELECT id, title, file_type FROM documents WHERE id = ?",
        (document_id,),
    )
    row = query_cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )
    return dict(row)


#根据 ID 修改文档信息。
@router.put("/{document_id}")
def update_document(document_id: int,
                    new_data: Document,
                    connection: sqlite3.Connection = Depends(get_connection),):

    update_cursor = connection.execute(
        """
        UPDATE documents
        SET title = ?, file_type = ?
        WHERE id = ?
        """,
        (new_data.title,new_data.file_type,document_id)
    )
    if update_cursor.rowcount == 0 :
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )
    connection.commit()
    return{
        "id": document_id,
        "title": new_data.title,
        "file_type": new_data.file_type
    }


#增加文档信息
@router.post("", status_code=201)
def create_document(
    document: Document,
    connection :sqlite3.Connection = Depends(get_connection),
    ):

    insert_cursor = connection.execute(
        "INSERT INTO documents (title, file_type) VALUES (?, ?)",
        (document.title, document.file_type),
    )
    connection.commit()
    return {
        "id":insert_cursor.lastrowid,
        "title":document.title,
        "file_type": document.file_type,
    }


#删除指定文档信息
@router.delete("/{document_id}")
def del_document(
    document_id: int,
    connection :sqlite3.Connection = Depends(get_connection),
    ):
    del_cursor = connection.execute(
            "DELETE FROM documents WHERE id = ?",
            (document_id,),
            )
    if del_cursor.rowcount == 0 :
        raise HTTPException(
                status_code=404,
                detail="文档不存在",
            )
    connection.commit()
    return {
        "message": "删除成功",
        "id": document_id,
    }


#上传markdown，pdf文件
@router.post("/upload", status_code=201)
async def upload_document(
    file: UploadFile,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 判断文件类型
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()

    if suffix not in (".md", ".pdf"):
        raise HTTPException(
            status_code=422,
            detail="请上传 .md 或 .pdf 文件",
        )

    # 2. 读取上传文件的二进制内容
    content = await file.read()

    if not content:
        raise HTTPException(status_code=422, detail="上传文件不能为空")

    # 3. 根据类型提取文字
    if suffix == ".md":
        file_type = "markdown"

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=422,
                detail="Markdown 文件必须是 UTF-8 编码",
            )

    else:
        file_type = "pdf"

        try:
            reader = PdfReader(BytesIO(content))

            if reader.is_encrypted:
                raise HTTPException(
                    status_code=422,
                    detail="暂不支持加密 PDF，请先解除密码保护",
                )

            page_texts = []

            for page in reader.pages:
                page_texts.append(page.extract_text() or "")

            text = "\n".join(page_texts)

        except PyPdfError:
            raise HTTPException(
                status_code=422,
                detail="PDF 解析失败，请确认文件有效且未损坏",
            )

        if not text.strip():
            raise HTTPException(
                status_code=422,
                detail="PDF 未提取到文字，暂不支持纯图片扫描件",
            )

    # 4. 保存原文件，保留对应的扩展名
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}{suffix}"
    file_path = UPLOAD_DIR / stored_name
    file_path.write_bytes(content)

    # 5. 将文件信息保存到数据库
    try:
        insert_cursor = connection.execute(
            """
            INSERT INTO documents (title, file_type, stored_name)
            VALUES (?, ?, ?)
            """,
            (filename, file_type, stored_name),
        )
        connection.commit()

    except sqlite3.Error:
        connection.rollback()
        file_path.unlink()
        raise

    return {
        "id": insert_cursor.lastrowid,
        "title": filename,
        "file_type": file_type,
        "stored_name": stored_name,
        "size": len(content),
        "preview": text[:100],
    }


# 根据文档 ID 读取正文
@router.get("/{document_id}/content")
def get_document_content(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 查询文档信息，同时获取文件类型
    document = connection.execute(
        """
        SELECT id, title, file_type, stored_name
        FROM documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )

    if not document["stored_name"]:
        raise HTTPException(
            status_code=404,
            detail="该文档尚未上传文件",
        )

    # 2. 找到原文件的保存路径
    file_path = UPLOAD_DIR / document["stored_name"]

    # 3. 根据文件类型读取正文
    try:
        if document["file_type"] == "markdown":
            content = file_path.read_text(encoding="utf-8")

        elif document["file_type"] == "pdf":
            # 使用 with，读取完成后自动关闭文件
            with file_path.open("rb") as pdf_file:
                reader = PdfReader(pdf_file)

                if reader.is_encrypted:
                    raise HTTPException(
                        status_code=422,
                        detail="暂不支持加密 PDF",
                    )

                page_texts = []

                for page in reader.pages:
                    page_texts.append(page.extract_text() or "")

                content = "\n".join(page_texts)

            if not content.strip():
                raise HTTPException(
                    status_code=422,
                    detail="PDF 未提取到文字，暂不支持纯图片扫描件",
                )

        else:
            raise HTTPException(
                status_code=422,
                detail="暂不支持读取此文件类型",
            )

    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="文档文件不存在",
        )

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422,
            detail="文档文字解码失败，请检查文件编码或内容",
        )

    except PyPdfError:
        raise HTTPException(
            status_code=422,
            detail="PDF 解析失败，请确认文件有效且未损坏",
        )

    # 4. 无论 Markdown 还是 PDF，都返回相同结构
    return {
        "id": document["id"],
        "title": document["title"],
        "content": content,
    }


#提问接口
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


#流式提问接口
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


# 查询指定文档的问答历史
@router.get("/{document_id}/history")
def get_document_history(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 按记录 ID 升序，先显示较早保存的问答
    rows = connection.execute(
        """
        SELECT id, document_id, question, answer, created_at
        FROM question_answers
        WHERE document_id = ?
        ORDER BY id ASC
        """,
        (document_id,),
    ).fetchall()

    items = [dict(row) for row in rows]
    return {
        "items": items,
        "total": len(items),
    }


# 为指定文档创建一个会话
@router.post("/{document_id}/sessions", status_code=201)
def create_document_session(
    document_id: int,
    session: SessionCreate,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 先确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 保存会话
    try:
        cursor = connection.execute(
            """
            INSERT INTO document_sessions (document_id, title)
            VALUES (?, ?)
            """,
            (document_id, session.title),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise HTTPException(status_code=500, detail="创建会话失败")

    return {
        "id": cursor.lastrowid,
        "document_id": document_id,
        "title": session.title,
    }


# 查询指定文档的所有会话
@router.get("/{document_id}/sessions")
def list_document_sessions(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 2. 查询这篇文档的会话
    rows = connection.execute(
        """
        SELECT id, document_id, title, created_at
        FROM document_sessions
        WHERE document_id = ?
        ORDER BY id DESC
        """,
        (document_id,),
    ).fetchall()

    # 3. 转换成字典列表并返回
    items = [dict(row) for row in rows]

    return {
        "items": items,
        "total": len(items),
    }


# 查询指定会话的问答历史
@router.get("/{document_id}/sessions/{session_id}/history")
def get_session_history(
    document_id: int,
    session_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 2. 确认会话存在，并且属于当前文档
    session = connection.execute(
        """
        SELECT id
        FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    # 3. 只查询这个会话中的问答
    rows = connection.execute(
        """
        SELECT id, document_id, session_id, question, answer, created_at
        FROM question_answers
        WHERE document_id = ? AND session_id = ?
        ORDER BY id ASC
        """,
        (document_id, session_id),
    ).fetchall()

    items = [dict(row) for row in rows]

    return {
        "items": items,
        "total": len(items),
    }


# 为指定文档生成并保存分块
@router.post("/{document_id}/chunks")
def create_document_chunks(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 复用正文读取函数，支持 Markdown 和 PDF
    document = get_document_content(document_id, connection)
    content = document["content"]

    if not content.strip():
        raise HTTPException(
            status_code=422,
            detail="文档内容为空，无法分块",
        )

    # 2. 将正文切分成多个片段
    chunks = split_text(
        content,
        chunk_size=500,
        overlap=100,
    )

    try:
        # 3. 删除当前文档以前的分块，避免重复生成
        connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ?",
            (document_id,),
        )

        # 4. 保存新的分块，块编号从 1 开始
        for chunk_index, chunk_content in enumerate(chunks, start=1):
            connection.execute(
                """
                INSERT INTO document_chunks
                    (document_id, chunk_index, content)
                VALUES (?, ?, ?)
                """,
                (document_id, chunk_index, chunk_content),
            )

        # 删除旧分块和插入新分块一起提交
        connection.commit()

    except sqlite3.Error:
        # 中途失败时，撤销本次修改，保留原来的分块
        connection.rollback()
        raise HTTPException(
            status_code=500,
            detail="保存文档分块失败",
        )

    return {
        "document_id": document_id,
        "total": len(chunks),
        "message": "文档分块已生成",
    }


# 查询指定文档的所有分块
@router.get("/{document_id}/chunks")
def list_document_chunks(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )

    # 2. 查询这篇文档的分块，按块编号从小到大排列
    rows = connection.execute(
        """
        SELECT id, document_id, chunk_index, content
        FROM document_chunks
        WHERE document_id = ?
        ORDER BY chunk_index ASC
        """,
        (document_id,),
    ).fetchall()

    # 3. 将每行转换为字典
    items = [dict(row) for row in rows]

    return {
        "document_id": document_id,
        "items": items,
        "total": len(items),
    }