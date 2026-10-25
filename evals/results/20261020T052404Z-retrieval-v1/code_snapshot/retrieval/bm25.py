import math
import re
from collections import Counter
from functools import lru_cache

import jieba


def tokenize(text: str) -> list[str]:
    tokens = []

    # 中文单独分词；英文参数、路径等保留为完整词。
    pieces = re.findall(
        r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+(?:[./:-][A-Za-z0-9_]+)*",
        text,
    )

    for piece in pieces:
        if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
            tokens.extend(jieba.cut_for_search(piece))
        else:
            tokens.append(piece.lower())

    return tokens


@lru_cache(maxsize=8)
def build_index(corpus: tuple):
    """
    corpus: ((chunk_id, content), ...)
    相同分块复用缓存；分块 ID 或正文变化时重新建索引。
    """
    frequencies = []
    document_frequency = Counter()
    lengths = []

    for chunk_id, content in corpus:
        terms = tokenize(content)
        counts = Counter(terms)

        frequencies.append(counts)
        lengths.append(len(terms))

        # 一个词在同一块中出现多次，也只计一个文档频次。
        document_frequency.update(counts.keys())

    average_length = sum(lengths) / len(lengths) if lengths else 0

    return frequencies, document_frequency, lengths, average_length


def search_bm25(chunks: list[dict], question: str, top_k: int = 5):
    if not chunks:
        return []

    query_terms = set(tokenize(question))
    if not query_terms:
        return []

    corpus = tuple(
        (chunk["chunk_id"], chunk["content"])
        for chunk in chunks
    )

    frequencies, document_frequency, lengths, average_length = (
        build_index(corpus)
    )

    if average_length == 0:
        return []

    # 初始参数，后续通过评测调整。
    k1 = 1.5
    b = 0.75
    count = len(chunks)
    results = []

    for index, chunk in enumerate(chunks):
        score = 0.0
        matched_terms = []

        for term in sorted(query_terms):
            frequency = frequencies[index].get(term, 0)
            if frequency == 0:
                continue

            matched_terms.append(term)
            df = document_frequency[term]

            # 使用正值 IDF，适合只有少量分块的初始项目。
            idf = math.log(
                1 + (count - df + 0.5) / (df + 0.5)
            )

            denominator = frequency + k1 * (
                1 - b + b * lengths[index] / average_length
            )

            score += idf * frequency * (k1 + 1) / denominator

        # 没有词语匹配的块不作为 BM25 候选返回。
        if matched_terms:
            results.append({
                **chunk,
                "score": score,
                "matched_terms": matched_terms,
            })

    # 同分时按块编号排序，保证结果可重复。
    results.sort(
        key=lambda item: (-item["score"], item["chunk_index"])
    )

    results = results[:top_k]

    for rank, item in enumerate(results, start=1):
        item["rank"] = rank

    return results