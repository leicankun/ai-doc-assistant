"""MySQL 8 业务表；长正文使用 LONGTEXT，迁移保留原始 ID。"""
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, MetaData, String, Table, UniqueConstraint, text
from sqlalchemy.dialects.mysql import LONGTEXT

metadata = MetaData()


def id_column():
    return Column("id", BigInteger, primary_key=True, autoincrement=True)


def ref(name, *, primary_key=False, nullable=False):
    return Column(name, BigInteger, primary_key=primary_key, nullable=nullable, autoincrement=False)


def body(name, *, nullable=False, default=None):
    return Column(name, LONGTEXT, nullable=nullable, server_default=default)


def timestamp(name="created_at"):
    # MySQL 存储 DATETIME；连接会话使用 UTC，返回行时转换回旧 API 字符串格式。
    return Column(name, DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))


def table(name, *columns):
    return Table(name, metadata, *columns, mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")


table("documents", id_column(), body("title"), Column("file_type", String(32), nullable=False),
      body("stored_name", nullable=True), Column("chunk_version", Integer, nullable=False, server_default="0"),
      Column("indexed_version", Integer))
table("question_answers", id_column(), ref("document_id"), body("question"), body("answer"),
      timestamp(), ref("session_id", nullable=True), Index("idx_qa_document_session", "document_id", "session_id", "id"))
table("document_sessions", id_column(), ref("document_id"), body("title"), timestamp(), Index("idx_sessions_document", "document_id"))
table("document_chunks", id_column(), ref("document_id"), Column("chunk_index", Integer, nullable=False),
      body("content"), body("source_name", nullable=True), Column("page_number", Integer),
      Column("start_char", Integer), Column("end_char", Integer), UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk"))
table("rag_turns", id_column(), ref("document_id"), ref("session_id"), body("question"),
      body("retrieval_question"), body("answer"), body("citations_json"), Column("mode", String(32), nullable=False),
      timestamp(), Index("idx_rag_turns_session", "document_id", "session_id", "id"))
table("knowledge_bases", id_column(), body("title"), timestamp())
table("knowledge_base_documents", ref("knowledge_base_id", primary_key=True), ref("document_id", primary_key=True),
      Index("idx_kb_documents_document", "document_id"))
table("knowledge_base_sessions", id_column(), ref("knowledge_base_id"), body("title"), timestamp(),
      Index("idx_kb_sessions_base", "knowledge_base_id"))
table("knowledge_base_turns", id_column(), ref("knowledge_base_id"), ref("session_id"), body("question"),
      body("retrieval_question"), body("answer"), body("citations_json"), Column("mode", String(32), nullable=False),
      timestamp(), body("tool_calls_json", default=text("('[]')")),
      Index("idx_kb_turns_session", "knowledge_base_id", "session_id", "id"))
table("knowledge_base_turn_documents", ref("turn_id", primary_key=True), ref("document_id", primary_key=True),
      Index("idx_kb_turn_documents_document", "document_id"))
table("agent_session_summaries", ref("knowledge_base_id", primary_key=True), ref("session_id", primary_key=True),
      body("summary_json"), ref("covered_turn_id"), Column("source_hash", String(64), nullable=False),
      Column("revision", Integer, nullable=False, server_default="1"), timestamp("updated_at"))

# 旧库还包含独立 ORM 练习数据，一并保留；业务接口不使用这些表。
table("chat_sessions", id_column(), body("title"))
table("chat_messages", id_column(), ref("session_id"), Column("role", String(32), nullable=False), body("content"))
