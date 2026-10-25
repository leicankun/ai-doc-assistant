"""只读真实检索诊断：依据原文片段检查 Top-5，不生成答案、不更改文档。"""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from main import app
from services import knowledge_base

CASES = [
    ('chunk_size', '每个分块的长度上限和重叠上限是多少？', ['五百个字符', '一百个字符']),
    ('pdf_pages', 'PDF有空白页时后面的页码怎么处理？', ['后续页面的页码不能向前压缩']),
    ('retry', '自动向量入库失败后，文件会删除吗，怎么恢复？', ['保留原文件和已有分块']),
    ('versions', '重新分块后怎样判断索引是否有效？', ['两个版本一致']),
    ('rrf', 'RRF如何处理同一个分块被两路检索找到的情况？', ['贡献累加到同一条记录']),
    ('citation', '引用编号和数据库分块ID是否一样？', ['不等于数据库分块 ID']),
    ('learning', '忘记代码后怎样重新找到需要修改的地方？', ['搜索接口路径']),
    ('no_answer', '火星探测器的燃料箱容量是多少升？', []),
    ('compare', '比较这三篇文章的共同点和不同侧重点', []),
    ('punctuation', '!!!', []),
]


def main():
    report = {'knowledge_base_id': 3, 'top_k': 5, 'results': []}
    path = ROOT / 'artifacts' / ('retrieval-diagnostic-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    original_embed = knowledge_base.embed_query
    def timed_embed(question, **kwargs):
        return original_embed(question, timeout=5)
    embedding_unavailable = False
    # 仍调用真实模型，仅缩短本次诊断的超时；发生连接错误后停止重复请求。
    with TestClient(app) as client, patch.object(knowledge_base, 'embed_query', side_effect=timed_embed):
        for case_id, question, evidence in CASES:
            for mode in ['bm25', 'dense', 'hybrid']:
                if embedding_unavailable and mode != 'bm25':
                    report['results'].append({'case': case_id, 'mode': mode, 'skipped': '真实 Embedding 服务不可用，未验证召回质量'})
                    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    continue
                response = client.post('/knowledge-bases/3/search', json={
                    'question': question, 'mode': mode, 'top_k': 5,
                })
                data = response.json()
                if mode != 'bm25' and response.status_code in [502, 503, 504]:
                    embedding_unavailable = True
                items = data.get('items', [])
                joined = '\n'.join(item['content'] for item in items)
                entry = {
                    'case': case_id, 'question': question, 'mode': mode,
                    'status_code': response.status_code, 'expected_evidence': evidence,
                    'evidence_hit': all(text in joined for text in evidence) if evidence and response.status_code == 200 else None,
                    'source_documents': sorted({item['document_id'] for item in items}),
                    'result': data,
                }
                report['results'].append(entry)
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({k: v for k, v in entry.items() if k != 'result'}, ensure_ascii=False), flush=True)
    print('REPORT=' + str(path), flush=True)


if __name__ == '__main__':
    main()
