import sqlite3
from fastapi import APIRouter, Depends, HTTPException, UploadFile
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
@router.post("/upload")
async def upload_document(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".md"):
        raise HTTPException(status_code=422, detail="请上传 .md 文件")

    content = await file.read()
    return {
        "filename": file.filename,
        "size": len(content),
    }
