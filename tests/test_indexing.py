"""Real Chroma operations with synthetic vectors; no external API calls."""
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

import chromadb
from retrieval import indexing


class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)
        self.connection.executescript('''
            CREATE TABLE documents (id INTEGER PRIMARY KEY, chunk_version INTEGER DEFAULT 0, indexed_version INTEGER DEFAULT 0);
            CREATE TABLE document_chunks (
                id INTEGER PRIMARY KEY, document_id INTEGER, chunk_index INTEGER,
                content TEXT, source_name TEXT, page_number INTEGER,
                start_char INTEGER, end_char INTEGER
            );
            INSERT INTO documents (id) VALUES (1), (2), (3);
        ''')
        self.connection.executemany(
            'INSERT INTO document_chunks VALUES (?,?,?,?,?,?,?,?)',
            [(10, 1, 1, 'First page', 'guide.pdf', 1, 0, 10),
             (11, 1, 2, 'Second page', 'guide.pdf', 3, 0, 11),
             (20, 2, 1, 'Other document', 'other.md', None, 0, 14)],
        )
        self.client = chromadb.EphemeralClient()
        self.collection = self.client.create_collection(
            name='test-' + uuid4().hex,
            embedding_function=None,
            configuration={'hnsw': {'space': 'cosine'}},
        )
        self.addCleanup(self.client.delete_collection, self.collection.name)
        self.store_patch = patch.object(indexing, 'get_collection', return_value=self.collection)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.embed_patch = patch.object(indexing, 'embed_texts', side_effect=self.vectors)
        self.embed = self.embed_patch.start()
        self.addCleanup(self.embed_patch.stop)

    @staticmethod
    def vectors(texts):
        return [[float(len(text))] + [0.0] * 1023 for text in texts]

    def test_write_update_cleanup_and_isolation(self):
        result = indexing.index_document(1, self.connection)
        self.assertEqual(result['indexed_chunks'], 2)
        indexing.index_document(2, self.connection)
        indexing.index_document(1, self.connection)
        self.assertEqual(self.collection.count(), 3)
        stored = self.collection.get(ids=['doc:1:chunk:10'], include=['documents', 'metadatas', 'embeddings'])
        self.assertEqual(stored['documents'], ['First page'])
        self.assertEqual(stored['metadatas'][0]['page_number'], 1)
        self.assertEqual(stored['metadatas'][0]['end_char'], 10)
        self.assertEqual(len(stored['embeddings'][0]), 1024)
        self.assertEqual(float(stored['embeddings'][0][0]), 10.0)
        other = self.collection.get(ids=['doc:2:chunk:20'])
        self.assertNotIn('page_number', other['metadatas'][0])
        self.connection.execute('DELETE FROM document_chunks WHERE id=11')
        self.connection.execute("UPDATE document_chunks SET content='Changed' WHERE id=10")
        result = indexing.index_document(1, self.connection)
        self.assertEqual(result['removed_chunks'], 1)
        self.assertEqual(self.collection.count(), 2)
        self.assertEqual(self.collection.get(ids=['doc:1:chunk:10'])['documents'], ['Changed'])
        self.assertEqual(self.collection.get(ids=['doc:2:chunk:20'])['documents'], ['Other document'])

    def test_invalid_input_and_embedding_failure_preserve_index(self):
        for document_id in [3, 999]:
            with self.assertRaises(ValueError):
                indexing.index_document(document_id, self.connection)
        self.embed.assert_not_called()
        indexing.index_document(1, self.connection)
        self.embed.side_effect = RuntimeError('Simulated API failure')
        with self.assertRaises(RuntimeError):
            indexing.index_document(1, self.connection)
        self.assertEqual(self.collection.count(), 2)
        self.embed.side_effect = lambda texts: []
        with self.assertRaises(RuntimeError):
            indexing.index_document(1, self.connection)
        self.assertEqual(self.collection.count(), 2)

    def test_multiple_write_batches_and_store_failure(self):
        self.connection.execute('DELETE FROM document_chunks WHERE document_id=1')
        self.connection.executemany(
            'INSERT INTO document_chunks VALUES (?,?,?,?,?,?,?,?)',
            [(100+i, 1, i+1, f'Chunk {i}', 'large.md', None, i, i+1) for i in range(205)],
        )
        indexing.index_document(1, self.connection)
        self.assertEqual(self.collection.count(), 205)
        self.connection.execute('DELETE FROM document_chunks WHERE document_id=1 AND id>100')
        with patch.object(self.collection, 'upsert', side_effect=RuntimeError('Write failed')):
            with self.assertRaises(RuntimeError):
                indexing.index_document(1, self.connection)
        self.assertEqual(self.collection.count(), 205)
        result = indexing.index_document(1, self.connection)
        self.assertEqual(result['removed_chunks'], 204)
        self.assertEqual(self.collection.count(), 1)


if __name__ == '__main__':
    unittest.main()
