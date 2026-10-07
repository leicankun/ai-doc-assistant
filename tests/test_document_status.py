"""验证状态切换、失败保护与旧数据库迁移；不调用真实模型。"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import chromadb
from fastapi import FastAPI
from fastapi.testclient import TestClient
from storage import database
from retrieval import indexing
from routers import documents, document_management, chunks, search


class DocumentStatusTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root/'test.db'
        with patch.object(database,'DB_PATH',self.path):
            database.init_db()
        self.db = sqlite3.connect(self.path,check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for mocker in (patch.object(document_management,'UPLOAD_DIR',self.root),
                       patch.object(chunks,'UPLOAD_DIR',self.root)):
            mocker.start()
            self.addCleanup(mocker.stop)
        chroma = chromadb.EphemeralClient()
        self.collection = chroma.create_collection('status-test-'+uuid4().hex,embedding_function=None)
        self.addCleanup(chroma.delete_collection,self.collection.name)
        mocker = patch.object(indexing,'get_collection',return_value=self.collection)
        mocker.start()
        self.addCleanup(mocker.stop)
        mocker = patch.object(indexing,'embed_texts',side_effect=lambda texts:[[1.,0.]]*len(texts))
        self.embedding = mocker.start()
        self.addCleanup(mocker.stop)
        app = FastAPI()
        app.include_router(documents.router)
        app.dependency_overrides[database.get_connection] = lambda:self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        response = self.client.post('/documents/upload',files={'file':('guide.md','SQLite 保存数据。'.encode(),'text/markdown')})
        self.assertEqual(response.status_code,201,response.text)
        self.doc = response.json()['id']
        self.url = f'/documents/{self.doc}'

    def status(self):
        detail = self.client.get(self.url).json()
        listed = self.client.get('/documents').json()['items'][0]
        self.assertEqual(detail,listed)
        return detail

    def chunk(self):
        response = self.client.post(self.url+'/chunks')
        self.assertEqual(response.status_code,200,response.text)

    def index(self):
        response = self.client.post(self.url+'/index')
        self.assertEqual(response.status_code,200,response.text)

    def test_lifecycle_and_stale_retrieval_protection(self):
        self.assertEqual(self.status()['processing_status'],'not_chunked')
        self.chunk()
        self.assertEqual(self.status()['processing_status'],'pending_index')
        self.assertEqual(self.status()['chunk_version'],1)
        self.index()
        self.assertEqual(self.status()['processing_status'],'indexed')
        self.assertEqual(self.status()['indexed_version'],1)
        self.chunk()
        self.assertEqual(self.status()['processing_status'],'index_stale')
        self.assertEqual(self.status()['chunk_version'],2)
        with patch.object(search,'search_dense') as dense, patch.object(search,'search_hybrid') as hybrid:
            for endpoint in ('search','ask-rag','ask-rag/stream'):
                for mode in ('dense','hybrid'):
                    response = self.client.post(self.url+'/'+endpoint,json={'question':'SQLite','mode':mode})
                    self.assertEqual(response.status_code,409,response.text)
            dense.assert_not_called()
            hybrid.assert_not_called()
        self.assertEqual(self.client.post(self.url+'/search',json={'question':'SQLite','mode':'bm25'}).status_code,200)
        self.index()
        self.assertEqual(self.status()['processing_status'],'indexed')
        self.assertEqual(self.status()['indexed_version'],2)

    def test_embedding_failure_does_not_mark_success(self):
        self.chunk()
        self.embedding.side_effect = RuntimeError('embedding failure')
        with patch.object(search,'logger'):
            self.assertEqual(self.client.post(self.url+'/index').status_code,500)
        self.assertEqual(self.status()['processing_status'],'pending_index')

    def test_partial_vector_failure_invalidates_existing_index(self):
        self.chunk()
        self.index()
        with patch.object(self.collection,'upsert',side_effect=RuntimeError('write failure')), patch.object(search,'logger'):
            self.assertEqual(self.client.post(self.url+'/index').status_code,500)
        self.assertEqual(self.status()['processing_status'],'index_stale')
        self.index()
        self.assertEqual(self.status()['processing_status'],'indexed')

    def test_chunk_change_during_index_is_not_marked_ready(self):
        self.chunk()
        original = self.collection.upsert
        def changed(**kwargs):
            original(**kwargs)
            self.db.execute('UPDATE documents SET chunk_version=chunk_version+1 WHERE id=?',(self.doc,))
            self.db.commit()
        with patch.object(self.collection,'upsert',side_effect=changed),patch.object(search,'logger'):
            self.assertEqual(self.client.post(self.url+'/index').status_code,500)
        self.assertEqual(self.status()['processing_status'],'index_stale')

    def test_old_database_migration_is_repeatable(self):
        legacy = self.root/'legacy.db'
        with closing(sqlite3.connect(legacy)) as db:
            db.execute('CREATE TABLE documents (id INTEGER PRIMARY KEY,title TEXT NOT NULL,file_type TEXT NOT NULL)')
            db.execute("INSERT INTO documents VALUES (1,'old','markdown')")
            db.commit()
        with patch.object(database,'DB_PATH',legacy):
            database.init_db()
            database.init_db()
        with closing(sqlite3.connect(legacy)) as db:
            self.assertEqual(db.execute('SELECT title,chunk_version,indexed_version FROM documents').fetchone(),('old',0,None))


if __name__ == '__main__':
    unittest.main()
