"""Run fixed pilot questions against real services in isolated storage."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
import chromadb
from storage import database
from storage import vector_store
from routers import documents, document_management, chunks


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def normalized(text):
    return ''.join(text.split())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['bm25', 'dense', 'hybrid'], default='hybrid')
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error('top-k must be 1..20')
    questions_path = ROOT / 'evals/questions.jsonl'
    questions = [json.loads(line) for line in questions_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len({q['id'] for q in questions}) == len(questions)
    for q in questions:
        source = (questions_path.parent / q['document']).read_text(encoding='utf-8')
        assert not q['answerable'] or q['evidence']
        for evidence in q['evidence']:
            assert evidence['quote'] in source, q['id']

    output = ROOT / 'evals/results' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + args.mode)
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'git_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        'working_tree': subprocess.check_output(['git','status','--short'], cwd=ROOT, text=True).strip(),
        'mode': args.mode, 'top_k': args.top_k, 'chunk_size': 500, 'overlap_max': 100,
        'rrf_k': 60, 'candidate_k': max(20, args.top_k),
        'embedding_model': os.getenv('EMBEDDING_MODEL', 'text-embedding-v4'), 'dimensions': 1024,
        'answer_model': os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'),
        'dataset_sha256': hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        'source_hashes': {q['document']: hashlib.sha256((questions_path.parent/q['document']).read_bytes()).hexdigest() for q in questions},
        'code_hashes': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['retrieval/chunking.py','retrieval/engine.py','retrieval/bm25.py','retrieval/embedding.py','services/llm.py','routers/search.py','routers/rag.py']},
        'transport': 'FastAPI TestClient; real Embedding and chat APIs; isolated SQLite/Chroma',
    }
    write_json(output/'metadata.json', metadata)
    records = []
    with tempfile.TemporaryDirectory(prefix='rag-eval-') as folder:
        storage = Path(folder)
        db = storage/'test.db'
        # Use a real in-memory Chroma store, avoiding Windows persistent-file locks.
        memory_client = chromadb.EphemeralClient()
        with patch.object(database, 'DB_PATH', db), patch.object(vector_store, 'CHROMA_DIR', storage/'chroma'), \
                patch.object(vector_store.chromadb, 'PersistentClient', return_value=memory_client), \
                patch.object(document_management, 'UPLOAD_DIR', storage), patch.object(chunks, 'UPLOAD_DIR', storage):
            database.init_db()
            app = FastAPI()
            app.include_router(documents.router)
            def connection():
                with closing(sqlite3.connect(db, check_same_thread=False)) as conn:
                    conn.row_factory = sqlite3.Row
                    yield conn
            app.dependency_overrides[database.get_connection] = connection
            with TestClient(app) as client:
                mapping = {}
                for document in sorted({q['document'] for q in questions}):
                    source = questions_path.parent/document
                    response = client.post('/documents/upload', files={'file': (source.name, source.read_bytes(), 'text/markdown')})
                    response.raise_for_status()
                    document_id = response.json()['id']
                    mapping[document] = document_id
                    response = client.post(f'/documents/{document_id}/chunks')
                    response.raise_for_status()
                    if args.mode != 'bm25':
                        response = client.post(f'/documents/{document_id}/index')
                        response.raise_for_status()
                write_json(output/'chunks.json', {source: client.get(f'/documents/{doc}/chunks').json() for source, doc in mapping.items()})
                for q in questions:
                    record = {'question': q, 'mode': args.mode, 'top_k': args.top_k,
                              'review': {'answer_grade': None, 'citation_support': None, 'refusal_correct': None, 'notes': ''}}
                    body = {'question': q['question'], 'mode': args.mode, 'top_k': args.top_k}
                    for stage, endpoint in [('retrieval','search'), ('answer','ask-rag')]:
                        start = time.perf_counter()
                        try:
                            response = client.post(f'/documents/{mapping[q["document"]]}/{endpoint}', json=body)
                            record[stage] = {'status': response.status_code, 'seconds': round(time.perf_counter()-start, 3), 'response': response.json()}
                        except Exception as exc:
                            record[stage] = {'status': None, 'seconds': round(time.perf_counter()-start, 3), 'error': type(exc).__name__}
                    retrieved = record['retrieval'].get('response', {}).get('items', [])
                    matches = [any(normalized(e['quote']) in normalized(item['content']) for item in retrieved) for e in q['evidence']]
                    record['evidence_quote_coverage'] = sum(matches)/len(matches) if matches else None
                    records.append(record)
                    with (output/'results.jsonl').open('a', encoding='utf-8') as f:
                        f.write(json.dumps(record, ensure_ascii=False)+'\n')
                    print(q['id'], 'retrieval', record['retrieval']['status'], 'answer', record['answer']['status'], 'coverage', record['evidence_quote_coverage'], flush=True)
    write_json(output/'summary.json', {
        'questions': len(records),
        'retrieval_success': sum(r['retrieval']['status']==200 for r in records),
        'answer_success': sum(r['answer']['status']==200 for r in records),
        'answerable_questions': sum(q['answerable'] for q in questions),
        'any_evidence_hit': sum(r['evidence_quote_coverage'] is not None and r['evidence_quote_coverage']>0 for r in records),
        'full_evidence_coverage': sum(r['evidence_quote_coverage']==1 for r in records),
        'note': 'Exact normalized quote matching is a retrieval diagnostic, not semantic accuracy. Human answer/refusal/citation review is pending.',
    })
    print('Saved:', output, flush=True)


if __name__ == '__main__':
    main()
