"""文档分块接口。"""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from config import UPLOAD_DIR
from database import get_connection
from chunking import split_text_with_offsets
from routers.document_management import get_document_content

router = APIRouter(prefix="/documents", tags=["文档分块"])


# 读取文档正文，生成带来源信息的分块，并替换 SQLite 中的旧分块。
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

    source = connection.execute(
        "SELECT title, file_type, stored_name FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    # PDF 保留物理页码（从 1 开始），字符位置相对于该页提取文本。
    # Markdown 字符位置相对于整篇正文，范围为 [start_char, end_char)。
    sections = [(None, content)]
    if source["file_type"] == "pdf":
        try:
            with (UPLOAD_DIR / source["stored_name"]).open("rb") as pdf_file:
                reader = PdfReader(pdf_file)
                sections = [
                    (page_number, page.extract_text() or "")
                    for page_number, page in enumerate(reader.pages, start=1)
                ]
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="文档文件不存在")
        except PyPdfError:
            raise HTTPException(status_code=422, detail="PDF 解析失败")

    chunks = []
    for page_number, text in sections:
        for chunk in split_text_with_offsets(text, chunk_size=500, overlap=100):
            chunks.append({**chunk, "page_number": page_number})

    if not chunks:
        raise HTTPException(status_code=422, detail="文档内容为空，无法分块")

    try:
        # 3. 删除当前文档以前的分块，避免重复生成
        connection.execute(
            "DELETE FROM document_chunks WHERE document_id = ?",
            (document_id,),
        )

        # 4. 保存新的分块，块编号从 1 开始
        for chunk_index, chunk in enumerate(chunks, start=1):
            connection.execute(
                """
                INSERT INTO document_chunks
                    (document_id, chunk_index, content, source_name,
                     page_number, start_char, end_char)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (document_id, chunk_index, chunk["content"], source["title"],
                 chunk["page_number"], chunk["start_char"], chunk["end_char"]),
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


# 查询指定文档的全部分块，返回原文、来源、页码和字符位置。
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
        SELECT id, id AS chunk_id, document_id, chunk_index, content,
               source_name, page_number, start_char, end_char
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
