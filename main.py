from database import init_db
from fastapi import FastAPI
from routers.documents import router as documents_router


# 创建应用实例
app = FastAPI(title = "技术文档问答助手")

init_db()

app.include_router(documents_router)

# GET /health：检查服务是否能响应请求
@app.get("/health")
def health_check():
    return {"status": "ok"}




    
    