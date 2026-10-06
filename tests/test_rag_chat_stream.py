import json
from unittest.mock import AsyncMock, MagicMock, patch
import llm
from routers import rag
from test_rag_chat import RagChatTests
from test_rag_stream import FakeStream


class RagChatStreamTests(RagChatTests):
    def stream(self, parts):
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.chat.completions.create = AsyncMock(return_value=FakeStream(parts, 'stop'))
        path = self.db.execute('PRAGMA database_list').fetchone()[2]
        with patch.object(rag, 'DB_PATH', path), patch.object(llm, 'AsyncOpenAI', return_value=client), \
                patch.object(rag, 'rewrite_question', return_value='SQLite'):
            response = self.client.post('/documents/1/ask-rag/chat/stream', json={
                'question': '它如何保存？', 'session_id': 1, 'mode': 'bm25'})
        self.assertEqual(response.status_code, 200, response.text)
        return [json.loads(s.removeprefix('data: ')) for s in response.text.strip().split('\n\n')], client

    def test_stream_saves_and_next_turn_reads_history(self):
        events, client = self.stream(['SQLite[', '1]'])
        self.assertEqual(events[0]['type'], 'meta')
        self.assertEqual(events[-1]['type'], 'done')
        row = self.db.execute('SELECT * FROM rag_turns').fetchone()
        self.assertEqual(row['answer'], 'SQLite[1]')
        self.assertEqual(json.loads(row['citations_json'])[0]['chunk_id'], 10)
        events, client = self.stream(['SQLite[1]'])
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('它如何保存', messages[1]['content'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM rag_turns').fetchone()[0], 2)

    def test_failed_stream_does_not_save(self):
        events, _ = self.stream(['SQLite[99]'])
        self.assertEqual(events[-1]['type'], 'error')
        self.assertEqual(self.db.execute('SELECT count(*) FROM rag_turns').fetchone()[0], 0)
