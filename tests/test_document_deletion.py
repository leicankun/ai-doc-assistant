"""HTTP 删除测试：隔离 SQLite、上传目录以及真实内存 Chroma。"""
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
from storage import document_deletion
from routers import document_management


class DocumentDeletionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        with patch.object(database, 'DB_PATH', self.root/'test.db'):
            database.init_db()
        self.db = sqlite3.connect(self.root/'test.db', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        for doc in (1, 2):
            self.db.execute('INSERT INTO documents (id,title,file_type,stored_name) VALUES (?, ?, ?, ?)',
                            (doc, f'{doc}.md', 'markdown', f'{doc}.md'))
            (self.root/f'{doc}.md').write_text('正文', encoding='utf-8')
            self.db.execute('INSERT INTO document_chunks (document_id,chunk_index,content) VALUES (?,1,?)', (doc,'正文'))
            self.db.execute('INSERT INTO document_sessions (id,document_id,title) VALUES (?,?,?)', (doc,doc,'会话'))
            self.db.execute('INSERT INTO question_answers (document_id,session_id,question,answer) VALUES (?,?,?,?)', (doc,doc,'问题','回答'))
            self.db.execute('INSERT INTO rag_turns (document_id,session_id,question,retrieval_question,answer,citations_json,mode) VALUES (?,?,?,?,?,?,?)',
                            (doc,doc,'问题','问题','回答','[]','hybrid'))
        self.db.commit()
        chroma = chromadb.EphemeralClient()
        self.collection = chroma.create_collection('delete-test-'+uuid4().hex, embedding_function=None)
        self.addCleanup(chroma.delete_collection, self.collection.name)
        self.collection.upsert(ids=['current','stale','other'], embeddings=[[1.,0.]]*3,
                               metadatas=[{'document_id':1},{'document_id':1},{'document_id':2}], documents=['a','b','c'])
        for mocker in (patch.object(document_management,'UPLOAD_DIR', self.root),
                       patch.object(document_deletion,'get_collection',return_value=self.collection)):
            mocker.start()
            self.addCleanup(mocker.stop)
        app = FastAPI()
        app.include_router(document_management.router)
        app.dependency_overrides[database.get_connection] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def assert_rows(self, doc, count):
        for table in ('documents','document_chunks','document_sessions','question_answers','rag_turns'):
            column = 'id' if table == 'documents' else 'document_id'
            self.assertEqual(self.db.execute(f'SELECT COUNT(*) FROM {table} WHERE {column}=?',(doc,)).fetchone()[0], count, table)

    def test_delete_all_resources_and_keep_other_document(self):
        response = self.client.delete('/documents/1')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json(), {'message':'删除成功','id':1})
        self.assert_rows(1,0)
        self.assert_rows(2,1)
        self.assertFalse((self.root/'1.md').exists())
        self.assertTrue((self.root/'2.md').exists())
        self.assertEqual(self.collection.get()['ids'],['other'])
        self.assertEqual(self.client.get('/documents/1').status_code,404)
        self.assertEqual(self.client.delete('/documents/1').status_code,404)

    def test_missing_file_and_empty_vectors(self):
        (self.root/'1.md').unlink()
        self.collection.delete(where={'document_id':1})
        self.assertEqual(self.client.delete('/documents/1').status_code,200)
        self.assert_rows(1,0)

    def test_metadata_only_document(self):
        self.db.execute('INSERT INTO documents (id,title,file_type) VALUES (3,?,?)',('empty','markdown'))
        self.db.commit()
        self.assertEqual(self.client.delete('/documents/3').status_code,200)

    def test_chroma_failure_rolls_back_sqlite_and_preserves_file(self):
        with patch.object(document_deletion,'get_collection',side_effect=RuntimeError('unavailable')), patch.object(document_management,'logger'):
            self.assertEqual(self.client.delete('/documents/1').status_code,500)
        self.assert_rows(1,1)
        self.assertTrue((self.root/'1.md').exists())
        self.assertEqual(self.client.delete('/documents/1').status_code,200)

    def test_file_failure_keeps_sqlite_for_retry(self):
        with patch.object(Path,'unlink',side_effect=PermissionError('locked')), patch.object(document_management,'logger'):
            self.assertEqual(self.client.delete('/documents/1').status_code,500)
        self.assert_rows(1,1)
        self.assertTrue((self.root/'1.md').exists())
        self.assertEqual(self.client.delete('/documents/1').status_code,200)
        self.assert_rows(1,0)

    def test_unknown_and_unsafe_path(self):
        with patch.object(document_deletion,'get_collection') as store:
            self.assertEqual(self.client.delete('/documents/999').status_code,404)
            self.db.execute("UPDATE documents SET stored_name='../outside.md' WHERE id=1")
            self.db.commit()
            with patch.object(document_management,'logger'):
                self.assertEqual(self.client.delete('/documents/1').status_code,500)
            store.assert_not_called()
        self.assert_rows(1,1)


if __name__ == '__main__':
    unittest.main()
