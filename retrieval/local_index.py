"""Separate BGE snapshot index for comparison; production API index is untouched."""
import hashlib
import time

from retrieval.local_embedding import embed_local_texts
from storage.vector_store import get_local_collection


def content_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def chunk_metadata(doc, chunk):
    metadata = {key: chunk[key] for key in (
        'document_id', 'chunk_index', 'source_name', 'page_number', 'start_char', 'end_char'
    ) if chunk.get(key) is not None}
    return {**metadata, 'chunk_id': chunk['id'], 'chunk_version': doc['chunk_version'],
            'content_hash': content_hash(chunk['content'])}


def document_chunks(connection, document_id):
    doc = connection.execute('SELECT id,chunk_version FROM documents WHERE id=?',
                             (document_id,)).fetchone()
    if not doc:
        raise ValueError('文档不存在')
    chunks = [dict(row) for row in connection.execute(
        'SELECT * FROM document_chunks WHERE document_id=? ORDER BY chunk_index', (document_id,))]
    return dict(doc), chunks


def index_local_document(connection, document_id, progress=None):
    doc, chunks = document_chunks(connection, document_id)
    collection = get_local_collection()
    expected = {f"doc:{document_id}:chunk:{chunk['id']}" for chunk in chunks}
    existing = collection.get(where={'document_id': document_id}, include=['metadatas'])
    old = dict(zip(existing['ids'], existing['metadatas']))
    pending = [chunk for chunk in chunks if old.get(f"doc:{document_id}:chunk:{chunk['id']}")
               != chunk_metadata(doc, chunk)]
    started = time.perf_counter()
    for start in range(0, len(pending), 32):
        batch = pending[start:start + 32]
        vectors = embed_local_texts([chunk['content'] for chunk in batch])
        metadatas = [chunk_metadata(doc, chunk) for chunk in batch]
        collection.upsert(ids=[f"doc:{document_id}:chunk:{c['id']}" for c in batch],
                          embeddings=vectors, documents=[c['content'] for c in batch],
                          metadatas=metadatas)
        if progress:
            progress(min(start + len(batch), len(pending)), len(pending))
    current_doc, current_chunks = document_chunks(connection, document_id)
    if (current_doc != doc or [(c['id'], c['content']) for c in current_chunks]
            != [(c['id'], c['content']) for c in chunks]):
        raise ValueError('文档分块在索引期间发生变化，请重新运行本地索引')
    stale = set(existing['ids']) - expected
    if stale:
        collection.delete(ids=sorted(stale))
    validate_local_document(connection, document_id, collection)
    return {'document_id': document_id, 'indexed_chunks': len(chunks), 'encoded_chunks': len(pending),
            'elapsed_seconds': round(time.perf_counter() - started, 3)}


def validate_local_document(connection, document_id, collection=None):
    collection = collection or get_local_collection()
    doc, chunks = document_chunks(connection, document_id)
    records = collection.get(where={'document_id': document_id}, include=['metadatas'])
    actual = dict(zip(records['ids'], records['metadatas']))
    if len(actual) != len(chunks) or any(
        actual.get(f"doc:{document_id}:chunk:{c['id']}") != chunk_metadata(doc, c)
        for c in chunks
    ):
        raise ValueError(f'文档 {document_id} 的 BGE 索引缺失、未完成或已过期，请重新 index')
    return chunks


def search_local(connection, question, top_k=5, document_id=None):
    if not isinstance(question, str) or not question.strip():
        raise ValueError('问题不能为空')
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 100:
        raise ValueError('top_k 必须是 1～100 的整数')
    collection = get_local_collection()
    document_ids = ([document_id] if document_id is not None else
                    [row['id'] for row in connection.execute('SELECT id FROM documents ORDER BY id')])
    ready, count = [], 0
    for doc_id in document_ids:
        chunks = validate_local_document(connection, doc_id, collection)
        if chunks:
            ready.append(doc_id)
            count += len(chunks)
    if not count:
        return []
    vector = embed_local_texts([question])[0]
    result = collection.query(query_embeddings=[vector], n_results=min(top_k, count),
                              where={'document_id': {'$in': ready}},
                              include=['documents', 'metadatas', 'distances'])
    return [{**metadata, 'content': content, 'distance': distance, 'rank': rank + 1}
            for rank, (metadata, content, distance) in enumerate(zip(
                result['metadatas'][0], result['documents'][0], result['distances'][0]))]
