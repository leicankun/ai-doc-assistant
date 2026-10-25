"""真实重排质量与接口联调，只读取已有知识库，不创建或修改文档。"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import HTTPException
from fastapi.testclient import TestClient
from config import DB_PATH
from main import app
from schemas import SearchRequest
from services.knowledge_base import search_base
from retrieval.reranker import rerank

CASES = [
    ('自动向量入库失败后，原文件会被删除吗？', '保留原文件和已有分块'),
    ('PDF中有空白页，后面的页码会向前压缩吗？', '后续页面的页码不能向前压缩'),
    ('回答中的引用编号等于数据库分块ID吗？', '不等于数据库分块 ID'),
    ('火星探测器的燃料箱容量是多少升？', None),
    ('比较这三篇文章的共同点和不同侧重点', None),
]


def main():
    path = ROOT / 'artifacts' / ('reranker-live-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    report = {'knowledge_base_id': 3, 'checks': [], 'interfaces': []}
    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        for question, evidence in CASES:
            os.environ['RERANK_ENABLED'] = 'false'
            candidates = search_base(db, 3, SearchRequest(question=question, mode='bm25', top_k=20))['items']
            os.environ['RERANK_ENABLED'] = 'true'
            started = time.monotonic()
            entry = {'question': question, 'expected_evidence': evidence, 'candidate_count': len(candidates)}
            try:
                items = rerank(question, candidates, 5, timeout=15)
                entry.update(api_passed=bool(items), items=items,
                             evidence_rank_before=next((i + 1 for i, x in enumerate(candidates) if evidence and evidence in x['content']), None),
                             evidence_rank_after=next((i + 1 for i, x in enumerate(items) if evidence and evidence in x['content']), None))
                assert len(items) == min(5, len(candidates))
                assert all(x['rerank_score'] >= y['rerank_score'] for x, y in zip(items, items[1:]))
                assert all(x['chunk_id'] in {c['chunk_id'] for c in candidates} for x in items)
            except HTTPException as exc:
                entry.update(api_passed=False, error=exc.detail, status_code=exc.status_code)
            entry['seconds'] = round(time.monotonic() - started, 3)
            report['checks'].append(entry)
            save()
            print(json.dumps({k: v for k, v in entry.items() if k != 'items'}, ensure_ascii=False), flush=True)

    # 同一问题验证三种模式的完整知识库接口：Dense/Hybrid 会调用真实 Embedding。
    with TestClient(app) as client:
        for mode in ['bm25', 'dense', 'hybrid']:
            started = time.monotonic()
            response = client.post('/knowledge-bases/3/search', json={
                'question': CASES[0][0], 'mode': mode, 'top_k': 5,
            })
            data = response.json()
            entry = {'mode': mode, 'status_code': response.status_code,
                     'seconds': round(time.monotonic() - started, 3), 'result': data}
            if response.status_code == 200:
                assert all('rerank_score' in item for item in data['items'])
                assert all(item['document_id'] in {10, 11, 12} for item in data['items'])
                entry['evidence_hit'] = any(CASES[0][1] in item['content'] for item in data['items'])
            report['interfaces'].append(entry)
            save()
            print(json.dumps({k: v for k, v in entry.items() if k != 'result'}, ensure_ascii=False), flush=True)
    print('REPORT=' + str(path), flush=True)


if __name__ == '__main__':
    main()
