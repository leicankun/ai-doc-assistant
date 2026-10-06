import os
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
import llm
from routers import rag
import test_indexing_api


class FakeStream:
    def __init__(self, parts, reason):
        self.parts, self.reason, self.closed = parts, reason, False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.closed = True
    async def __aiter__(self):
        yield NS(choices=[])
        for text in self.parts:
            yield NS(choices=[NS(delta=NS(content=text), finish_reason=None)])
        if self.reason:
            yield NS(choices=[NS(delta=NS(content=None), finish_reason=self.reason)])


class RagStreamTests(test_indexing_api.IndexingApiTests):
    def call_stream(self, parts, reason='stop'):
        stream = FakeStream(parts, reason)
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.chat.completions.create = AsyncMock(return_value=stream)
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}), patch.object(llm, 'AsyncOpenAI', return_value=client):
            response = self.client.post('/documents/1/ask-rag/stream', json={'question': 'First'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/event-stream', response.headers['content-type'])
        self.assertTrue(stream.closed)
        return [json.loads(event.removeprefix('data: ')) for event in response.text.strip().split('\n\n')]

    def test_split_reference_and_done(self):
        events = self.call_stream(['回答 [', '1', ']'])
        self.assertEqual(''.join(e['content'] for e in events if e['type']=='delta'), '回答 [1]')
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['citations'][0]['chunk_id'], 10)
        self.assertEqual(events[-1]['document_id'], 1)

    def test_invalid_reference_empty_and_incomplete(self):
        for parts, reason in [(['回答[99]'], 'stop'), ([], 'stop'), (['部分'], 'length'), (['部分'], None)]:
            events = self.call_stream(parts, reason)
            self.assertEqual(events[-1]['type'], 'error')
            self.assertFalse(any(e['type']=='done' for e in events))

    def test_empty_and_preflight(self):
        with patch.object(llm, 'AsyncOpenAI') as model:
            response = self.client.post('/documents/1/ask-rag/stream', json={'question': 'unmatchedword'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('"type": "done"', response.text)
            model.assert_not_called()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            self.assertEqual(self.client.post('/documents/1/ask-rag/stream', json={'question': 'First'}).status_code, 503)
        self.assertEqual(self.client.post('/documents/999/ask-rag/stream', json={'question': 'First'}).status_code, 404)
