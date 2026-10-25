"""In-memory state for one Agent run."""
from dataclasses import dataclass, field
import re
import os
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    status: Literal['grounded', 'catalogue', 'insufficient', 'clarification', 'general']
    answer: str = Field(min_length=1, max_length=12000)


class InvalidFinalAnswer(ValueError):
    pass


def is_general_question(question):
    return bool(re.fullmatch(r'(你好|您好|嗨|谢谢|hello|hi|你能做什么|你有什么功能)[！!。.?？\s]*',
                             question.strip(), re.I))


@dataclass
class AgentState:
    knowledge_base_id: int
    messages: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    max_tool_calls: int = field(default_factory=lambda: max(1,min(12,int(os.getenv('AGENT_MAX_TOOL_CALLS','6')))))
    evidence: dict = field(default_factory=dict)
    context_exhausted: bool = False
    context_omitted: int = 0
    context_deduplicated: int = 0
    context_decisions: list[dict] = field(default_factory=list)
    deadline: float = field(default_factory=lambda: time.monotonic() + max(1,float(os.getenv('AGENT_TIMEOUT_SECONDS','120'))))

    def validate_final(self, content, question):
        if re.search(r'DSML|<\|.*?\|>|<tool_call|<function_call', content, re.I):
            raise InvalidFinalAnswer('不要输出工具协议或计划，必须返回最终 JSON 回答')
        try:
            final = FinalAnswer.model_validate_json(content)
        except ValueError as exc:
            raise InvalidFinalAnswer('最终输出必须是 JSON，只有 status 和 answer 两个字段') from exc
        answer = re.sub(r'\[citation_id\s*[:=]?\s*(\d+)\]', r'[\1]', final.answer)
        citations = self.citations_for(answer)
        if final.status == 'grounded' and not citations:
            raise InvalidFinalAnswer('正文回答必须使用本轮证据并标注有效引用；先检索，不能沿用历史事实')
        if final.status == 'catalogue':
            pages = [c['result'] for c in self.tool_calls
                     if c['tool']=='list_documents' and 'error' not in c['result']]
            if not pages:
                raise InvalidFinalAnswer('目录回答必须先调用目录工具，正文问题应检索并使用 grounded')
            if citations:
                raise InvalidFinalAnswer('正文事实应使用 grounded 状态，不能作为目录回答')
            # 目录模式只能呈现真实元数据，不能通过改标签绕过正文引用约束。
            docs = {d['document_id']:d for page in pages for d in page['documents']}
            answer = f"当前知识库共有 {pages[-1]['total']} 篇文档，本次列出 {len(docs)} 篇："
            answer += ''.join(f"\n- {d['title']}（ID {d['document_id']}，{d['file_type']}，{d['processing_status_label']}）"
                              for d in docs.values())
            if len(docs) < pages[-1]['total']:
                answer += '\n目录尚未全部读取。'
        if final.status == 'general' and not is_general_question(question):
            raise InvalidFinalAnswer('general 仅用于问候和能力介绍；文档事实必须重新检索')
        if final.status == 'insufficient':
            attempted = any(c['tool'] in ('search_knowledge_base','read_chunk_context') for c in self.tool_calls)
            if not attempted and self.can_call_tool:
                raise InvalidFinalAnswer('资料不足前先尝试检索；缺少用户条件时用 clarification')
            answer = ('本次检索未获得足够依据，暂时无法根据资料回答。请补充相关文档、版本或更具体的问题。'
                      if attempted else '工具调用预算已用完，尚未获得正文依据，暂时无法回答。请缩小问题范围或提高调用预算后重试。')
            citations = []
        if final.status == 'clarification':
            if citations or not re.search(r'[?？]', answer):
                raise InvalidFinalAnswer('clarification 只能询问缺失条件，不能给出文档事实')
        skipped = self.warnings()
        if skipped:
            answer += '\n\n本次未检索以下未就绪文档：' + '、'.join(d['title'] for d in skipped) + '。检索范围不完整。'
        if self.context_omitted:
            answer += '\n\n受上下文预算限制，部分工具结果未纳入本轮回答。'
        return {'answer':answer,'citations':citations,'answer_status':final.status}

    def warnings(self):
        skipped = {}
        for call in self.tool_calls:
            for doc in call['result'].get('skipped_documents',[]):
                skipped[doc['id']] = {'document_id':doc['id'],'title':doc['title'],
                                       'processing_status':doc['processing_status']}
        return list(skipped.values())

    def register_evidence(self, result):
        """来源由后端赋予稳定编号，重复片段复用编号。"""
        for item in result.get("items", []):
            key = (item["document_id"], item["chunk_id"])
            if key in self.evidence and self.evidence[key]["content"] != item["content"]:
                raise ValueError("执行期间文档内容发生变化，请重新提问")
            if key not in self.evidence:
                self.evidence[key] = {**item, "citation_id": len(self.evidence) + 1}
            item["citation_id"] = self.evidence[key]["citation_id"]
        return result

    def citations_for(self, answer):
        used = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
        valid = {item["citation_id"] for item in self.evidence.values()}
        if not used.issubset(valid):
            raise InvalidFinalAnswer("模型返回了不存在的引用编号")
        return [dict(item) for item in self.evidence.values() if item["citation_id"] in used]

    @property
    def can_call_tool(self):
        return not self.context_exhausted and len(self.tool_calls) < self.max_tool_calls
