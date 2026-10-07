"""知识库内统一检索，复用文档分块与向量，不重复入库。"""

import json

from fastapi import HTTPException

from retrieval.bm25 import search_bm25, tokenize
from storage.document_status import DOCUMENT_SELECT, with_processing_status
from retrieval.embedding import embed_query
from retrieval.engine import fuse_rrf
from storage.vector_store import get_collection
from config import RAG_HISTORY_TURNS
def require_base(connection, base_id):
    """查询知识库；不存在时返回 404，存在时返回字典。"""
    row = connection.execute(
        "SELECT * FROM knowledge_bases WHERE id = ?", (base_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "知识库不存在")
    return dict(row)


def members(connection, base_id):
    """根据关联表查询成员文档，并补充每份文档的处理状态。"""
    require_base(connection, base_id)
    rows = connection.execute(
        DOCUMENT_SELECT + """
        JOIN knowledge_base_documents m ON m.document_id = d.id
        WHERE m.knowledge_base_id = ? ORDER BY d.id
        """,
        (base_id,),
    ).fetchall()
    return [with_processing_status(row) for row in rows]


def ready_documents(connection, base_id, mode):
    """检查知识库所有文档是否满足当前检索模式的条件。"""
    documents = members(connection, base_id)
    if not documents:
        raise HTTPException(
            409, {"message": "知识库为空，请先添加文档", "documents": []}
        )

    # BM25 需要分块；Dense 和 Hybrid 还需要完成向量入库。
    blocked = [
        d for d in documents
        if not d["chunk_count"]
        or (mode != "bm25" and d["processing_status"] != "indexed")
    ]
    if blocked:
        raise HTTPException(
            409,
            {"message": "知识库资料未就绪，请处理以下文档", "documents": blocked},
        )
    return documents


def search_base(connection, base_id, request, *, document_ids=None,
                allow_partial=False, min_dense_similarity=None, timeout=None):
    """在全部成员文档中统一检索，返回整个知识库的 Top-K 分块。"""
    # 1. 检查处理状态，并确定本次允许检索的文档 ID。
    skipped = []
    if allow_partial or document_ids is not None:
        documents = members(connection, base_id)
        if document_ids is not None:
            if not set(document_ids).issubset({d['id'] for d in documents}):
                raise HTTPException(404, '指定文档不存在或不属于当前知识库')
            documents = [d for d in documents if d['id'] in document_ids]
        skipped = [d for d in documents if not d['chunk_count'] or
                   (request.mode != 'bm25' and d['processing_status'] != 'indexed')]
        if skipped and not allow_partial:
            raise HTTPException(409, {'message':'知识库资料未就绪','documents':skipped})
        documents = [d for d in documents if d not in skipped]
        if not documents:
            return {'knowledge_base_id':base_id,'question':request.question,'mode':request.mode,
                    'items':[],'total':0,'skipped_documents':skipped,
                    'warning':'当前范围没有符合检索条件的文档，不能据此判断资料不存在'}
    else:
        documents = ready_documents(connection, base_id, request.mode)
    ids = [d["id"] for d in documents]

    # 2. 读取成员文档的所有分块，附上标题以便展示引用来源。
    # 每个 ID 对应一个 ? 占位符；实际值通过参数绑定传入 SQL。
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content,
               d.title AS source_name, c.page_number, c.start_char, c.end_char
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.document_id IN ({placeholders})
        ORDER BY c.document_id, c.chunk_index
        """,
        ids,
    ).fetchall()
    chunks = [dict(row) for row in rows]

    # 3. 涉及向量检索时至少取 20 个候选，供后续截取或 RRF 融合。
    candidate_k = request.top_k if request.mode == "bm25" else max(20, request.top_k)
    if request.mode == "bm25" and not tokenize(request.question):
        raise HTTPException(422, "问题中没有可检索的词语")

    # BM25 对所有成员分块统一评分；纯 Dense 模式跳过关键词检索。
    lexical = (
        search_bm25(chunks, request.question, candidate_k)
        if request.mode != "dense" else []
    )
    if request.mode == "bm25":
        items = lexical
    else:
        # 4. 共用已有 Chroma collection，通过元数据限制知识库范围。
        collection = get_collection()
        where = {"document_id": {"$in": ids}}
        records = collection.get(where=where, include=["metadatas"])

        # 检查 Chroma 与 SQLite 的分块身份集合，防止缺失或旧向量参与检索。
        actual = {
            (m["document_id"], m["chunk_id"])
            for m in records["metadatas"]
        }
        expected = {(c["document_id"], c["chunk_id"]) for c in chunks}
        if actual != expected:
            raise HTTPException(
                409,
                {
                    "message": "知识库向量与分块不一致，请重新入库",
                    "documents": documents,
                },
            )

        # 问题转成向量后查询；候选数量不能超过当前范围的实际向量数量。
        result = collection.query(
            query_embeddings=[embed_query(request.question, timeout=timeout) if timeout is not None
                              else embed_query(request.question)],
            n_results=min(candidate_k, len(records["ids"])),
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        # Chroma 支持批量查询，[0] 取出本次唯一问题的结果。
        # zip 配对同一结果的 ID、元数据、正文和距离，enumerate 从 1 编排名。
        titles = {d["id"]: d["title"] for d in documents}
        dense = [
            {
                **metadata,
                "record_id": record_id,
                "content": content,
                "distance": distance,
                "source_name": titles[metadata["document_id"]],
                "rank": rank,
            }
            for rank, (record_id, metadata, content, distance) in enumerate(
                zip(
                    result["ids"][0],
                    result["metadatas"][0],
                    result["documents"][0],
                    result["distances"][0],
                ),
                start=1,
            )
        ]

        if min_dense_similarity is not None:
            dense = [item for item in dense if 1 - item['distance'] >= min_dense_similarity]
        # 5. Dense 直接截取；Hybrid 按两路排名进行 RRF 融合后取 Top-K。
        items = (
            dense[:request.top_k]
            if request.mode == "dense"
            else fuse_rrf(lexical, dense, request.top_k)
        )

    return {
        "knowledge_base_id": base_id,
        "question": request.question,
        "mode": request.mode,
        "items": items,
        "total": len(items),
        "skipped_documents": skipped,
    }


def session_history(connection, base_id, session_id):
    """校验会话归属，并按从旧到新的顺序返回最近 5 轮问答。"""
    require_base(connection, base_id)
    # 同时检查两个 ID，避免读取其他知识库的会话。
    session = connection.execute(
        """
        SELECT id FROM knowledge_base_sessions
        WHERE id = ? AND knowledge_base_id = ?
        """,
        (session_id, base_id),
    ).fetchone()
    if not session:
        raise HTTPException(404, "会话不存在或不属于当前知识库")

    rows = connection.execute(
        """
        SELECT question, answer FROM knowledge_base_turns
        WHERE knowledge_base_id = ? AND session_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (base_id, session_id,RAG_HISTORY_TURNS),
    ).fetchall()
    # 倒序取最新记录，再反转成模型需要的时间顺序。
    return [dict(row) for row in reversed(rows)]


def save_turn(connection, base_id, request, rewritten, answer, citations, chunks):
    """保存问答、引用快照和来源关联；事务由调用方负责提交。"""
    # 回答生成期间成员可能被删除或移出，保存前再次检查会话与成员。
    session_history(connection, base_id, request.session_id)
    current_ids = {d["id"] for d in members(connection, base_id)}
    source_ids = {c["document_id"] for c in chunks}
    if not source_ids.issubset(current_ids):
        raise ValueError("知识库成员发生变化，本轮未保存，请重新提问")

    # 原始问题用于展示，改写问题记录实际检索内容；引用以 JSON 保存快照。
    cursor = connection.execute(
        """
        INSERT INTO knowledge_base_turns
            (knowledge_base_id, session_id, question, retrieval_question,
             answer, citations_json, mode)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            base_id,
            request.session_id,
            request.question,
            rewritten,
            answer,
            json.dumps(citations, ensure_ascii=False),
            request.mode,
        ),
    )
    turn_id = cursor.lastrowid

    # 记录所有检索来源文档，便于删除原文档时清理相关问答历史。
    connection.executemany(
        "INSERT INTO knowledge_base_turn_documents VALUES (?, ?)",
        [(turn_id, doc) for doc in source_ids],
    )
    return turn_id
