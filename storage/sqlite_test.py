"""仅供隔离测试和历史评测使用；生产连接由 storage.database 提供。"""
import sqlite3





def init_test_db(path):
    connection = sqlite3.connect(path)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                file_type TEXT NOT NULL,
                 stored_name TEXT
            )
        """)
        # 查询当前表的字段信息
        rows = connection.execute(
            "PRAGMA table_info(documents)"
        ).fetchall()

        # 每行的第 2 项是字段名
        column_names = [row[1] for row in rows]

        # 兼容以前创建的数据库：缺少字段时才添加
        if "stored_name" not in column_names:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN stored_name TEXT"
            )

        for name, definition in (("chunk_version", "INTEGER NOT NULL DEFAULT 0"), ("indexed_version", "INTEGER")):
            if name not in column_names:
                connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")

        connection.execute("""
            CREATE TABLE IF NOT EXISTS question_answers (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """)
          # 检查问答表是否已有 session_id 字段
        rows = connection.execute(
            "PRAGMA table_info(question_answers)"
        ).fetchall()

        column_names = [row[1] for row in rows]

        # 为已有数据库补充会话编号
        if "session_id" not in column_names:
            connection.execute(
                "ALTER TABLE question_answers ADD COLUMN session_id INTEGER"
            )

        # 一篇文档可以创建多个问答会话
        connection.execute("""
            CREATE TABLE IF NOT EXISTS document_sessions (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


        # 保存文档分块
        connection.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                UNIQUE (document_id, chunk_index)
            )
        """)

        # 旧分块的来源未知，新增字段保留 NULL；重新生成后填充。
        chunk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(document_chunks)")
        }
        for name, column_type in (
            ("source_name", "TEXT"),
            ("page_number", "INTEGER"),
            ("start_char", "INTEGER"),
            ("end_char", "INTEGER"),
        ):
            if name not in chunk_columns:
                connection.execute(
                    f"ALTER TABLE document_chunks ADD COLUMN {name} {column_type}"
                )

        # RAG 历史单独保存，避免与全文问答历史混用。
        connection.execute("""
            CREATE TABLE IF NOT EXISTS rag_turns (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                retrieval_question TEXT NOT NULL,
                answer TEXT NOT NULL,
                citations_json TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_rag_turns_session
            ON rag_turns(document_id, session_id, id)
        """)

        connection.executescript('''
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_base_documents (
                knowledge_base_id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                PRIMARY KEY (knowledge_base_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_base_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_base_id INTEGER NOT NULL,
                title TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS knowledge_base_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_base_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL, question TEXT NOT NULL,
                retrieval_question TEXT NOT NULL, answer TEXT NOT NULL,
                citations_json TEXT NOT NULL, mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_kb_turns_session
                ON knowledge_base_turns(knowledge_base_id, session_id, id);
            CREATE TABLE IF NOT EXISTS knowledge_base_turn_documents (
                turn_id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                PRIMARY KEY (turn_id, document_id)
            );
            CREATE TABLE IF NOT EXISTS agent_session_summaries (
                knowledge_base_id INTEGER NOT NULL, session_id INTEGER NOT NULL,
                summary_json TEXT NOT NULL, covered_turn_id INTEGER NOT NULL,
                source_hash TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (knowledge_base_id, session_id)
            );
        ''')
        columns = {row[1] for row in connection.execute('PRAGMA table_info(knowledge_base_turns)')}
        if 'tool_calls_json' not in columns:
            connection.execute("ALTER TABLE knowledge_base_turns ADD COLUMN tool_calls_json TEXT NOT NULL DEFAULT '[]'")
        connection.commit()
    finally:
        connection.close()
