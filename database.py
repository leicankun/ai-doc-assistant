import sqlite3
from config import DB_PATH




def init_db():
    connection = sqlite3.connect(DB_PATH)

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

        connection.commit()
    finally:
        connection.close()

def get_connection():
    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row

    try:
        yield connection
    finally:
        connection.close()

if __name__ == "__main__":
    init_db()
    print("数据库和文档表已准备好")
