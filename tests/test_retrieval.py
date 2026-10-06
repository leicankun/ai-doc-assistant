import math
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import embedding
from routers import documents, document_management, chunks as chunks_router


class EmbeddingTests(unittest.TestCase):
    def test_batches_and_order(self):
        client = MagicMock()
        client.__enter__.return_value = client
        def response(**kwargs):
            return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[float(text)] * 1024)
                                         for i, text in reversed(list(enumerate(kwargs['input'])))])
        client.embeddings.create.side_effect = response
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test', 'DASHSCOPE_BASE_URL': 'https://example.invalid', 'EMBEDDING_MODEL': 'text-embedding-v4'}), patch.object(embedding, 'OpenAI', return_value=client):
            result = embedding.embed_texts([str(i) for i in range(1, 13)])
        self.assertEqual([v[0] for v in result], list(range(1, 13)))
        self.assertEqual([len(c.kwargs['input']) for c in client.embeddings.create.call_args_list], [10, 2])

    def test_validation(self):
        self.assertEqual(embedding.embed_texts([]), [])
        for value in [[' '], [None]]:
            with self.assertRaises(ValueError):
                embedding.embed_texts(value)
        client = MagicMock()
        client.__enter__.return_value = client
        bad_results = [[], [SimpleNamespace(index=1, embedding=[1.] * 1024)],
                       [SimpleNamespace(index=0, embedding=[1.])],
                       [SimpleNamespace(index=0, embedding=[0.] * 1024)],
                       [SimpleNamespace(index=0, embedding=[math.nan] * 1024)]]
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'test', 'DASHSCOPE_BASE_URL': 'https://example.invalid', 'EMBEDDING_MODEL': 'text-embedding-v4'}), patch.object(embedding, 'OpenAI', return_value=client):
            for items in bad_results:
                client.embeddings.create.return_value = SimpleNamespace(data=items)
                with self.assertRaises(RuntimeError):
                    embedding.embed_texts(['test'])


class RetrievalTests(unittest.TestCase):
    def test_upload_chunk_search_and_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / 'test.db'
            with closing(sqlite3.connect(db)) as c:
                c.execute('CREATE TABLE document_chunks (id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER, content TEXT, UNIQUE(document_id, chunk_index))')
                c.execute("INSERT INTO document_chunks VALUES (99,99,1,'legacy')")
                c.commit()
            with patch.object(database, 'DB_PATH', db):
                database.init_db()
                database.init_db()
            def connection():
                with closing(sqlite3.connect(db, check_same_thread=False)) as c:
                    c.row_factory = sqlite3.Row
                    yield c
            app = FastAPI()
            app.include_router(documents.router)
            app.dependency_overrides[database.get_connection] = connection
            with patch.object(document_management, 'UPLOAD_DIR', root), patch.object(chunks_router, 'UPLOAD_DIR', root), TestClient(app) as client:
                text = 'SQLite 保存数据。' + '甲' * 600 + 'FastAPI 提供接口。'
                r = client.post('/documents/upload', files={'file': ('guide.md', text.encode(), 'text/markdown')})
                self.assertEqual(r.status_code, 201)
                doc = r.json()['id']
                url = f'/documents/{doc}'
                self.assertEqual(client.post(url+'/search', json={'question':'SQLite'}).status_code, 409)
                for _ in range(2):
                    self.assertEqual(client.post(url+'/chunks').status_code, 200)
                    chunks = client.get(url+'/chunks').json()['items']
                    self.assertGreaterEqual(len(chunks), 2)
                    self.assertTrue(all(len(chunk['content']) <= 500 for chunk in chunks))
                    for chunk in chunks:
                        self.assertEqual(text[chunk['start_char']:chunk['end_char']], chunk['content'])
                        self.assertEqual(chunk['source_name'], 'guide.md')
                result = client.post(url+'/search', json={'question':'SQLite', 'top_k':1}).json()
                self.assertEqual(result['total'], 1)
                self.assertEqual(result['items'][0]['chunk_index'], 1)
                self.assertEqual(result['items'][0]['rank'], 1)
                self.assertGreater(result['items'][0]['score'], 0)
                self.assertEqual(client.post(url+'/search', json={'question':'legacy'}).json()['items'], [])
                for body in [{'question':' '}, {'question':'!!!'}, {'question':'test','top_k':0}, {'question':'test','mode':'invalid'}]:
                    self.assertEqual(client.post(url+'/search',json=body).status_code,422)
                self.assertEqual(client.post('/documents/999/search',json={'question':'test'}).status_code,404)
                class Page:
                    def __init__(self, text): self.text = text
                    def extract_text(self): return self.text
                reader = SimpleNamespace(is_encrypted=False, pages=[Page('First page'), Page(''), Page('Third page')])
                with patch.object(document_management, 'PdfReader', return_value=reader), patch.object(chunks_router, 'PdfReader', return_value=reader):
                    pdf = client.post('/documents/upload', files={'file':('guide.pdf',b'test','application/pdf')}).json()['id']
                    self.assertEqual(client.post(f'/documents/{pdf}/chunks').status_code,200)
                    rows = client.get(f'/documents/{pdf}/chunks').json()['items']
                    self.assertEqual([r['page_number'] for r in rows],[1,3])


if __name__ == '__main__':
    unittest.main()
