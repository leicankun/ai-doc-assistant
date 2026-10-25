"""知识库管理、成员关联、检索和多轮流式问答。"""
import json
import logging
import os
import sqlite3
import asyncio
import threading
from contextlib import aclosing, closing
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import APIError, APITimeoutError
from starlette.concurrency import run_in_threadpool
from config import DB_PATH
from storage.database import get_connection
from schemas import KnowledgeBaseCreate, KnowledgeBaseDocuments, SessionCreate, SearchRequest, RagChatRequest, AgentRequest
from services.knowledge_base import require_base, members, ready_documents, search_base, session_history, save_turn
from services.rag_chat import rewrite_question
from services.llm import answer_with_chunks, stream_answer_with_chunks
from agent import run_document_agent
from agent.runner import AgentCancelled

router = APIRouter(prefix='/knowledge-bases', tags=['知识库'])
logger = logging.getLogger(__name__)


@router.post('/{base_id}/agent')
async def document_agent(base_id:int,body:AgentRequest,http_request:Request,connection=Depends(get_connection)):
    """断开连接后协作取消，在外部调用结束的检查点终止，不再保存。"""
    stop = threading.Event()
    task = asyncio.create_task(run_in_threadpool(_document_agent,base_id,body,connection,stop))
    try:
        while not task.done():
            await asyncio.wait({task},timeout=0.2)
            if await http_request.is_disconnected():
                stop.set()
        return await task
    finally:
        stop.set()
        # 连接由请求依赖持有，必须等待 worker 结束后才能释放。
        if not task.done():
            try:
                await asyncio.shield(task)
            except Exception:
                pass


def _document_agent(base_id,body,connection,stop):
    """Agent 问答；提供会话 ID 时加载历史并保存回答、引用和工具记录。"""
    try:
        require_base(connection,base_id)
        if not os.getenv('DEEPSEEK_API_KEY'):
            raise HTTPException(503,'服务尚未配置回答模型 API Key')
        previous = session_history(connection,base_id,body.session_id) if body.session_id else []
        result = run_document_agent(connection,base_id,body.question,history=previous,
                                    cancelled=stop.is_set,session_id=body.session_id)
        if stop.is_set():
            raise AgentCancelled('已停止执行，本轮不保存')
        turn_id = None
        if body.session_id:
            sources = [item for call in result['tool_calls'] for item in call['result'].get('items', [])]
            request = RagChatRequest(question=body.question,session_id=body.session_id,mode='hybrid')
            with connection:
                turn_id = save_turn(connection,base_id,request,body.question,
                                    result['answer'],result['citations'],sources)
                connection.execute('UPDATE knowledge_base_turns SET mode=?, tool_calls_json=? WHERE id=?',
                                   ('agent',json.dumps(result['tool_calls'],ensure_ascii=False),turn_id))
        return {**result,'knowledge_base_id':base_id,'question':body.question,
                'session_id':body.session_id,'id':turn_id,'mode':'agent'}
    except Exception as exc:
        raise translate_error(exc) from exc


def translate_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc,AgentCancelled):
        return HTTPException(499,'已停止执行，本轮未保存')
    if isinstance(exc,TimeoutError):
        return HTTPException(504,'Agent 执行超时，本轮未保存')
    if isinstance(exc, APITimeoutError):
        return HTTPException(504,'模型调用超时，请重试')
    if isinstance(exc, APIError):
        return HTTPException(502,'模型调用失败，请检查配置')
    if isinstance(exc, ValueError):
        return HTTPException(502,str(exc))
    logger.exception('知识库处理失败')
    return HTTPException(500,'知识库处理失败，请查看后端日志')


# 查询全部知识库与文档数量。
@router.get('')
def list_bases(connection=Depends(get_connection)):
    items = [dict(r) for r in connection.execute('''SELECT b.*,
        (SELECT COUNT(*) FROM knowledge_base_documents m WHERE m.knowledge_base_id=b.id) AS document_count
        FROM knowledge_bases b ORDER BY b.id DESC''')]
    return {'items':items,'total':len(items)}


# 创建空知识库。
@router.post('',status_code=201)
def create_base(body:KnowledgeBaseCreate,connection=Depends(get_connection)):
    with connection:
        cursor = connection.execute('INSERT INTO knowledge_bases(title) VALUES (?)',(body.title,))
    return require_base(connection,cursor.lastrowid)


# 查看知识库详情和所有成员文档的处理状态。
@router.get('/{base_id}')
def get_base(base_id:int,connection=Depends(get_connection)):
    return {**require_base(connection,base_id),'documents':members(connection,base_id)}


# 重命名知识库。
@router.put('/{base_id}')
def rename_base(base_id:int,body:KnowledgeBaseCreate,connection=Depends(get_connection)):
    require_base(connection,base_id)
    with connection:
        connection.execute('UPDATE knowledge_bases SET title=? WHERE id=?',(body.title,base_id))
    return require_base(connection,base_id)


# 删除知识库、成员关联及其会话历史，保留原文档和向量。
@router.delete('/{base_id}')
def delete_base(base_id:int,connection=Depends(get_connection)):
    require_base(connection,base_id)
    with connection:
        connection.execute('DELETE FROM knowledge_base_turn_documents WHERE turn_id IN (SELECT id FROM knowledge_base_turns WHERE knowledge_base_id=?)',(base_id,))
        for table in ('agent_session_summaries','knowledge_base_turns','knowledge_base_sessions','knowledge_base_documents'):
            connection.execute(f'DELETE FROM {table} WHERE knowledge_base_id=?',(base_id,))
        connection.execute('DELETE FROM knowledge_bases WHERE id=?',(base_id,))
    return {'id':base_id,'message':'知识库删除成功，原文档保留'}


# 批量添加已有文档，可重复调用；缺失文档导致整批失败。
@router.post('/{base_id}/documents')
def add_documents(base_id:int,body:KnowledgeBaseDocuments,connection=Depends(get_connection)):
    require_base(connection,base_id)
    with connection:
        for doc in body.document_ids:
            if not connection.execute('SELECT id FROM documents WHERE id=?',(doc,)).fetchone():
                raise HTTPException(404,f'文档 {doc} 不存在')
        connection.executemany('INSERT OR IGNORE INTO knowledge_base_documents VALUES (?,?)',[(base_id,doc) for doc in body.document_ids])
    return {'items':members(connection,base_id)}


# 移出一个成员文档，只解除关联，保留原文档和既有历史。
@router.delete('/{base_id}/documents/{document_id}')
def remove_document(base_id:int,document_id:int,connection=Depends(get_connection)):
    require_base(connection,base_id)
    with connection:
        cursor = connection.execute('DELETE FROM knowledge_base_documents WHERE knowledge_base_id=? AND document_id=?',(base_id,document_id))
        if not cursor.rowcount:
            raise HTTPException(404,'文档不在当前知识库')
    return {'message':'已移出知识库，原文档保留'}


# 在知识库所有文档中统一检索 Top-K。
@router.post('/{base_id}/search')
def search(base_id:int,body:SearchRequest,connection=Depends(get_connection)):
    try:
        return search_base(connection,base_id,body)
    except Exception as exc:
        raise translate_error(exc) from exc


# 根据知识库检索资料生成单轮带引用回答。
@router.post('/{base_id}/ask-rag')
def ask(base_id:int,body:SearchRequest,connection=Depends(get_connection)):
    try:
        result = search_base(connection,base_id,body)
        return {**result,**answer_with_chunks(body.question,result['items'])}
    except Exception as exc:
        raise translate_error(exc) from exc


# 创建属于知识库的独立会话。
@router.post('/{base_id}/sessions',status_code=201)
def create_session(base_id:int,body:SessionCreate,connection=Depends(get_connection)):
    require_base(connection,base_id)
    with connection:
        cursor = connection.execute('INSERT INTO knowledge_base_sessions(knowledge_base_id,title) VALUES (?,?)',(base_id,body.title))
    return {'id':cursor.lastrowid,'knowledge_base_id':base_id,'title':body.title}


# 查询知识库会话列表。
@router.get('/{base_id}/sessions')
def sessions(base_id:int,connection=Depends(get_connection)):
    require_base(connection,base_id)
    return {'items':[dict(r) for r in connection.execute('SELECT * FROM knowledge_base_sessions WHERE knowledge_base_id=? ORDER BY id DESC',(base_id,))]}


# 查询知识库指定会话的完整 RAG 历史与引用快照。
@router.get('/{base_id}/sessions/{session_id}/rag-history')
def history(base_id:int,session_id:int,connection=Depends(get_connection)):
    session_history(connection,base_id,session_id)
    items=[]
    for row in connection.execute('SELECT * FROM knowledge_base_turns WHERE knowledge_base_id=? AND session_id=? ORDER BY id',(base_id,session_id)):
        item=dict(row)
        item['citations']=json.loads(item.pop('citations_json'))
        item['tool_calls']=json.loads(item.pop('tool_calls_json'))
        items.append(item)
    return {'items':items,'total':len(items)}


def prepare(connection,base_id,body):
    history = session_history(connection,base_id,body.session_id)
    ready_documents(connection,base_id,body.mode)
    if not os.getenv('DEEPSEEK_API_KEY'):
        raise HTTPException(503,'服务尚未配置回答模型 API Key')
    rewritten = rewrite_question(body.question,history)
    result = search_base(connection,base_id,SearchRequest(question=rewritten,mode=body.mode,top_k=body.top_k))
    return history,rewritten,result['items']


# 多轮非流式知识库问答：改写、重新检索、生成并保存。
@router.post('/{base_id}/ask-rag/chat')
def chat(base_id:int,body:RagChatRequest,connection=Depends(get_connection)):
    try:
        previous,rewritten,chunks=prepare(connection,base_id,body)
        result=answer_with_chunks(body.question,chunks,history=previous)
        with connection:
            turn_id=save_turn(connection,base_id,body,rewritten,result['answer'],result['citations'],chunks)
        return {**result,'id':turn_id,'question':body.question,'retrieval_question':rewritten,'knowledge_base_id':base_id,'session_id':body.session_id,'mode':body.mode,'retrieved_chunks':len(chunks)}
    except Exception as exc:
        raise translate_error(exc) from exc


# 多轮流式知识库问答，成功完成才保存历史并发送 done。
@router.post('/{base_id}/ask-rag/chat/stream')
def chat_stream(base_id:int,body:RagChatRequest,http_request:Request,connection=Depends(get_connection)):
    try:
        previous,rewritten,chunks=prepare(connection,base_id,body)
    except Exception as exc:
        raise translate_error(exc) from exc

    def persist(answer,citations):
        with closing(sqlite3.connect(DB_PATH)) as db, db:
            db.row_factory=sqlite3.Row
            return save_turn(db,base_id,body,rewritten,answer,citations,chunks)

    def encode(event):
        return 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'

    async def generate():
        parts=[]
        try:
            if await http_request.is_disconnected():
                return
            yield encode({'type':'meta','retrieval_question':rewritten})
            async with aclosing(stream_answer_with_chunks(body.question,chunks,history=previous)) as events:
                async for event in events:
                    if await http_request.is_disconnected():
                        return
                    if event['type']=='delta':
                        parts.append(event['content'])
                        yield encode(event)
                    elif event['type']=='done':
                        answer=''.join(parts)
                        turn_id=await run_in_threadpool(persist,answer,event['citations'])
                        yield encode({**event,'id':turn_id,'question':body.question,'answer':answer,'retrieval_question':rewritten,'knowledge_base_id':base_id,'session_id':body.session_id,'mode':body.mode,'retrieved_chunks':len(chunks)})
        except Exception as exc:
            error=translate_error(exc)
            yield encode({'type':'error','message':error.detail})
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
