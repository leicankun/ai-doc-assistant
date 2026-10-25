"""用已有资料验证真实重排服务，不生成回答或更改文档。"""
import json
import os
import sqlite3
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import DB_PATH
from schemas import SearchRequest
from services.knowledge_base import search_base
from retrieval.reranker import rerank
from fastapi import HTTPException

with sqlite3.connect(DB_PATH) as connection:
    connection.row_factory = sqlite3.Row
    os.environ['RERANK_ENABLED'] = 'false'
    question = '自动向量入库失败后，原文件会被删除吗？'
    baseline = search_base(connection, 3, SearchRequest(question=question, mode='bm25', top_k=20))
    os.environ['RERANK_ENABLED'] = 'true'
    try:
        items = rerank(question, baseline['items'], 5, timeout=15)
        report = {'passed': bool(items), 'question': question,
                  'candidate_count': len(baseline['items']), 'items': items}
    except HTTPException as exc:
        report = {'passed': False, 'status_code': exc.status_code, 'error': exc.detail}
    (ROOT / 'artifacts/reranker-real-check.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
