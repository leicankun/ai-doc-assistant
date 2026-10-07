import json
import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from agent.context import ContextBudget, ContextBudgetExceeded, build_messages, final_messages
from agent.memory import prepare_session_context, validate_summary
from agent.state import AgentState
import test_document_agent as support
from test_document_agent import response, tool


def entry(turn_id=1, text='使用 Windows', quote='使用 Windows'):
    return {'key': 'platform', 'kind': 'constraint', 'text': text,
            'source_turn_id': turn_id, 'source_quote': quote}


class AgentMemoryTests(unittest.TestCase):
    setUp = support.DocumentAgentTests.setUp
    run_agent = support.DocumentAgentTests.run_agent

    def seed(self, questions):
        self.db.execute("INSERT OR IGNORE INTO knowledge_base_sessions(id,knowledge_base_id,title) VALUES (1,1,'chat')")
        for q in questions:
            self.db.execute('''INSERT INTO knowledge_base_turns
                (knowledge_base_id,session_id,question,retrieval_question,answer,citations_json,mode)
                VALUES (1,1,?,?,'旧回答 [99]','[]','agent')''', (q, q))
        self.db.commit()

    def prepare(self, summaries):
        client = MagicMock()
        client.chat.completions.create.side_effect = [response(json.dumps(s, ensure_ascii=False), raw=True) for s in summaries]
        result = prepare_session_context(self.db, 1, 1, client, lambda: 60)
        return result, client

    def test_short_session_keeps_raw_history_without_summary_call(self):
        self.seed(['使用 Windows', '部署 v1'])
        (recent, summary, stats), client = self.prepare([])
        self.assertEqual(len(recent), 2)
        self.assertEqual(summary['entries'], [])
        client.chat.completions.create.assert_not_called()
        self.assertEqual(stats['covered_turn_id'], 0)

    def test_six_turns_create_persistent_summary_and_keep_last_five(self):
        self.seed(['使用 Windows'] + [f'后续问题 {i}' for i in range(5)])
        (recent, summary, stats), client = self.prepare([{'entries': [entry()]}])
        self.assertEqual([t['id'] for t in recent], [2, 3, 4, 5, 6])
        self.assertEqual(stats['covered_turn_id'], 1)
        self.assertEqual(summary['entries'][0]['source_turn_id'], 1)
        row = self.db.execute('SELECT * FROM agent_session_summaries').fetchone()
        self.assertEqual(row['covered_turn_id'], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0], 6)
        payload = json.loads(client.chat.completions.create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(payload['new_user_turns'], [{'id': 1, 'question': '使用 Windows'}])
        self.assertNotIn('旧回答', json.dumps(payload, ensure_ascii=False))
        (again, reused, info), second = self.prepare([])
        second.chat.completions.create.assert_not_called()
        self.assertEqual(reused, summary)
        self.assertEqual(info['summary_updates'], 0)

    def test_incremental_correction_overwrites_old_constraint(self):
        self.seed(['使用 Windows', '更正：使用 Linux'] + ['继续'] * 4)
        self.prepare([{'entries': [entry()]}])
        self.seed(['新问题'])
        (_, summary, stats), client = self.prepare([
            {'entries': [entry(2, '使用 Linux', '使用 Linux')]}])
        self.assertEqual(stats['covered_turn_id'], 2)
        self.assertEqual(summary['entries'][0]['text'], '使用 Linux')
        payload = json.loads(client.chat.completions.create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual([t['id'] for t in payload['new_user_turns']], [2])
        self.assertEqual(payload['previous_summary']['entries'][0]['text'], '使用 Windows')

    def test_failed_update_does_not_advance_cursor_or_delete_raw(self):
        self.seed(['使用 Windows', '更正：使用 Linux'] + ['继续'] * 4)
        self.prepare([{'entries': [entry()]}])
        self.seed(['新问题'])
        with self.assertRaisesRegex(ValueError, '摘要更新失败'):
            self.prepare([{'entries': [entry(999)]}])
        row = self.db.execute('SELECT * FROM agent_session_summaries').fetchone()
        self.assertEqual(row['covered_turn_id'], 1)
        self.assertEqual(row['revision'], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM knowledge_base_turns').fetchone()[0], 7)

    def test_summary_cannot_import_another_session_or_fabricate_quote(self):
        sources = {1: {'id': 1, 'question': '使用 Windows'}}
        for invalid in [entry(2), entry(1, quote='使用 Linux')]:
            with self.assertRaises(ValueError):
                validate_summary(json.dumps({'entries': [invalid]}), sources, {'entries': []}, [sources[1]], 4000)
        self.seed(['使用 Windows'])
        with self.assertRaises(HTTPException):
            prepare_session_context(self.db, 2, 1, MagicMock(), lambda: 60)

    def test_deleted_source_invalidates_old_summary(self):
        self.seed(['使用 Windows'] + ['继续'] * 6)
        self.prepare([{'entries': [entry()]}])
        self.db.execute('DELETE FROM knowledge_base_turns WHERE id=1')
        self.db.commit()
        (_, summary, stats), _ = self.prepare([{'entries': []}])
        self.assertEqual(summary['entries'], [])
        self.assertEqual(stats['covered_turn_id'], 2)

    def test_concurrent_late_update_cannot_overwrite_new_summary(self):
        self.seed(['使用 Windows', '更正：使用 Linux'] + ['继续'] * 4)
        self.prepare([{'entries': [entry()]}])
        self.seed(['新问题'])
        client = MagicMock()
        def concurrent(**kwargs):
            self.db.execute('UPDATE agent_session_summaries SET revision=revision+1')
            self.db.commit()
            return response(json.dumps({'entries': [entry(2, '使用 Linux', '使用 Linux')]}), raw=True)
        client.chat.completions.create.side_effect = concurrent
        with self.assertRaisesRegex(ValueError, '另一个请求'):
            prepare_session_context(self.db, 1, 1, client, lambda: 60)
        row = self.db.execute('SELECT * FROM agent_session_summaries').fetchone()
        self.assertEqual(row['covered_turn_id'], 1)
        self.assertEqual(row['revision'], 2)

    def test_raw_history_budget_compacts_additional_complete_turns(self):
        self.seed(['使用 Windows'] + ['继续说明部署过程'] * 5)
        with patch.dict('os.environ', {'AGENT_HISTORY_TOKENS': '240'}):
            (recent, _, stats), _ = self.prepare([{'entries': [entry()]}])
        self.assertLess(len(recent), 5)
        self.assertEqual(stats['covered_turn_id'], recent[0]['id'] - 1)

    def test_single_large_turn_rejected_without_truncation(self):
        self.seed(['继续说明部署过程' * 100])
        with patch.dict('os.environ', {'AGENT_HISTORY_TOKENS': '240'}):
            with self.assertRaises(ContextBudgetExceeded):
                self.prepare([])
        self.assertEqual(self.db.execute('SELECT LENGTH(question) FROM knowledge_base_turns').fetchone()[0], 800)

    def test_runner_injects_summary_and_removes_old_citation_numbers(self):
        self.seed(['使用 Windows'] + ['继续'] * 5)
        result, client = self.run_agent([
            response(json.dumps({'entries': [entry()]}), raw=True), response('你好')],
            question='你好', session_id=1)
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('旧会话摘要', messages[1]['content'])
        self.assertNotIn('[99]', json.dumps(messages, ensure_ascii=False))
        self.assertEqual(result['context_usage']['summary_entries'], 1)
        self.assertEqual(result['context_usage']['recent_turns'], 5)
        self.assertLessEqual(result['context_usage']['peak_estimated_tokens'], result['context_usage']['budget'])

    def test_request_over_budget_never_calls_model(self):
        with patch.dict('os.environ', {'AGENT_CONTEXT_TOKENS': '500'}):
            with self.assertRaises(ContextBudgetExceeded):
                self.run_agent([], question='你好')

    def test_tool_budget_omits_whole_chunks_and_keeps_citation_protocol(self):
        small = {'document_id': 1, 'chunk_id': 11, 'content': '部署要求 Python 3.11。'}
        huge = {'document_id': 1, 'chunk_id': 12, 'content': '无关段落' * 20000}
        with patch('agent.tools.search_base', return_value={'items': [small, huge]}):
            result, client = self.run_agent([
                response(calls=tool('{"query":"部署"}', 'search_knowledge_base')),
                response('需要 Python 3.11。[1]')], question='部署要求？')
        self.assertEqual([c['chunk_id'] for c in result['citations']], [11])
        trace = result['tool_calls'][0]['result']
        self.assertEqual(trace['items'][0]['content'], small['content'])
        self.assertEqual(trace['omitted_by_context_budget'], 1)
        messages = client.chat.completions.create.call_args.kwargs['messages']
        self.assertEqual(messages[-2]['tool_calls'][0]['id'], messages[-1]['tool_call_id'])
        for call in client.chat.completions.create.call_args_list:
            kwargs = call.kwargs
            self.assertLessEqual(ContextBudget().used(kwargs['messages'], kwargs.get('tools')), 24000)
        self.assertIn('上下文预算', result['answer'])

    def test_memory_is_never_registered_as_document_evidence(self):
        state = AgentState(1)
        summary = {'entries': [entry()]}
        messages = final_messages('部署？', [], state, summary)
        self.assertIn('不是文档证据', messages[0]['content'])
        self.assertEqual(state.evidence, {})
        with self.assertRaises(ValueError):
            state.validate_final('{"status":"grounded","answer":"Windows [1]"}', '部署？')

    def test_deleting_base_cleans_summary(self):
        self.seed(['使用 Windows'] + ['继续'] * 5)
        self.prepare([{'entries': [entry()]}])
        from routers.knowledge_bases import delete_base
        delete_base(1, self.db)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM agent_session_summaries').fetchone()[0], 0)

    def test_deletion_during_summary_generation_prevents_commit(self):
        self.seed(['使用 Windows'] + ['继续'] * 5)
        client = MagicMock()
        def deleted(**kwargs):
            self.db.execute('DELETE FROM knowledge_base_turns WHERE id=1')
            self.db.commit()
            return response(json.dumps({'entries': [entry()]}), raw=True)
        client.chat.completions.create.side_effect = deleted
        with self.assertRaisesRegex(ValueError, '来源发生变化'):
            prepare_session_context(self.db, 1, 1, client, lambda: 60)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM agent_session_summaries').fetchone()[0], 0)

    def test_cancel_during_summary_prevents_commit(self):
        self.seed(['使用 Windows'] + ['继续'] * 5)
        from agent.runner import AgentCancelled
        client = MagicMock()
        client.chat.completions.create.return_value = response(json.dumps({'entries': [entry()]}), raw=True)
        checks = MagicMock(side_effect=[60, AgentCancelled('stop')])
        with self.assertRaises(AgentCancelled):
            prepare_session_context(self.db, 1, 1, client, checks)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM agent_session_summaries').fetchone()[0], 0)

    def test_final_after_tool_limit_still_receives_summary_and_real_citations(self):
        self.seed(['使用 Windows'] + ['继续'] * 5)
        chunk = {'document_id': 1, 'chunk_id': 11, 'content': '部署要求 Python 3.11。'}
        with patch.dict('os.environ', {'AGENT_MAX_TOOL_CALLS': '1'}), patch('agent.tools.search_base', return_value={'items': [chunk]}):
            result, client = self.run_agent([
                response(json.dumps({'entries': [entry()]}), raw=True),
                response(calls=tool('{"query":"部署"}', 'search_knowledge_base')),
                response('需要 Python 3.11。[1]')], question='部署要求？', session_id=1)
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertNotIn('tools', kwargs)
        self.assertIn('旧会话摘要', kwargs['messages'][1]['content'])
        self.assertIn('Python 3.11', kwargs['messages'][-1]['content'])
        self.assertEqual(result['citations'][0]['content'], chunk['content'])
