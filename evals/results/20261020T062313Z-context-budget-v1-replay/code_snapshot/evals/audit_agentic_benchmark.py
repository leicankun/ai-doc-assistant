"""只读核对来源身份与模型实际收到的证据；不改原始答案或评分。"""
import argparse
import json
import re
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.run_agentic_benchmark import coverage


def records(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.exists() else []


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('result_directory',type=Path)
    folder=parser.parse_args().result_directory.resolve()
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    chunks={r['chunk_id']:r for r in json.loads((folder/'chunks.json').read_text(encoding='utf-8'))}
    rows=records(folder/'answers.jsonl')+records(folder/'memory.jsonl')
    issues=[]
    metrics=[]
    citation_count=0
    heading_only={identifier for identifier,chunk in chunks.items()
                  if all(not line.strip() or line.strip().startswith('#') for line in chunk['content'].splitlines())}
    for row in rows:
        response=row.get('response',{})
        if row['status'] != 200:
            continue
        cited=response.get('citations',[])
        valid={c['citation_id'] for c in cited}
        used={int(n) for n in re.findall(r'\[(\d+)\]',response.get('answer',''))}
        if used != valid:
            issues.append({'id':row['id'],'arm':row['arm'],'type':'citation_id_mismatch'})
        for citation in cited:
            citation_count+=1
            chunk=chunks.get(citation['chunk_id'])
            if not chunk or chunk['document_id']!=citation['document_id'] or chunk['content']!=citation['content']:
                issues.append({'id':row['id'],'arm':row['arm'],'type':'citation_source_mismatch'})
        calls=response.get('tool_calls',[]) if row['arm']=='agent' else row.get('tool_events',[])
        items=[item for call in calls for item in call.get('result',{}).get('items',[])]
        evidence=row['question_spec'].get('evidence',[])
        metrics.append({'id':row['id'],'arm':row['arm'],
                        'model_available_evidence':coverage(evidence,items,metadata['document_ids'],chunks),
                        'items_sent':len(items),'unique_chunks_sent':len({r['chunk_id'] for r in items}),
                        'heading_only_items_sent':sum(r['chunk_id'] in heading_only for r in items),
                        'omitted_by_context_budget':response.get('context_usage',{}).get('omitted_tool_items',0)})
    ablations=records(folder/'retrieval.jsonl')
    top5_heading={}
    for row in ablations:
        if row['status'] != 200:
            continue
        label=row['configuration']
        group=top5_heading.setdefault(label,{'returned_items':0,'heading_only_items':0})
        items=row['response']['items']
        group['returned_items']+=len(items)
        group['heading_only_items']+=sum(r['chunk_id'] in heading_only for r in items)
    audit={'successful_answers_checked':len(metrics),'citations_checked':citation_count,
           'identity_issues':issues,'heading_only_chunk_count':len(heading_only),
           'heading_only_chunk_ids':sorted(heading_only),'retrieval_heading_only':top5_heading,
           'agent_single_turn_context_omission_count':sum(r['arm']=='agent' and r['response'].get('context_usage',{}).get('omitted_tool_items',0)>0 for r in records(folder/'answers.jsonl')),
           'note':'只验证编号、正文和来源身份，不验证语义支持；语义支持仍见自动评分及复核备注。answers.jsonl的retrieval_metrics是原始工具召回覆盖，本文件另计预算筛选后实际纳入上下文的证据。'}
    (folder/'audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'context_evidence.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in metrics),encoding='utf-8')
    print(json.dumps(audit,ensure_ascii=False,indent=2))
    if issues:
        raise SystemExit('Source identity audit failed')


if __name__=='__main__':
    main()
