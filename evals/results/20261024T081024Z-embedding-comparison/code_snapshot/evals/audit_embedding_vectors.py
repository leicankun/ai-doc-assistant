"""Offline exact-cosine replay to check approximate-index effects; no model calls."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from evals.embedding_comparison import score_question, aggregate, groups, dump
from retrieval.engine import fuse_rrf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run
    questions = json.loads((run / 'questions.json').read_text(encoding='utf-8'))
    records = [json.loads(line) for line in (run / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    native = {(r['id'], r['provider']): r for r in records if r['round'] == 1}
    bm25 = json.loads((run / 'bm25.json').read_text(encoding='utf-8'))
    checks, exact_scores = [], {}
    with np.load(run / 'first_round_query_vectors.npz') as queries:
        for corpus in ('synthetic', 'live'):
            chunks = json.loads((run / f'{corpus}-chunks.json').read_text(encoding='utf-8'))
            by_id = {r['record_id']: r for r in chunks}
            for provider in ('api', 'local'):
                matrix = np.load(run / f'{provider}-{corpus}-vectors.npy').astype(np.float64)
                if not np.isfinite(matrix).all() or (np.linalg.norm(matrix, axis=1) == 0).any():
                    raise ValueError('Invalid stored document vectors')
                matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
                for spec in [q for q in questions if q['corpus'] == corpus]:
                    vector = queries[provider + ':' + spec['id']].astype(np.float64)
                    vector /= np.linalg.norm(vector)
                    similarities = matrix @ vector
                    order = np.argsort(-similarities, kind='stable')[:30]
                    dense = [{**chunks[int(i)], 'distance': 1 - float(similarities[i])} for i in order]
                    lexical = [{**by_id[key], 'score': 0.0} for key in bm25[spec['id']]['ids']]
                    hybrid = fuse_rrf(lexical, dense, top_k=30, rrf_k=60)
                    exact_scores[(spec['id'], provider)] = {
                        'dense': score_question(spec, dense[:5]), 'hybrid': score_question(spec, hybrid[:5])}
                    expected = native[(spec['id'], provider)]['dense_ids'][:5]
                    actual = [r['record_id'] for r in dense[:5]]
                    checks.append({'id': spec['id'], 'provider': provider, 'corpus': corpus,
                                   'top5_set_overlap': len(set(expected) & set(actual)) / 5,
                                   'top5_order_equal': expected == actual,
                                   'native_top5': expected, 'exact_top5': actual})
    quality = {}
    for group, ids in groups(questions).items():
        quality[group] = {provider: {arm: aggregate([exact_scores[(identifier, provider)][arm] for identifier in ids])
                                     for arm in ('dense', 'hybrid')} for provider in ('api', 'local')}
    result = {'queries': len(checks), 'exact_order_matches': sum(r['top5_order_equal'] for r in checks),
              'mean_top5_set_overlap': float(np.mean([r['top5_set_overlap'] for r in checks])),
              'quality': quality, 'checks': checks}
    dump(run / 'exact_cosine_audit.json', result)
    print(json.dumps({k: result[k] for k in ('queries', 'exact_order_matches', 'mean_top5_set_overlap')}))


if __name__ == '__main__':
    main()
