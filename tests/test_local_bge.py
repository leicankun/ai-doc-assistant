"""Offline checks for BGE snapshot completeness, reuse and API isolation."""
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import chromadb
from retrieval import local_index


class LocalIndexTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript('''
            CREATE TABLE documents(id INTEGER PRIMARY KEY,chunk_version INTEGER,indexed_version INTEGER);
            CREATE TABLE document_chunks(id INTEGER PRIMARY KEY,document_id INTEGER,chunk_index INTEGER,
                content TEXT,source_name TEXT,page_number INTEGER,start_char INTEGER,end_char INTEGER);
            INSERT INTO documents VALUES(1,1,77),(2,1,88);
            INSERT INTO document_chunks VALUES(10,1,1,'Windows MySQL','setup.md',NULL,0,13);
            INSERT INTO document_chunks VALUES(20,2,1,'Python learning','learn.md',NULL,0,15);
        ''')
        client = chromadb.EphemeralClient()
        self.collection = client.create_collection('bge-test-' + uuid4().hex, embedding_function=None,
                                                   configuration={'hnsw': {'space': 'cosine'}})
        self.addCleanup(client.delete_collection, self.collection.name)
        patcher = patch.object(local_index, 'get_local_collection', return_value=self.collection)
        patcher.start()
        self.addCleanup(patcher.stop)
        embed = patch.object(local_index, 'embed_local_texts',
                             side_effect=lambda texts: [[1.0, float(len(t))] for t in texts])
        self.embed = embed.start()
        self.addCleanup(embed.stop)

    def test_index_reuse_does_not_write_api_status(self):
        first = local_index.index_local_document(self.connection, 1)
        second = local_index.index_local_document(self.connection, 1)
        self.assertEqual(first['encoded_chunks'], 1)
        self.assertEqual(second['encoded_chunks'], 0)
        self.assertEqual(self.embed.call_count, 1)
        self.assertEqual(self.connection.execute('SELECT indexed_version FROM documents WHERE id=1').fetchone()[0], 77)

    def test_changed_content_is_rejected_then_reindexed(self):
        local_index.index_local_document(self.connection, 1)
        self.connection.execute("UPDATE document_chunks SET content='Linux MySQL' WHERE id=10")
        with self.assertRaisesRegex(ValueError, '过期'):
            local_index.search_local(self.connection, 'MySQL', document_id=1)
        result = local_index.index_local_document(self.connection, 1)
        self.assertEqual(result['encoded_chunks'], 1)
        self.assertEqual(local_index.search_local(self.connection, 'MySQL', document_id=1)[0]['content'], 'Linux MySQL')

    def test_partial_index_cannot_be_queried(self):
        self.connection.executemany('INSERT INTO document_chunks VALUES(?,1,?,\'text\',NULL,NULL,0,4)',
                                    [(100 + i, i + 2) for i in range(35)])
        calls = 0
        def failing(texts):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('GPU failure')
            return [[1.0, 0.0] for _ in texts]
        self.embed.side_effect = failing
        with self.assertRaisesRegex(RuntimeError, 'GPU failure'):
            local_index.index_local_document(self.connection, 1)
        self.assertEqual(self.collection.count(), 32)
        with self.assertRaisesRegex(ValueError, '未完成'):
            local_index.search_local(self.connection, 'text', document_id=1)

    def test_deleted_documents_are_excluded_and_missing_index_rejected(self):
        local_index.index_local_document(self.connection, 1)
        with self.assertRaisesRegex(ValueError, '索引缺失'):
            local_index.search_local(self.connection, 'text')
        local_index.index_local_document(self.connection, 2)
        self.connection.execute('DELETE FROM documents WHERE id=2')
        items = local_index.search_local(self.connection, 'text')
        self.assertEqual({item['document_id'] for item in items}, {1})


if __name__ == '__main__':
    unittest.main()
