"""MMR 贪心选择与向量缺失回退，不调用外部模型。"""
import copy
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from retrieval import mmr


class MmrTests(unittest.TestCase):
    def setUp(self):
        self.items = [{'document_id': 1, 'chunk_id': i, 'content': str(i),
                       'rank': i + 1, 'rerank_score': 1 - i / 100}
                      for i in range(4)]
        self.vectors = [[1., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]

    def test_checks_all_selected_vectors_and_preserves_inputs(self):
        original = copy.deepcopy((self.items, self.vectors))
        result = mmr.select_mmr(self.items, self.vectors, 3, .8)
        self.assertEqual([x['chunk_id'] for x in result], [0, 2, 3])
        self.assertEqual([x['rerank_rank'] for x in result], [1, 3, 4])
        self.assertEqual([x['rank'] for x in result], [1, 2, 3])
        self.assertEqual((self.items, self.vectors), original)

    def test_extreme_weights_ties_and_short_pool(self):
        self.assertEqual([x['chunk_id'] for x in mmr.select_mmr(self.items, self.vectors, 3, 1)], [0, 1, 2])
        self.assertEqual([x['chunk_id'] for x in mmr.select_mmr(self.items, self.vectors, 3, 0)], [0, 2, 3])
        self.assertEqual(len(mmr.select_mmr(self.items, self.vectors, 10, .8)), 4)
        self.assertEqual(mmr.select_mmr([], [], 5, .8), [])
        self.assertEqual(mmr.select_mmr(self.items, self.vectors, 0, .8), [])

    def test_negative_cosine_does_not_reward_unrelated_candidates(self):
        vectors = [[1., 0.], [0., 1.], [-1., 0.], [1., 0.]]
        result = mmr.select_mmr(self.items, vectors, 2, .8)
        self.assertEqual([x['chunk_id'] for x in result], [0, 1])

    def test_invalid_vectors_are_rejected(self):
        for bad in [[], [[0., 0.]] * 4, [[float('nan'), 1.]] * 4,
                    [[float('inf'), 1.]] * 4, [[1.], [1., 0.], [1.], [1.]]]:
            with self.subTest(vectors=bad), self.assertRaises(ValueError):
                mmr.select_mmr(self.items, bad, 2, .8)

    def test_settings_validate_finite_range_and_can_disable(self):
        for value in ['nan', 'inf', '-0.1', '1.1', 'bad']:
            with patch.dict(os.environ, {'MMR_ENABLED': 'true', 'MMR_LAMBDA': value}), \
                    self.assertRaises(HTTPException) as exc:
                mmr.mmr_weight()
            self.assertEqual(exc.exception.status_code, 503)
        with patch.dict(os.environ, {'MMR_ENABLED': 'false', 'MMR_LAMBDA': 'bad'}):
            self.assertIsNone(mmr.mmr_weight())

    def test_missing_stale_invalid_or_unreadable_vectors_fall_back(self):
        valid = {'ids': [f'doc:1:chunk:{i}' for i in range(4)],
                 'documents': [str(i) for i in range(4)],
                 'metadatas': [{'document_id': 1, 'chunk_id': i} for i in range(4)],
                 'embeddings': self.vectors}
        missing = {key: value[:-1] for key, value in valid.items()}
        stale = copy.deepcopy(valid)
        stale['documents'][0] = 'old content'
        wrong_source = copy.deepcopy(valid)
        wrong_source['metadatas'][0]['document_id'] = 2
        zero = copy.deepcopy(valid)
        zero['embeddings'][0] = [0., 0., 0.]
        for records in [missing, stale, wrong_source, zero]:
            with patch.object(mmr, 'get_collection') as collection:
                collection.return_value.get.return_value = records
                self.assertEqual(mmr.apply_mmr(self.items, 2, .8), self.items[:2])
        with patch.object(mmr, 'get_collection', side_effect=RuntimeError('unavailable')), \
                self.assertLogs(mmr.logger, level='ERROR'):
            self.assertEqual(mmr.apply_mmr(self.items, 2, .8), self.items[:2])
