"""只读复算专项指标、选择结果和来源身份，不请求模型或改写原始评分。"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.run_retrieval_benchmark import read_dataset, retrieval_metrics, digest
from retrieval.reranker import clean_candidates
from retrieval.mmr import select_mmr


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def audit(output):
    output=Path(output)
    metadata=json.loads((output/'metadata.json').read_text(encoding='utf-8'))
    _,specs,_,_=read_dataset(output/'dataset_snapshot')
    gold={q['id']:q for q in specs}
    chunks={int(k):v for k,v in json.loads((output/'chunks.json').read_text(encoding='utf-8')).items()}
    raw=json.loads((output/'vectors.json').read_text(encoding='utf-8'))
    vectors=dict(zip(raw['ids'],raw['embeddings']))
    records=rows(output/'retrieval.jsonl')
    answers=rows(output/'answers.jsonl')
    issues=[]
    compared=[]
    citation_count=0
    def problem(identifier,kind,**extra):
        issues.append({'id':identifier,'kind':kind,**extra})
    for name,expected in metadata['code_hashes'].items():
        if digest(output/'code_snapshot'/name)!=expected:
            problem(name,'code_snapshot_hash')
    if len({r['id'] for r in records})!=len(records):
        problem(None,'duplicate_question_record')
    for record in records:
        identifier=record['id']
        if record['question_spec']!=gold[identifier]:
            problem(identifier,'question_spec_changed')
        pool=record.get('pool',[])
        allowed={x['chunk_id'] for x in pool}
        for arm,value in record['arms'].items():
            if value['status']!='ok':
                continue
            items=value['items']
            if len(items)>5 or len({x['chunk_id'] for x in items})!=len(items):
                problem(identifier,'bad_top_k',arm=arm)
            if [x['rank'] for x in items]!=list(range(1,len(items)+1)):
                problem(identifier,'bad_rank',arm=arm)
            for item in items:
                original=chunks.get(item['chunk_id'])
                if original is None or item['chunk_id'] not in allowed:
                    problem(identifier,'outside_shared_pool',arm=arm)
                    continue
                if any(item.get(k)!=original.get(k) for k in ['document_id','content','start_char','end_char','source_name']):
                    problem(identifier,'source_changed',arm=arm)
            if retrieval_metrics(gold[identifier],items)!=value['metrics']:
                problem(identifier,'metric_mismatch',arm=arm)
        ids=lambda items:[x['chunk_id'] for x in items]
        if record['arms']['A']['status']=='ok' and ids(record['arms']['A']['items'])!=ids(pool[:5]):
            problem(identifier,'A_selection')
        if record['arms']['B']['status']=='ok' and ids(record['arms']['B']['items'])!=ids(clean_candidates(pool)[:5]):
            problem(identifier,'B_selection')
        scored=record.get('scored_candidates')
        if scored:
            if ids(scored)!=ids(sorted(scored,key=lambda x:-x['rerank_score'])):
                problem(identifier,'rerank_order')
            if record['arms']['C']['status']=='ok' and ids(record['arms']['C']['items'])!=ids(scored[:5]):
                problem(identifier,'C_selection')
            if record['arms']['D']['status']=='ok' and record['arms']['D'].get('mmr_applied'):
                stored=[vectors[x.get('record_id') or f"doc:{x['document_id']}:chunk:{x['chunk_id']}"] for x in scored]
                expected=select_mmr(scored,stored,5,.8)
                actual=record['arms']['D']['items']
                if ids(expected)!=ids(actual):
                    problem(identifier,'D_replay_selection')
                elif any(not math.isclose(a['mmr_score'],b['mmr_score'],abs_tol=1e-6) for a,b in zip(expected,actual)):
                    problem(identifier,'D_replay_score')
            if record['question_spec']['expected_behavior']=='answer':
                c=record['arms']['C']['metrics']['evidence_coverage']
                d=record['arms']['D']['metrics']['evidence_coverage']
                if c!=d:
                    compared.append({'id':identifier,'split':gold[identifier]['split'],
                        'category':gold[identifier]['category'],'question':gold[identifier]['question'],
                        'C_coverage':c,'D_coverage':d,'C_ids':ids(scored[:5]),
                        'D_ids':ids(record['arms']['D']['items'])})
    for row in answers:
        if row['status']!='ok':
            continue
        response=row['response']
        for citation in response.get('citations',[]):
            citation_count+=1
            original=chunks.get(citation['chunk_id'])
            if original is None or original['content']!=citation['content'] or original['document_id']!=citation['document_id']:
                problem(row['id'],'citation_identity',arm=row['arm'])
    result={'retrieval_questions':len(records),'successful_variants':sum(v['status']=='ok' for r in records for v in r['arms'].values()),
        'answers':len(answers),'citations_checked':citation_count,'identity_or_metric_issues':issues,
        'mmr_evidence_changes':compared,
        'mmr_test_improved':sum(x['split']=='test' and x['D_coverage']>x['C_coverage'] for x in compared),
        'mmr_test_regressed':sum(x['split']=='test' and x['D_coverage']<x['C_coverage'] for x in compared),
        'note':'程序复算和来源身份核对，不等于独立人工语义评分；不更改题集、模型输出或原始评分。'}
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='mmr_evidence_changes'},ensure_ascii=False,indent=2))
    if issues:
        raise SystemExit('Audit failed')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('result_directory',type=Path)
    audit(parser.parse_args().result_directory)
