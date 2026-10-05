import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from openai import APIError, APITimeoutError
import llm
import retrieval
from routers import documents
from routers import rag as business
import test_indexing_api


class RagApiTests(test_indexing_api.IndexingApiTests):
    def response(self, answer='First chunk。[1]', finish='stop'):
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=answer))])
        return client

    def test_all_modes_and_citation_source(self):
        self.collection.upsert(ids=['a', 'b'], embeddings=[[1., 0.], [0., 1.]],
            documents=['First chunk', 'Second chunk'], metadatas=[
                {'document_id': 1, 'chunk_id': 10, 'source_name': 'guide.pdf', 'page_number': 1},
                {'document_id': 1, 'chunk_id': 11}])
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}), \
                patch.object(llm, 'OpenAI', return_value=self.response()) as model, \
                patch.object(retrieval, 'get_collection', return_value=self.collection), \
                patch.object(retrieval, 'embed_query', return_value=[1., 0.]):
            for mode in ['bm25', 'dense', 'hybrid']:
                r = self.client.post('/documents/1/ask-rag', json={'question': 'First', 'mode': mode})
                self.assertEqual(r.status_code, 200, r.text)
                data = r.json()
                self.assertEqual(data['mode'], mode)
                self.assertEqual(data['answer'], 'First chunk。[1]')
                self.assertEqual(len(data['citations']), 1)
                self.assertEqual(data['citations'][0]['chunk_id'], 10)
                self.assertEqual(data['citations'][0]['content'], 'First chunk')
                self.assertEqual(data['citations'][0]['page_number'], 1)
            self.assertEqual(model.call_count, 3)

    def test_empty_results_and_missing_config(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}), patch.object(llm, 'OpenAI') as model:
            r = self.client.post('/documents/1/ask-rag', json={'question': 'unmatchedword'})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['citations'], [])
            self.assertEqual(r.json()['retrieved_chunks'], 0)
            model.assert_not_called()
            self.assertEqual(self.client.post('/documents/1/ask-rag', json={'question': 'First'}).status_code, 503)

    def test_invalid_output(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}):
            for answer, finish in [('Answer [99]', 'stop'), (' ', 'stop'), ('partial [1]', 'length')]:
                with patch.object(llm, 'OpenAI', return_value=self.response(answer, finish)):
                    r = self.client.post('/documents/1/ask-rag', json={'question': 'First'})
                    self.assertEqual(r.status_code, 502, r.text)
            # Current contract permits uncited refusal/output; this is not evidence validation.
            with patch.object(llm, 'OpenAI', return_value=self.response('文档中未提供足够信息')):
                r = self.client.post('/documents/1/ask-rag', json={'question': 'First'})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['citations'], [])

    def test_request_and_upstream_errors(self):
        request = httpx.Request('POST', 'https://example.invalid')
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}):
            for error, status in [(APITimeoutError(request=request), 504),
                                  (APIError('failure', request=request, body=None), 502),
                                  (RuntimeError('failure'), 500)]:
                with patch.object(business, 'answer_with_chunks', side_effect=error), patch.object(business, 'logger'):
                    self.assertEqual(self.client.post('/documents/1/ask-rag', json={'question': 'First'}).status_code, status)
        for path, body, status in [('/documents/999/ask-rag', {'question': 'First'}, 404),
                                   ('/documents/2/ask-rag', {'question': 'First'}, 409),
                                   ('/documents/1/ask-rag', {'question': ' '}, 422),
                                   ('/documents/1/ask-rag', {'question': 'First', 'mode': 'bad'}, 422)]:
            self.assertEqual(self.client.post(path, json=body).status_code, status)
        with patch.object(retrieval, 'get_collection', return_value=self.collection):
            self.assertEqual(self.client.post('/documents/1/ask-rag', json={'question': 'First', 'mode': 'hybrid'}).status_code, 409)
