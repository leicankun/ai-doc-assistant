from pathlib import Path
import os
from dotenv import load_dotenv
from sqlalchemy.engine import URL


BASE_DIR = Path(__file__).resolve().parent
# 所有入口（包括命令行迁移和 SSE worker）使用同一份配置。
load_dotenv(BASE_DIR / ".env")
DB_PATH = BASE_DIR / "documents.db"
UPLOAD_DIR = BASE_DIR / "uploads"
CHROMA_DIR = BASE_DIR / "chroma_data"
RAG_HISTORY_TURNS = 5


def database_url():
    """只连接 MySQL；不因配置缺失而退回旧 SQLite 文件。"""
    if "MYSQL_PASSWORD" not in os.environ:
        raise RuntimeError("请在 .env 配置 MYSQL_PASSWORD 及 MySQL 连接信息，参见 docs/mysql.md")
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", "ai_doc_assistant"),
        password=os.environ["MYSQL_PASSWORD"],
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "ai_doc_assistant"),
        query={"charset": "utf8mb4"},
    )
