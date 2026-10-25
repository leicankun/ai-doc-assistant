"""删除文档及关联资源；MySQL 最后提交，外部清理失败可重试。"""
from pathlib import Path
import json

from storage.vector_store import get_collection


class DocumentNotFoundError(LookupError):
    pass


def delete_document(document_id, connection, upload_dir):
    document = connection.execute(
        'SELECT id, stored_name FROM documents WHERE id = ?', (document_id,)
    ).fetchone()
    if document is None:
        raise DocumentNotFoundError('文档不存在')

    file_path = None
    if document['stored_name']:
        root = Path(upload_dir).resolve()
        file_path = (root / document['stored_name']).resolve()
        # 只允许删除上传目录中的单个文件，拒绝越界路径和目录。
        if file_path.parent != root or file_path.is_dir():
            raise ValueError('文档文件路径无效')

    try:
        # 文档实际删除时，清除知识库关联及曾使用该文档的问答轮次。
        turn_ids = [r[0] for r in connection.execute('SELECT turn_id FROM knowledge_base_turn_documents WHERE document_id=?',(document_id,))]
        for turn_id in turn_ids:
            connection.execute('DELETE FROM knowledge_base_turn_documents WHERE turn_id=?',(turn_id,))
            connection.execute('DELETE FROM knowledge_base_turns WHERE id=?',(turn_id,))
        # 引用快照也包含原文，因此整轮清理，不仅删除引用编号。
        rows = connection.execute('SELECT id,citations_json FROM knowledge_base_turns').fetchall()
        for row in rows:
            if any(c.get('document_id') == document_id for c in json.loads(row['citations_json'])):
                connection.execute('DELETE FROM knowledge_base_turn_documents WHERE turn_id=?',(row['id'],))
                connection.execute('DELETE FROM knowledge_base_turns WHERE id=?',(row['id'],))
        connection.execute('DELETE FROM knowledge_base_documents WHERE document_id=?',(document_id,))
        # 先修改 MySQL，保持写事务，外部清理成功后一起提交。
        for table in ('question_answers', 'rag_turns', 'document_sessions', 'document_chunks'):
            connection.execute(f'DELETE FROM {table} WHERE document_id = ?', (document_id,))
        connection.execute('DELETE FROM documents WHERE id = ?', (document_id,))

        # 按文档元数据过滤，也能清理重新分块后遗留的旧向量。
        collection = get_collection()
        collection.delete(where={'document_id': document_id})
        if collection.get(where={'document_id': document_id}, include=[])['ids']:
            raise RuntimeError('文档向量未清理完成')

        # 原文件已经缺失时仍允许清理数据库和向量。
        if file_path is not None:
            file_path.unlink(missing_ok=True)
        connection.commit()
    except Exception:
        connection.rollback()
        # 文件系统/Chroma 不参与 MySQL 事务；已完成的外部清理不能回滚。
        # 保留文档记录，下一次 DELETE 可继续清理，无需重新调用模型。
        raise

    return {'message': '删除成功', 'id': document_id}
