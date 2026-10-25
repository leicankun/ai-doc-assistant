"""状态来自 MySQL 版本记录；旧索引未经验证时需要重新入库。"""
LABELS = {'not_chunked': '未分块', 'pending_index': '待入库',
          'indexed': '已入库', 'index_stale': '索引过期'}


def with_processing_status(document):
    result = dict(document)
    if not result['chunk_count']:
        status = 'not_chunked'
    elif result['indexed_version'] is None:
        status = 'pending_index'
    elif result['indexed_version'] == result['chunk_version']:
        status = 'indexed'
    else:
        status = 'index_stale'
    result['processing_status'] = status
    result['processing_status_label'] = LABELS[status]
    return result


DOCUMENT_SELECT = '''
    SELECT d.id, d.title, d.file_type, d.chunk_version, d.indexed_version,
           (SELECT COUNT(*) FROM document_chunks c WHERE c.document_id = d.id) AS chunk_count
    FROM documents d
'''
