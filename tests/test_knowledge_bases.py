"""隔离真实 SQLite/Chroma，模型使用固定响应，验证知识库完整链路。"""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import chromadb
from fastapi import FastAPI
from fastapi.testclient import TestClient
from storage import database
from retrieval import indexing
from services import knowledge_base
from storage import document_deletion
from routers import documents, document_management, chunks, knowledge_bases as api


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root=Path(folder.name)
        self.path=self.root/'test.db'
        with patch.object(database,'DB_PATH',self.path):
            database.init_db()
            database.init_db()
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.addCleanup(self.db.close)
        chroma=chromadb.EphemeralClient()
        self.collection=chroma.create_collection('kb-test-'+uuid4().hex,embedding_function=None,
                                                 configuration={'hnsw':{'space':'cosine'}})
        self.addCleanup(chroma.delete_collection,self.collection.name)
        patches=[patch.object(document_management,'UPLOAD_DIR',self.root),patch.object(chunks,'UPLOAD_DIR',self.root),
                 patch.object(indexing,'get_collection',return_value=self.collection),
                 patch.object(knowledge_base,'get_collection',return_value=self.collection),
                 patch.object(document_deletion,'get_collection',return_value=self.collection),
                 patch.object(indexing,'embed_texts',side_effect=lambda texts:[[1.,0.]]*len(texts)),
                 patch.object(knowledge_base,'embed_query',return_value=[1.,0.]),
                 patch.object(api,'DB_PATH',self.path),patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test'})]
        for mocker in patches:
            mocker.start(); self.addCleanup(mocker.stop)
        app=FastAPI(); app.include_router(documents.router)
        app.dependency_overrides[database.get_connection]=lambda:self.db
        self.client=TestClient(app); self.addCleanup(self.client.close)
        self.ids=[]
        for name,text in [('a.md','shared alpha fact.'),('b.md','shared beta fact.'),('outside.md','shared secret fact.')]:
            response=self.client.post('/documents/upload',files={'file':(name,text.encode(),'text/markdown')})
            doc=response.json()['id']; self.ids.append(doc)
            self.assertEqual(self.client.post(f'/documents/{doc}/chunks').status_code,200)
            self.assertEqual(self.client.post(f'/documents/{doc}/index').status_code,200)
        self.base=self.create('Main')
        self.other=self.create('Other')
        self.url=f'/knowledge-bases/{self.base}'
        self.assertEqual(self.client.post(self.url+'/documents',json={'document_ids':self.ids[:2]}).status_code,200)
        self.client.post(f'/knowledge-bases/{self.other}/documents',json={'document_ids':[self.ids[0],self.ids[2]]})
        self.session=self.client.post(self.url+'/sessions',json={'title':'Chat'}).json()['id']

    def create(self,title):
        response=self.client.post('/knowledge-bases',json={'title':title})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['id']

    def body(self,mode='hybrid'):
        return {'question':'shared','mode':mode,'top_k':2,'session_id':self.session}

    def test_three_modes_global_top_k_and_scope(self):
        for mode in ('bm25','dense','hybrid'):
            response=self.client.post(self.url+'/search',json=self.body(mode))
            self.assertEqual(response.status_code,200,response.text)
            result=response.json()
            self.assertEqual(result['total'],2)
            self.assertEqual({r['document_id'] for r in result['items']},set(self.ids[:2]))
            self.assertEqual({r['source_name'] for r in result['items']},{'a.md','b.md'})
            self.assertNotIn('secret',json.dumps(result))

    def test_management_and_atomic_member_validation(self):
        self.assertEqual(self.client.put(self.url,json={'title':'Renamed'}).json()['title'],'Renamed')
        self.assertEqual(self.client.post(self.url+'/documents',json={'document_ids':self.ids[:2]*2}).status_code,200)
        response=self.client.post(self.url+'/documents',json={'document_ids':[self.ids[2],999]})
        self.assertEqual(response.status_code,404)
        self.assertEqual(len(self.client.get(self.url).json()['documents']),2)
        self.assertEqual(self.client.delete(self.url+f'/documents/{self.ids[0]}').status_code,200)
        self.assertEqual(self.client.get(f'/documents/{self.ids[0]}').status_code,200)
        self.assertTrue(self.collection.get(where={'document_id':self.ids[0]})['ids'])
        self.assertEqual(len(self.client.get(f'/knowledge-bases/{self.other}').json()['documents']),2)

    def test_readiness_and_request_validation(self):
        self.client.post(f'/documents/{self.ids[0]}/chunks')
        response=self.client.post(self.url+'/search',json=self.body())
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['documents'][0]['id'],self.ids[0])
        with patch.object(api,'rewrite_question') as rewrite:
            self.assertEqual(self.client.post(self.url+'/ask-rag/chat',json=self.body()).status_code,409)
            self.assertEqual(self.client.post(self.url+'/ask-rag/chat/stream',json=self.body()).status_code,409)
            rewrite.assert_not_called()
        self.assertEqual(self.client.post(self.url+'/search',json=self.body('bm25')).status_code,200)
        self.db.execute('DELETE FROM document_chunks WHERE document_id=?',(self.ids[1],)); self.db.commit()
        self.assertEqual(self.client.post(self.url+'/search',json=self.body('bm25')).status_code,409)
        empty=self.create('Empty')
        self.assertEqual(self.client.post(f'/knowledge-bases/{empty}/search',json=self.body()).status_code,409)
        self.assertEqual(self.client.post(self.url+'/documents',json={'document_ids':[-1]}).status_code,422)
        self.assertEqual(self.client.post(self.url+'/search',json={**self.body(),'top_k':21}).status_code,422)
        self.assertEqual(self.client.post('/knowledge-bases',json={'title':'  '}).status_code,422)

    def test_missing_vectors_rejected_before_embedding(self):
        self.collection.delete(where={'document_id':self.ids[1]})
        with patch.object(knowledge_base,'embed_query') as embed:
            self.assertEqual(self.client.post(self.url+'/search',json=self.body()).status_code,409)
            embed.assert_not_called()

    @staticmethod
    def answer(question, chunks, history=None):
        return {'answer':'alpha [1] beta [2]','citations':[
            {**chunk,'citation_id':number} for number,chunk in enumerate(chunks,1)]}

    def test_multiturn_history_cross_document_sources_and_session_isolation(self):
        with patch.object(api,'answer_with_chunks',side_effect=self.answer),patch.object(api,'rewrite_question',side_effect=lambda q,h:q) as rewrite:
            for _ in range(2):
                response=self.client.post(self.url+'/ask-rag/chat',json=self.body())
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual({c['document_id'] for c in response.json()['citations']},set(self.ids[:2]))
            self.assertEqual(len(rewrite.call_args.args[1]),1)
        history=self.client.get(self.url+f'/sessions/{self.session}/rag-history').json()
        self.assertEqual(history['total'],2)
        self.assertEqual(self.client.get(f'/knowledge-bases/{self.other}/sessions/{self.session}/rag-history').status_code,404)
        self.assertEqual(self.client.post(f'/knowledge-bases/{self.other}/ask-rag/chat',json=self.body()).status_code,404)

    def test_stream_success_and_failure_save_contract(self):
        async def stream(question,chunks,history=None):
            yield {'type':'delta','content':'answer [1]'}
            yield {'type':'done','citations':[{**chunks[0],'citation_id':1}]}
        with patch.object(api,'stream_answer_with_chunks',side_effect=stream):
            response=self.client.post(self.url+'/ask-rag/chat/stream',json=self.body())
        self.assertEqual(response.status_code,200,response.text)
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([e['type'] for e in events],['meta','delta','done'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0],1)
        async def failure(question,chunks,history=None):
            yield {'type':'delta','content':'partial'}
            raise ValueError('invalid citation')
        with patch.object(api,'stream_answer_with_chunks',side_effect=failure),patch.object(api,'rewrite_question',return_value='shared'):
            response=self.client.post(self.url+'/ask-rag/chat/stream',json=self.body())
        self.assertIn('"type": "error"',response.text)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0],1)

    def test_deletion_boundaries(self):
        with patch.object(api,'answer_with_chunks',side_effect=self.answer):
            self.assertEqual(self.client.post(self.url+'/ask-rag/chat',json=self.body()).status_code,200)
        self.assertEqual(self.client.delete(f'/documents/{self.ids[0]}').status_code,200)
        self.assertEqual(len(self.client.get(self.url).json()['documents']),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turn_documents').fetchone()[0],0)
        self.assertEqual(self.client.delete(self.url).status_code,200)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.client.get(f'/documents/{self.ids[1]}').status_code,200)
        self.assertTrue(self.collection.get(where={'document_id':self.ids[1]})['ids'])
        self.assertEqual(self.client.get(f'/knowledge-bases/{self.other}').status_code,200)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_sessions WHERE knowledge_base_id=?',(self.base,)).fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
