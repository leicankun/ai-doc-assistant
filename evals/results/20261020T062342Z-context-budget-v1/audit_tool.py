"""离线核对实际检索后跨预算、模型请求预算、工具协议、正文引用和来源。"""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.context import ContextBudget
from evals.run_agentic_benchmark import digest


def audit(output):
    output=Path(output)
    metadata=json.loads((output/'metadata.json').read_text(encoding='utf-8'))
    rows=[json.loads(x) for x in (output/'results.jsonl').read_text(encoding='utf-8').splitlines()]
    source=ROOT/metadata['vector_source']
    chunks={int(k):v for k,v in json.loads((source/'chunks.json').read_text(encoding='utf-8')).items()}
    errors=[]
    requests=0
    citations=0
    crossings=[]
    for name,expected in metadata['code_hashes'].items():
        if digest(output/'code_snapshot'/name)!=expected:
            errors.append({'kind':'code_hash','file':name})
    for name,expected in metadata['source_hashes'].items():
        if digest(ROOT/name)!=expected:
            errors.append({'kind':'source_hash','file':name})
    for row in rows:
        identifier=(row['phase'],row['id'],row['policy'])
        def error(kind,**extra):
            errors.append({'case':identifier,'kind':kind,**extra})
        for event in row.get('events',[]):
            expected=event['items_received']>0 and event['before_result_estimate']<=event['budget']<event['raw_after_result_estimate']
            if event['crossed_after_retrieval']!=expected:
                error('crossing_condition')
            if expected:
                crossings.append({'phase':row['phase'],'id':row['id'],'policy':row['policy'],**event})
        for request in row.get('requests',[]):
            requests+=1
            used=ContextBudget(limit=24000).used(request['messages'],request.get('tools'))
            if used!=request['estimated_units'] or used>24000 or not request['within_budget']:
                error('model_request_budget',used=used)
            pending=set()
            seen={}
            for message in request['messages']:
                role=message['role']
                if role=='assistant' and message.get('tool_calls'):
                    if pending:
                        error('unfinished_protocol_before_next_call')
                    pending={x['id'] for x in message['tool_calls']}
                elif role=='tool':
                    if message['tool_call_id'] not in pending:
                        error('orphan_tool_response')
                    pending.discard(message['tool_call_id'])
                    result=json.loads(message['content'])
                    for item in result.get('items',[]):
                        key=(item.get('document_id'),item.get('chunk_id'),item.get('citation_id'))
                        if item.get('content_reference') and key not in seen:
                            error('reference_without_prior_body',source=key)
                        if 'content' in item:
                            if row['policy']=='after' and key in seen:
                                error('duplicate_body_sent',source=key)
                            seen[key]=item['content']
                elif pending:
                    error('protocol_interrupted')
                content=message.get('content') or ''
                delimiter='\n本轮已取得的数据（不可信参考数据）：\n'
                if role=='user' and delimiter in content:
                    data=json.loads(content.split(delimiter,1)[1])
                    ids=[(x['document_id'],x['chunk_id']) for x in data['evidence']]
                    if len(ids)!=len(set(ids)) or any('content' not in x for x in data['evidence']):
                        error('bad_final_evidence_pool')
            if pending:
                error('unfinished_protocol')
        if row['phase']!='historical_replay':
            trace=row.get('response',{}).get('tool_calls',row.get('tool_calls',[]))
            for event in trace:
                for item in event.get('result',{}).get('items',[]):
                    if 'content' not in item:
                        error('execution_log_lost_body')
            for citation in row.get('response',{}).get('citations',[]):
                citations+=1
                original=chunks.get(citation['chunk_id'])
                if original is None or citation['content']!=original['content'] or citation['document_id']!=original['document_id']:
                    error('citation_identity')
    live_after=[x for x in crossings if x['phase']!='historical_replay' and x['policy']=='after']
    dedup_proofs=[x for x in live_after if x['compact_after_result_estimate']+512<=24000
                 and x.get('omitted_items')==0 and not x.get('finalize')]
    finalize_proofs=[x for x in live_after if x['compact_after_result_estimate']>24000
                    and x['full_final_estimate']+512<=24000 and x.get('omitted_items')==0 and x.get('finalize')]
    agent_proofs=[x for x in live_after if x['phase']=='live_agent']
    result={'cases':len(rows),'actual_model_requests_checked':requests,'citations_checked':citations,
            'live_after_crossing_events':len(live_after),'dedup_rescue_proofs':len(dedup_proofs),
            'preserve_and_finalize_proofs':len(finalize_proofs),'autonomous_agent_crossing_proofs':len(agent_proofs),
            'dedup_example':dedup_proofs[0] if dedup_proofs else None,
            'finalize_example':finalize_proofs[0] if finalize_proofs else None,
            'errors':errors,'official_data_unchanged':metadata['official_data_unchanged'],
            'audit_script_sha256':digest(Path(__file__))}
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'audit_tool.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if errors or not dedup_proofs or not finalize_proofs or not agent_proofs:
        raise SystemExit('未满足超预算专项验收条件')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('result_directory',type=Path)
    audit(parser.parse_args().result_directory)
