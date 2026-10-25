"""只读复算分块布局、证据覆盖、MMR、引用身份及快照哈希。"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.run_chunking_benchmark import CONFIGS, extra_metrics, within_budget, summarize, layouts, transformed_corpus, read_dataset, digest
from retrieval.mmr import select_mmr


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


def audit(output):
    output = Path(output)
    metadata = json.loads((output/'metadata.json').read_text(encoding='utf-8'))
    records,answers = rows(output/'retrieval.jsonl'),rows(output/'answers.jsonl')
    chunks = {int(k):v for k,v in json.loads((output/'chunks.json').read_text(encoding='utf-8')).items()}
    vectors = {r['text']:r['vector'] for r in json.loads((output/'embedding_cache.json').read_text(encoding='utf-8'))}
    issues, citations = [],0
    def issue(kind,**values):
        issues.append({'kind':kind,**values})
    for name, expected in metadata['code_hashes'].items():
        if digest(output/'code_snapshot'/name)!=expected:issue('code_snapshot_hash',file=name)
    for name, expected in metadata['dataset_snapshot_hashes'].items():
        if digest(output/name)!=expected:issue('dataset_snapshot_hash',file=name)
    _,gold_questions,gold_texts,gold_sections = read_dataset()
    originals = [layouts(gold_texts,*config) for config in CONFIGS]
    original_equal = originals[0] == originals[1] == originals[2]
    if not original_equal:issue('original_layout_equality')
    long_texts,long_sections,long_questions = transformed_corpus(gold_texts,gold_sections,gold_questions)
    for key, group in metadata['groups'].items():
        source_texts = gold_texts if key=='original_shared' else long_texts
        source_questions = gold_questions if key=='original_shared' else long_questions
        snapshot_questions = json.loads((output/'dataset_snapshot'/key/'questions.json').read_text(encoding='utf-8'))
        if snapshot_questions != source_questions:issue('question_snapshot',group=key)
        expected_layout = layouts(source_texts,*group['config'])
        for name,text in source_texts.items():
            actual_text = (output/'dataset_snapshot'/key/'documents'/name).read_text(encoding='utf-8')
            if actual_text!=text:issue('corpus_fact_or_format',group=key,document=name)
            actual = sorted((c for c in chunks.values() if c['group']==key and c['document']==name),key=lambda c:c['chunk_index'])
            expected = expected_layout[name]
            if [(c['content'],c['start_char'],c['end_char']) for c in actual] != [(c['content'],c['start_char'],c['end_char']) for c in expected]:
                issue('chunk_layout',group=key,document=name)
        by_id = {q['id']:q for q in source_questions}
        group_records = [r for r in records if r['group']==key]
        expected_count = metadata.get('limit') or 60
        if len(group_records)!=expected_count or len({r['id'] for r in group_records})!=expected_count:
            issue('question_count',group=key)
        expected_answers = {r['id'] for r in group_records if r['question_spec']['answer_check']}
        group_answers = [r for r in answers if r['group']==key]
        if len(group_answers)!=len(expected_answers) or {r['id'] for r in group_answers}!=expected_answers:
            issue('answer_count',group=key)
        for record in group_records:
            if record['question_spec']!=by_id[record['id']]:issue('question_spec',group=key,id=record['id'])
    def ids(items):return [x['chunk_id'] for x in items]
    for record in records:
        if record['status']!='ok':continue
        spec = record['question_spec']
        for field in ('pool','scored','items'):
            for item in record[field]:
                source = chunks.get(item['chunk_id'])
                if source is None or source['group']!=record['group'] or source['content']!=item['content'] or source['document_id']!=item['document_id']:
                    issue('retrieval_identity',group=record['group'],id=record['id'],field=field)
        if extra_metrics(spec,record['pool'])!=record['pool_metrics']:issue('pool_metric',id=record['id'])
        if extra_metrics(spec,record['items'])!=record['metrics']:issue('top5_metric',id=record['id'])
        selected = within_budget(record['items'])
        if ids(selected)!=ids(record['budget_items']) or extra_metrics(spec,selected)!=record['budget_metrics']:
            issue('budget_metric',id=record['id'])
        scored = record['scored']
        expected = select_mmr(scored,[vectors[x['content']] for x in scored],5,.8) if len(scored)>5 else scored[:5]
        if ids(expected)!=ids(record['items']):issue('mmr_selection',group=record['group'],id=record['id'])
        elif any(not math.isclose(a.get('mmr_score',0),b.get('mmr_score',0),abs_tol=1e-6) for a,b in zip(expected,record['items'])):
            issue('mmr_score',id=record['id'])
    retrieval_by_id = {(r['group'],r['id']):r for r in records}
    for answer in answers:
        if answer['status']!='ok':continue
        grade = answer.get('grade',{})
        if grade.get('status')=='scored':
            passed = all(grade['matched_points']) and grade['behavior_correct'] and not grade['unsupported_claims']
            if grade['task_pass'] != passed or grade['strict_pass'] != (passed and not grade['citation_errors']):
                issue('grade_derived_fields',group=answer['group'],id=answer['id'])
        retrieved = {i['chunk_id']:i for i in retrieval_by_id[(answer['group'],answer['id'])]['items']}
        for citation in answer['response'].get('citations',[]):
            citations+=1
            source = retrieved.get(citation['chunk_id'])
            if source is None or source['document_id']!=citation['document_id'] or source['content']!=citation['content']:
                issue('citation_identity',group=answer['group'],id=answer['id'])
    actual_summary = json.loads((output/'summary.json').read_text(encoding='utf-8'))
    if summarize(records,answers,metadata['groups'])!=actual_summary:issue('summary_recalculation')
    if not metadata.get('official_data_unchanged'):issue('official_data_fingerprint')
    result = {'retrieval_cases':len(records),'answers':len(answers),'citations_checked':citations,
              'original_layouts_identical':original_equal,'issues':issues,'official_data_unchanged':metadata.get('official_data_unchanged'),
              'note':'确定性指标与来源核验，不是独立人工答案评分。'}
    (output/'audit_tool.py').write_bytes(Path(__file__).read_bytes())
    result['audit_script_sha256'] = digest(output/'audit_tool.py')
    (output/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if issues:raise SystemExit('Audit failed')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('result_directory',type=Path)
    audit(parser.parse_args().result_directory)
