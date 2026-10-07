import copy
import unittest
from unittest.mock import patch

from retrieval import engine as retrieval
import test_indexing_api


class RrfTests(unittest.TestCase):
    def test_fusion_dedup_scores_and_input_preservation(self):
        def item(chunk, **fields):
            return {'document_id': 1, 'chunk_id': chunk, 'content': str(chunk), **fields}
        bm25 = [item(1, score=8), item(2, score=3)]
        dense = [item(2, distance=.1, record_id='b'), item(3, distance=.2, record_id='c')]
        original = copy.deepcopy((bm25, dense))
        result = retrieval.fuse_rrf(bm25, dense, top_k=3)
        self.assertEqual([r['chunk_id'] for r in result], [2, 1, 3])
        self.assertAlmostEqual(result[0]['rrf_score'], 1/62 + 1/61)
        self.assertEqual((result[0]['bm25_rank'], result[0]['dense_rank']), (2, 1))
        self.assertIsNone(result[1]['dense_rank'])
        self.assertIsNone(result[2]['bm25_rank'])
        self.assertEqual((bm25, dense), original)
        self.assertEqual(len(retrieval.fuse_rrf(bm25 + [bm25[0]], [], top_k=5)), 2)
        self.assertEqual(retrieval.fuse_rrf([], []), [])

    def test_candidate_pool_and_document_filter(self):
        chunks = [{'document_id': 1}, {'document_id': 2}]
        with patch.object(retrieval, 'search_bm25', return_value=[]) as bm25, \
                patch.object(retrieval, 'search_dense', return_value=[]) as dense:
            self.assertEqual(retrieval.search_hybrid(1, chunks, 'question', 5), [])
            self.assertEqual(bm25.call_args.kwargs['chunks'], chunks[:1])
            self.assertEqual(bm25.call_args.kwargs['top_k'], 20)
            self.assertEqual(dense.call_args.kwargs['top_k'], 20)


class HybridApiTests(test_indexing_api.IndexingApiTests):
    def test_real_chroma_hybrid_and_bm25_empty(self):
        self.collection.upsert(
            ids=['a', 'b', 'other'], documents=['First chunk', 'Second chunk', 'Other'],
            embeddings=[[1., 0.], [0., 1.], [1., 0.]],
            metadatas=[{'document_id': 1, 'chunk_id': 10},
                       {'document_id': 1, 'chunk_id': 11}, {'document_id': 99, 'chunk_id': 99}],
        )
        with patch.object(retrieval, 'get_collection', return_value=self.collection), \
                patch.object(retrieval, 'embed_query', return_value=[1., 0.]):
            for question in ['First', 'unmatchedword']:
                response = self.client.post('/documents/1/search', json={
                    'question': question, 'mode': 'hybrid', 'top_k': 1})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data['mode'], 'hybrid')
                self.assertEqual(data['total'], 1)
                item = data['items'][0]
                self.assertEqual(item['chunk_id'], 10)
                self.assertGreater(item['rrf_score'], 0)
                self.assertEqual(item['dense_rank'], 1)
                self.assertEqual(item['bm25_rank'], 1 if question == 'First' else None)

    def test_missing_index(self):
        with patch.object(retrieval, 'get_collection', return_value=self.collection), \
                patch.object(retrieval, 'embed_query') as embed:
            response = self.client.post('/documents/1/search', json={'question': 'First', 'mode': 'hybrid'})
            self.assertEqual(response.status_code, 409)
            embed.assert_not_called()
