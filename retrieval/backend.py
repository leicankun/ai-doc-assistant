"""Per-request embedding choice, isolated across threads and nested tool calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature

_provider = ContextVar('embedding_provider', default='api')


def current_provider():
    return _provider.get()


@contextmanager
def use_backend(provider):
    if provider not in ('api', 'local'):
        raise ValueError('Unsupported embedding provider')
    token = _provider.set(provider)
    try:
        yield
    finally:
        _provider.reset(token)


def request_backend(parameter):
    """Scope synchronous retrieval/worker code, never an SSE response's lifetime."""
    def decorate(function):
        sig = signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            request = sig.bind(*args, **kwargs).arguments[parameter]
            with use_backend(request.embedding_provider):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def document_statuses(connection, documents):
    if current_provider() != 'local':
        return documents
    from retrieval.local_index import validate_local_document
    result = []
    for document in documents:
        row = dict(document)
        if not row['chunk_count']:
            row.update(processing_status='not_chunked', processing_status_label='未分块')
        else:
            try:
                validate_local_document(connection, row['id'])
                row.update(processing_status='indexed', processing_status_label='本地索引已就绪')
            except ValueError:
                row.update(processing_status='index_stale', processing_status_label='本地索引待准备')
        result.append(row)
    return result
