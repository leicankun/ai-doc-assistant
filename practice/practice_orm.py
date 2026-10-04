import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import (
    create_engine,
    Integer,
    String,
    ForeignKey,
    select,
    event,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column, Session

from config import DB_PATH


# 创建数据库引擎，echo=True 会在终端显示执行的 SQL
engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    echo=True,
)


# SQLite 每次建立连接时，开启外键约束检查
@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


# 数据库模型的公共基类
class Base(DeclarativeBase):
    pass


# 聊天会话表
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = mapped_column(Integer, primary_key=True)
    title = mapped_column(String, nullable=False)


# 聊天消息表
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = mapped_column(Integer, primary_key=True)

    # 关联聊天会话表的 ID
    session_id = mapped_column(
        Integer,
        ForeignKey("chat_sessions.id"),
        nullable=False,
    )

    role = mapped_column(String, nullable=False)
    content = mapped_column(String, nullable=False)


if __name__ == "__main__":
    # 创建不存在的表，不会清空已有数据
    Base.metadata.create_all(engine)

    with Session(engine) as db_session:
        # 1. 创建聊天会话
        chat = ChatSession(title="Python 字典讨论")
        db_session.add(chat)

        # 执行插入，取得 ID，但暂不提交事务
        db_session.flush()
        chat_id = chat.id

        # 2. 创建两条消息，关联到刚才的会话
        question = ChatMessage(
            session_id=chat_id,
            role="user",
            content="什么是 Python 字典？",
        )

        answer = ChatMessage(
            session_id=chat_id,
            role="assistant",
            content="字典用来保存键值对。",
        )

        db_session.add_all([question, answer])

        # 一起提交会话和消息
        db_session.commit()

        print("新增聊天会话的 ID：", chat_id)

        # 3. JOIN 查询本次会话的标题和消息
        statement = (
            select(
                ChatSession.title,
                ChatMessage.role,
                ChatMessage.content,
            )
            .join(
                ChatMessage,
                ChatSession.id == ChatMessage.session_id,
            )
            .where(ChatSession.id == chat_id)
            .order_by(ChatMessage.id)
        )

        rows = db_session.execute(statement).all()

        # 4. 打印查询结果
        for title, role, content in rows:
            print(title, role, content)