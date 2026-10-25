import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import knowledge_bases as api

from openai.types.chat import ChatCompletionMessage
from agent import runner as agent
from agent.tools import list_documents, execute_tool
from agent.state import AgentState
from storage import database


def response(content=None, calls=None, status=None, raw=False):
    if content is not None and not raw:
        status = status or ('grounded' if '[' in content else 'general' if content=='你好' else 'catalogue')
        content = json.dumps({'status':status,'answer':content},ensure_ascii=False)
    message = ChatCompletionMessage(role="assistant", content=content, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(
        message=message, finish_reason="tool_calls" if calls else "stop")])


def tool(arguments="{}", name="list_documents"):
    return [{"id": "call_1", "type": "function",
             "function": {"name": name, "arguments": arguments}}]


class DocumentAgentTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "test.db"
        database.init_test_db(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executemany("INSERT INTO knowledge_bases(id,title) VALUES (?,?)",
                            [(1, "main"), (2, "other")])
        self.db.executemany("INSERT INTO documents(id,title,file_type) VALUES (?,?,'markdown')",
                            [(1, "first"), (2, "second"), (3, "secret")])
        self.db.executemany("INSERT INTO knowledge_base_documents VALUES (?,?)",
                            [(1, 1), (1, 2), (2, 3)])
        self.db.commit()

    def run_agent(self, responses, question='有哪些资料？', history=None, **options):
        client = MagicMock()
        client.chat.completions.create.side_effect = responses
        with patch.object(agent, "OpenAI") as factory, patch.dict(
            "os.environ", {"DEEPSEEK_API_KEY": "test"}
        ):
            factory.return_value.__enter__.return_value = client
            result = agent.run_document_agent(self.db, 1, question,history=history,**options)
        return result, client

    def test_scope_and_pagination(self):
        result = list_documents(self.db, 1, limit=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(result["documents"][0]["title"], "first")
        self.assertNotIn("secret", json.dumps(result))

    def test_tool_result_is_sent_back(self):
        result, client = self.run_agent([response(calls=tool()), response("有两份资料")])
        self.assertEqual(len(result["tool_calls"]), 1)
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], "call_1")
        self.assertEqual(json.loads(messages[-1]["content"])["total"], 2)

    def test_no_tool_and_invalid_scope_parameter(self):
        result, _ = self.run_agent([response("你好")], question='你好')
        self.assertEqual(result["tool_calls"], [])
        result, _ = self.run_agent([
            response(calls=tool('{"base_id":2}')), response(calls=tool()),response("已取得目录")])
        self.assertIn("error", result["tool_calls"][0]["result"])

    def test_budget_forces_final_answer(self):
        with patch.dict('os.environ',{'AGENT_MAX_TOOL_CALLS':'3'}):
            result, client = self.run_agent([
                response(calls=tool()), response(calls=tool()),
                response(calls=tool()), response("已取得目录")])
        self.assertEqual(len(result["tool_calls"]), 3)
        self.assertNotIn('tools',client.chat.completions.create.call_args.kwargs)
        self.assertEqual(client.chat.completions.create.call_args.kwargs['response_format'],{'type':'json_object'})

    def add_chunks(self):
        self.db.executemany("""INSERT INTO document_chunks
            (id,document_id,chunk_index,content,source_name)
            VALUES (?,?,?,?,?)""", [
                (11,1,1,"部署前提：安装 Python。","first"),
                (12,1,2,"部署命令：python main.py。","first"),
                (13,1,3,"启动后检查健康状态。","first"),
                (31,3,1,"private secret","secret")])
        self.db.commit()

    def test_context_scope_and_validation(self):
        self.add_chunks()
        result = execute_tool(self.db,1,"read_chunk_context",'{"chunk_id":12}')
        self.assertEqual([i["chunk_id"] for i in result["items"]],[11,12,13])
        blocked = execute_tool(self.db,1,"read_chunk_context",'{"chunk_id":31}')
        self.assertEqual(blocked["status_code"],404)
        invalid = execute_tool(self.db,1,"read_chunk_context",'{"chunk_id":12,"before":20}')
        self.assertIn("error",invalid)

    def test_real_bm25_search(self):
        self.add_chunks()
        # Both members must have chunks before the existing search service runs.
        self.db.execute("INSERT INTO document_chunks(document_id,chunk_index,content) VALUES (2,1,'其他资料')")
        result = execute_tool(self.db,1,"search_knowledge_base",
                              '{"query":"部署命令","mode":"bm25"}')
        self.assertTrue(result["items"])
        self.assertNotIn(3,[i["document_id"] for i in result["items"]])

    def test_search_then_context_stable_citations(self):
        self.add_chunks()
        chunk = {"document_id":1,"chunk_id":12,"content":"部署命令：python main.py。"}
        with patch("agent.tools.search_base",return_value={"items":[chunk],"total":1}):
            result, _ = self.run_agent([
                response(calls=tool('{"query":"部署"}',"search_knowledge_base")),
                response(calls=tool('{"chunk_id":12}',"read_chunk_context")),
                response("执行 python main.py。[1] 需要 Python。[2]")])
        items = result["tool_calls"][1]["result"]["items"]
        self.assertEqual([i["citation_id"] for i in items],[2,1,3])
        self.assertEqual([i["chunk_id"] for i in result["citations"]],[12,11])

    def test_fabricated_citation_rejected(self):
        state = AgentState(1)
        with self.assertRaises(ValueError):
            state.citations_for("不存在的依据。[9]")

    def test_api_persists_trace_and_passes_history(self):
        self.add_chunks()
        self.db.execute("INSERT INTO knowledge_base_sessions(id,knowledge_base_id,title) VALUES (1,1,'chat')")
        self.db.commit()
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[database.get_connection] = lambda: self.db
        result = {"answer":"需要 Python。[1]", "citations":[{"document_id":1,"chunk_id":11,"citation_id":1,"content":"部署前提：安装 Python。"}],
                  "tool_calls":[{"tool":"search_knowledge_base","arguments":"{}","result":{"items":[{"document_id":1,"chunk_id":11,"content":"部署前提：安装 Python。"}]}}]}
        with TestClient(app) as client, patch.dict('os.environ',{'DEEPSEEK_API_KEY':'test'}), patch.object(api,'run_document_agent',return_value=result) as run:
            first = client.post('/knowledge-bases/1/agent',json={'question':'前提是什么？','session_id':1})
            self.assertEqual(first.status_code,200,first.text)
            history = client.get('/knowledge-bases/1/sessions/1/rag-history').json()['items']
            self.assertEqual(history[0]['tool_calls'],result['tool_calls'])
            self.assertEqual(history[0]['mode'],'agent')
            client.post('/knowledge-bases/1/agent',json={'question':'接下来呢？','session_id':1})
            self.assertEqual(len(run.call_args.kwargs['history']),1)
            blocked = client.post('/knowledge-bases/2/agent',json={'question':'问题','session_id':1})
            self.assertEqual(blocked.status_code,404)
            self.assertEqual(client.post('/knowledge-bases/1/agent',json={'question':'  '}).status_code,422)

    def test_citation_label_normalized(self):
        chunk = {"document_id":1,"chunk_id":11,"content":"Python"}
        with patch('agent.tools.search_base',return_value={'items':[chunk]}):
            result, _ = self.run_agent([response(calls=tool('{"query":"Python"}', 'search_knowledge_base')),
                                       response('需要 Python。[citation_id 1]')])
        self.assertEqual(result['answer'],'需要 Python。[1]')
        self.assertEqual(len(result['citations']),1)

    def test_uncited_history_requires_new_search(self):
        chunk={'document_id':1,'chunk_id':11,'content':'v2 内存 16GB'}
        with patch('agent.tools.search_base',return_value={'items':[chunk]}):
            result,_=self.run_agent([
                response('v2 内存 16GB',status='grounded'),
                response(calls=tool('{"query":"v2"}','search_knowledge_base')),
                response('v2 内存 16GB。[1]')], question='那 v2 呢？',
                history=[{'question':'v1 呢？','answer':'旧答案。[9]'}])
        self.assertEqual(result['citations'][0]['chunk_id'],11)

    def test_protocol_repaired_and_repeated_failure_rejected(self):
        result,_=self.run_agent([response(calls=tool()),response('<tool_call>bad',raw=True),response('已取得目录')])
        self.assertNotIn('<tool_call>',result['answer'])
        with self.assertRaises(ValueError):
            self.run_agent([response('DSML',raw=True)]*3)

    def test_partial_readiness_and_document_filter(self):
        self.add_chunks()
        # Document 2 has no chunks, document 1 is searchable by BM25.
        result=execute_tool(self.db,1,'search_knowledge_base','{"query":"部署","mode":"bm25"}')
        self.assertTrue(result['items'])
        self.assertEqual(result['skipped_documents'][0]['id'],2)
        filtered=execute_tool(self.db,1,'search_knowledge_base','{"query":"部署","mode":"bm25","document_ids":[1]}')
        self.assertEqual(filtered['skipped_documents'],[])
        blocked=execute_tool(self.db,1,'search_knowledge_base','{"query":"secret","document_ids":[3]}')
        self.assertEqual(blocked['status_code'],404)

    def test_cancel_and_deadline(self):
        from agent.runner import AgentCancelled
        with self.assertRaises(AgentCancelled):
            self.run_agent([],cancelled=lambda:True)
        with patch('agent.runner.time.monotonic',side_effect=[100,300]):
            with self.assertRaises(TimeoutError):
                self.run_agent([])

    def test_insufficient_is_conservative(self):
        result,_=self.run_agent([
            response(calls=tool('{"query":"price","mode":"bm25"}','search_knowledge_base')),
            response('知识库肯定没有价格',status='insufficient')])
        self.assertIn('本次检索',result['answer'])
        self.assertNotIn('肯定',result['answer'])

    def test_invalid_final_is_not_saved_and_cancel_prevents_save(self):
        import threading
        self.db.execute("INSERT INTO knowledge_base_sessions(id,knowledge_base_id,title) VALUES (1,1,'chat')")
        self.db.commit()
        from schemas import AgentRequest
        body=AgentRequest(question='部署要求？',session_id=1)
        from fastapi import HTTPException
        from agent.runner import InvalidFinalAnswer
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'test'}), patch.object(api,'run_document_agent',side_effect=InvalidFinalAnswer('bad final')):
            with self.assertRaises(HTTPException) as caught:
                api._document_agent(1,body,self.db,threading.Event())
            self.assertEqual(caught.exception.status_code,502)
        stop=threading.Event()
        def completed(*args,**kwargs):
            stop.set()
            return {'answer':'stale','citations':[],'tool_calls':[]}
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':'test'}), patch.object(api,'run_document_agent',side_effect=completed):
            with self.assertRaises(HTTPException) as caught:
                api._document_agent(1,body,self.db,stop)
            self.assertEqual(caught.exception.status_code,499)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0],0)

    def test_unready_warning_is_always_in_answer(self):
        state=AgentState(1)
        state.tool_calls=[{'tool':'search_knowledge_base','result':{
            'items':[],'skipped_documents':[{'id':2,'title':'pending','processing_status':'not_chunked'}]}}]
        result=state.validate_final(json.dumps({'status':'insufficient','answer':'没有资料'}),'价格？')
        self.assertIn('pending',result['answer'])
        self.assertIn('范围不完整',result['answer'])

    def test_catalogue_cannot_smuggle_document_facts(self):
        result,_=self.run_agent([response(calls=tool()),response('最低内存为 99GB',status='catalogue')])
        self.assertNotIn('99GB',result['answer'])
        self.assertIn('first',result['answer'])

    def test_dense_similarity_filter(self):
        self.add_chunks()
        self.db.execute('UPDATE documents SET indexed_version=chunk_version WHERE id=1')
        from services import knowledge_base
        collection=MagicMock()
        metadata=[{'document_id':1,'chunk_id':i} for i in [11,12,13]]
        collection.get.return_value={'ids':['a','b','c'],'metadatas':metadata}
        collection.query.return_value={'ids':[['a','b','c']],'metadatas':[metadata],
                                       'documents':[['relevant','noise','noise2']],'distances':[[0.2,0.9,0.8]]}
        with patch.object(knowledge_base,'get_collection',return_value=collection),patch.object(knowledge_base,'embed_query',return_value=[1.,0.]):
            result=execute_tool(self.db,1,'search_knowledge_base','{"query":"requirement","mode":"dense","document_ids":[1]}')
        self.assertEqual([i['chunk_id'] for i in result['items']],[11])
