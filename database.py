import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "documents.db"


def init_db():
    connection = sqlite3.connect(DB_PATH)

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                file_type TEXT NOT NULL
            )
        """)

        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    init_db()
    print("数据库和文档表已准备好")