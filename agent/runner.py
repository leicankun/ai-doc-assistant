"""有预算的工具循环、最终回答校验和有限纠正。"""
import json
import os
import time
from openai import OpenAI
from services.knowledge_base import require_base
from agent.context import build_messages, final_messages, ContextBudget, fit_tool_result, compact_tool_messages
from agent.memory import prepare_session_context
from agent.state import AgentState, InvalidFinalAnswer
from agent.tools import TOOLS, execute_tool
from agent.streaming import stream_completion


class AgentCancelled(Exception):
    pass


def run_document_agent(connection, base_id, question, history=None, cancelled=None, session_id=None, on_event=None):
    # 先校验当前知识库和模型配置，工具只能在这个知识库范围内执行。
    require_base(connection, base_id)
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        raise RuntimeError('没有配置 DEEPSEEK_API_KEY')
    # state 保存本轮消息、工具记录、证据和截止时间；budget 管理上下文大小。
    state = AgentState(base_id)
    budget = ContextBudget()
    summary = None
    memory_info = {'recent_turns': len(history or []), 'covered_turn_id': 0,
                   'summary_entries': 0, 'summary_updates': 0}
    corrections = 0
    last_error = None

    def check_runtime():
        # 在外部调用前后检查取消与超时，返回剩余秒数供请求设置超时。
        if cancelled and cancelled():
            raise AgentCancelled('已停止执行，本轮不保存')
        remaining = state.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Agent 已达到总时间上限')
        return remaining

    with OpenAI(api_key=api_key, base_url='https://api.deepseek.com',
                timeout=60.0, max_retries=0) as client:
        check_runtime()
        if on_event:
            on_event({'type': 'status', 'message': '正在读取会话上下文…'})
        # 先检查固定提示、当前问题和工具定义是否已经超过预算。
        budget.check(build_messages(question), TOOLS)
        if session_id is not None:
            # 有会话 ID 时加载近期问答，并按需增量更新旧会话摘要。
            history, summary, memory_info = prepare_session_context(
                connection, base_id, session_id, client, check_runtime, budget)
        # 初始消息：系统提示 + 旧摘要 + 最近对话 + 当前问题。
        state.messages = build_messages(question, history, summary)
        # 限制循环内的模型请求次数，额外留出最终回答和纠正的机会。
        # 一次响应可以提出多个工具调用，工具执行次数另由 state 控制。
        for _ in range(state.max_tool_calls + 3):
            remaining = check_runtime()
            kwargs = dict(model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'),
                          messages=state.messages, timeout=min(60.0,remaining),
                          extra_body={'thinking':{'type':'disabled'}}, max_tokens=2000)
            if state.can_call_tool:
                # 预算允许时提供工具；auto 让模型决定是否调用以及调用哪个。
                kwargs.update(tools=TOOLS,tool_choice='auto')
            else:
                # 工具次数或上下文预算受限后，使用已有证据生成最终 JSON。
                kwargs.update(messages=final_messages(question,history,state,summary),
                              response_format={'type':'json_object'})
                if last_error:
                    kwargs['messages'].append({'role':'user','content':f'上次校验失败：{last_error}。请修正最终 JSON。'})
            # 每次请求都重新检查预算，工具结果和纠正消息也会占用上下文。
            budget.check(kwargs['messages'], kwargs.get('tools'))
            preview = ''
            if on_event:
                on_event({'type': 'status', 'message': '正在整理资料并生成回答，引用将在完成后显示。'})
                response, preview = stream_completion(client, kwargs, check_runtime, on_event)
            else:
                response = client.chat.completions.create(**kwargs)
            check_runtime()
            choice = response.choices[0]
            message = choice.message
            if message.tool_calls:
                # 分支一：模型提出工具调用请求，由后端执行实际 Python 函数。
                if not state.can_call_tool or choice.finish_reason != 'tool_calls':
                    raise InvalidFinalAnswer('模型工具调用响应无效')
                # 先保存调用请求，后续 tool 消息才能与它配对。
                state.messages.append(message.model_dump(exclude_none=True))
                for call in message.tool_calls:
                    check_runtime()
                    # 多个调用逐个检查额度，避免同一响应中的后续调用超限。
                    if not state.can_call_tool:
                        result = {'error':('上下文预算已触发最终回答，未执行后续工具调用'
                                           if state.context_exhausted else
                                           '工具调用次数已达上限，请根据已取得的结果回答')}
                    else:
                        if on_event:
                            on_event({'type': 'tool_start', 'tool': call.function.name})
                        result = execute_tool(connection,base_id,call.function.name,
                                              call.function.arguments,deadline=state.deadline)
                        check_runtime()
                        # 按预算筛选完整结果，再给纳入上下文的正文注册引用编号。
                        result, omitted, exhausted = fit_tool_result(
                            result, state, call, question, history, summary, budget, TOOLS)
                        state.context_omitted += omitted
                        state.context_exhausted = exhausted
                        result = state.register_evidence(result)
                        # tool_calls 用于返回执行记录；messages 用于下一轮模型输入。
                        state.tool_calls.append({'tool':call.function.name,
                                                 'arguments':call.function.arguments,'result':result})
                        if on_event:
                            on_event({'type': 'tool_call', **state.tool_calls[-1]})
                    # tool_call_id 将执行结果关联到模型提出的那一次调用。
                    state.messages.append({'role':'tool','tool_call_id':call.id,
                                           'content':json.dumps(result,ensure_ascii=False)})
                # 完整结果留在 tool_calls；模型历史只发送一次正文，后续复用引用。
                state.messages, replaced = compact_tool_messages(state.messages)
                state.context_deduplicated += replaced
                # 结果交回模型后再决策：继续检索、读上下文，或者给出答案。
                continue
            try:
                # 分支二：没有工具调用，按最终回答校验格式、状态和本轮引用。
                if choice.finish_reason != 'stop':
                    raise InvalidFinalAnswer('回答未完整生成')
                final = state.validate_final(message.content or '',question)
                check_runtime()
                if on_event:
                    # 引用规范化、范围提示或后端生成的目录以校验结果为准。
                    if not final['answer'].startswith(preview):
                        on_event({'type': 'reset'})
                        preview = ''
                    if final['answer'][len(preview):]:
                        on_event({'type': 'delta', 'content': final['answer'][len(preview):]})
                # 返回答案及执行统计；外层接口负责成功后的问答历史保存。
                return {**final,'tool_calls':state.tool_calls,
                        'skipped_documents':state.warnings(),
                        'context_usage': {**memory_info, 'budget': budget.limit,
                                          'peak_estimated_tokens': budget.peak,
                                          'output_reserve': budget.output_reserve,
                                          'omitted_tool_items': state.context_omitted,
                                          'deduplicated_tool_items': state.context_deduplicated,
                                          'finalized_due_to_context': state.context_exhausted,
                                          'context_decisions': state.context_decisions,
                                          'estimation': 'conservative_utf8_bytes'}}
            except InvalidFinalAnswer as exc:
                # 把校验错误交回模型，最多提供两次纠正机会，并受总循环限制。
                if on_event:
                    on_event({'type': 'reset'})
                    on_event({'type': 'status', 'message': '正在校验并修正回答…'})
                last_error = str(exc)
                corrections += 1
                if corrections > 2:
                    raise InvalidFinalAnswer('最终回答校验失败，本轮未保存，请重试') from exc
                state.messages.append({'role':'user','content':f'后端校验失败：{exc}。请纠正；如缺少本轮证据，先调用检索工具。'})
    # 循环用完仍没有合法答案时抛出异常，外层接口不会保存失败的问答。
    raise InvalidFinalAnswer('Agent 已达到执行上限，本轮未保存')
