import chromadb
from config import CHROMA_DIR

COLLECTION_NAME = "document_chunks_v4_1024"

def get_collection():
    from retrieval.backend import current_provider
    if current_provider() == 'local':
        return get_local_collection()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,
        configuration={
            "hnsw": {
                "space": "cosine",
            },
        },
        metadata={
            "embedding_model": "text-embedding-v4",
            "dimensions": 1024,
        },
    )

    return collection


def get_local_collection():
    """BGE collection, selected explicitly or by a local retrieval request."""
    from retrieval.local_embedding import COLLECTION_NAME as local_name, MODEL_ID, MODEL_REVISION
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=local_name, embedding_function=None,
        configuration={'hnsw': {'space': 'cosine'}},
        metadata={'embedding_model': MODEL_ID, 'dimensions': 1024, 'revision': MODEL_REVISION},
    )
    if (collection.metadata.get('embedding_model') != MODEL_ID
            or collection.metadata.get('revision') != MODEL_REVISION):
        raise ValueError('本地集合的模型或版本不匹配，请使用新的集合')
    return collection
