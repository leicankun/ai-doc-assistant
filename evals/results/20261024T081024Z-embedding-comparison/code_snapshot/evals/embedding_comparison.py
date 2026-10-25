"""Dataset and deterministic scoring helpers for the local/API embedding comparison."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import statistics
import unicodedata

import numpy as np

from evals.retrieval_quality import evaluate_stage

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def normalized_with_positions(text):
    """Match quotes despite PDF compatibility characters and layout whitespace."""
    letters, positions = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize('NFKC', char):
            if not normalized.isspace():
                letters.append(normalized)
                positions.append(index)
    return ''.join(letters), positions


def quote_span(text, quote):
    normalized, positions = normalized_with_positions(text)
    target, _ = normalized_with_positions(quote)
    if not target:
        raise ValueError('Gold quote cannot be empty')
    start = normalized.find(target)
    return None if start < 0 else (positions[start], positions[start + len(target) - 1] + 1)


def read_live_sources():
    from storage.database import connect
    from storage.vector_store import get_collection, get_local_collection
    from storage.schema import metadata as schema
    with closing(connect()) as connection:
        documents = {r['id']: dict(r) for r in connection.execute('SELECT * FROM documents')}
        current = {f"doc:{r['document_id']}:chunk:{r['id']}": dict(r)
                   for r in connection.execute('SELECT * FROM document_chunks ORDER BY id')}
        mysql = {}
        for name, table in schema.tables.items():
            order = ','.join('`' + column.name + '`' for column in table.primary_key)
            rows = [dict(r) for r in connection.execute(f'SELECT * FROM `{name}` ORDER BY {order}')]
            mysql[name] = {'rows': len(rows), 'sha256': canonical_hash(rows)}
    sources, fingerprints = {}, {}
    for provider, collection in [('api', get_collection()), ('local', get_local_collection())]:
        raw = collection.get(include=['documents', 'metadatas', 'embeddings'])
        sources[provider] = {key: {'content': text, 'metadata': meta, 'vector': np.asarray(vector, dtype=np.float32)}
                             for key, text, meta, vector in zip(raw['ids'], raw['documents'], raw['metadatas'], raw['embeddings'])}
        digest = hashlib.sha256()
        for key, row in sorted(sources[provider].items()):
            digest.update(canonical_hash([key, row['content'], row['metadata']]).encode())
            digest.update(row['vector'].astype('<f4').tobytes())
        fingerprints[provider] = {'collection': collection.name, 'count': len(raw['ids']),
                                  'logical_sha256': digest.hexdigest()}
    eligible = []
    for key, chunk in current.items():
        doc = documents[chunk['document_id']]
        if doc['indexed_version'] is None or doc['indexed_version'] != doc['chunk_version']:
            continue
        if not all(key in source and source[key]['content'] == chunk['content']
                   and source[key]['metadata'].get('document_id') == chunk['document_id']
                   and source[key]['metadata'].get('chunk_id') == chunk['id']
                   for source in sources.values()):
            continue
        if sources['local'][key]['metadata'].get('chunk_version') != doc['chunk_version']:
            continue
        eligible.append({'record_id': key, 'chunk_id': chunk['id'], 'document_id': chunk['document_id'],
                         'document': f"live:{chunk['document_id']}:page:{chunk['page_number'] or 0}",
                         'title': doc['title'], 'page_number': chunk['page_number'],
                         'chunk_index': chunk['chunk_index'], 'content': chunk['content'],
                         'start_char': chunk['start_char'], 'end_char': chunk['end_char']})
    files = {}
    for path in [ROOT / 'documents.db', *sorted((ROOT / 'uploads').glob('*'))]:
        if path.is_file():
            with path.open('rb') as stream:
                files[str(path.relative_to(ROOT))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    state = {'mysql': mysql, 'collections': fingerprints, 'files': files,
             'current_chunks': len(current), 'eligible_common_chunks': len(eligible),
             'excluded_current_record_ids': sorted(set(current) - {r['record_id'] for r in eligible}),
             'api_only_ids': sorted(set(sources['api']) - set(sources['local'])),
             'local_only_ids': sorted(set(sources['local']) - set(sources['api']))}
    matrices = {name: np.stack([source[row['record_id']]['vector'] for row in eligible])
                for name, source in sources.items()}
    return eligible, matrices, state


def build_live_questions(chunks):
    definitions = json.loads((ROOT / 'evals/embedding_benchmark/live_questions.json').read_text(encoding='utf-8'))
    questions = []
    for definition in definitions:
        groups = []
        for reference in definition['refs']:
            alternatives = {}
            for chunk in chunks:
                if chunk['document_id'] != reference['document_id']:
                    continue
                span = quote_span(chunk['content'], reference['quote'])
                if span is not None:
                    start, end = span
                    alt = {'document': chunk['document'], 'start_char': chunk['start_char'] + start,
                           'end_char': chunk['start_char'] + end, 'quote': chunk['content'][start:end]}
                    alternatives[(alt['document'], alt['start_char'], alt['end_char'])] = alt
            if not alternatives:
                raise ValueError(f"Gold quote missing from shared index: {definition['id']} / {reference['quote']}")
            groups.append({'alternatives': list(alternatives.values())})
        questions.append({key: definition[key] for key in ('id', 'category', 'question')} | {
            'corpus': 'live', 'split': 'exploratory', 'fact_family': definition['id'],
            'expected_behavior': 'answer', 'evidence_groups': groups})
    if len(questions) != 30:
        raise ValueError('Expected 30 predeclared live questions')
    return questions


def prepare():
    from evals.run_retrieval_benchmark import read_dataset
    from retrieval.chunking import split_text_with_offsets
    manifest, questions, texts, _ = read_dataset()
    chunks, chunk_id = [], 0
    for document_id, (name, text) in enumerate(sorted(texts.items()), 1):
        for index, chunk in enumerate(split_text_with_offsets(text, 500, 100), 1):
            chunk_id += 1
            chunks.append({**chunk, 'chunk_id': chunk_id, 'record_id': f'synthetic:{chunk_id}',
                           'document_id': document_id, 'document': name, 'chunk_index': index})
    live_chunks, matrices, state = read_live_sources()
    questions = [{**q, 'corpus': 'synthetic'} for q in questions] + build_live_questions(live_chunks)
    for question in questions:
        relevant = chunks if question['corpus'] == 'synthetic' else live_chunks
        if question['expected_behavior'] == 'answer':
            score = evaluate_stage(question, relevant)
            if not score['full_evidence_coverage']:
                raise ValueError('Gold cannot be fully covered: ' + question['id'])
    return {'synthetic': chunks, 'live': live_chunks}, matrices, questions, manifest, state


def score_question(spec, items, success=True):
    stage = evaluate_stage(spec, items, status='ok' if success else 'failed')
    first = evaluate_stage(spec, items[:1], status='ok' if success else 'failed')
    return {**stage, 'hit_at_5': any(stage['evidence_hits']) if stage['evidence_hits'] else None,
            'hit_at_1': any(first['evidence_hits']) if first['evidence_hits'] else None}


def aggregate(rows):
    eligible = [row for row in rows if row['evidence_coverage'] is not None]
    mean = lambda key: statistics.mean(row[key] for row in eligible) if eligible else None
    return {'questions': len(rows), 'answerable_questions': len(eligible),
            'failed_questions': sum(row['status'] != 'ok' for row in rows),
            'hit_at_1': mean('hit_at_1'), 'hit_at_5': mean('hit_at_5'),
            'evidence_coverage_at_5': mean('evidence_coverage'),
            'full_evidence_at_5': sum(row['full_evidence_coverage'] for row in eligible),
            'full_evidence_rate_at_5': mean('full_evidence_coverage'),
            'mrr_at_5': mean('reciprocal_rank_at_5')}


def latency_summary(values):
    return {'n': len(values), 'mean_ms': float(np.mean(values)) if values else None,
            'p50_ms': float(np.percentile(values, 50)) if values else None,
            'p95_ms': float(np.percentile(values, 95)) if values else None}


def groups(questions):
    return {'synthetic_all': [q['id'] for q in questions if q['corpus'] == 'synthetic'],
            'synthetic_dev': [q['id'] for q in questions if q['split'] == 'dev'],
            'synthetic_test': [q['id'] for q in questions if q['split'] == 'test'],
            'live_all': [q['id'] for q in questions if q['corpus'] == 'live'],
            **{category: [q['id'] for q in questions if q['category'] == category]
               for category in ('live_markdown', 'live_classics', 'live_pdf')}}
