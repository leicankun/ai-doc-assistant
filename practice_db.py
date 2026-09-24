import sqlite3

from database import DB_PATH, init_db


init_db()

connection = sqlite3.connect(DB_PATH)

try:
    # 添加一条文档
    insert_cursor = connection.execute(
        "INSERT INTO documents (title, file_type) VALUES (?, ?)",
        ("数据库学习笔记", "markdown"),
    )

    connection.commit()
    print("新增文档的 ID：", insert_cursor.lastrowid)

    # 查询所有文档
    query_cursor = connection.execute(
        "SELECT id, title, file_type FROM documents"
    )

    rows = query_cursor.fetchall()

    for row in rows:
        print(row)

finally:
    connection.close()