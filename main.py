from typing import Literal
import sqlite3
from database import DB_PATH, init_db
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field,field_validator

# 创建应用实例
app = FastAPI(title = "技术文档问答助手")

init_db()

class Document(BaseModel):
    title: str = Field(..., description="文档标题",min_length=1, max_length=100)
    file_type: Literal["markdown", "pdf"] = Field(..., description="文档类型")
    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value

# 临时使用内存中的示例数据
documents =[ 
    {"id": 1, "title": "Python 基础笔记", "file_type": "markdown"},
    {"id": 2, "title": "FastAPI 学习资料", "file_type": "pdf"},
    {"id": 3, "title": "Vue 学习笔记", "file_type": "markdown"},
    ]

# GET /health：检查服务是否能响应请求
@app.get("/health")
def health_check():
    return {"status": "ok"}

# GET /documents：返回文档信息列表
@app.get("/documents")
def list_documents():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
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

    finally:
        connection.close()

# GET /documents：返回指定id文档信息列表
@app.get("/documents/{document_id}")
def get_document(document_id: int):
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
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

    finally:
        connection.close()

#根据 ID 修改文档信息。
@app.put("/documents/{document_id}")
def update_document(document_id: int , new_data: Document):
    connection = sqlite3.connect(DB_PATH)
    try:
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
    finally:
        connection.close()
    
#增加文档信息
@app.post("/documents", status_code=201)
def create_document(document: Document):
    connection = sqlite3.connect(DB_PATH)

    try:
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
    
    finally:
        connection.close()
#删除指定文档信息
@app.delete("/documents/{document_id}")
def del_document(document_id: int):
    connection = sqlite3.connect(DB_PATH)
    try:
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
    finally:
        connection.close()



    
    