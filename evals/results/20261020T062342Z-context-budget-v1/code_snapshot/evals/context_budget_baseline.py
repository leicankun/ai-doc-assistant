"""修复前的按尾部删减策略；用于前后对照，沿用当前提示词与最终上下文格式。"""
import json
from copy import deepcopy

from agent.context import final_messages, ContextBudgetExceeded


def legacy_fit_tool_result(result,state,call,question,history,summary,budget,tools):
    """保留原算法：要求运行历史和最终证据都装得下，否则先从新结果尾部删项。"""
    candidate=deepcopy(result)
    omitted=0
    while True:
        probe=deepcopy(state)
        if omitted:
            candidate['omitted_by_context_budget']=omitted
            candidate['context_budget_note']='上下文预算有限，以下是部分结果；未发送的片段不能引用。'
        registered=probe.register_evidence(deepcopy(candidate))
        probe.tool_calls.append({'tool':call.function.name,'arguments':call.function.arguments,'result':registered})
        probe.messages.append({'role':'tool','tool_call_id':call.id,'content':json.dumps(registered,ensure_ascii=False)})
        final_fits=budget.fits(final_messages(question,history,probe,summary),margin=512)
        running_fits=budget.fits(probe.messages,tools,margin=512)
        if final_fits and running_fits:
            return candidate,omitted,False
        field_name='items' if candidate.get('items') else 'documents' if candidate.get('documents') else None
        if field_name:
            candidate[field_name].pop()
            omitted+=1
            continue
        if final_fits:
            return candidate,omitted,True
        raise ContextBudgetExceeded('工具元数据超过上下文预算，本轮未保存；请缩小检索范围或提高预算。')
