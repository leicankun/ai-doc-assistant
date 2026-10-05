from embedding import embed_query
from vector_store import get_collection


def search_dense(
    document_id: int,
    question: str,
    top_k: int = 5,
) -> list[dict]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题不能为空")

    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k 必须是正整数")

    collection = get_collection()

    # 1. 确认当前文档已有向量，避免无数据时调用模型。
    records = collection.get(
        where={"document_id": document_id},
        include=["metadatas"],
    )

    if not records["ids"]:
        raise ValueError("该文档尚未向量入库，请先调用入库接口")

    # 2. 将用户问题转换成向量。
    query_vector = embed_query(question)

    # 3. 只检索指定文档，取距离最近的几个分块。
    result = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_k, len(records["ids"])),
        where={"document_id": document_id},
        include=["documents", "metadatas", "distances"],
    )

    # 4. 整理查询结果。
    # 只查询了一个问题，所以取第 0 组结果。
    items = []

    for index, record_id in enumerate(result["ids"][0]):
        metadata = result["metadatas"][0][index] or {}

        items.append({
            **metadata,
            "record_id": record_id,
            "content": result["documents"][0][index],
            "distance": result["distances"][0][index],
            "rank": index + 1,
        })

    return items