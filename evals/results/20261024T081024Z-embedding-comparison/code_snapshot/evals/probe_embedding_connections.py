"""Supplementary, small matched latency check for API client reuse; no app changes."""
import argparse
import json
import math
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import BASE_DIR
from openai import OpenAI
from retrieval.embedding import embed_texts
from retrieval.local_embedding import embed_local_texts
from evals.embedding_comparison import dump, latency_summary
from evals.run_embedding_comparison import Calls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run
    metadata = json.loads((run / 'metadata.json').read_text(encoding='utf-8'))
    if metadata['status'] not in ('complete', 'complete_with_issues'):
        raise ValueError('Run this probe only after the main experiment, to avoid concurrent API load')
    questions = json.loads((run / 'questions.json').read_text(encoding='utf-8'))
    sample = random.Random(20261025).sample(questions, 10)
    output = run / 'connection_probe'
    output.mkdir(exist_ok=False)
    dump(output / 'questions.json', [q['id'] for q in sample])
    calls = Calls(output)
    rows, reuse_usage = [], []
    started = time.perf_counter()
    client = OpenAI(api_key=os.environ['DASHSCOPE_API_KEY'], base_url=os.environ['DASHSCOPE_BASE_URL'],
                    timeout=30, max_retries=0)
    construction_ms = (time.perf_counter() - started) * 1000
    def reuse(text):
        response = client.embeddings.create(model=os.getenv('EMBEDDING_MODEL', 'text-embedding-v4'),
                                             input=[text], dimensions=1024, encoding_format='float')
        vector = response.data[0].embedding
        if len(vector) != 1024 or not all(math.isfinite(value) for value in vector):
            raise ValueError('Invalid vector')
        reuse_usage.append(response.usage.model_dump() if response.usage else {})
    try:
        # Exclude connection warmup from the persistent-client latency samples.
        reuse(sample[0]['question'])
        for index, question in enumerate(sample):
            order = ['api_fresh', 'api_reused', 'local']
            order = order[index % 3:] + order[:index % 3]
            for mode in order:
                row = {'id': question['id'], 'mode': mode}
                started = time.perf_counter()
                try:
                    if mode == 'api_reused':
                        reuse(question['question'])
                    else:
                        calls.embed('api' if mode == 'api_fresh' else 'local', [question['question']], mode, question['id'])
                    row['success'] = True
                except Exception as exc:
                    row.update(success=False, error_type=type(exc).__name__)
                row['milliseconds'] = (time.perf_counter() - started) * 1000
                rows.append(row)
            print(f'connection probe {index + 1}/10', flush=True)
    finally:
        client.close()
    result = {'sample_questions': [q['id'] for q in sample], 'constructor_ms': construction_ms,
              'api_model': metadata['api_model'], 'dimensions': 1024,
              'note': '10 matched queries, modes interleaved; small supplementary latency check, no quality retuning.',
              'summary': {mode: latency_summary([r['milliseconds'] for r in rows if r['mode'] == mode and r['success']])
                          for mode in ('api_fresh', 'api_reused', 'local')},
              'failed': sum(not r['success'] for r in rows), 'rows': rows,
              'additional_api_reported_tokens': sum(r.get('total_tokens', 0) for r in reuse_usage)
                  + sum(r['reported_total_tokens'] for r in calls.rows if r['provider'] == 'api')}
    dump(output / 'summary.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('rows', 'sample_questions')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
