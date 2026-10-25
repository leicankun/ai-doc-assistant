"""Prepare the optional local index without changing the default API backend."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from storage.database import get_connection
from retrieval.local_index import index_local_document
from services.knowledge_base import members
from scripts.local_bge import start, health, UNAVAILABLE

router = APIRouter(prefix='/embedding/local', tags=['本地向量检索'])
logger = logging.getLogger(__name__)


class PrepareLocalRequest(BaseModel):
    document_id: int | None = Field(default=None, gt=0)
    knowledge_base_id: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def exactly_one_scope(self):
        if (self.document_id is None) == (self.knowledge_base_id is None):
            raise ValueError('请选择一份文档或一个知识库')
        return self


@router.get('/status')
def local_status():
    try:
        result = health()
        return {key: result[key] for key in ('status', 'model', 'device', 'dimensions')}
    except UNAVAILABLE:
        return {'status': 'stopped', 'model': 'BAAI/bge-m3'}


@router.post('/prepare')
def prepare_local(body: PrepareLocalRequest, connection=Depends(get_connection)):
    if body.document_id is not None:
        document = connection.execute('SELECT id,title FROM documents WHERE id=?', (body.document_id,)).fetchone()
        if document is None:
            raise HTTPException(404, '文档不存在')
        count = connection.execute('SELECT COUNT(*) FROM document_chunks WHERE document_id=?',
                                   (body.document_id,)).fetchone()[0]
        if not count:
            raise HTTPException(409, '文档尚未分块，请先完成文档分块')
        documents, skipped = [dict(document)], []
    else:
        all_documents = members(connection, body.knowledge_base_id)
        skipped = [d for d in all_documents if not d['chunk_count']]
        documents = [d for d in all_documents if d['chunk_count']]
        if not documents:
            raise HTTPException(409, '知识库没有已分块的文档，请先完成文档分块')
    try:
        service = start()
        reports = [index_local_document(connection, document['id']) for document in documents]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('本地检索准备失败')
        raise HTTPException(503, '本地检索准备失败，请查看 .local_bge/server.log 后重试；不会切换到云端') from exc
    return {'embedding_provider': 'local', 'model': service['model'], 'device': service['device'],
            'documents': reports, 'skipped_documents': [{'id': d['id'], 'title': d['title']} for d in skipped],
            'message': f'本地检索已就绪，共 {len(reports)} 篇文档；无分块跳过 {len(skipped)} 篇'}
