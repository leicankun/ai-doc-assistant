import sqlite3
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from config import UPLOAD_DIR
from database import get_connection
from schemas import Document


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


#上传markdown文件
@router.post("/upload", status_code=201)
async def upload_document(file: UploadFile,
                          connection :sqlite3.Connection = Depends(get_connection),
                          ):
    if not file.filename or not file.filename.lower().endswith(".md"):
        raise HTTPException(status_code=422, detail="请上传 .md 文件")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="文件必须是 UTF-8 编码")
    #保存文件
    UPLOAD_DIR.mkdir(exist_ok=True)
    stored_name = f"{uuid4().hex}.md"
    file_path = UPLOAD_DIR / stored_name
    file_path.write_bytes(content)

    try:
        insert_cursor = connection.execute(
            """
            INSERT INTO documents (title, file_type, stored_name)
            VALUES (?, ?, ?)
            """,
            (file.filename, "markdown", stored_name),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        file_path.unlink()  # 数据库保存失败时，清理本次上传的文件
        raise

    return {
        "id": insert_cursor.lastrowid,
        "title": file.filename,
        "file_type": "markdown",
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
    # 1. 查询数据库，找到文档关联的文件名
    query_cursor = connection.execute(
        "SELECT id, title, stored_name FROM documents WHERE id = ?",
        (document_id,),
    )
    document = query_cursor.fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if not document["stored_name"]:
        raise HTTPException(status_code=404, detail="该文档尚未上传文件")

    # 2. 根据文件名找到磁盘上的文件
    file_path = UPLOAD_DIR / document["stored_name"]

    # 3. 读取文件内容
    try:
        content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文档文件不存在")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="文件必须是 UTF-8 编码")

    # 4. 返回文档正文
    return {
        "id": document["id"],
        "title": document["title"],
        "content": content,
    }