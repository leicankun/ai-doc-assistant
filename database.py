import sqlite3
from config import DB_PATH




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