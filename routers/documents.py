"""文档业务路由聚合入口，供 main.py 注册。"""
from fastapi import APIRouter
from routers import document_management, chunks, search, sessions, question_answering, rag, knowledge_bases
from routers import embedding_backends

router = APIRouter()
for business_router in (
    document_management.router,
    chunks.router,
    search.router,
    sessions.router,
    question_answering.router,
    rag.router,
    knowledge_bases.router,
    embedding_backends.router,
):
    router.include_router(business_router)
