from fastapi import FastAPI

# 创建应用实例
app = FastAPI(title = "技术文档问答助手")

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