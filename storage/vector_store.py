import chromadb
from config import CHROMA_DIR

COLLECTION_NAME = "document_chunks_v4_1024"

def get_collection():
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