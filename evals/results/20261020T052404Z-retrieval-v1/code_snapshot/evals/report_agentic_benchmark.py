"""仅从已保存结果生成报告，不重新请求模型或改动评分。"""
import argparse
import json
from datetime import datetime
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []


def ratio(n,d):
    return f'{n}/{d}（{n/d:.1%}）' if d else '未统计'


def cell(value):
    return str(value).replace('|',' / ').replace('\n',' ')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('result_directory',type=Path)
    args=parser.parse_args()
    folder=args.result_directory.resolve()
    meta=read(folder/'metadata.json')
    data=read(folder/'summary.json')
    answers=lines(folder/'answers.jsonl')
    memory=lines(folder/'memory.jsonl')
    retrieval=lines(folder/'retrieval.jsonl')
    setup=read(folder/'setup_calls.json')
    rag,agent=data['arms']['rag'],data['arms']['agent']
    from datetime import timedelta, timezone
    date=datetime.fromisoformat(meta['started_at_utc']).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
    report=[
        '# Agentic RAG 真实模型对照评测', '',
        f'运行开始：{date}（北京时间）。结果目录：`{folder.name}`。', '',
        f"使用 {meta['answer_model']}、{meta['embedding_model']} 和 {meta['rerank_model']}；"
        f"{len(meta['document_ids'])} 份合成 Markdown 文档、{meta['chunk_count']} 个真实分块、30 道单轮题。", '',
        '**以下质量计数来自固定规则的同模型自动评审，不是独立人工验证的准确率。** '
        '两个系统使用相同知识库、回答模型、底层检索实现；普通 RAG 固定 Hybrid/Top-K=5/候选池30/重排，'
        'Agent 自主选择工具和检索参数。两者提示词、范围与过滤策略也有差异，因此结果是整体系统比较。'
        '自动评审存在误判与口径偏严，具体例子见 [助手复核备注](REVIEW_NOTES.md)，原始分数未事后改写。', '',
        '## 单轮问答结果', '',
        '| 指标 | 普通 RAG | Agentic RAG |','|---|---:|---:|',
        f"| 请求成功 | {ratio(rag['request_success'],rag['questions'])} | {ratio(agent['request_success'],agent['questions'])} |",
        f"| 自动评审成功 | {ratio(rag['judge_success'],rag['questions'])} | {ratio(agent['judge_success'],agent['questions'])} |",
        f"| 任务通过：全部要点、行为正确、未发现无依据事实 | {ratio(rag['task_pass'],rag['questions'])} | {ratio(agent['task_pass'],agent['questions'])} |",
        f"| 严格通过：再要求引用支持 | {ratio(rag['strict_pass'],rag['questions'])} | {ratio(agent['strict_pass'],agent['questions'])} |",
        f"| 平均要点覆盖（成功评分样本） | {rag['point_coverage_mean_scored']:.1%} | {agent['point_coverage_mean_scored']:.1%} |",
        f"| 可回答题未发现引用错误 | {ratio(rag['supported_citations'],rag['citation_scored_questions'])} | {ratio(agent['supported_citations'],agent['citation_scored_questions'])} |",
        f"| 发现无依据事实的回答数 | {rag['unsupported_answer_count']} | {agent['unsupported_answer_count']} |",
        f"| 请求耗时 P50 / P95（全体尝试，秒） | {rag['latency_all_attempts_seconds']['p50']} / {rag['latency_all_attempts_seconds']['p95']} | {agent['latency_all_attempts_seconds']['p50']} / {agent['latency_all_attempts_seconds']['p95']} |",
        f"| 每题平均聊天 token（不含 Embedding/重排/评分） | {rag['chat_tokens_mean']} | {agent['chat_tokens_mean']} |",
        f"| 每题平均工具调用（成功请求） | {rag['tool_calls_mean']} | {agent['tool_calls_mean']} |", '',
        '请求失败及评分失败均留在总题数分母。耗时不含资料入库和评审，使用 TestClient，'
        f"同时最多 {meta['workers']} 个独立工作线程；不等于浏览器延迟、SSE 首段延迟或并发容量。", '',
        '### 按题型分组', '',
        '| 题型 | 普通 RAG 任务 / 严格通过 | Agent 任务 / 严格通过 |', '|---|---|---|',
    ]
    names={'direct':'直接题','cross_document':'综合题（部分跨文档）','version':'版本/产品辨识',
           'context':'完整上下文','unanswerable':'不可回答','clarification':'条件澄清'}
    for category in names:
        r,a=rag['categories'][category],agent['categories'][category]
        report.append(f"| {names[category]} | {r['task_pass']}/{r['n']} / {r['strict_pass']}/{r['n']} | {a['task_pass']}/{a['n']} / {a['strict_pass']}/{a['n']} |")
    paired={}
    for row in answers:
        paired.setdefault(row['id'],{})[row['arm']]=row
    gains,losses=[],[]
    for identifier,rows in sorted(paired.items()):
        r=rows['rag'].get('judge',{}).get('task_pass',False)
        a=rows['agent'].get('judge',{}).get('task_pass',False)
        if a and not r: gains.append(identifier)
        if r and not a: losses.append(identifier)
    report.extend(['',f"Agent 通过而普通 RAG 未通过：{', '.join(gains) or '无'}。",
                   f"普通 RAG 通过而 Agent 未通过：{', '.join(losses) or '无'}。", '',
                   '## 检索消融（只测检索）','',
                   '| 配置 | 请求成功 | 可回答题任何依据命中 | 可回答题全部依据覆盖 | 平均依据覆盖 |',
                   '|---|---|---|---|---|'])
    for label,row in data['retrieval_ablations'].items():
        report.append(f"| {label} | {ratio(row['success'],row['questions'])} | {ratio(row['any_evidence_hit'],row['answerable_questions'])} | {ratio(row['full_evidence_coverage'],row['answerable_questions'])} | {row['mean_evidence_coverage']:.1%} |")
    report.extend(['','全部配置固定候选池30、Top-K=5。依据覆盖按来源文档的字符区间并集计算，支持跨块；'
                   '24 道可回答题构成覆盖率分母。重排与无重排的 Hybrid 对照不会同时改变候选池。', '',
                   '## 多轮会话与摘要', '',
                   '| 场景 | 方法 | 成功轮数 | 第8轮任务通过 | 第8轮严格通过 | 摘要更新批次 / 覆盖游标 |',
                   '|---|---|---|---|---|---|'])
    for scenario,arms in data['memory'].items():
        for arm,row in arms.items():
            grade=row.get('final_judge') or {}
            usage=row.get('context_usage') or {}
            report.append(f"| {scenario} | {arm} | {row['request_success']}/{row['attempts']} | {grade.get('task_pass','未评分')} | {grade.get('strict_pass','未评分')} | {usage.get('summary_updates','—')} / {usage.get('covered_turn_id','—')} |")
    report.extend(['','第2轮用户纠正的实际环境或版本被移出最近5轮；中间明确询问其他话题；第8轮回到实际场景。'
                   'Agent 使用真实持久化摘要；普通 RAG 使用生产多轮问题改写与最近5轮。'
                   '各自保留生成历史，属于多轮系统比较，只有2个会话不能推广为普遍记忆成功率。', '',
                   '## 实际供应商用量', '',
                   '| 阶段 | 聊天 token | Embedding token | 重排 token | 真实外部调用尝试 |',
                   '|---|---:|---:|---:|---:|'])
    groups=[('资料入库',setup)]
    for arm in ('rag','agent'):
        groups.append((f'单轮 {arm}',[c for r in answers if r['arm']==arm for c in r['calls']]))
    groups.extend([('检索消融',[c for r in retrieval for c in r['calls']]),
                   ('多轮会话',[c for r in memory for c in r['calls']]),
                   ('自动评审',[c for r in answers+memory for c in r.get('judge',{}).get('calls',[])])])
    for label,calls in groups:
        totals={kind:sum(int((c.get('usage') or {}).get('total_tokens') or 0) for c in calls if c['kind']==kind) for kind in ('chat','embedding','rerank')}
        report.append(f"| {label} | {totals['chat']} | {totals['embedding']} | {totals['rerank']} | {len(calls)} |")
    report.extend(['','聊天、向量和重排的 token 是不同服务的计费单位，不能混成统一费用。'
                   '没有返回 usage 的失败调用不能推断为免费；缓存命中细节保存在原始调用记录中。', '',
                   '## 结果解释', '',
                   f"Agent 固定要点完整通过数增加 {agent['task_pass']-rag['task_pass']} 题，"
                   f"平均聊天 token 为普通 RAG 的 {agent['chat_tokens_mean']/rag['chat_tokens_mean']:.1f} 倍；"
                   '优势主要集中在完整上下文与综合题，不能据此认为所有问题都应该走 Agent。',''])
    for arm in ('rag','agent'):
        refused=[r for r in answers if r['arm']==arm and r['question_spec']['expected_behavior']=='refuse']
        correct=sum(r.get('judge',{}).get('behavior_correct',False) and not r.get('judge',{}).get('unsupported_claims') for r in refused)
        report.append(f"- {arm} 正确拒答行为：{ratio(correct,len(refused))}；这与要求额外解释全部匹配的固定要点通过数不同。")
    hybrid_off=data['retrieval_ablations'].get('hybrid-rerank-False-c30')
    hybrid_on=data['retrieval_ablations'].get('hybrid-rerank-True-c30')
    if hybrid_off and hybrid_on:
        report.append(f"- Hybrid 不重排 / 重排的完整依据覆盖分别为 {hybrid_off['full_evidence_coverage']}/24 与 {hybrid_on['full_evidence_coverage']}/24；"
                      '本次运行没有表现出重排的完整依据覆盖收益，不写成重排提升准确率。')
    if (folder/'audit.json').exists():
        audit=read(folder/'audit.json')
        report.append(f"- {audit['heading_only_chunk_count']} 个分块只含标题；{audit['agent_single_turn_context_omission_count']} 个单轮 Agent 成功响应触发上下文省略。"
                      '这是分块和上下文分配需要改进的具体线索。')
        report.append(f"- 对 {audit['successful_answers_checked']} 个成功回答、{audit['citations_checked']} 个引用进行编号与原文身份核对，"
                      f"发现 {len(audit['identity_issues'])} 个身份不一致项。这只验证来源身份，不等于事实语义支持。")
    report.extend(['',
                   '## 失败与扣分记录', '',
                   '| 题号 | 方法 | HTTP | 评分/扣分理由 |', '|---|---|---|---|'])
    for row in sorted(answers,key=lambda r:(r['id'],r['arm'])):
        grade=row.get('judge',{})
        if grade.get('strict_pass'):
            continue
        reason=grade.get('rationale') or grade.get('error_type') or str(row.get('response'))
        report.append(f"| {row['id']} | {row['arm']} | {row['status']} | {cell(reason)} |")
    report.extend(['','## 可用于简历的表述', '',
                   f"构建覆盖 12 份文档、87 个分块的 30 题固定评测集，使用真实模型对照普通 RAG 与 Agentic RAG，"
                   f"在同模型自动评审下，任务通过数分别为 {rag['task_pass']}/30 与 {agent['task_pass']}/30；"
                   '补充检索重排消融、引用支持度及多轮条件纠正测试，并记录实际 token 用量与 P50/P95 耗时。', '',
                   '数字只能描述本固定合成测试集和此次运行。若引用“提升”，需要同时说明自动评审口径、'
                   '时延与用量代价；不写生产准确率、通用基准或独立人工验证。', '',
                   '## 可复现性与局限', '',
                   f"正式数据库与上传文件前后哈希一致：{meta.get('official_data_unchanged')}。",
                   '固定数据、代码哈希、模型配置和 Git 未提交状态见 `metadata.json`；全部答案、引用、'
                   '工具返回和评分原文见 `answers.jsonl`；完整多轮输出见 `memory.jsonl`。', '',
                   '每题正式跑一遍，题量小、资料为合成 Markdown；没有独立评分者、重复运行置信区间、'
                   '真实长PDF/OCR、真实用户文档或高并发测试。Agent 自主选择参数且提示词不同，'
                   '对照结果不证明单独某个组件的因果贡献。'])
    target=folder/'REPORT.md'
    target.write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(target)


if __name__=='__main__':
    main()
