"""Real BGE-M3 vs DashScope embedding evaluation, isolated from production writes."""
import argparse
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chromadb
import numpy as np
from config import BASE_DIR
from openai.resources.embeddings import Embeddings
from retrieval import embedding, local_embedding
from retrieval.bm25 import search_bm25
from retrieval.engine import fuse_rrf
from evals.embedding_comparison import (prepare, read_live_sources, dump, canonical_hash,
                                       score_question, aggregate, latency_summary, groups)

PROVIDERS = ('api', 'local')


def append(path, row):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


class GPURecorder:
    def __init__(self):
        self.samples = []
        self.event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        executable = shutil.which('nvidia-smi')
        if not executable:
            return
        while not self.event.is_set():
            try:
                raw = subprocess.check_output([
                    executable, '--query-gpu=memory.used,memory.total,utilization.gpu',
                    '--format=csv,noheader,nounits'], text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                used, total, utilization = [int(x.strip()) for x in raw.splitlines()[0].split(',')]
                self.samples.append({'time_utc': datetime.now(timezone.utc).isoformat(),
                                     'gpu_memory_mib': used, 'gpu_total_mib': total,
                                     'gpu_utilization_percent': utilization})
            except Exception:
                pass
            self.event.wait(1)


class Calls:
    def __init__(self, output):
        self.output = output
        self.rows = []

    def embed(self, provider, texts, phase, identifier):
        observed = []
        started = time.perf_counter()
        row = {'provider': provider, 'phase': phase, 'id': identifier, 'texts': len(texts),
               'characters': sum(len(text) for text in texts)}
        original_sdk = Embeddings.create
        original_urlopen = local_embedding.urlopen

        def sdk(client, *args, **kwargs):
            result = original_sdk(client, *args, **kwargs)
            observed.append({'usage': result.usage.model_dump() if result.usage else None,
                             'model': result.model})
            return result

        def local_request(*args, **kwargs):
            response = original_urlopen(*args, **kwargs)
            class Response:
                def __enter__(self):
                    return self
                def __exit__(self, *unused):
                    response.close()
                def read(self):
                    data = response.read()
                    result = json.loads(data)
                    observed.append({'usage': result.get('usage'), 'model': result.get('model'),
                                     'server_seconds': result.get('elapsed_seconds')})
                    return data
            return Response()
        try:
            if provider == 'api':
                with patch.object(Embeddings, 'create', sdk):
                    vectors = embedding.embed_texts(texts)
            else:
                with patch.object(local_embedding, 'urlopen', local_request):
                    vectors = local_embedding.embed_local_texts(texts)
            row['success'] = True
            return np.asarray(vectors, dtype=np.float32), row
        except Exception as exc:
            row.update(success=False, error_type=type(exc).__name__,
                       status_code=getattr(exc, 'status_code', getattr(exc, 'code', None)))
            raise
        finally:
            row.update(milliseconds=(time.perf_counter() - started) * 1000,
                       requests=len(observed), responses=observed,
                       reported_total_tokens=sum((r.get('usage') or {}).get('total_tokens', 0) or 0
                                                 for r in observed))
            self.rows.append(row)
            append(self.output / 'calls.jsonl', row)


def search(collection, chunks, vector):
    started = time.perf_counter()
    result = collection.query(query_embeddings=[vector.tolist()], n_results=min(30, len(chunks)),
                              include=['distances'])
    by_id = {chunk['record_id']: chunk for chunk in chunks}
    items = [{**by_id[key], 'record_id': key, 'distance': distance, 'rank': rank + 1}
             for rank, (key, distance) in enumerate(zip(result['ids'][0], result['distances'][0]))]
    return items, (time.perf_counter() - started) * 1000


def summarize(records, questions, calls, index_stats, batch_stats):
    specs = {q['id']: q for q in questions}
    result = {'quality': {}, 'latency': {}, 'usage': {}, 'index_build': index_stats,
              'document_batches': batch_stats}
    for name, ids in groups(questions).items():
        result['quality'][name] = {}
        for provider in PROVIDERS:
            selected = [row for row in records if row['round'] == 1 and row['provider'] == provider and row['id'] in ids]
            result['quality'][name][provider] = {arm: aggregate([row['metrics'][arm] for row in selected])
                                                for arm in ('dense', 'hybrid')}
    for provider in PROVIDERS:
        rows = [row for row in records if row['provider'] == provider]
        ok = [row for row in rows if row['success']]
        result['latency'][provider] = {
            'attempted': len(rows), 'failed': len(rows) - len(ok),
            **{key: latency_summary([row[key] for row in ok])
               for key in ('embedding_ms', 'vector_query_ms', 'dense_component_ms', 'hybrid_component_ms')},
            'by_corpus': {corpus: latency_summary([row['embedding_ms'] for row in ok if row['corpus'] == corpus])
                          for corpus in ('synthetic', 'live')},
        }
        values = [row for row in calls if row['provider'] == provider]
        result['usage'][provider] = {'logical_calls': len(values), 'failed': sum(not r['success'] for r in values),
            'reported_total_tokens': sum(row['reported_total_tokens'] for row in values),
            'by_phase': {phase: {'calls': sum(r['phase'] == phase for r in values),
                                 'reported_total_tokens': sum(r['reported_total_tokens'] for r in values if r['phase'] == phase)}
                         for phase in sorted({r['phase'] for r in values})}}
    return result


def report(output, summary, metadata, questions, records):
    pct = lambda value: f'{value * 100:.2f}%' if value is not None else '—'
    number = lambda value: f'{value:.2f}' if value is not None else '—'
    lines = ['# 本地 BGE-M3 与百炼 Embedding API 实测', '',
        f"运行状态：{metadata['status']}；开始时间（北京时间）：{metadata['started_at_shanghai']}。", '',
        '比较对象为 BGE-M3（本机 RTX 4050、FP16）与 text-embedding-v4（当前百炼 API、1024 维）。'
        '模型和部署方式同时不同，不能将差异全部解释为本地或云端部署造成。', '',
        '## 评测设计', '',
        '- 固定合成集：既有 60 题、20 份虚构运维文档，48 题可回答；其中 test 为 40 题、32 题可回答。'
        '保留既有 dev/test 标签，未根据本次结果调参；这些题曾用于历史评测，不是全新盲测。',
        '- 当前文档：评测前固定 30 题（18 道原创 Markdown、6 道经典文献、6 道 Hello-Agents PDF），'
        '均标注明确原文依据。由助手编题，属于探索性样本，没有独立人工评分。',
        f"- 当前文档只使用双方均有且与 MySQL 正文一致的 {metadata['source_before']['eligible_common_chunks']} 块；"
        '排除旧记录和只有一方有的测试分块。合成语料按生产 500＋100 切成相同的 183 块。',
        '- 1024 维、cosine、相同 Chroma HNSW 配置，候选 30、最终 Top-5；混合检索复用相同 BM25，RRF k=60。'
        '未启用 rerank、MMR、查询改写、Agent、回答生成或自动评审，以观察 Embedding 本身的影响。',
        f"- 每题每方案单条请求重复 {metadata['rounds']} 次，串行执行；题序固定种子打乱，两个方案交替先后。"
        '质量只计第一轮，重复请求只增加耗时样本，不将它们当作独立质量样本。',
        '- 使用项目现有调用函数：API 每次建立 SDK 客户端，本地请求常驻模型服务。耗时包含调用开销及网络，'
        '不等于模型纯计算时间；这里没有尝试持久连接等额外优化。', '',
        '## 检索质量', '',
        '证据覆盖为每题必要证据组的命中比例；组内可替代来源任一命中即可，支持同文档原文区间并集。'
        '完整覆盖要求所有必要组都找到。Hit@5 为至少完整命中一组。MRR@5 为前缀首次完整覆盖任意一组的倒数排名。'
        '失败按 0 计，不从题数中删除；12 道拒答/澄清题不进入证据指标，也不据召回内容判断其回答行为。'
        '这些是原文区间代理指标，不是最终答案准确率，标注可能没有穷尽其他有效出处。', '',
        '| 数据集 | 检索 | 方案 | 可回答题 | Hit@1 | Hit@5 | 平均证据覆盖@5 | 完整覆盖@5 | MRR@5 |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for name, group in summary['quality'].items():
        for arm in ('dense', 'hybrid'):
            for provider in PROVIDERS:
                row = group[provider][arm]
                lines.append(f"| {name} | {arm} | {provider} | {row['answerable_questions']} | {pct(row['hit_at_1'])} | "
                    f"{pct(row['hit_at_5'])} | {pct(row['evidence_coverage_at_5'])} | "
                    f"{row['full_evidence_at_5']}/{row['answerable_questions']} | {number(row['mrr_at_5'])} |")
    lines += ['', '## 单条查询耗时', '',
        '单位毫秒；P95 使用 NumPy 线性百分位。预热调用不进入下表，失败次数单列，分位数只看成功请求。'
        'dense 组件和包含 Embedding 与 Chroma；hybrid 组件和额外加上共享 BM25 和融合耗时，'
        '不是两个独立 HTTP 端到端接口测量，不包括数据库校验、回答生成或 SSE。', '',
        '| 方案 | 成功/尝试 | Embedding P50 | Embedding P95 | dense组件和 P50 | hybrid组件和 P50 |',
        '|---|---:|---:|---:|---:|---:|']
    for provider, row in summary['latency'].items():
        lines.append(f"| {provider} | {row['attempted'] - row['failed']}/{row['attempted']} | "
            f"{number(row['embedding_ms']['p50_ms'])} | {number(row['embedding_ms']['p95_ms'])} | "
            f"{number(row['dense_component_ms']['p50_ms'])} | {number(row['hybrid_component_ms']['p50_ms'])} |")
    lines += ['', '## 文档编码、资源和调用量', '',
        '文档批量使用相同的 40 个真实分块（按字符长度分位取样），每批固定 4 条、串行请求。'
        '该配置控制单次输入数量，并非各服务最优吞吐配置；不代表冷启动或并发吞吐。', '',
        '| 方案 | 文本数 | 总字符 | 编码秒数 | 文本/秒 | API或本地服务报告token |',
        '|---|---:|---:|---:|---:|---:|']
    for provider, row in summary['document_batches'].items():
        lines.append(f"| {provider} | {row['texts']} | {row['characters']} | {number(row['seconds'])} | "
                     f"{number(row['texts_per_second'])} | {row['reported_total_tokens']} |")
    gpu = metadata.get('gpu', {})
    lines += ['', f"GPU 采样：{gpu.get('samples', 0)} 次；整张显卡显存范围 "
              f"{gpu.get('min_memory_mib')}～{gpu.get('max_memory_mib')} MiB。"
              '包含桌面及其他应用，且 API 测试时本地模型仍常驻，不能当成两种方案各自的显存占用。', '',
              'API 按供应商报告的 token 统计，不用未经核对的地域单价估算人民币；本地没有供应商调用费，'
              '但本次未测电费或硬件摊销。两种模型的 tokenizer 不同，其 token 数不能直接当成统一文本量。', '',
              '```json', json.dumps(summary['usage'], ensure_ascii=False, indent=2), '```', '',
              '## 数据保护与局限', '',
              f"生产逻辑指纹（MySQL、上传文件、原 SQLite、两套集合正文/元数据/向量）前后一致："
              f"{metadata.get('source_unchanged')}。评测索引保存在本结果目录，未改生产配置或生产向量。", '',
              '- 主结论应分别看固定 test 集和当前文档探索集，避免混合语料掩盖差异；样本规模有限，'
              '没有据此估计真实用户总体准确率。经典文献与 PDF 在生产中分块较长，未在评测中重新切分。',
              '- 单机、单次运行、低并发、已加载 GPU 模型；网络、桌面 GPU 负载和服务商负载会影响耗时。'
              '未测 CPU、本地冷启动、大批量最大吞吐、完整 RAG 回答质量或长时间稳定性。',
              '- 第一轮逐题排名见 results.jsonl，所有真实调用及 usage 见 calls.jsonl。数据和代码快照、'
              '向量及 SHA256 均保存在本目录，可使用 --audit 做只读复算。', '',
              '## 两方案出现差异的题目（第一轮、完整证据覆盖）', '']
    by_key = {(r['id'], r['provider']): r for r in records if r['round'] == 1}
    for spec in questions:
        if spec['expected_behavior'] != 'answer':
            continue
        for arm in ('dense', 'hybrid'):
            a = by_key.get((spec['id'], 'api'))
            b = by_key.get((spec['id'], 'local'))
            if not a or not b:
                continue
            if a['metrics'][arm]['full_evidence_coverage'] != b['metrics'][arm]['full_evidence_coverage']:
                lines.append(f"- {spec['id']} / {arm}：{spec['question']}；api="
                    f"{a['metrics'][arm]['evidence_coverage']:.2f}，local={b['metrics'][arm]['evidence_coverage']:.2f}。")
    (output / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def audit(output):
    output = Path(output)
    metadata = json.loads((output / 'metadata.json').read_text(encoding='utf-8'))
    for name, expected in metadata['artifact_hashes'].items():
        with (output / name).open('rb') as stream:
            import hashlib
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('Artifact changed: ' + name)
    questions = json.loads((output / 'questions.json').read_text(encoding='utf-8'))
    corpora = {name: {r['record_id']: r for r in json.loads((output / f'{name}-chunks.json').read_text(encoding='utf-8'))}
               for name in ('synthetic', 'live')}
    specs = {q['id']: q for q in questions}
    rows = [json.loads(line) for line in (output / 'results.jsonl').read_text(encoding='utf-8').splitlines()]
    for row in rows:
        for arm in ('dense', 'hybrid'):
            items = [corpora[row['corpus']][key] for key in row[arm + '_ids'][:5]]
            if score_question(specs[row['id']], items, row['success']) != row['metrics'][arm]:
                raise ValueError('Metric mismatch: ' + row['id'])
    calls = [json.loads(line) for line in (output / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]
    saved = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    recalculated = summarize(rows, questions, calls, saved['index_build'], saved['document_batches'])
    if recalculated != saved:
        raise ValueError('Summary differs from independently recomputed records')
    print(json.dumps({'status': 'audit_passed', 'records': len(rows), 'artifacts_checked': len(metadata['artifact_hashes'])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--rounds', type=int, choices=range(1, 6), default=3)
    parser.add_argument('--direct', action='store_true')
    parser.add_argument('--audit', type=Path)
    args = parser.parse_args()
    if args.audit:
        return audit(args.audit)
    corpora, live_matrices, questions, manifest, before = prepare()
    if args.validate_only:
        print(json.dumps({'questions': len(questions), 'answerable': sum(q['expected_behavior'] == 'answer' for q in questions),
                          'chunks': {k: len(v) for k, v in corpora.items()}, 'source': before['collections']}, indent=2))
        return
    if args.direct:
        os.environ.update(NO_PROXY='*', no_proxy='*')
    timestamp = datetime.now(timezone.utc)
    output = ROOT / 'evals/results' / (timestamp.strftime('%Y%m%dT%H%M%SZ') + '-embedding-comparison')
    output.mkdir(parents=True, exist_ok=False)
    print(f'OUTPUT={output}', flush=True)
    metadata = {'status': 'running', 'started_at_utc': timestamp.isoformat(),
                'started_at_shanghai': timestamp.astimezone(timezone(timedelta(hours=8))).isoformat(),
                'rounds': args.rounds, 'seed': 20261024, 'source_before': before,
                'network_mode': 'direct' if args.direct else 'environment',
                'api_model': os.getenv('EMBEDDING_MODEL'), 'local_model': local_embedding.MODEL_ID,
                'local_revision': local_embedding.MODEL_REVISION, 'dimensions': 1024,
                'top_k': 5, 'candidate_k': 30, 'rrf_k': 60, 'batch_size': 4, 'dataset_manifest': manifest}
    for name, chunks in corpora.items():
        dump(output / f'{name}-chunks.json', chunks)
    dump(output / 'questions.json', questions)
    for name in ['evals/run_embedding_comparison.py', 'evals/embedding_comparison.py',
                 'evals/retrieval_quality.py', 'evals/embedding_benchmark/live_questions.json',
                 'retrieval/embedding.py', 'retrieval/local_embedding.py', 'retrieval/bm25.py', 'retrieval/engine.py']:
        destination = output / 'code_snapshot' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    metadata['dataset_frozen_sha256'] = canonical_hash({'corpora': corpora, 'questions': questions})
    with urlopen(local_embedding.service_url().removesuffix('/v1') + '/health', timeout=5) as response:
        metadata['local_health'] = json.load(response)
    dump(output / 'metadata.json', metadata)
    calls, recorder = Calls(output), GPURecorder()
    recorder.thread.start()
    records, index_stats, batch_stats, collections, query_vectors = [], {}, {}, {}, {}
    try:
        # Predeclared inputs are frozen before the first model request.
        for provider in PROVIDERS:
            for i, text in enumerate(['Embedding benchmark warmup.', '知识库向量检索预热。']):
                calls.embed(provider, [text], 'warmup', str(i))
        client = chromadb.PersistentClient(path=str(output / 'isolated_chroma'))
        for provider in PROVIDERS:
            started = time.perf_counter()
            vectors = []
            for offset in range(0, len(corpora['synthetic']), 4):
                batch = corpora['synthetic'][offset:offset + 4]
                current, _ = calls.embed(provider, [r['content'] for r in batch], 'synthetic_index', str(offset))
                vectors.extend(current)
            index_stats[provider] = {'texts': len(vectors), 'encoding_seconds': time.perf_counter() - started}
            matrices = {'synthetic': np.asarray(vectors, dtype=np.float32), 'live': live_matrices[provider]}
            for corpus, chunks in corpora.items():
                collection = client.create_collection(f'{provider}-{corpus}', embedding_function=None,
                                                      configuration={'hnsw': {'space': 'cosine'}})
                write_started = time.perf_counter()
                collection.upsert(ids=[r['record_id'] for r in chunks], embeddings=matrices[corpus])
                collections[(provider, corpus)] = collection
                np.save(output / f'{provider}-{corpus}-vectors.npy', matrices[corpus])
                index_stats[provider][corpus + '_write_seconds'] = time.perf_counter() - write_started
            print(f'Built isolated indexes: {provider}', flush=True)
        ordered = sorted(corpora['live'], key=lambda r: (len(r['content']), r['record_id']))
        sample = [ordered[int(index)] for index in np.linspace(0, len(ordered) - 1, 40)]
        dump(output / 'document_batch_sample.json', [r['record_id'] for r in sample])
        for provider in PROVIDERS:
            offset_start = len(calls.rows)
            started = time.perf_counter()
            for offset in range(0, len(sample), 4):
                calls.embed(provider, [r['content'] for r in sample[offset:offset + 4]], 'document_batch', str(offset))
            elapsed = time.perf_counter() - started
            batch_stats[provider] = {'texts': len(sample), 'characters': sum(len(r['content']) for r in sample),
                'seconds': elapsed, 'texts_per_second': len(sample) / elapsed,
                'reported_total_tokens': sum(r['reported_total_tokens'] for r in calls.rows[offset_start:])}
        bm25, bm25_times = {}, {}
        for spec in questions:
            started = time.perf_counter()
            bm25[spec['id']] = search_bm25(corpora[spec['corpus']], spec['question'], top_k=30)
            bm25_times[spec['id']] = (time.perf_counter() - started) * 1000
        dump(output / 'bm25.json', {key: {'ids': [r['record_id'] for r in rows], 'milliseconds': bm25_times[key]}
                                   for key, rows in bm25.items()})
        order = questions.copy()
        random.Random(20261024).shuffle(order)
        dump(output / 'query_order.json', [q['id'] for q in order])
        for round_index in range(args.rounds):
            for question_index, spec in enumerate(order):
                providers = PROVIDERS if (question_index + round_index) % 2 == 0 else PROVIDERS[::-1]
                for provider in providers:
                    row = {'id': spec['id'], 'corpus': spec['corpus'], 'provider': provider,
                           'round': round_index + 1, 'success': False, 'dense_ids': [], 'hybrid_ids': []}
                    dense, hybrid = [], []
                    try:
                        vectors, call = calls.embed(provider, [spec['question']], 'query', f"{spec['id']}:r{round_index + 1}")
                        if round_index == 0:
                            query_vectors[provider + ':' + spec['id']] = vectors[0]
                        dense, query_ms = search(collections[(provider, spec['corpus'])], corpora[spec['corpus']], vectors[0])
                        fusion_started = time.perf_counter()
                        hybrid = fuse_rrf(bm25[spec['id']], dense, top_k=30, rrf_k=60)
                        fusion_ms = (time.perf_counter() - fusion_started) * 1000
                        row.update(success=True, embedding_ms=call['milliseconds'], vector_query_ms=query_ms,
                                   dense_component_ms=call['milliseconds'] + query_ms,
                                   hybrid_component_ms=call['milliseconds'] + query_ms + bm25_times[spec['id']] + fusion_ms,
                                   dense_ids=[r['record_id'] for r in dense], hybrid_ids=[r['record_id'] for r in hybrid],
                                   dense_distances=[r['distance'] for r in dense])
                    except Exception as exc:
                        row['error_type'] = type(exc).__name__
                    row['metrics'] = {arm: score_question(spec, items[:5], row['success'])
                                      for arm, items in [('dense', dense), ('hybrid', hybrid)]}
                    row['dense_coverage_at_30'] = score_question(spec, dense, row['success'])['evidence_coverage']
                    records.append(row)
                    append(output / 'results.jsonl', row)
                if (question_index + 1) % 10 == 0:
                    print(f'round {round_index + 1}/{args.rounds}: {question_index + 1}/{len(order)} questions', flush=True)
        np.savez_compressed(output / 'first_round_query_vectors.npz', **query_vectors)
        metadata['source_after'] = read_live_sources()[2]
        metadata['source_unchanged'] = metadata['source_after'] == before
        metadata['status'] = 'complete' if metadata['source_unchanged'] and all(r['success'] for r in records) else 'complete_with_issues'
    except Exception as exc:
        metadata.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        recorder.event.set()
        recorder.thread.join(timeout=6)
        dump(output / 'gpu_samples.json', recorder.samples)
        metadata['gpu'] = {'samples': len(recorder.samples),
                           'min_memory_mib': min((r['gpu_memory_mib'] for r in recorder.samples), default=None),
                           'max_memory_mib': max((r['gpu_memory_mib'] for r in recorder.samples), default=None)}
        metadata['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        summary = summarize(records, questions, calls.rows, index_stats, batch_stats)
        dump(output / 'summary.json', summary)
        import hashlib
        metadata['artifact_hashes'] = {}
        for path in output.rglob('*'):
            if path.is_file() and 'isolated_chroma' not in path.parts and path.name not in ('metadata.json', 'REPORT.md'):
                with path.open('rb') as stream:
                    metadata['artifact_hashes'][str(path.relative_to(output))] = hashlib.file_digest(stream, 'sha256').hexdigest()
        dump(output / 'metadata.json', metadata)
        report(output, summary, metadata, questions, records)
        print(f"Finished: {metadata['status']} -> {output / 'REPORT.md'}", flush=True)


if __name__ == '__main__':
    main()
