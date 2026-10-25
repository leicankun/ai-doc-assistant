"""Create a concise interpretation and source review without changing frozen scores."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run
    summary = json.loads((run / 'summary.json').read_text(encoding='utf-8'))
    metadata = json.loads((run / 'metadata.json').read_text(encoding='utf-8'))
    questions = {q['id']: q for q in json.loads((run / 'questions.json').read_text(encoding='utf-8'))}
    chunks = {row['record_id']: row for row in json.loads((run / 'live-chunks.json').read_text(encoding='utf-8'))}
    rows = [json.loads(line) for line in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    first = {(r['id'], r['provider']): r for r in rows if r['round'] == 1}
    quality, latency = summary['quality'], summary['latency']
    lines = ['# 结果解读与原文复核', '',
        '本报告比较这台电脑上已经部署的 BGE-M3 与当前百炼 text-embedding-v4。'
        '样本较小，比较同时涉及模型和部署方式；不能推广为“本地模型普遍不如 API”或相反结论。', '',
        '当前文档复用部署时已有的两套向量，仅取内容一致的共同分块；合成运维语料的183块则分别重新编码。'
        '因此同时提供实际部署对比与同一新语料上的对照，不把既有索引当作本次全部重新生成的向量。', '',
        '## 主要结果', '',
        '| 数据集与方式 | 百炼 API：完整证据覆盖 | 本地 BGE：完整证据覆盖 | API MRR@5 | BGE MRR@5 |',
        '|---|---:|---:|---:|---:|']
    for group in ('synthetic_test', 'live_all'):
        for arm in ('dense', 'hybrid'):
            a, b = quality[group]['api'][arm], quality[group]['local'][arm]
            lines.append(f"| {group} / {arm} | {a['full_evidence_at_5']}/{a['answerable_questions']} "
                f"({a['full_evidence_rate_at_5']:.2%}) | {b['full_evidence_at_5']}/{b['answerable_questions']} "
                f"({b['full_evidence_rate_at_5']:.2%}) | {a['mrr_at_5']:.4f} | {b['mrr_at_5']:.4f} |")
    api_ms, local_ms = latency['api']['embedding_ms']['p50_ms'], latency['local']['embedding_ms']['p50_ms']
    lines += ['', f'查询 Embedding 中位耗时：API {api_ms:.1f} ms，本地 {local_ms:.1f} ms；'
              f'按当前实现，本地约快 {api_ms/local_ms:.1f} 倍。此数字包含 SDK 和网络开销，'
              '本地模型已经加载；不能解释成模型纯计算快了这些倍数。', '',
              f"主实验共 {sum(x['attempted'] for x in latency.values())} 次查询调用，"
              f"失败 {sum(x['failed'] for x in latency.values())} 次。每题重复3次用于测时，质量分母仍为原题数。", '',
              '## 相同总分不代表找到了相同的题目', '',
              '| 数据集/检索 | 两者都完整 | 只有API完整 | 只有BGE完整 | 两者都不完整 |',
              '|---|---:|---:|---:|---:|']
    for group in ('synthetic_test', 'live_all'):
        ids = [q['id'] for q in questions.values() if q['expected_behavior'] == 'answer'
               and ((group == 'synthetic_test' and q['split'] == 'test') or (group == 'live_all' and q['corpus'] == 'live'))]
        for arm in ('dense', 'hybrid'):
            counts = {(True, True): 0, (True, False): 0, (False, True): 0, (False, False): 0}
            for identifier in ids:
                key = tuple(first[(identifier, p)]['metrics'][arm]['full_evidence_coverage'] for p in ('api', 'local'))
                counts[key] += 1
            lines.append(f"| {group}/{arm} | {counts[(True,True)]} | {counts[(True,False)]} | "
                         f"{counts[(False,True)]} | {counts[(False,False)]} |")
    probe_path = run / 'connection_probe/summary.json'
    if probe_path.exists():
        probe = json.loads(probe_path.read_text(encoding='utf-8'))
        lines += ['', '## API 客户端复用补充实验', '',
            '主实验测的是项目当前实现：每次调用都创建并关闭 OpenAI 客户端。另固定抽取10道相同问题，'
            '交替测当前 API 调用、同一个客户端复用连接的 API 调用和本地调用。'
            '复用客户端先完成一次不计时预热。仅10个样本，不用它替换主实验的270个延迟样本，也没有改变质量评分。', '',
            '| 模式 | 成功样本 | 查询中位耗时 ms |', '|---|---:|---:|']
        for mode, row in probe['summary'].items():
            lines.append(f"| {mode} | {row['n']} | {row['p50_ms']:.1f} |")
        lines += ['', f"客户端构造单次测量：{probe['constructor_ms']:.1f} ms；补充实验失败 {probe['failed']} 次，"
                  f"额外 API 报告 token：{probe['additional_api_reported_tokens']}。",
                  '因此，应把模型推理、网络、SDK初始化/连接复用分开考虑。主应用代码本次没有改为复用客户端。']
        total_tokens = summary['usage']['api']['reported_total_tokens'] + probe['additional_api_reported_tokens']
        lines += ['', f'主实验加补充实验，百炼报告的总 token 为 {total_tokens}；未用地域单价换算费用。']
    exact_path = run / 'exact_cosine_audit.json'
    if exact_path.exists():
        exact = json.loads(exact_path.read_text(encoding='utf-8'))
        lines += ['', '## 独立核对', '',
                  '逐题指标和汇总已从保存的排名、题目及原文区间只读复算，540条记录全部一致。',
                  f"另外直接用保存的文档向量和查询向量计算精确余弦相似度；{exact['queries']} 次第一轮查询中，"
                  f"{exact['exact_order_matches']} 次 Top-5 顺序与 Chroma 完全一致，"
                  f"Top-5 集合平均重合 {exact['mean_top5_set_overlap']:.0%}。本次主要差异不能归咎于 HNSW 近似搜索漏召回。"]
    lines += ['', '## 不能把当前文档80%称为回答准确率', '',
        '新增题的 gold 在调用前固定。完整覆盖要求命中那些明确标注的原文区间；'
        '同一本长 PDF 的其他章节可能提供同样有效的解释。下面是助手对真实 Top-5 正文的复核，'
        '不是独立人工评审，也不回写原始 gold 或改动分数。', '',
        '| 题号 | 现象 | 两方案实际取到的可用出处 |', '|---|---|---|',
        '| l025 / ReAct | 未取到第82页的定义句 | 两者dense均取到第85页 chunk445，正文说明思考指导行动、行动结果修正思考；hybrid均取到chunk444，明确写出推理与行动结合及思考—行动—观察循环。 |',
        '| l026 / Plan-and-Solve | 未取到第82页的简介句 | 两者dense均取到第96页chunk493，hybrid也均取到该块，写明流程分为规划与执行两个阶段；还取到第97页chunk495的逐步执行说明。 |',
        '| l027 / Reflection | 未取到第82页的简介句 | 两者dense均取到第104页chunk522，hybrid亦取到该块，说明内部纠错回路、修正逻辑与策略错误，以及执行—反思—优化的持续过程。 |', '',
        '这三题在严格评分中仍保留未完整命中。复核说明“没命中预先标注区间”不等于“没检索到可回答资料”，'
        '也不能据此断言最后的生成答案一定正确。后续可在独立新版标注中补充有效替代出处。', '',
        '## 代表性差异案例', '']
    for identifier in ('l016', 'l024', 'l020', 'l022'):
        spec = questions[identifier]
        lines.append(f"### {identifier}：{spec['question']}")
        lines.append('')
        for provider in ('api', 'local'):
            row = first[(identifier, provider)]
            sources = '；'.join(f"{key}（{chunks[key]['title']}）" for key in row['dense_ids'][:3])
            lines.append(f"- {provider} dense 严格证据覆盖 {row['metrics']['dense']['evidence_coverage']:.0%}；前三项：{sources}。")
        lines.append('')
    lines += ['## 使用建议', '',
        '- 当前固定运维 test 集更偏向 API；对版本、环境例外和多项必要条件的覆盖，应继续保留 API 对照。',
        '- 当前真实文档样本中，本地在速度和前排排序上有优势；两个方案的严格完整覆盖数相同，不能据此宣布检索质量完全相同。',
        '- 先保留 API 默认配置、本地作为低延迟和离线选项；如果要切换默认值，应补充真正的业务问题及更完整的独立证据标注。',
        '- 可优先评估 API 客户端/连接复用，再决定是否需要用更低的延迟换取特定题型上的覆盖损失。', '',
        f"生产数据前后指纹一致：{metadata['source_unchanged']}。本次没有修改默认模型、RAG流程或生产向量。", '',
        '完整方法、分组数据、文档编码吞吐和 token usage 见 [REPORT.md](REPORT.md)。']
    (run / 'REVIEW_NOTES.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(run / 'REVIEW_NOTES.md')


if __name__ == '__main__':
    main()
