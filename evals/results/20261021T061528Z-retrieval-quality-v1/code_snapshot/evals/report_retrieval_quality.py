"""从已保存的真实检索轨迹复算核心质量报告；不请求模型或连接数据库。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.retrieval_quality import evaluate_record, failure_cases, summarize, write_outputs


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def source_hashes(source):
    names = ['metadata.json','retrieval.jsonl','chunks.json']
    if (source/'audit.json').exists():names.append('audit.json')
    return {name:digest(source/name) for name in names}


def verify_cleanup(source):
    # 从历史候选池复算清理时，要求生产清理代码与原始真实运行相同。
    meta = json.loads((source/'metadata.json').read_text(encoding='utf-8'))
    hashes = meta.get('code_hashes',{})
    expected = next((value for name,value in hashes.items() if name.replace('\\','/')=='retrieval/reranker.py'),None)
    if expected is None or digest(ROOT/'retrieval/reranker.py')!=expected:
        raise ValueError('清理代码与原运行不一致，不能把当前清理结果冒充历史阶段')


def create_report(source, output=None, budget_chars=2500):
    source = Path(source).resolve(strict=True)
    verify_cleanup(source)
    if budget_chars<0:raise ValueError('预算不能为负')
    records = read_rows(source/'retrieval.jsonl')
    if not records:raise ValueError('没有检索轨迹')
    before = source_hashes(source)
    output = Path(output) if output else ROOT/'evals/results'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-retrieval-quality-v1')
    output = output.resolve()
    if output==source or source in output.parents:
        raise ValueError('离线报告使用独立结果目录，不改写原运行')
    output.mkdir(parents=True,exist_ok=False)
    metadata = {'version':1,'mode':'offline_replay_of_real_retrieval','model_calls':0,
        'source_directory':str(source),'source_hashes':before,'budget_characters':budget_chars,
        'retrieval_records':len(records),'started_at_utc':datetime.now(timezone.utc).isoformat(),
        'code_hashes':{name:digest(ROOT/name) for name in ['evals/retrieval_quality.py','evals/report_retrieval_quality.py','retrieval/reranker.py']}}
    for name in metadata['code_hashes']:
        target = output/'code_snapshot'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    rows,summary = write_outputs(output,records,budget_chars=budget_chars,
        source_note=f'只读复算真实运行 {source.name} 的 {len(records)} 条检索轨迹；本次模型调用为0，未重跑或改变原始模型结果。')
    metadata.update(source_unchanged=before==source_hashes(source),run_status='completed',finished_at_utc=datetime.now(timezone.utc).isoformat())
    (output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    audit(output)
    return output


def audit(output):
    output = Path(output)
    metadata = json.loads((output/'metadata.json').read_text(encoding='utf-8'))
    source = Path(metadata['source_directory'])
    issues = []
    if source_hashes(source)!=metadata['source_hashes']:issues.append('source_hashes')
    for name,expected in metadata['code_hashes'].items():
        if digest(output/'code_snapshot'/name)!=expected:issues.append('code_snapshot:'+name)
        if digest(ROOT/name)!=expected:issues.append('replay_code_changed:'+name)
    verify_cleanup(source)
    rows = [evaluate_record(r,metadata['budget_characters']) for r in read_rows(source/'retrieval.jsonl')]
    if rows!=read_rows(output/'retrieval_quality.jsonl'):issues.append('stage_records')
    if summarize(rows)!=json.loads((output/'retrieval_quality.json').read_text(encoding='utf-8')):issues.append('summary')
    if failure_cases(rows)!=json.loads((output/'retrieval_failure_cases.json').read_text(encoding='utf-8')):issues.append('failure_cases')
    if not metadata.get('source_unchanged'):issues.append('source_changed_during_run')
    result = {'retrieval_records_checked':len(rows),'stages_checked':sum(len(r['stages']) for r in rows),
              'source_unchanged':metadata.get('source_unchanged'),'model_calls':0,'issues':issues}
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    if issues:raise ValueError('审计失败：'+', '.join(issues))
    return result


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--budget-chars',type=int,default=2500)
    parser.add_argument('--audit',action='store_true')
    args=parser.parse_args()
    if args.audit:
        print(json.dumps(audit(args.source),ensure_ascii=False,indent=2))
    else:
        output=create_report(args.source,args.output,args.budget_chars)
        print('RESULT='+str(output))
