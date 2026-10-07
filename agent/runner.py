"""有预算的工具循环、最终回答校验和有限纠正。"""
import json
import os
import time
from openai import OpenAI
from services.knowledge_base import require_base
from agent.context import build_messages, final_messages, ContextBudget, fit_tool_result
from agent.memory import prepare_session_context
from agent.state import AgentState, InvalidFinalAnswer
from agent.tools import TOOLS, execute_tool


class AgentCancelled(Exception):
    pass


def run_document_agent(connection, base_id, question, history=None, cancelled=None, session_id=None):
    require_base(connection, base_id)
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError('没有配置 DEEPSEEK_API_KEY')
    state = AgentState(base_id)
    budget = ContextBudget()
    summary = None
    memory_info = {'recent_turns': len(history or []), 'covered_turn_id': 0,
                   'summary_entries': 0, 'summary_updates': 0}
    corrections = 0
    last_error = None

    def check_runtime():
        if cancelled and cancelled():
            raise AgentCancelled('已停止执行，本轮不保存')
        remaining = state.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Agent 已达到总时间上限')
        return remaining

    with OpenAI(api_key=api_key, base_url='https://api.deepseek.com',
                timeout=60.0, max_retries=0) as client:
        check_runtime()
        budget.check(build_messages(question), TOOLS)
        if session_id is not None:
            history, summary, memory_info = prepare_session_context(
                connection, base_id, session_id, client, check_runtime, budget)
        state.messages = build_messages(question, history, summary)
        for _ in range(state.max_tool_calls + 3):
            remaining = check_runtime()
            kwargs = dict(model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'),
                          messages=state.messages, timeout=min(60.0,remaining),
                          extra_body={'thinking':{'type':'disabled'}}, max_tokens=2000)
            if state.can_call_tool:
                kwargs.update(tools=TOOLS,tool_choice='auto')
            else:
                kwargs.update(messages=final_messages(question,history,state,summary),
                              response_format={'type':'json_object'})
                if last_error:
                    kwargs['messages'].append({'role':'user','content':f'上次校验失败：{last_error}。请修正最终 JSON。'})
            budget.check(kwargs['messages'], kwargs.get('tools'))
            response = client.chat.completions.create(**kwargs)
            check_runtime()
            choice = response.choices[0]
            message = choice.message
            if message.tool_calls:
                if not state.can_call_tool or choice.finish_reason != 'tool_calls':
                    raise InvalidFinalAnswer('模型工具调用响应无效')
                state.messages.append(message.model_dump(exclude_none=True))
                for call in message.tool_calls:
                    check_runtime()
                    if not state.can_call_tool:
                        result = {'error':'工具调用次数已达上限，请根据已取得的结果回答'}
                    else:
                        result = execute_tool(connection,base_id,call.function.name,
                                              call.function.arguments,deadline=state.deadline)
                        check_runtime()
                        result, omitted, exhausted = fit_tool_result(
                            result, state, call, question, history, summary, budget, TOOLS)
                        state.context_omitted += omitted
                        state.context_exhausted = exhausted
                        result = state.register_evidence(result)
                        state.tool_calls.append({'tool':call.function.name,
                                                 'arguments':call.function.arguments,'result':result})
                    state.messages.append({'role':'tool','tool_call_id':call.id,
                                           'content':json.dumps(result,ensure_ascii=False)})
                continue
            try:
                if choice.finish_reason != 'stop':
                    raise InvalidFinalAnswer('回答未完整生成')
                final = state.validate_final(message.content or '',question)
                check_runtime()
                return {**final,'tool_calls':state.tool_calls,
                        'skipped_documents':state.warnings(),
                        'context_usage': {**memory_info, 'budget': budget.limit,
                                          'peak_estimated_tokens': budget.peak,
                                          'output_reserve': budget.output_reserve,
                                          'omitted_tool_items': state.context_omitted,
                                          'estimation': 'conservative_utf8_bytes'}}
            except InvalidFinalAnswer as exc:
                last_error = str(exc)
                corrections += 1
                if corrections > 2:
                    raise InvalidFinalAnswer('最终回答校验失败，本轮未保存，请重试') from exc
                state.messages.append({'role':'user','content':f'后端校验失败：{exc}。请纠正；如缺少本轮证据，先调用检索工具。'})
    raise InvalidFinalAnswer('Agent 已达到执行上限，本轮未保存')
