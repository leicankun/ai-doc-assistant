"""文档管理接口。"""
import sqlite3
import logging
from uuid import uuid4
from pathlib import Path
from io import BytesIO
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from config import UPLOAD_DIR
from storage.database import get_connection
from schemas import Document
from storage.document_deletion import DocumentNotFoundError, delete_document
from storage.document_status import DOCUMENT_SELECT, with_processing_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["文档管理"])


# 查询所有文档的信息列表，返回文档条目和总数。
@router.get("")
def list_documents(
    connection: sqlite3.Connection = Depends(get_connection),
):
    query_cursor = connection.execute(
        DOCUMENT_SELECT + " ORDER BY d.id"
        )
    rows = query_cursor.fetchall()
    items = []
    for row in rows:
        items.append(with_processing_status(row))
    return{
        "items":items,
        "total":len(items),
    }


# 根据文档 ID 查询文档信息，不读取正文。
@router.get("/{document_id}")
def get_document(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
    ):
    query_cursor = connection.execute(
        DOCUMENT_SELECT + " WHERE d.id = ?",
        (document_id,),
    )
    row = query_cursor.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="文档不存在",
        )
    return with_processing_status(row)


# 根据文档 ID 修改标题和文件类型。
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


# 创建文档信息记录，仅登记标题和类型，不上传文件。
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


# 删除文档、上传原文件、SQLite 分块、Chroma 向量及两类问答历史和会话。
@router.delete("/{document_id}")
def del_document(
    document_id: int,
    connection :sqlite3.Connection = Depends(get_connection),
    ):
    try:
        return delete_document(document_id, connection, UPLOAD_DIR)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文档不存在") from exc
    except Exception as exc:
        logger.exception("文档删除失败，document_id=%s", document_id)
        raise HTTPException(
            status_code=500,
            detail="文档清理未完成，请重试删除；如仍失败请查看后端日志",
        ) from exc


# 上传 Markdown 或文本型 PDF，校验并保存原文件和文档信息，返回正文预览。
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


# 根据文档 ID 读取已上传文件，提取并返回完整正文。
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
