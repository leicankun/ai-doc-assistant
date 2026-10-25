"""Backend isolation and local RAG integration; no production data or network calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from threading import Barrier
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import chromadb
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from retrieval import backend, embedding, local_embedding, local_index, reranker
from storage import database, vector_store
from schemas import SearchRequest, AgentRequest
from routers import documents, embedding_backends, knowledge_bases, rag, document_management
from services import llm


def vector(first=True):
    return ([1., 0.] if first else [0., 1.]) + [0.] * 1022


class BackendContextTests(unittest.TestCase):
    def test_api_defaults_and_thread_isolation(self):
        self.assertEqual(SearchRequest(question='test').embedding_provider, 'api')
        self.assertEqual(AgentRequest(question='test').embedding_provider, 'api')
        barrier = Barrier(2)
        def run(provider):
            with backend.use_backend(provider):
                barrier.wait(timeout=5)
                return backend.current_provider()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, p) for p in ('api', 'local')]
            self.assertEqual([f.result() for f in futures], ['api', 'local'])
        with self.assertRaises(RuntimeError):
            with backend.use_backend('local'):
                raise RuntimeError('cancelled')
        self.assertEqual(backend.current_provider(), 'api')

    def test_local_embedding_failure_never_falls_back_to_api(self):
        with backend.use_backend('local'), patch.object(embedding, 'OpenAI') as api, \
                patch.object(local_embedding, 'embed_local_texts', side_effect=local_embedding.LocalEmbeddingError('offline')):
            with self.assertRaises(HTTPException) as caught:
                embedding.embed_query('question')
            self.assertEqual(caught.exception.status_code, 503)
            api.assert_not_called()

    def test_local_retrieval_skips_cloud_rerank(self):
        with backend.use_backend('local'), patch.dict(os.environ, {'RERANK_ENABLED': 'true'}), \
                patch.object(reranker, 'urlopen') as cloud:
            result = reranker.rerank('question', [{'content': 'real content', 'document_id': 1}], 1)
            self.assertEqual(len(result), 1)
            cloud.assert_not_called()

    def test_agent_tool_preserves_selected_backend(self):
        from agent import tools
        with backend.use_backend('local'), patch.object(tools, 'search_base', return_value={'items': [], 'total': 0}) as search:
            result = tools.execute_tool(None, 1, 'search_knowledge_base', '{"query":"test"}')
            self.assertEqual(search.call_args.args[2].embedding_provider, 'local')
            self.assertIsNone(search.call_args.kwargs['min_dense_similarity'])
            self.assertEqual(result['embedding_provider'], 'local')


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'test.db'
        self.upload_dir = Path(folder.name) / 'uploads'
        self.upload_dir.mkdir()
        database.init_test_db(self.path)
        self.db = database.connect_test_db(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript('''
            INSERT INTO documents(id,title,file_type,chunk_version,indexed_version) VALUES(1,'Guide','markdown',1,1);
            INSERT INTO document_chunks(id,document_id,chunk_index,content,source_name,start_char,end_char)
                VALUES(10,1,1,'Local first.','Guide',0,12),(11,1,2,'API second.','Guide',12,23);
            INSERT INTO document_sessions(id,document_id,title) VALUES(1,1,'chat');
            INSERT INTO knowledge_bases(id,title) VALUES(1,'KB');
            INSERT INTO knowledge_base_documents VALUES(1,1);
        ''')
        self.db.commit()
        client = chromadb.EphemeralClient()
        self.api_collection = client.create_collection('primary-' + uuid4().hex, embedding_function=None,
                                                       configuration={'hnsw': {'space': 'cosine'}})
        self.local_collection = client.create_collection('local-' + uuid4().hex, embedding_function=None,
                                                         configuration={'hnsw': {'space': 'cosine'}})
        self.addCleanup(client.delete_collection, self.api_collection.name)
        self.addCleanup(client.delete_collection, self.local_collection.name)
        self.api_collection.upsert(ids=['doc:1:chunk:10', 'doc:1:chunk:11'],
            embeddings=[vector(False), vector(True)], documents=['Local first.', 'API second.'],
            metadatas=[{'document_id': 1, 'chunk_id': 10, 'chunk_index': 1},
                       {'document_id': 1, 'chunk_id': 11, 'chunk_index': 2}])
        factory = MagicMock()
        factory.get_or_create_collection.return_value = self.api_collection
        api_client = MagicMock()
        api_client.__enter__.return_value = api_client
        api_client.embeddings.create.return_value = SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=vector(True))])
        self.api_client = api_client
        self.patches = [
            patch.object(vector_store.chromadb, 'PersistentClient', return_value=factory),
            patch.object(vector_store, 'get_local_collection', return_value=self.local_collection),
            patch.object(local_index, 'get_local_collection', return_value=self.local_collection),
            patch.object(local_embedding, 'embed_local_texts',
                         side_effect=lambda texts, **kw: [vector('first' in text.lower()) for text in texts]),
            patch.object(local_index, 'embed_local_texts',
                         side_effect=lambda texts, **kw: [vector('first' in text.lower()) for text in texts]),
            patch.object(embedding, 'OpenAI', return_value=api_client),
            patch.object(embedding_backends, 'start', return_value={'model': 'BAAI/bge-m3', 'device': 'cuda'}),
            patch('scripts.local_bge.start', return_value={'model': 'BAAI/bge-m3', 'device': 'cuda'}),
            patch.object(document_management, 'UPLOAD_DIR', self.upload_dir),
            patch.object(knowledge_bases, 'connect', side_effect=lambda: database.connect_test_db(self.path)),
            patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test', 'RERANK_ENABLED': 'false'}),
        ]
        for mocker in self.patches:
            mocker.start()
            self.addCleanup(mocker.stop)
        app = FastAPI()
        app.include_router(documents.router)
        app.dependency_overrides[database.get_connection] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def prepare(self):
        response = self.client.post('/embedding/local/prepare', json={'document_id': 1})
        self.assertEqual(response.status_code, 200, response.text)

    def search(self, provider='local', knowledge_base=False):
        path = '/knowledge-bases/1/search' if knowledge_base else '/documents/1/search'
        body = {'question': 'first', 'mode': 'dense', 'top_k': 1}
        if provider is not None:
            body['embedding_provider'] = provider
        return self.client.post(path, json=body)

    def test_selected_collection_and_default_api_remain_independent(self):
        self.prepare()
        local = self.search()
        self.assertEqual(local.status_code, 200, local.text)
        self.assertEqual(local.json()['items'][0]['chunk_id'], 10)
        self.api_client.embeddings.create.assert_not_called()
        api = self.search(provider=None)
        self.assertEqual(api.status_code, 200, api.text)
        self.assertEqual(api.json()['embedding_provider'], 'api')
        self.assertEqual(api.json()['items'][0]['chunk_id'], 11)
        self.api_client.embeddings.create.assert_called_once()
        self.assertEqual(self.api_collection.count(), 2)

    def test_local_status_does_not_require_api_index_and_detects_changed_content(self):
        self.db.execute('UPDATE documents SET indexed_version=NULL WHERE id=1')
        self.db.commit()
        self.prepare()
        self.assertIsNone(self.db.execute('SELECT indexed_version FROM documents').fetchone()[0])
        response = self.search(knowledge_base=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['items'][0]['chunk_id'], 10)
        self.db.execute("UPDATE document_chunks SET content='changed' WHERE id=10")
        self.db.commit()
        self.assertEqual(self.search().status_code, 409)
        self.assertEqual(self.search(knowledge_base=True).status_code, 409)

    def test_missing_index_and_invalid_scope_are_rejected(self):
        self.assertEqual(self.search().status_code, 409)
        for body in ({}, {'document_id': 1, 'knowledge_base_id': 1}):
            self.assertEqual(self.client.post('/embedding/local/prepare', json=body).status_code, 422)
        self.assertEqual(self.client.post('/embedding/local/prepare', json={'document_id': 999}).status_code, 404)
        self.assertEqual(self.search(provider='invalid').status_code, 422)

    def test_rag_history_keeps_local_provenance(self):
        self.prepare()
        model = MagicMock()
        model.__enter__.return_value = model
        model.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='Local first.[1]'))])
        with patch.object(llm, 'OpenAI', return_value=model):
            response = self.client.post('/documents/1/ask-rag/chat', json={
                'question': 'first', 'session_id': 1, 'mode': 'dense', 'embedding_provider': 'local'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['citations'][0]['embedding_provider'], 'local')
        saved = json.loads(self.db.execute('SELECT citations_json FROM rag_turns').fetchone()[0])
        self.assertEqual(saved[0]['embedding_provider'], 'local')
        self.api_client.embeddings.create.assert_not_called()

    def test_agent_sse_worker_is_scoped_per_request(self):
        observed = []
        def run(*args, **kwargs):
            observed.append(backend.current_provider())
            return {'answer': '你好', 'citations': [], 'tool_calls': [], 'answer_status': 'general'}
        with patch.object(knowledge_bases, 'run_document_agent', side_effect=run):
            for body in ({'question': '你好', 'embedding_provider': 'local'}, {'question': '你好'}):
                response = self.client.post('/knowledge-bases/1/agent/stream', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
                self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(observed, ['local', 'api'])
        self.assertEqual(backend.current_provider(), 'api')

    def test_local_upload_and_delete_never_generate_cloud_vectors(self):
        response = self.client.post('/documents/upload?embedding_provider=local',
                                    files={'file': ('local.md', b'Local first.', 'text/markdown')})
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertTrue(result['processing_completed'], result)
        self.assertEqual(result['embedding_provider'], 'local')
        self.api_client.embeddings.create.assert_not_called()
        doc_id = result['id']
        self.assertTrue(self.local_collection.get(where={'document_id': doc_id})['ids'])
        self.assertFalse(self.api_collection.get(where={'document_id': doc_id})['ids'])
        self.assertIsNone(self.db.execute('SELECT indexed_version FROM documents WHERE id=?', (doc_id,)).fetchone()[0])
        response = self.client.delete(f'/documents/{doc_id}')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.local_collection.get(where={'document_id': doc_id})['ids'])
        self.assertFalse((self.upload_dir / result['stored_name']).exists())
        self.assertEqual(self.api_collection.count(), 2)


if __name__ == '__main__':
    unittest.main()
