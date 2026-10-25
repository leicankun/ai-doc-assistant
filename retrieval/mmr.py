"""使用重排相关性与已有分块向量，逐轮选择相关且较少重复的证据。"""
import logging
import math
import os

from fastapi import HTTPException
from storage.vector_store import get_collection

logger = logging.getLogger(__name__)


def mmr_weight():
    if os.getenv('MMR_ENABLED', 'true').lower() not in {'true', '1', 'yes'}:
        return None
    try:
        weight = float(os.getenv('MMR_LAMBDA', '0.8'))
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(503, 'MMR_LAMBDA 必须为 0～1 的有限数值') from exc
    return weight


def select_mmr(items, vectors, top_k, weight):
    """首项取相关性最高者；其后扣除与全部已选片段的最大非负余弦相似度。

    输入按 rerank_score 降序排列，同分保留原顺序。返回独立字典，保留来源。
    """
    if len(items) != len(vectors) or not 0 <= weight <= 1:
        raise ValueError('MMR 输入数量或权重无效')
    if not items or top_k < 1:
        return []
    normalized = []
    dimension = len(vectors[0])
    for vector in vectors:
        if not dimension or len(vector) != dimension or not all(math.isfinite(x) for x in vector):
            raise ValueError('MMR 向量维度或数值无效')
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError('MMR 向量长度无效')
        normalized.append([x / norm for x in vector])

    remaining = list(range(len(items)))
    redundancy = [0.0] * len(items)
    selected = []
    result = []
    while remaining and len(result) < top_k:
        def score(index):
            return weight * items[index]['rerank_score'] - (1 - weight) * redundancy[index]

        best = remaining[0] if not selected else max(remaining, key=score)
        result.append({**items[best], 'rerank_rank': items[best]['rank'],
                       'mmr_score': score(best), 'rank': len(result) + 1})
        selected.append(best)
        remaining.remove(best)
        for index in remaining:
            similarity = sum(a * b for a, b in zip(normalized[index], normalized[best]))
            # 负相似度不额外奖励，避免把不相干的内容当作多样性收益。
            redundancy[index] = max(redundancy[index], min(1.0, similarity))
    return result


def apply_mmr(items, top_k, weight):
    """仅读取现有向量；缺失、过期或不可读时，整体回退到普通重排 Top-K。"""
    try:
        ids = [item.get('record_id') or f"doc:{item['document_id']}:chunk:{item['chunk_id']}"
               for item in items]
        records = get_collection().get(ids=ids, include=['documents', 'metadatas', 'embeddings'])
        by_id = {
            record_id: (content, metadata, vector)
            for record_id, content, metadata, vector in zip(
                records['ids'], records['documents'], records['metadatas'], records['embeddings'])
        }
        vectors = []
        for item, record_id in zip(items, ids):
            content, metadata, vector = by_id[record_id]
            if (content != item['content'] or metadata['document_id'] != item['document_id']
                    or metadata['chunk_id'] != item['chunk_id']):
                raise ValueError('分块正文或身份与向量不一致')
            vectors.append(vector)
        return select_mmr(items, vectors, top_k, weight)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        logger.info('MMR 未应用，候选缺少一致有效的向量，保留普通重排结果：%s', exc)
    except Exception:
        logger.exception('MMR 读取向量失败，保留普通重排结果')
    return items[:top_k]
