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