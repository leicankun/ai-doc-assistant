"""Live local-only verification; run after starting BGE and building its index."""
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import BASE_DIR
from retrieval.local_embedding import embed_local_texts, service_url
from retrieval.local_index import search_local, validate_local_document
from storage.database import connect
from storage.vector_store import get_collection, get_local_collection


def main():
    started = time.perf_counter()
    with urlopen(service_url().removesuffix('/v1') + '/health', timeout=5) as response:
        service = json.load(response)
    vectors = embed_local_texts([
        '如何把文档分块转换成向量？',
        '将文档分块传入 Embedding 模型，生成向量后写入 Chroma 向量数据库。',
        '今天的晚饭是红烧鱼和米饭。',
    ])
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    if any(abs(norm - 1) > .005 for norm in norms):
        raise AssertionError('本地向量没有正确归一化')
    similarity = lambda a, b: sum(x * y for x, y in zip(a, b))
    related = similarity(vectors[0], vectors[1])
    unrelated = similarity(vectors[0], vectors[2])
    if related <= unrelated:
        raise AssertionError('语义相关示例未排在无关示例之前')
    invalid = Request(service_url() + '/embeddings', data=json.dumps({
        'model': 'text-embedding-v4', 'input': ['test'],
    }).encode(), headers={'Content-Type': 'application/json'})
    try:
        urlopen(invalid, timeout=5)
        raise AssertionError('服务错误地接受了其他模型名称')
    except HTTPError as exc:
        if exc.code != 400:
            raise
    collection = get_collection()
    records = collection.get(include=['documents', 'metadatas'])
    api_hash = hashlib.sha256(json.dumps(sorted(zip(
        records['ids'], records['documents'], records['metadatas']
    )), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with closing(connect()) as connection:
        status = [dict(row) for row in connection.execute(
            'SELECT id,chunk_version,indexed_version FROM documents ORDER BY id')]
        baseline = ROOT / '.local_bge/api-baseline.json'
        baseline_checked = baseline.exists()
        if baseline_checked:
            original = json.loads(baseline.read_text(encoding='utf-8'))
            if (api_hash != original['api_records_hash'] or status != original['api_index_versions']
                    or collection.count() != original['api_count']):
                raise AssertionError('原 API 索引或 MySQL 索引状态与部署前不同')
        local = get_local_collection()
        valid_chunks = sum(len(validate_local_document(connection, doc['id'], local)) for doc in status)
        results = search_local(connection, '上传文档之后如何生成向量索引？', top_k=3)
    report = {'service': service_url(), 'model': 'BAAI/bge-m3', 'dimensions': len(vectors[0]),
              'device': service['device'], 'gpu': service['gpu'], 'revision': service['revision'],
              'normalization_norms': norms, 'related_similarity': related,
              'unrelated_similarity': unrelated, 'rejects_wrong_model': True,
              'api_collection': collection.name, 'api_count': collection.count(),
              'api_baseline_unchanged': True if baseline_checked else None,
              'bge_collection': local.name, 'bge_count': local.count(),
              'validated_current_chunks': valid_chunks,
              'search_results': results, 'verification_seconds': round(time.perf_counter() - started, 3)}
    path = ROOT / 'artifacts/bge-deployment.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'search_results'},
                     ensure_ascii=False, indent=2))
    print(f'Report: {path}')


if __name__ == '__main__':
    main()
