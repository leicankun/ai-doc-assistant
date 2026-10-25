"""真实 MySQL 测试，使用 MYSQL_TEST_* 连接独立服务并创建随机临时库。"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pymysql
from sqlalchemy.engine import URL

from storage import database
from routers import documents, knowledge_bases, rag
from scripts.migrate_sqlite_to_mysql import migrate


class ParameterBindingTests(unittest.TestCase):
    def test_quoted_question_marks_comments_percent_and_parameter_values(self):
        sql, params = database.bind_sql("SELECT '?', `?`, ? /* ? */ -- ?\n, '100%'", ["'; DROP TABLE documents;--%?"])
        self.assertEqual(sql, "SELECT '?', `?`, %s /* ? */ -- ?\n, '100%%'")
        self.assertEqual(params, ("'; DROP TABLE documents;--%?",))
        with self.assertRaises(ValueError):
            database.bind_sql("SELECT ?, ?", [1])


@unittest.skipUnless(os.getenv("MYSQL_TEST_PORT"), "需要独立 MySQL：scripts/test_mysql_sandbox.py")
class MySQLTests(unittest.TestCase):
    def setUp(self):
        self.admin = pymysql.connect(
            host=os.getenv("MYSQL_TEST_HOST", "127.0.0.1"), port=int(os.environ["MYSQL_TEST_PORT"]),
            user=os.getenv("MYSQL_TEST_USER", "root"), password=os.getenv("MYSQL_TEST_PASSWORD", ""),
            charset="utf8mb4", autocommit=True,
        )
        self.name = "test_ai_doc_" + uuid4().hex
        with self.admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        url = URL.create("mysql+pymysql", username=os.getenv("MYSQL_TEST_USER", "root"),
                         password=os.getenv("MYSQL_TEST_PASSWORD", ""),
                         host=os.getenv("MYSQL_TEST_HOST", "127.0.0.1"),
                         port=int(os.environ["MYSQL_TEST_PORT"]), database=self.name, query={"charset": "utf8mb4"})
        self.url = url
        self.patcher = patch.object(database, "database_url", return_value=url)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.cleanup_database)
        database.init_db()
        self.db = database.connect()
        self.addCleanup(self.db.close)

    def cleanup_database(self):
        database.get_engine(self.url).dispose()
        with self.admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE `{self.name}`")
        self.admin.close()

    def client(self):
        app = FastAPI()
        app.include_router(documents.router)
        result = TestClient(app)
        self.addCleanup(result.close)
        return result

    def test_schema_unicode_long_text_defaults_and_matching_rowcount(self):
        database.init_db()
        title = "中文😀' ? 100%" * 8000
        with self.db:
            result = self.db.execute("INSERT INTO documents(title,file_type) VALUES (?,?)", (title, "markdown"))
        self.assertGreater(result.lastrowid, 0)
        row = self.db.execute("SELECT * FROM documents WHERE id=?", (result.lastrowid,)).fetchone()
        self.assertEqual(row["title"], title)
        self.assertEqual(row[0], result.lastrowid)
        with self.db:
            self.assertEqual(self.db.execute("UPDATE documents SET title=? WHERE id=?", (title, row[0])).rowcount, 1)
            self.db.execute("INSERT INTO knowledge_base_sessions(knowledge_base_id,title) VALUES (?,?)", (1, "会话"))
        row = self.db.execute("SELECT * FROM knowledge_base_sessions").fetchone()
        self.assertRegex(row["created_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_transaction_rollback_close_and_fresh_reads(self):
        with self.assertRaises(RuntimeError):
            with self.db:
                self.db.execute("INSERT INTO documents(title,file_type) VALUES ('rollback','markdown')")
                raise RuntimeError("abort")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.db.execute("SELECT * FROM documents").fetchall()
        with closing(database.connect()) as other:
            with other:
                other.execute("INSERT INTO documents(title,file_type) VALUES ('committed','markdown')")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        with closing(database.connect()) as abandoned:
            abandoned.execute("INSERT INTO documents(title,file_type) VALUES ('uncommitted','markdown')")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)

    def test_duplicate_only_ignored_and_summary_revision_guard(self):
        sql = "INSERT INTO knowledge_base_documents VALUES (?,?)"
        with self.db:
            self.assertEqual(database.insert_if_absent(self.db, sql, (1, 2)).rowcount, 1)
            self.assertEqual(database.insert_if_absent(self.db, sql, (1, 2)).rowcount, 0)
            with self.assertRaises(pymysql.IntegrityError):
                database.insert_if_absent(self.db, sql, (None, 3))
            summary = "INSERT INTO agent_session_summaries(knowledge_base_id,session_id,summary_json,covered_turn_id,source_hash) VALUES (?,?,?,?,?)"
            self.assertEqual(database.insert_if_absent(self.db, summary, (1,1,'{}',0,'hash')).rowcount, 1)
            self.assertEqual(database.insert_if_absent(self.db, summary, (1,1,'{}',0,'hash')).rowcount, 0)
            self.assertEqual(self.db.execute("UPDATE agent_session_summaries SET revision=revision+1 WHERE knowledge_base_id=? AND session_id=? AND revision=?", (1,1,1)).rowcount, 1)
            self.assertEqual(self.db.execute("UPDATE agent_session_summaries SET revision=revision+1 WHERE knowledge_base_id=? AND session_id=? AND revision=?", (1,1,1)).rowcount, 0)

    def test_document_and_membership_api_atomicity(self):
        client = self.client()
        response = client.post("/documents", json={"title":"中文文档😀", "file_type":"markdown"})
        self.assertEqual(response.status_code, 201, response.text)
        doc = response.json()["id"]
        response = client.post("/knowledge-bases", json={"title":"项目资料"})
        self.assertEqual(response.status_code, 201, response.text)
        base = response.json()["id"]
        for _ in range(2):
            response = client.post(f"/knowledge-bases/{base}/documents", json={"document_ids":[doc]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["items"]), 1)
        response = client.post(f"/knowledge-bases/{base}/documents", json={"document_ids":[doc,999999]})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM knowledge_base_documents").fetchone()[0], 1)
        response = client.put(f"/documents/{doc}", json={"title":"中文文档😀", "file_type":"markdown"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.delete(f"/knowledge-bases/{base}").status_code, 200)
        self.assertIsNotNone(self.db.execute("SELECT id FROM documents WHERE id=?", (doc,)).fetchone())

    def test_stream_history_uses_independent_mysql_connection(self):
        with self.db:
            self.db.execute("INSERT INTO documents(id,title,file_type,chunk_version) VALUES (1,'Guide','markdown',1)")
            self.db.execute("INSERT INTO document_chunks(id,document_id,chunk_index,content) VALUES (10,1,0,'数据库使用 MySQL。')")
            self.db.execute("INSERT INTO document_sessions(id,document_id,title) VALUES (1,1,'chat')")

        async def events(*args, **kwargs):
            yield {"type":"delta", "content":"数据库使用 MySQL[1]"}
            yield {"type":"done", "citations":[{"citation_id":1,"document_id":1,"chunk_id":10}]}

        with patch.object(rag, "rewrite_question", return_value="数据库"), \
             patch.object(rag, "stream_answer_with_chunks", side_effect=events), \
             patch.dict(os.environ, {"DEEPSEEK_API_KEY":"test", "RERANK_ENABLED":"false"}):
            response = self.client().post("/documents/1/ask-rag/chat/stream", json={"session_id":1,"question":"数据库是什么", "mode":"bm25"})
        self.assertEqual(response.status_code, 200, response.text)
        frames = [json.loads(s.removeprefix("data: ")) for s in response.text.strip().split("\n\n")]
        self.assertEqual(frames[-1]["type"], "done", frames)
        row = self.db.execute("SELECT * FROM rag_turns").fetchone()
        self.assertEqual(row["answer"], "数据库使用 MySQL[1]")
        self.assertEqual(json.loads(row["citations_json"])[0]["chunk_id"], 10)

    def test_migration_preserves_ids_body_and_refuses_nonempty_target(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.db"
            database.init_test_db(path)
            with closing(database.connect_test_db(path)) as source, source:
                source.execute("INSERT INTO documents(id,title,file_type,chunk_version,indexed_version) VALUES (42,?,'markdown',3,3)", ("中文😀",))
                source.execute("INSERT INTO document_chunks(id,document_id,chunk_index,content) VALUES (71,42,0,?)", ("证据😀"*30000,))
                source.execute("INSERT INTO knowledge_bases(id,title) VALUES (5,'资料库')")
                source.execute("INSERT INTO knowledge_base_documents VALUES (5,42)")
                source.execute("CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL)")
                source.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL)")
                source.execute("INSERT INTO chat_sessions VALUES (8,'practice')")
                source.execute("INSERT INTO chat_messages VALUES (9,8,'user','retained')")
            original = hashlib.sha256(path.read_bytes()).hexdigest()
            report = migrate(path)
            self.assertEqual(report["tables"]["documents"]["rows"], 1)
            self.assertEqual(report["tables"]["chat_messages"]["rows"], 1)
            self.assertEqual(migrate(path, verify_only=True)["status"], "verified")
            with self.assertRaisesRegex(ValueError, "必须全部为空"):
                migrate(path)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
            self.assertEqual(self.db.execute("SELECT id FROM document_chunks").fetchone()[0], 71)
            with self.db:
                new_id = self.db.execute("INSERT INTO documents(title,file_type) VALUES ('new','markdown')").lastrowid
            self.assertGreater(new_id, 42)

    def test_failed_migration_rolls_back_all_inserted_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.db"
            database.init_test_db(path)
            with closing(database.connect_test_db(path)) as source, source:
                source.execute("INSERT INTO documents(id,title,file_type) VALUES (42,'ok','markdown')")
                source.execute("INSERT INTO knowledge_base_turns(knowledge_base_id,session_id,question,retrieval_question,answer,citations_json,mode) VALUES (1,1,'q','q','a','[]',?)", ("x" * 100,))
            with self.assertRaises(pymysql.DataError):
                migrate(path)
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_existing_source_migration(self):
        path = os.getenv("MYSQL_MIGRATION_SOURCE")
        if not path:
            self.skipTest("可通过 --migration-source 提供现有 SQLite 的只读预检")
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        report = migrate(path)
        self.assertEqual(migrate(path, verify_only=True)["status"], "verified")
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), before)
        print("\nExisting source migrated and verified:", {name: table["rows"] for name, table in report["tables"].items()})


if __name__ == "__main__":
    unittest.main()
