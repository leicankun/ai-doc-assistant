"""使用真实 SDK 的流式累积器和隔离 SQLite 验证 Agent SSE。"""
import asyncio
import json
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import OpenAI

from agent import runner as agent
from agent.runner import AgentCancelled
from routers import knowledge_bases as api
from schemas import AgentRequest
from storage import database
import test_document_agent as support


def chunks(parts, finish='stop'):
    deltas = [{'content': part} if isinstance(part, str) else part for part in parts]
    return [
        {'id': 'completion', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test',
         'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
        for delta in deltas
    ] + [{'id': 'completion', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test',
          'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]


def tool_chunks(name='search_knowledge_base', arguments='{"query":"Python"}'):
    return chunks([
        {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_1', 'type': 'function',
          'function': {'name': name[:6], 'arguments': arguments[:3]}}]},
        {'tool_calls': [{'index': 0, 'function': {'name': name[6:], 'arguments': arguments[3:]}}]},
    ], finish='tool_calls')


class ModelBody(httpx2.SyncByteStream):
    def __init__(self, frames, timeline):
        self.frames = frames
        self.timeline = timeline
        self.closed = False

    def __iter__(self):
        for frame in self.frames:
            yield ('data: '+json.dumps(frame, ensure_ascii=False)+'\n\n').encode()
        self.timeline.append('model_finished')
        yield b'data: [DONE]\n\n'

    def close(self):
        self.closed = True


class AgentStreamRunnerTests(unittest.TestCase):
    setUp = support.DocumentAgentTests.setUp

    def run_stream(self, sequences, question='部署要求？', on_event=None):
        self.events = []
        self.timeline = []
        self.requests = []
        self.bodies = []
        sequence = iter(sequences)

        def respond(request):
            self.requests.append(json.loads(request.content))
            body = ModelBody(next(sequence), self.timeline)
            self.bodies.append(body)
            return httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, stream=body)

        client = OpenAI(api_key='test', http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))

        def emit(event):
            self.events.append(event)
            self.timeline.append(event['type'])
            if on_event:
                on_event(event)

        self.cancelled = False
        with patch.object(agent, 'OpenAI', return_value=client), patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'}):
            return agent.run_document_agent(self.db, 1, question, on_event=emit,
                                            cancelled=lambda: self.cancelled)

    def visible_answer(self):
        answer = ''
        for event in self.events:
            if event['type'] == 'reset':
                answer = ''
            elif event['type'] == 'delta':
                answer += event['content']
        return answer

    def test_real_sdk_streams_answer_before_completion_and_accumulates_tools(self):
        item = {'document_id': 1, 'chunk_id': 11, 'content': '需要 Python'}
        with patch.object(agent, 'execute_tool', return_value={'items': [item], 'total': 1}) as execute:
            result = self.run_stream([tool_chunks(), chunks([
                '{"status":"grounded","answer":"需要', ' Python。', '[1]"}',
            ])])
        self.assertEqual(self.visible_answer(), '需要 Python。[1]')
        self.assertEqual(result['citations'][0]['chunk_id'], 11)
        self.assertGreaterEqual(sum(e['type'] == 'delta' for e in self.events), 3)
        self.assertLess(self.timeline.index('delta'), len(self.timeline) - 1)
        self.assertEqual(self.timeline[-1], 'model_finished')
        self.assertTrue(all(r['stream'] for r in self.requests))
        self.assertEqual(execute.call_args.args[2:4], ('search_knowledge_base', '{"query":"Python"}'))
        self.assertEqual(self.requests[1]['messages'][-1]['tool_call_id'], 'call_1')
        self.assertEqual(json.loads(self.requests[1]['messages'][-1]['content'])['items'][0]['citation_id'], 1)
        self.assertEqual(sum(e['type'] == 'tool_call' for e in self.events), 1)
        self.assertTrue(all(body.closed for body in self.bodies))

    def test_json_escapes_unicode_and_answer_before_status(self):
        expected = '你好\n"路径\\资料"😀'
        result = self.run_stream([chunks([
            '{"status":"general","answer":"你好\\', 'n\\"路径\\\\资料\\"\\ud83d', '\\ude00"}',
        ])], question='你好')
        self.assertEqual(result['answer'], expected)
        self.assertEqual(self.visible_answer(), expected)
        self.assertNotIn('"status"', self.visible_answer())
        result = self.run_stream([chunks(['{"answer":"你好', '","status":"general"}'])], question='你好')
        self.assertEqual(self.visible_answer(), result['answer'])

    def test_repeated_tool_body_is_compacted_in_streaming_requests(self):
        item = {'document_id': 1, 'chunk_id': 11, 'content': '部署要求 Python 3.11。'}
        second = tool_chunks()
        second[0]['choices'][0]['delta']['tool_calls'][0]['id'] = 'call_2'
        with patch.object(agent, 'execute_tool', return_value={'items': [item]}):
            result = self.run_stream([tool_chunks(), second,
                chunks(['{"status":"grounded","answer":"需要 Python 3.11。[1]"}'])])
        tools = [m for m in self.requests[-1]['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tools], ['call_1', 'call_2'])
        self.assertIn('content', json.loads(tools[0]['content'])['items'][0])
        self.assertTrue(json.loads(tools[1]['content'])['items'][0]['content_reference'])
        self.assertEqual(result['context_usage']['deduplicated_tool_items'], 1)
        self.assertTrue(all(t['result']['items'][0]['content'] == item['content'] for t in result['tool_calls']))
        self.assertEqual(self.visible_answer(), result['answer'])

    def test_invalid_citation_preview_is_reset_before_corrected_answer(self):
        item = {'document_id': 1, 'chunk_id': 11, 'content': '需要 Python'}
        with patch.object(agent, 'execute_tool', return_value={'items': [item]}):
            result = self.run_stream([tool_chunks(),
                chunks(['{"status":"grounded","answer":"错误[99]', '"}']),
                chunks(['{"status":"grounded","answer":"需要 Python。', '[citation_id 1]"}'])])
        self.assertTrue(any(e['type'] == 'reset' for e in self.events))
        self.assertEqual(self.visible_answer(), '需要 Python。[1]')
        self.assertEqual(self.visible_answer(), result['answer'])

    def test_truncated_completion_is_corrected(self):
        result = self.run_stream([
            chunks(['{"status":"general","answer":"未完成'], finish='length'),
            chunks(['{"status":"general","answer":"你好"}']),
        ], question='你好')
        self.assertEqual(self.visible_answer(), result['answer'])
        self.assertEqual(result['answer'], '你好')
        self.assertTrue(any(e['type'] == 'reset' for e in self.events))

    def test_catalogue_and_insufficient_use_backend_text(self):
        result = self.run_stream([tool_chunks('list_documents', '{}'),
            chunks(['{"status":"catalogue","answer":"最低内存 99GB"}'])])
        self.assertNotIn('99GB', self.visible_answer())
        self.assertIn('first', self.visible_answer())
        self.assertEqual(self.visible_answer(), result['answer'])
        with patch.object(agent, 'execute_tool', return_value={'items': [], 'skipped_documents': [
                {'id': 2, 'title': 'pending', 'processing_status': 'not_chunked'}]}):
            result = self.run_stream([tool_chunks(),
                chunks(['{"status":"insufficient","answer":"知识库肯定没有"}'])])
        self.assertNotIn('肯定', self.visible_answer())
        self.assertIn('范围不完整', self.visible_answer())
        self.assertEqual(self.visible_answer(), result['answer'])

    def test_cancel_during_tokens_closes_model_stream(self):
        def stop(event):
            if event['type'] == 'delta':
                self.cancelled = True
        with self.assertRaises(AgentCancelled):
            self.run_stream([chunks(['{"status":"general","answer":"你好', '继续"}'])],
                            question='你好', on_event=stop)
        self.assertEqual(self.visible_answer(), '你好')
        self.assertTrue(self.bodies[0].closed)
        self.assertNotIn('model_finished', self.timeline)


class AgentStreamApiTests(unittest.TestCase):
    setUp = support.DocumentAgentTests.setUp

    def prepare(self):
        self.path = self.db.execute('PRAGMA database_list').fetchone()['file']
        self.db.execute("INSERT INTO knowledge_base_sessions(id,knowledge_base_id,title) VALUES (1,1,'chat')")
        self.db.commit()
        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[database.get_connection] = lambda: self.db
        client = TestClient(app)
        self.addCleanup(client.close)
        for mocker in (patch.object(api, 'connect', side_effect=lambda: database.connect_test_db(self.path)), patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'})):
            mocker.start()
            self.addCleanup(mocker.stop)
        return client

    @staticmethod
    def result():
        item = {'document_id': 1, 'chunk_id': 11, 'content': '需要 Python', 'citation_id': 1}
        return {'answer': '需要 Python。[1]', 'citations': [item], 'answer_status': 'grounded',
                'tool_calls': [{'tool': 'search_knowledge_base', 'arguments': '{}', 'result': {'items': [item]}}]}

    def count_turns(self):
        return self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0]

    def test_done_includes_saved_answer_sources_and_tool_trace(self):
        client = self.prepare()
        result = self.result()
        def run(*args, on_event, **kwargs):
            self.assertEqual(self.count_turns(), 0)
            on_event({'type': 'tool_start', 'tool': 'search_knowledge_base'})
            on_event({'type': 'tool_call', **result['tool_calls'][0]})
            on_event({'type': 'delta', 'content': '需要 Python。'})
            on_event({'type': 'delta', 'content': '[1]'})
            self.assertEqual(self.count_turns(), 0)
            return result
        with patch.object(api, 'run_document_agent', side_effect=run):
            response = client.post('/knowledge-bases/1/agent/stream', json={'question': '要求？', 'session_id': 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('text/event-stream', response.headers['content-type'])
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([e['type'] for e in events], ['meta', 'tool_start', 'tool_call', 'delta', 'delta', 'done'])
        self.assertEqual(events[-1]['answer'], result['answer'])
        self.assertEqual(events[-1]['citations'], result['citations'])
        history = client.get('/knowledge-bases/1/sessions/1/rag-history').json()['items']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['id'], events[-1]['id'])
        self.assertEqual(history[0]['tool_calls'], result['tool_calls'])
        self.assertEqual(history[0]['mode'], 'agent')

    def test_failure_after_delta_sends_error_and_does_not_save(self):
        client = self.prepare()
        def fail(*args, on_event, **kwargs):
            on_event({'type': 'delta', 'content': 'partial'})
            raise ValueError('最终引用校验失败')
        with patch.object(api, 'run_document_agent', side_effect=fail):
            response = client.post('/knowledge-bases/1/agent/stream', json={'question': '要求？', 'session_id': 1})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual([e['type'] for e in events], ['meta', 'delta', 'error'])
        self.assertEqual(events[-1]['message'], '最终引用校验失败')
        self.assertEqual(self.count_turns(), 0)

    def test_preflight_rejects_invalid_base_session_key_and_body(self):
        client = self.prepare()
        with patch.object(api, 'run_document_agent') as run:
            for path, body, status in [
                ('/knowledge-bases/999/agent/stream', {'question': '你好'}, 404),
                ('/knowledge-bases/2/agent/stream', {'question': '你好', 'session_id': 1}, 404),
                ('/knowledge-bases/1/agent/stream', {'question': '  '}, 422),
            ]:
                self.assertEqual(client.post(path, json=body).status_code, status)
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
                self.assertEqual(client.post('/knowledge-bases/1/agent/stream', json={'question': '你好'}).status_code, 503)
            run.assert_not_called()

    def test_without_session_returns_done_without_saving(self):
        client = self.prepare()
        with patch.object(api, 'run_document_agent', return_value=self.result()):
            response = client.post('/knowledge-bases/1/agent/stream', json={'question': '要求？'})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIsNone(events[-1]['id'])
        self.assertEqual(self.count_turns(), 0)

    def test_disconnect_after_first_delta_cancels_worker_without_saving(self):
        self.prepare()
        disconnected = False
        observed = threading.Event()
        async def is_disconnected():
            return disconnected
        def run(*args, on_event, cancelled, **kwargs):
            on_event({'type': 'delta', 'content': 'partial'})
            for _ in range(400):
                if cancelled():
                    observed.set()
                    raise AgentCancelled()
                observed.wait(0.005)
            raise AssertionError('worker 没有收到取消信号')
        async def consume():
            nonlocal disconnected
            response = api.document_agent_stream(1, AgentRequest(question='要求？', session_id=1),
                SimpleNamespace(is_disconnected=is_disconnected), self.db)
            iterator = response.body_iterator
            self.assertIn('"type": "meta"', await anext(iterator))
            self.assertIn('"type": "delta"', await anext(iterator))
            disconnected = True
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
        with patch.object(api, 'run_document_agent', side_effect=run):
            asyncio.run(consume())
        self.assertTrue(observed.is_set())
        self.assertEqual(self.count_turns(), 0)


if __name__ == '__main__':
    unittest.main()
