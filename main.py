from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field,field_validator

# 创建应用实例
app = FastAPI(title = "技术文档问答助手")

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
    return {
        "items": documents,
        "total": len(documents)
    }

# GET /documents：返回指定id文档信息列表
@app.get("/documents/{document_id}")
def get_document(document_id: int):
    for document in documents:
        if document["id"] == document_id:
            return document

    raise HTTPException(
        status_code=404,
        detail="文档不存在",
    )

#根据 ID 修改文档信息。
@app.put("/documents/{document_id}")
def update_document(document_id: int , new_data: Document):
    for document in documents:
        if(document["id"] == document_id):
            document["title"] = new_data.title
            document["file_type"] = new_data.file_type
            return document

    raise HTTPException(
    status_code=404,
    detail="文档不存在",
)

#增加文档信息
@app.post("/documents", status_code=201)
def create_document(document: Document):
    new_id = max(
        (document["id"] for document in documents),default=0,) + 1
    new_document = {"id": new_id, "title": document.title, "file_type": document.file_type}
    documents.append(new_document)
    return new_document

#删除指定文档信息
@app.delete("/documents/{document_id}")
def del_document(document_id: int):
    for index , document in enumerate(documents):
        if(document_id == document["id"]):
            deleted_document = documents.pop(index)

            return {
                "message": "删除成功",
                "document": deleted_document,
            }
    raise HTTPException(
        status_code=404,
        detail="文档不存在",
    )