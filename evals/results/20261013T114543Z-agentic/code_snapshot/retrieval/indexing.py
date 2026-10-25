import sqlite3

from retrieval.embedding import embed_texts
from storage.vector_store import get_collection


def index_document(
    document_id: int,
    connection: sqlite3.Connection,
):
    # 1. 检查文档是否存在。
    document = connection.execute(
        "SELECT id, chunk_version FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise ValueError("文档不存在")
    chunk_version = document["chunk_version"]

    # 2. 从 SQLite 读取当前文档的分块。
    # connection 需要设置 row_factory = sqlite3.Row。
    rows = connection.execute(
        """
        SELECT id, document_id, chunk_index, content,
               source_name, page_number, start_char, end_char
        FROM document_chunks
        WHERE document_id = ?
        ORDER BY chunk_index
        """,
        (document_id,),
    ).fetchall()

    chunks = [dict(row) for row in rows]

    if not chunks:
        raise ValueError("该文档尚未生成分块")

    # 3. 提取正文，并生成向量。
    texts = [chunk["content"] for chunk in chunks]
    vectors = embed_texts(texts)

    if len(vectors) != len(chunks):
        raise RuntimeError("向量数量与分块数量不一致")

    # 4. 准备每个分块的标识和来源信息。
    ids = []
    metadatas = []

    for chunk in chunks:
        # Chroma 的记录 ID 使用字符串。
        record_id = f"doc:{document_id}:chunk:{chunk['id']}"
        ids.append(record_id)

        metadata = {
            "document_id": document_id,
            "chunk_id": chunk["id"],
            "chunk_index": chunk["chunk_index"],
        }

        # 没有页码等信息时，省略相应字段。
        for field in (
            "source_name",
            "page_number",
            "start_char",
            "end_char",
        ):
            if chunk[field] is not None:
                metadata[field] = chunk[field]

        metadatas.append(metadata)

    # 5. 打开 Chroma 集合，查询当前文档已有的向量记录。
    collection = get_collection()

    # 后续写入可能部分失败，先撤销“已入库”，避免使用不完整索引。
    connection.execute("UPDATE documents SET indexed_version = -1 WHERE id = ?", (document_id,))
    connection.commit()

    old_records = collection.get(
        where={"document_id": document_id},
        include=["metadatas"],
    )

    old_ids = set(old_records["ids"])

    # 6. 分批写入：相同 ID 更新，不存在的 ID 新增。
    write_batch_size = 100

    for start in range(0, len(ids), write_batch_size):
        end = start + write_batch_size

        collection.upsert(
            ids=ids[start:end],
            embeddings=vectors[start:end],
            documents=texts[start:end],
            metadatas=metadatas[start:end],
        )

    # 7. 新记录全部写入成功后，删除失效的旧记录。
    # 例如重新分块后，有些旧块 ID 已不在 SQLite 中。
    stale_ids = sorted(old_ids - set(ids))

    for start in range(0, len(stale_ids), write_batch_size):
        collection.delete(
            ids=stale_ids[start:start + write_batch_size],
        )

    # 8. 检查保存后的记录 ID 是否与当前分块一致。
    saved_records = collection.get(
        where={"document_id": document_id},
        include=["metadatas"],
    )

    if set(saved_records["ids"]) != set(ids):
        raise RuntimeError("向量入库后的记录与当前分块不一致")

    # 入库期间重新分块时，旧版本不能被标记为最新索引。
    cursor = connection.execute(
        "UPDATE documents SET indexed_version = ? WHERE id = ? AND chunk_version = ?",
        (chunk_version, document_id, chunk_version),
    )
    connection.commit()
    if cursor.rowcount != 1:
        raise RuntimeError("分块在入库期间发生变化，请重新向量入库")

    return {
        "document_id": document_id,
        "indexed_chunks": len(ids),
        "removed_chunks": len(stale_ids),
        "message": "文档向量入库成功",
    }
