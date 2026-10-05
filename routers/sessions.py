"""会话与历史接口。"""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException
from database import get_connection
from schemas import SessionCreate

router = APIRouter(prefix="/documents", tags=["会话与历史"])


# 查询指定文档所有会话的问答历史，按记录 ID 从小到大返回。
@router.get("/{document_id}/history")
def get_document_history(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 按记录 ID 升序，先显示较早保存的问答
    rows = connection.execute(
        """
        SELECT id, document_id, question, answer, created_at
        FROM question_answers
        WHERE document_id = ?
        ORDER BY id ASC
        """,
        (document_id,),
    ).fetchall()

    items = [dict(row) for row in rows]
    return {
        "items": items,
        "total": len(items),
    }


# 为指定文档创建问答会话，返回会话信息。
@router.post("/{document_id}/sessions", status_code=201)
def create_document_session(
    document_id: int,
    session: SessionCreate,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 先确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 保存会话
    try:
        cursor = connection.execute(
            """
            INSERT INTO document_sessions (document_id, title)
            VALUES (?, ?)
            """,
            (document_id, session.title),
        )
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise HTTPException(status_code=500, detail="创建会话失败")

    return {
        "id": cursor.lastrowid,
        "document_id": document_id,
        "title": session.title,
    }


# 查询指定文档的全部问答会话，按新到旧返回。
@router.get("/{document_id}/sessions")
def list_document_sessions(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 2. 查询这篇文档的会话
    rows = connection.execute(
        """
        SELECT id, document_id, title, created_at
        FROM document_sessions
        WHERE document_id = ?
        ORDER BY id DESC
        """,
        (document_id,),
    ).fetchall()

    # 3. 转换成字典列表并返回
    items = [dict(row) for row in rows]

    return {
        "items": items,
        "total": len(items),
    }


# 校验会话归属，查询指定文档下该会话的问答历史。
@router.get("/{document_id}/sessions/{session_id}/history")
def get_session_history(
    document_id: int,
    session_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 确认文档存在
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 2. 确认会话存在，并且属于当前文档
    session = connection.execute(
        """
        SELECT id
        FROM document_sessions
        WHERE id = ? AND document_id = ?
        """,
        (session_id, document_id),
    ).fetchone()

    if session is None:
        raise HTTPException(
            status_code=404,
            detail="会话不存在或不属于当前文档",
        )

    # 3. 只查询这个会话中的问答
    rows = connection.execute(
        """
        SELECT id, document_id, session_id, question, answer, created_at
        FROM question_answers
        WHERE document_id = ? AND session_id = ?
        ORDER BY id ASC
        """,
        (document_id, session_id),
    ).fetchall()

    items = [dict(row) for row in rows]

    return {
        "items": items,
        "total": len(items),
    }
