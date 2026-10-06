import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openai import APITimeoutError
import database
import llm
import rag_chat
from routers import rag


class RagChatTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        db_path = Path(folder.name) / 'test.db'
        with patch.object(database, 'DB_PATH', db_path):
            database.init_db()
            database.init_db()
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('''
            INSERT INTO documents(id,title,file_type) VALUES (1,'Guide','markdown'),(2,'Other','markdown');
            INSERT INTO document_sessions(id,document_id,title) VALUES (1,1,'Chat'),(2,1,'Separate'),(3,2,'Other');
            INSERT INTO document_chunks(id,document_id,chunk_index,content,source_name)
                VALUES (10,1,1,'SQLite stores data on disk.','guide.md');
        ''')
        app = FastAPI()
        app.include_router(rag.router)
        app.dependency_overrides[database.get_connection] = lambda: self.db
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test'})
        env.start()
        self.addCleanup(env.stop)

    def response(self, text):
        client = MagicMock()
        client.__enter__.return_value = client
        client.chat.completions.create.return_value = NS(choices=[NS(
            finish_reason='stop', message=NS(content=text))])
        return client

    def ask(self, question='SQLite', session=1):
        return self.client.post('/documents/1/ask-rag/chat', json={
            'question': question, 'session_id': session, 'mode': 'bm25'})

    def test_two_turns_rewrite_and_citation_history(self):
        answer_client = self.response('SQLite stores data on disk.[1]')
        rewrite_client = self.response('SQLite stores data where?')
        with patch.object(llm, 'OpenAI', return_value=answer_client), \
                patch.object(rag_chat, 'OpenAI', return_value=rewrite_client) as rewrite:
            first = self.ask()
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['retrieval_question'], 'SQLite')
            rewrite.assert_not_called()
            second = self.ask('它保存在哪里？')
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(second.json()['question'], '它保存在哪里？')
            self.assertEqual(second.json()['retrieval_question'], 'SQLite stores data where?')
            prompt = answer_client.chat.completions.create.call_args.kwargs['messages'][1]['content']
            self.assertIn('历史对话', prompt)
            rewrite_prompt = rewrite_client.chat.completions.create.call_args.kwargs['messages'][1]['content']
            self.assertNotIn('[1]', rewrite_prompt)
        history = self.client.get('/documents/1/sessions/1/rag-history').json()
        self.assertEqual(history['total'], 2)
        self.assertEqual(history['items'][1]['question'], '它保存在哪里？')
        self.assertEqual(history['items'][0]['citations'][0]['chunk_id'], 10)
        self.assertNotIn('citations_json', history['items'][0])
        self.assertEqual(self.client.get('/documents/1/sessions/2/rag-history').json()['total'], 0)

    def test_recent_five_and_isolation(self):
        for i in range(7):
            self.db.execute('INSERT INTO rag_turns(document_id,session_id,question,retrieval_question,answer,citations_json,mode) VALUES(1,1,?,?,?,?,?)',
                            (str(i), str(i), 'Answer', '[]', 'bm25'))
        with patch.object(rag, 'rewrite_question', return_value='SQLite') as rewrite, \
                patch.object(llm, 'OpenAI', return_value=self.response('Answer[1]')):
            self.assertEqual(self.ask().status_code, 200)
            self.assertEqual([t['question'] for t in rewrite.call_args.args[1]], ['2','3','4','5','6'])
            self.assertEqual(self.ask(session=2).status_code, 200)
            self.assertEqual(rewrite.call_args.args[1], [])

    def test_errors_do_not_save_turn(self):
        for error, code in [(ValueError('bad rewrite'), 502),
                            (APITimeoutError(request=httpx.Request('POST','https://example.invalid')), 504)]:
            with patch.object(rag, 'rewrite_question', side_effect=error):
                self.assertEqual(self.ask().status_code, code)
        with patch.object(llm, 'OpenAI', return_value=self.response('Answer[99]')):
            self.assertEqual(self.ask().status_code, 502)
        self.assertEqual(self.db.execute('SELECT count(*) FROM rag_turns').fetchone()[0], 0)

    def test_preflight_and_empty_results(self):
        self.assertEqual(self.ask(session=3).status_code, 404)
        self.assertEqual(self.client.get('/documents/1/sessions/3/rag-history').status_code, 404)
        self.assertEqual(self.ask(question=' ').status_code, 422)
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            self.assertEqual(self.ask().status_code, 503)
        with patch.object(llm, 'OpenAI') as model:
            response = self.ask(question='unmatchedword')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['citations'], [])
            model.assert_not_called()

    def test_rewrite_rejects_invalid_response(self):
        for text in ['', 'x'*1001]:
            with patch.object(rag_chat, 'OpenAI', return_value=self.response(text)):
                with self.assertRaises(ValueError):
                    rag_chat.rewrite_question('它呢？', [{'question':'SQLite','answer':'Answer[1]'}])
