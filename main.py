from storage.database import init_db
from fastapi import FastAPI
from routers.documents import router as documents_router
from fastapi.middleware.cors import CORSMiddleware


# 创建应用实例
app = FastAPI(title = "技术文档问答助手")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)

init_db()

app.include_router(documents_router)

# 检查服务是否正常响应，返回健康状态。
@app.get("/health")
def health_check():
    return {"status": "ok"}
