"""Explicit local BGE client; does not change the existing DashScope backend."""
import json
import math
import os
from urllib.request import Request, urlopen

MODEL_ID = 'BAAI/bge-m3'
MODEL_REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
WEIGHTS_SHA256 = 'b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38'
DIMENSIONS = 1024
COLLECTION_NAME = 'document_chunks_bge_m3_1024'


def service_url():
    return os.getenv('BGE_BASE_URL', 'http://127.0.0.1:8001/v1').rstrip('/')


def embed_local_texts(texts, timeout=120):
    if not texts:
        return []
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError('输入必须是非空白字符串列表')
    vectors = []
    batch_size = max(1, min(32, int(os.getenv('BGE_BATCH_SIZE', '4'))))
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        request = Request(service_url() + '/embeddings', data=json.dumps({
            'model': MODEL_ID, 'input': batch, 'dimensions': DIMENSIONS,
            'encoding_format': 'float',
        }, ensure_ascii=False).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        if result.get('model') != MODEL_ID:
            raise RuntimeError('本地服务返回了不同的模型')
        items = sorted(result['data'], key=lambda item: item['index'])
        if [item['index'] for item in items] != list(range(len(batch))):
            raise RuntimeError('本地向量数量或编号与输入不一致')
        for item in items:
            vector = item['embedding']
            if (len(vector) != DIMENSIONS or not all(math.isfinite(v) for v in vector)
                    or not any(v != 0 for v in vector)):
                raise RuntimeError('本地模型返回了无效向量')
            vectors.append(vector)
    return vectors
