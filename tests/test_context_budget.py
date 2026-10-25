"""明确覆盖检索结果加入后超预算、去重救回预算和保留完整证据收尾。"""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.context import (ContextBudget, build_messages, final_messages,
                           compact_tool_messages, fit_tool_result)
from agent.state import AgentState
from agent.tools import TOOLS
from evals.context_budget_baseline import legacy_fit_tool_result
import test_document_agent as support


def call(identifier):
    return SimpleNamespace(id=identifier,function=SimpleNamespace(
        name='search_knowledge_base',arguments='{"query":"部署"}'))


def append_result(state, identifier, items):
    state.messages.append({'role':'assistant','tool_calls':[{'id':identifier,'type':'function',
        'function':{'name':'search_knowledge_base','arguments':'{"query":"部署"}'}}]})
    result=state.register_evidence({'items':copy.deepcopy(items)})
    state.tool_calls.append({'tool':'search_knowledge_base','arguments':'{}','result':result})
    state.messages.append({'role':'tool','tool_call_id':identifier,'content':json.dumps(result,ensure_ascii=False)})


class ContextBudgetTests(unittest.TestCase):
    def item(self, index, text=None):
        return {'document_id':1,'chunk_id':index,'content':text or f'第{index}条部署依据。' * 35}

    def test_compaction_retains_protocol_first_body_and_cross_document_sources(self):
        state=AgentState(1,messages=build_messages('部署要求？'))
        item=self.item(1)
        append_result(state,'first',[item])
        append_result(state,'second',[item,{**item,'document_id':2}])
        original=copy.deepcopy(state)
        messages,count=compact_tool_messages(state.messages)
        self.assertEqual(count,1)
        first=json.loads(messages[3]['content'])['items'][0]
        second=json.loads(messages[5]['content'])['items']
        self.assertEqual(first['content'],item['content'])
        self.assertTrue(second[0]['content_reference'])
        self.assertNotIn('content',second[0])
        self.assertEqual(second[1]['content'],item['content'])
        self.assertEqual([m['tool_call_id'] for m in messages if m['role']=='tool'],['first','second'])
        self.assertEqual(state,original)
        again,replaced=compact_tool_messages(messages)
        self.assertEqual(again,messages)
        self.assertEqual(replaced,0)

    def preview(self,state,result,current):
        probe=copy.deepcopy(state)
        registered=probe.register_evidence(copy.deepcopy(result))
        probe.tool_calls.append({'tool':current.function.name,'arguments':current.function.arguments,'result':registered})
        probe.messages.append({'role':'tool','tool_call_id':current.id,'content':json.dumps(registered,ensure_ascii=False)})
        return probe

    def test_running_over_budget_final_fits_keeps_every_new_chunk(self):
        question='部署要求与恢复步骤是什么？'
        state=AgentState(1,messages=build_messages(question))
        append_result(state,'first',[self.item(1),self.item(2)])
        current=call('second')
        state.messages.append({'role':'assistant','tool_calls':[{'id':current.id,'type':'function',
            'function':{'name':current.function.name,'arguments':current.function.arguments}}]})
        result={'items':[self.item(3),self.item(4)]}
        probe=self.preview(state,result,current)
        base=ContextBudget()
        final_size=base.used(final_messages(question,[],probe))
        running_size=base.used(probe.messages,TOOLS)
        budget=ContextBudget(limit=(final_size+512+running_size)//2)
        self.assertLessEqual(final_size+512,budget.limit)
        self.assertGreater(running_size,budget.limit)
        fitted,omitted,exhausted=fit_tool_result(result,state,current,question,[],None,budget,TOOLS)
        self.assertEqual(fitted,result)
        self.assertEqual(omitted,0)
        self.assertTrue(exhausted)
        self.assertEqual(state.context_decisions[-1]['action'],'finalize')
        self.assertTrue(state.context_decisions[-1]['raw_over_budget'])
        _,baseline_omitted,_=legacy_fit_tool_result(result,state,current,question,[],None,budget,TOOLS)
        self.assertGreater(baseline_omitted,0)

    def test_duplicate_bodies_fit_after_compaction_without_losing_results(self):
        question='部署要求？'
        state=AgentState(1,messages=build_messages(question))
        items=[self.item(i) for i in range(1,6)]
        append_result(state,'first',items)
        current=call('second')
        result={'items':copy.deepcopy(items)}
        probe=self.preview(state,result,current)
        compact,_=compact_tool_messages(probe.messages)
        budget=ContextBudget(limit=ContextBudget().used(compact,TOOLS)+512)
        self.assertGreater(budget.used(probe.messages,TOOLS),budget.limit)
        fitted,omitted,exhausted=fit_tool_result(result,state,current,question,[],None,budget,TOOLS)
        self.assertEqual(fitted,result)
        self.assertEqual(omitted,0)
        self.assertFalse(exhausted)
        self.assertEqual(state.context_decisions[-1]['preview_deduplicated_items'],5)
        _,baseline_omitted,_=legacy_fit_tool_result(result,state,current,question,[],None,budget,TOOLS)
        self.assertGreater(baseline_omitted,0)


class ContextBudgetRunnerTests(unittest.TestCase):
    setUp=support.DocumentAgentTests.setUp
    run_agent=support.DocumentAgentTests.run_agent

    def test_runner_compacts_body_but_keeps_complete_logs_and_citations(self):
        item={'document_id':1,'chunk_id':11,'content':'部署要求 Python 3.11。'}
        calls=[]
        for identifier in ['one','two']:
            calls.append([{'id':identifier,'type':'function','function':{
                'name':'search_knowledge_base','arguments':'{"query":"Python"}'}}])
        with patch('agent.runner.execute_tool',return_value={'items':[item]}):
            result,client=self.run_agent([support.response(calls=calls[0]),support.response(calls=calls[1]),
                support.response('需要 Python 3.11。[1]')],question='部署要求？')
        tools=[m for m in client.chat.completions.create.call_args.kwargs['messages'] if m['role']=='tool']
        self.assertIn('content',json.loads(tools[0]['content'])['items'][0])
        self.assertNotIn('content',json.loads(tools[1]['content'])['items'][0])
        self.assertEqual(result['context_usage']['deduplicated_tool_items'],1)
        self.assertEqual(result['context_usage']['omitted_tool_items'],0)
        self.assertEqual(result['citations'][0]['content'],item['content'])
        self.assertTrue(all(r['result']['items'][0]['content']==item['content'] for r in result['tool_calls']))
