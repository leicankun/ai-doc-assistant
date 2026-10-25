"""检索核心指标：必要证据、完整覆盖、统一 MRR@5 和逐阶段证据损失。"""
import json
from pathlib import Path
import statistics

STAGES = {
    "candidate_pool":"召回候选池（上限30）",
    "cleaned_pool":"清理后的候选池",
    "cleaned_top5":"清理后RRF Top-5（同K基线）",
    "rerank_top5":"Rerank Top-5",
    "mmr_top5":"MMR Top-5",
    "budget_top5":"2500字符预算后",
}
PATH = ["candidate_pool","cleaned_pool","rerank_top5","mmr_top5","budget_top5"]
TRANSITIONS = [(a,b,"pipeline") for a,b in zip(PATH,PATH[1:])] + [
    ("cleaned_top5","rerank_top5","same_k_comparison")]


def interval_covered(alt, items):
    ranges = sorted((max(alt['start_char'],x['start_char']), min(alt['end_char'],x['end_char']))
                    for x in items if x['document']==alt['document']
                    and x['start_char']<alt['end_char'] and x['end_char']>alt['start_char'])
    cursor = alt['start_char']
    for start,end in ranges:
        if start>cursor:
            break
        cursor=max(cursor,end)
    return cursor>=alt['end_char']


def evidence_hits(spec, items):
    return [any(interval_covered(alt,items) for alt in group['alternatives'])
            for group in spec['evidence_groups']]


def fit_characters(items, limit=2500):
    if limit<0:
        raise ValueError('字符预算不能为负数')
    result,used = [],0
    for item in items:
        if used+len(item['content'])<=limit:
            result.append(item)
            used+=len(item['content'])
    return result


def evaluate_stage(spec, items, *, status='ok', rank_k=5):
    if rank_k!=5:
        raise ValueError('核心报告统一使用 MRR@5，rank_k 必须为5')
    answerable = spec['expected_behavior']=='answer'
    if answerable != bool(spec['evidence_groups']):
        raise ValueError('可回答题必须标注必要证据组；拒答/澄清题不进入证据指标')
    values = items if status=='ok' else []
    hits = evidence_hits(spec,values)
    rr = next((1/rank for rank in range(1,min(rank_k,len(values))+1)
               if any(evidence_hits(spec,values[:rank]))),0.0) if answerable else None
    return {'status':status,'item_count':len(values),'context_characters':sum(len(x['content']) for x in values),
            'chunk_ids':[x['chunk_id'] for x in values], 'evidence_hits':hits,
            'evidence_coverage':sum(hits)/len(hits) if answerable else None,
            'full_evidence_coverage':all(hits) if answerable else None,
            'reciprocal_rank_at_5':rr,'rank_k':rank_k}


def transition(before, after):
    left,right = before['evidence_hits'],after['evidence_hits']
    if len(left)!=len(right):
        raise ValueError('阶段的必要证据组数量不一致')
    lost = [i+1 for i,(a,b) in enumerate(zip(left,right)) if a and not b]
    gained = [i+1 for i,(a,b) in enumerate(zip(left,right)) if not a and b]
    return {'lost_groups':lost,'gained_groups':gained,
            'before_hit_groups':sum(left),'after_hit_groups':sum(right),
            'complete_to_incomplete':bool(left) and all(left) and not all(right),
            'failure_associated':before['status']!='ok' or after['status']!='ok'}


def evaluate_record(record, budget_chars=2500):
    from retrieval.reranker import clean_candidates
    spec = record['question_spec']
    pool_ok = 'pool' in record
    pool = record.get('pool',[])
    cleaned = clean_candidates(pool) if pool_ok else []
    if 'arms' in record:
        scored_ok = record.get('arms',{}).get('C',{}).get('status')=='ok'
        scored = record.get('scored_candidates',[])
        final_ok = record.get('arms',{}).get('D',{}).get('status')=='ok'
        final = record.get('arms',{}).get('D',{}).get('items',[])
    else:
        scored_ok = 'scored' in record
        scored = record.get('scored',[])
        final_ok = record.get('status')=='ok'
        final = record.get('items',[])
    chosen = fit_characters(final,budget_chars) if final_ok else []
    if budget_chars==2500 and 'budget_items' in record:
        if [x['chunk_id'] for x in chosen] != [x['chunk_id'] for x in record['budget_items']]:
            raise ValueError('保存的预算选择与复算不一致')
    stages = {}
    for name,items,ok in [
        ('candidate_pool',pool,pool_ok),('cleaned_pool',cleaned,pool_ok),
        ('cleaned_top5',cleaned[:5],pool_ok),('rerank_top5',scored[:5],scored_ok),
        ('mmr_top5',final,final_ok),('budget_top5',chosen,final_ok),
    ]:
        stages[name] = evaluate_stage(spec,items,status='ok' if ok else 'failed')
    changes = {a+'->'+b:{'before':a,'after':b,'kind':kind,**transition(stages[a],stages[b])}
               for a,b,kind in TRANSITIONS}
    return {'id':record['id'],'group':record.get('group','original_500_100'),
            'question_spec':spec,'stages':stages,'transitions':changes}


def aggregate(rows):
    gold = [r for r in rows if r['question_spec']['expected_behavior']=='answer']
    denominator = len(gold)
    stages,changes = {},{}
    mean = lambda values:round(statistics.mean(values),6) if values else None
    for name in STAGES:
        values = [r['stages'][name] for r in gold]
        needed = sum(len(v['evidence_hits']) for v in values)
        found = sum(sum(v['evidence_hits']) for v in values)
        stages[name] = {'questions':len(rows),'answerable_questions':denominator,
            'success':sum(r['stages'][name]['status']=='ok' for r in rows),
            'failed_questions':sum(r['stages'][name]['status']!='ok' for r in rows),
            'mean_evidence_coverage':mean([v['evidence_coverage'] for v in values]),
            'full_evidence_questions':sum(v['full_evidence_coverage'] for v in values),
            'full_evidence_rate':sum(v['full_evidence_coverage'] for v in values)/denominator if denominator else None,
            'mrr_at_5':mean([v['reciprocal_rank_at_5'] for v in values]),
            'necessary_groups':needed,'found_groups':found,'missing_groups':needed-found,
            'mean_items':mean([r['stages'][name]['item_count'] for r in rows]),
            'mean_context_characters':mean([r['stages'][name]['context_characters'] for r in rows])}
    for before,after,kind in TRANSITIONS:
        key = before+'->'+after
        values = [r['transitions'][key] for r in gold]
        lost = sum(len(v['lost_groups']) for v in values)
        gained = sum(len(v['gained_groups']) for v in values)
        available = sum(v['before_hit_groups'] for v in values)
        changes[key] = {'kind':kind,'before':before,'after':after,
            'answerable_questions':denominator,'lost_groups':lost,'gained_groups':gained,
            'before_hit_groups':available,'loss_rate_of_previously_found':lost/available if available else None,
            'questions_with_loss':sum(bool(v['lost_groups']) for v in values),
            'questions_with_gain':sum(bool(v['gained_groups']) for v in values),
            'complete_to_incomplete_questions':sum(v['complete_to_incomplete'] for v in values),
            'failure_associated_questions':sum(v['failure_associated'] for v in values)}
    return {'questions':len(rows),'answerable_questions':denominator,
            'excluded_refuse_or_clarify':len(rows)-denominator,'stages':stages,'transitions':changes}


def summarize(rows):
    groups = sorted({r['group'] for r in rows},key=lambda name:(not name.startswith('original'),name))
    return {group:{split:aggregate([r for r in rows if r['group']==group
                and (split=='all' or r['question_spec']['split']==split)])
            for split in ('dev','test','all')} for group in groups}


def failure_cases(rows):
    result = []
    for row in rows:
        spec = row['question_spec']
        if spec['expected_behavior']!='answer':
            continue
        missing = [i+1 for i,hit in enumerate(row['stages']['budget_top5']['evidence_hits']) if not hit]
        losses = {name:change['lost_groups'] for name,change in row['transitions'].items() if change['lost_groups']}
        if not missing and not losses:
            continue
        result.append({'id':row['id'],'group':row['group'],'split':spec['split'],'question':spec['question'],
            'missing_at_recall':[i+1 for i,hit in enumerate(row['stages']['candidate_pool']['evidence_hits']) if not hit],
            'missing_after_budget':missing,'stage_losses':losses,
            'evidence_groups':spec['evidence_groups'],
            'stage_chunk_ids':{name:s['chunk_ids'] for name,s in row['stages'].items()}})
    return result


def render_report(summary, budget_chars, source_note):
    lines = ['# RAG 检索核心质量报告','','重点：必要证据覆盖、完整覆盖题数、MRR@5、逐阶段证据损失。','',source_note,'']
    pct = lambda value:f'{value*100:.2f}%' if value is not None else '—'
    for group,splits in summary.items():
        row = splits['test']
        lines += [f'## {group}：test 集','',f"共 {row['questions']} 题；证据指标分母为 {row['answerable_questions']} 道可回答题；拒答/澄清 {row['excluded_refuse_or_clarify']} 题单列排除。",'',
            '| 阶段 | 成功 | 平均证据覆盖 | 完整覆盖 | MRR@5 | 平均保留块数 | 平均正文字符 |',
            '|---|---:|---:|---:|---:|---:|---:|']
        for name,value in row['stages'].items():
            label = STAGES[name] if name!='budget_top5' else f'{budget_chars}字符预算后'
            lines.append(f"| {label} | {value['success']}/{value['questions']} | {pct(value['mean_evidence_coverage'])} | {value['full_evidence_questions']}/{row['answerable_questions']} | {value['mrr_at_5']} | {value['mean_items']} | {value['mean_context_characters']} |")
        pool = row['stages']['candidate_pool']
        lines += ['',f"召回遗漏：{pool['missing_groups']}/{pool['necessary_groups']} 个标注必要证据组未进入候选池。",'',
            '| 阶段变化 | 丢失证据组 | 补回证据组 | 已命中证据损失率 | 有丢失的题数 | 完整变不完整 |',
            '|---|---:|---:|---:|---:|---:|']
        for value in row['transitions'].values():
            label = f"{STAGES[value['before']]} → {STAGES[value['after']]}"
            if value['kind']=='same_k_comparison':label+='（同K方案对比）'
            lines.append(f"| {label} | {value['lost_groups']} | {value['gained_groups']} | {pct(value['loss_rate_of_previously_found'])} | {value['questions_with_loss']} | {value['complete_to_incomplete_questions']} |")
    lines += ['', '## 指标口径','',
        '- 证据覆盖对每题的必要证据组求覆盖比例，再按题平均；完整覆盖要求该题所有必要组都命中。组内替代出处命中一个即可，重复命中不加分，允许同一文档原文区间并集覆盖。',
        '- MRR统一只看该阶段前5项：第一个完整覆盖任意必要组的前缀排名取倒数，多题平均；不是所有必要证据已找齐的分数。候选池覆盖检查全部候选（最多30），其MRR仍只看前5项。',
        '- 阶段丢失是“上一步命中、下一步未命中”的证据组；补回反向计算。两者分别报告，不能只用净覆盖差掩盖证据替换。损失率分母是上一步已命中的证据组，属于按证据组汇总，不等于按题平均覆盖差。',
        '- 清理候选池→重排Top-5含30→5容量筛选，不能全部解释为模型排错；另给清理后RRF Top-5→Rerank Top-5的同K比较。该比较是两个选集对照，不是再次过滤前一个Top-5。MMR可以从完整重排候选中补回片段。',
        f'- 预算使用固定{budget_chars}原文字符，按最终排名保留完整可装入片段，不截断、不补Top-5以外的项。这是统一预算检索诊断，不是Agent的24000估算单位上下文保护。',
        '- 失败留在分母中并按未命中计，阶段错误单独统计；没有已命中证据时损失率为空，不制造0%成功。拒答、澄清不靠检索为空判定正确，需另看回答行为。',
        '- 分组保持原始短章节与长章节的区别，不跨语料结构归因。标注原文覆盖是可复算代理指标，仍可能遗漏其他有效出处，不能直接等同答案准确率。',
        '', '逐题缺失和丢失位置见 retrieval_failure_cases.json，完整阶段记录见 retrieval_quality.jsonl，dev/test/all 汇总见 retrieval_quality.json。']
    return '\n'.join(lines)+'\n'


def write_outputs(output, records, *, budget_chars=2500, source_note='基于本次真实模型运行保存的检索轨迹复算，不增加模型调用。'):
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    rows = [evaluate_record(r,budget_chars) for r in records]
    summary = summarize(rows)
    (output/'retrieval_quality.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    (output/'retrieval_quality.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'retrieval_failure_cases.json').write_text(json.dumps(failure_cases(rows),ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'RETRIEVAL_QUALITY.md').write_text(render_report(summary,budget_chars,source_note),encoding='utf-8')
    return rows,summary
