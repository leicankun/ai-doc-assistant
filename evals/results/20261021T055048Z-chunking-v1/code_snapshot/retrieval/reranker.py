"""百炼文本重排：先召回候选，再根据问题与正文重新排序。"""
import json
import math
import os
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import HTTPException
from config import BASE_DIR
from retrieval.mmr import apply_mmr, mmr_weight

load_dotenv(BASE_DIR / '.env')


def _heading_only(lines):
    """仅识别明确的 Markdown ATX / Setext 标题，正文或代码不当作标题。"""
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
        elif re.fullmatch(r' {0,3}#{1,6}(?:[ \t]+.*)?', line):
            index += 1
        elif (index + 1 < len(lines)
              and re.match(r' {0,3}[^\s`~>*+\-]', line)
              and not re.match(r' {0,3}\d+[.)][ \t]', line)
              and re.fullmatch(r' {0,3}(?:={3,}|-{3,})[ \t]*', lines[index + 1])):
            index += 2
        else:
            return False
    return True


def clean_candidates(items):
    """过滤空白/纯标题；同文档正文仅统一换行与首尾空行后精确去重。

    保留排名靠前的原始分块及来源，不合并跨文档内容或近似、重叠片段。
    """
    cleaned = []
    seen = set()
    for item in items:
        lines = item['content'].splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines or _heading_only(lines):
            continue
        key = (item.get('document_id'), '\n'.join(lines))
        # 没有文档身份时，不推断两个片段来自同一文档。
        if key[0] is not None:
            if key in seen:
                continue
            seen.add(key)
        cleaned.append(item)
    return cleaned


def rerank_enabled():
    return os.getenv('RERANK_ENABLED', 'true').lower() in {'true', '1', 'yes'}


def candidate_count(top_k):
    """扩大候选池，不能在重排之前就截断成最终 Top-K。"""
    if not rerank_enabled():
        return top_k
    try:
        count = int(os.getenv('RERANK_CANDIDATES', '30'))
        if not 1 <= count <= 100:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(503, 'RERANK_CANDIDATES 必须为 1～100') from exc
    return max(count, top_k)


def rerank(question, items, top_k, *, timeout=None):
    """返回独立的结果字典，保留召回排名和来源，校验模型返回的下标。"""
    started = time.monotonic()
    items = clean_candidates(items)
    if not items or not rerank_enabled():
        return [{**item, 'rank': rank}
                for rank, item in enumerate(items[:top_k], start=1)]
    api_key = os.getenv('RERANK_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    model = os.getenv('RERANK_MODEL', 'gte-rerank-v2')
    endpoint = os.getenv('RERANK_URL')
    if not endpoint:
        base = urlsplit(os.getenv('DASHSCOPE_BASE_URL', ''))
        # 独立重排接口，不能把请求发到 embedding 的 compatible-mode 路径。
        if base.hostname and (base.hostname == 'dashscope.aliyuncs.com'
                              or base.hostname.endswith('.aliyuncs.com')):
            endpoint = f'https://{base.netloc}/api/v1/services/rerank/text-rerank/text-rerank'
    if not api_key or not endpoint:
        raise HTTPException(503, '请配置 RERANK_URL 和百炼 API Key，或设置 RERANK_ENABLED=false')
    try:
        seconds = float(os.getenv('RERANK_TIMEOUT', '15'))
        if timeout is not None:
            seconds = min(seconds, timeout)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(503, '重排超时时间必须为正数') from exc

    documents = [item['content'] for item in items]
    weight = mmr_weight()
    use_mmr = weight is not None and weight < 1 and 1 < top_k < len(items)
    # MMR 必须能从最终 Top-K 以外挑选补充证据，先取回候选池全部分数。
    limit = len(items) if use_mmr else min(top_k, len(items))
    # qwen3-rerank 与 gte-rerank-v2 的请求结构不同，按官方 API 区分。
    payload = {'model': model}
    if model == 'qwen3-rerank':
        payload.update(query=question, documents=documents, top_n=limit)
    else:
        payload.update(input={'query': question, 'documents': documents},
                       parameters={'top_n': limit, 'return_documents': False})
    request = Request(endpoint, data=json.dumps(payload).encode('utf-8'),
                      headers={'Authorization': f'Bearer {api_key}',
                               'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=seconds) as response:
            body = json.loads(response.read())
    except (TimeoutError, socket.timeout) as exc:
        raise HTTPException(504, '重排模型调用超时，请稍后重试') from exc
    except HTTPError as exc:
        raise HTTPException(502, f'重排模型调用失败（HTTP {exc.code}），请检查模型和地域配置') from exc
    except URLError as exc:
        status = 504 if isinstance(exc.reason, TimeoutError) else 502
        raise HTTPException(status, '重排服务连接失败，请稍后重试') from exc
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(502, '重排服务返回了无效 JSON') from exc

    try:
        results = body['results'] if model == 'qwen3-rerank' else body['output']['results']
        if not isinstance(results, list) or len(results) != limit:
            raise ValueError
        seen = set()
        ranked = []
        for result in results:
            index = result['index']
            score = result['relevance_score']
            if (type(index) is not int or not 0 <= index < len(items) or index in seen
                    or type(score) not in (float, int) or not math.isfinite(score)
                    or not 0 <= score <= 1):
                raise ValueError
            seen.add(index)
            ranked.append({**items[index], 'retrieval_rank': items[index].get('rank', index + 1),
                           'rerank_score': score, 'rerank_model': model})
        ranked.sort(key=lambda item: -item['rerank_score'])
        for rank, item in enumerate(ranked, start=1):
            item['rank'] = rank
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, '重排服务返回的编号、数量或评分无效') from exc
    if use_mmr:
        if timeout is not None and time.monotonic() - started >= timeout:
            raise HTTPException(504, '检索时间预算已耗尽')
        ranked = apply_mmr(ranked, top_k, weight)
        if timeout is not None and time.monotonic() - started >= timeout:
            raise HTTPException(504, '检索时间预算已耗尽')
    return ranked
