import unittest
from unittest.mock import patch

import httpx
from openai import APIError, APITimeoutError
from retrieval import engine as retrieval
from routers import documents
from routers import search as business
import test_indexing_api


class DenseApiTests(test_indexing_api.IndexingApiTests):
    def test_dense_ranking_filter_and_top_k(self):
        self.collection.upsert(
            ids=['a', 'b', 'other'],
            embeddings=[[1., 0.], [0., 1.], [1., 0.]],
            documents=['First chunk', 'Second chunk', 'Other document'],
            metadatas=[{'document_id': 1, 'chunk_id': 10, 'page_number': 1},
                       {'document_id': 1, 'chunk_id': 11}, {'document_id': 99}],
        )
        with patch.object(retrieval, 'get_collection', return_value=self.collection), \
                patch.object(retrieval, 'embed_query', return_value=[1., 0.]):
            for limit, expected in [(1, 1), (20, 2)]:
                response = self.client.post('/documents/1/search', json={
                    'question': 'First', 'mode': 'dense', 'top_k': limit})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                self.assertEqual(data['mode'], 'dense')
                self.assertEqual(data['total'], expected)
                self.assertEqual(data['items'][0]['content'], 'First chunk')
                self.assertAlmostEqual(data['items'][0]['distance'], 0., places=5)
                self.assertEqual(data['items'][0]['page_number'], 1)
                self.assertTrue(all(i['document_id'] == 1 for i in data['items']))

    def test_dense_missing_index_and_errors(self):
        body = {'question': 'First', 'mode': 'dense'}
        with patch.object(retrieval, 'get_collection', return_value=self.collection), \
                patch.object(retrieval, 'embed_query') as embed:
            self.assertEqual(self.client.post('/documents/1/search', json=body).status_code, 409)
            embed.assert_not_called()
        request = httpx.Request('POST', 'https://example.invalid')
        for error, status in [(APITimeoutError(request=request), 504),
                              (APIError('failure', request=request, body=None), 502),
                              (ValueError('bad config'), 500), (RuntimeError('store failed'), 500)]:
            with patch.object(business, 'search_dense', side_effect=error), patch.object(business, 'logger'):
                self.assertEqual(self.client.post('/documents/1/search', json=body).status_code, status)
        self.assertEqual(self.client.post('/documents/999/search', json=body).status_code, 404)
        self.assertEqual(self.client.post('/documents/2/search', json=body).status_code, 409)
        for changes in [{'question': ' '}, {'mode': 'bad'}, {'top_k': 0}, {'top_k': 21}]:
            self.assertEqual(self.client.post('/documents/1/search', json={**body, **changes}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
