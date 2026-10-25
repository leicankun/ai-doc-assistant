from retrieval.embedding import embed_query
from storage.vector_store import get_collection
from retrieval.bm25 import search_bm25

#深度检索 稠密嵌入
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


#混合检索 hybrid
def fuse_rrf(
    bm25_items: list[dict],
    dense_items: list[dict],
    top_k: int = 5,
    rrf_k: int = 60,
) -> list[dict]:
    # 按文档 ID 和分块 ID 合并同一个分块。
    merged = {}

    for mode, items in (
        ("bm25", bm25_items),
        ("dense", dense_items),
    ):
        # 两路结果已经按相关程度排序，排名从 1 开始。
        seen = set()

        for rank, item in enumerate(items, start=1):
            key = (item["document_id"], item["chunk_id"])

            # 同一路中，同一个分块只贡献一次分数。
            if key in seen:
                continue
            seen.add(key)

            if key not in merged:
                # 复制原文和来源，避免修改原始检索结果。
                entry = dict(item)
                entry.pop("rank", None)
                entry.pop("score", None)
                entry.pop("distance", None)
                entry.pop("matched_terms", None)

                entry.update({
                    "rrf_score": 0.0,
                    "bm25_rank": None,
                    "dense_rank": None,
                    "bm25_score": None,
                    "dense_distance": None,
                    "matched_terms": [],
                })
                merged[key] = entry

            entry = merged[key]

            # 每一路根据自己的排名贡献 RRF 分数。
            entry["rrf_score"] += 1.0 / (rrf_k + rank)

            if mode == "bm25":
                entry["bm25_rank"] = rank
                entry["bm25_score"] = item["score"]
                entry["matched_terms"] = item.get("matched_terms", [])
            else:
                entry["dense_rank"] = rank
                entry["dense_distance"] = item["distance"]
                entry["record_id"] = item["record_id"]

    # RRF 分数越大越靠前；同分时按 ID 排序，保证结果稳定。
    results = sorted(
        merged.values(),
        key=lambda item: (
            -item["rrf_score"],
            item["document_id"],
            item["chunk_id"],
        ),
    )[:top_k]

    for rank, item in enumerate(results, start=1):
        item["rank"] = rank

    return results


def search_hybrid(
    document_id: int,
    chunks: list[dict],
    question: str,
    top_k: int = 5,
) -> list[dict]:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("问题不能为空")

    if (
        not isinstance(top_k, int)
        or isinstance(top_k, bool)
        or top_k < 1
    ):
        raise ValueError("top_k 必须是正整数")

    # 只使用当前文档的分块。
    document_chunks = [
        chunk for chunk in chunks
        if chunk["document_id"] == document_id
    ]

    if not document_chunks:
        return []

    # 两路先各取较多候选，融合后再截取最终 Top-K。
    candidate_k = max(20, top_k)

    bm25_items = search_bm25(
        chunks=document_chunks,
        question=question,
        top_k=candidate_k,
    )

    dense_items = search_dense(
        document_id=document_id,
        question=question,
        top_k=candidate_k,
    )

    return fuse_rrf(
        bm25_items=bm25_items,
        dense_items=dense_items,
        top_k=top_k,
        rrf_k=60,
    )