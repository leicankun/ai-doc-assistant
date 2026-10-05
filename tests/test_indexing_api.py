"""HTTP indexing tests with isolated SQLite and real in-memory Chroma."""
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import chromadb
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APIError, APITimeoutError

import indexing
from database import get_connection
from routers import documents
from routers import search as business


class IndexingApiTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE documents (id INTEGER PRIMARY KEY);
            CREATE TABLE document_chunks (
                id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
                content TEXT, source_name TEXT, page_number INTEGER,
                start_char INTEGER, end_char INTEGER
            );
            INSERT INTO documents VALUES (1), (2);
            INSERT INTO document_chunks VALUES
                (10, 1, 1, 'First chunk', 'guide.pdf', 1, 0, 11),
                (11, 1, 2, 'Second chunk', 'guide.pdf', 2, 0, 12);
        ''')
        app = FastAPI()
        app.include_router(documents.router)
        app.dependency_overrides[get_connection] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        chroma = chromadb.EphemeralClient()
        self.collection = chroma.create_collection(
            name='api-test-' + uuid4().hex, embedding_function=None,
            configuration={'hnsw': {'space': 'cosine'}},
        )
        self.addCleanup(chroma.delete_collection, self.collection.name)
        store = patch.object(indexing, 'get_collection', return_value=self.collection)
        store.start()
        self.addCleanup(store.stop)
        embed = patch.object(indexing, 'embed_texts',
                             side_effect=lambda texts: [[1.] + [0.] * 1023 for _ in texts])
        self.embed = embed.start()
        self.addCleanup(embed.stop)

    def test_success_repeat_and_stale_cleanup(self):
        for _ in range(2):
            response = self.client.post('/documents/1/index')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {
                'document_id': 1, 'indexed_chunks': 2, 'removed_chunks': 0,
                'message': '文档向量入库成功',
            })
            self.assertEqual(self.collection.count(), 2)
        saved = self.collection.get(ids=['doc:1:chunk:10'],
                                    include=['documents', 'metadatas', 'embeddings'])
        self.assertEqual(saved['documents'], ['First chunk'])
        self.assertEqual(saved['metadatas'][0]['page_number'], 1)
        self.assertEqual(len(saved['embeddings'][0]), 1024)
        self.db.execute('DELETE FROM document_chunks WHERE id=11')
        response = self.client.post('/documents/1/index')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['removed_chunks'], 1)
        self.assertEqual(self.collection.count(), 1)

    def test_invalid_document_and_missing_chunks(self):
        for path, status in [('/documents/999/index', 404),
                             ('/documents/2/index', 422),
                             ('/documents/abc/index', 422)]:
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path).status_code, status)
        self.embed.assert_not_called()
        self.assertEqual(self.collection.count(), 0)

    def test_embedding_errors(self):
        request = httpx.Request('POST', 'https://example.invalid/embeddings')
        for error, status in [
            (APITimeoutError(request=request), 504),
            (APIError('synthetic API failure', request=request, body=None), 502),
            (RuntimeError('synthetic configuration failure'), 500),
        ]:
            with self.subTest(status=status):
                self.embed.side_effect = error
                with self.assertLogs(business.logger, level='ERROR') if status == 500 else patch.object(business, 'logger'):
                    response = self.client.post('/documents/1/index')
                self.assertEqual(response.status_code, status, response.text)
                self.assertNotIn('synthetic', response.text)
                self.assertEqual(self.collection.count(), 0)

    def test_chroma_write_failure(self):
        with patch.object(self.collection, 'upsert', side_effect=RuntimeError('write failed')):
            with self.assertLogs(business.logger, level='ERROR') as logs:
                response = self.client.post('/documents/1/index')
        self.assertEqual(response.status_code, 500)
        self.assertTrue(any('document_id=1' in line for line in logs.output))
        self.assertEqual(self.collection.count(), 0)


if __name__ == '__main__':
    unittest.main()
