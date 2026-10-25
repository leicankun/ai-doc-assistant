import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import (
    create_engine,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Session

from config import database_url
from storage.schema import metadata


# ORM Session 使用普通事务引擎。
engine = create_engine(
    database_url(),
    pool_pre_ping=True,
    hide_parameters=True,
)


# 数据库模型的公共基类
class Base(DeclarativeBase):
    pass


# 聊天会话表
class ChatSession(Base):
    __table__ = metadata.tables["chat_sessions"]


# 聊天消息表
class ChatMessage(Base):
    __table__ = metadata.tables["chat_messages"]


if __name__ == "__main__":
    # 创建不存在的表，不会清空已有数据
    metadata.create_all(engine, tables=[ChatSession.__table__, ChatMessage.__table__])

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
