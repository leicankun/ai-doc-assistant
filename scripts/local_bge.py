"""Manage local BGE-M3 and its independent comparison index."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import BASE_DIR  # Loads project .env, without printing credentials.
from retrieval.local_embedding import MODEL_ID, MODEL_REVISION, COLLECTION_NAME, WEIGHTS_SHA256

RUNTIME = ROOT / '.local_bge'
STATE = RUNTIME / 'runtime.json'
UNAVAILABLE = (URLError, TimeoutError, ConnectionError)


def model_dir():
    value = Path(os.getenv('BGE_MODEL_DIR', 'models/bge-m3'))
    return value if value.is_absolute() else ROOT / value


def health():
    port = int(os.getenv('BGE_PORT', '8001'))
    with urlopen(f'http://127.0.0.1:{port}/health', timeout=3) as response:
        result = json.load(response)
    if result.get('model') != MODEL_ID or result.get('revision') != MODEL_REVISION:
        raise RuntimeError('该端口不是项目预期的 BGE-M3 服务')
    return result


def start():
    try:
        result = health()
    except UNAVAILABLE:
        result = None
    if result:
        return result
    python = ROOT / '.venv-bge' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        raise RuntimeError('请先按 docs/bge-m3.md 创建 .venv-bge 并安装依赖')
    if not (model_dir() / 'deployment.json').exists():
        raise RuntimeError('请先运行 download 下载并固定模型版本')
    RUNTIME.mkdir(exist_ok=True)
    state = {'deployment_id': secrets.token_hex(16), 'token': secrets.token_urlsafe(32)}
    env = {**os.environ, 'BGE_DEPLOYMENT_ID': state['deployment_id'],
           'BGE_MANAGEMENT_TOKEN': state['token'], 'PYTHONIOENCODING': 'utf-8'}
    command = [str(python), '-u', str(ROOT / 'scripts/bge_server.py'),
               '--model-dir', str(model_dir()), '--port', os.getenv('BGE_PORT', '8001'),
               '--device', os.getenv('BGE_DEVICE', 'cuda'),
               '--batch-size', os.getenv('BGE_BATCH_SIZE', '4')]
    with (RUNTIME / 'server.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    state['pid'] = process.pid
    STATE.write_text(json.dumps(state), encoding='utf-8')
    for _ in range(180):
        if process.poll() is not None:
            raise RuntimeError('服务启动失败，请查看 .local_bge/server.log')
        try:
            result = health()
            if result['deployment_id'] != state['deployment_id']:
                raise RuntimeError('服务端口被其他实例占用')
            return result
        except UNAVAILABLE:
            time.sleep(1)
    raise TimeoutError('模型仍在加载，请查看日志并运行 status 检查')


def stop():
    try:
        current = health()
    except UNAVAILABLE:
        return {'status': 'stopped'}
    state = json.loads(STATE.read_text(encoding='utf-8'))
    if current['deployment_id'] != state['deployment_id']:
        raise RuntimeError('拒绝停止不属于本项目启动记录的服务')
    request = Request(f"http://127.0.0.1:{os.getenv('BGE_PORT', '8001')}/shutdown", data=b'',
                      headers={'X-Management-Token': state['token']})
    with urlopen(request, timeout=10) as response:
        json.load(response)
    for _ in range(60):
        try:
            if health()['deployment_id'] != state['deployment_id']:
                break
        except UNAVAILABLE:
            break
        time.sleep(.5)
    else:
        raise TimeoutError('服务仍在结束请求，请稍后运行 status')
    return {'status': 'stopped'}


def download():
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    from huggingface_hub import snapshot_download
    weights = model_dir() / 'pytorch_model.bin'
    verified = False
    if weights.exists():
        with weights.open('rb') as stream:
            verified = hashlib.file_digest(stream, 'sha256').hexdigest() == WEIGHTS_SHA256
    patterns = ['*.json', 'sentencepiece.bpe.model', '1_Pooling/*']
    if not verified:
        patterns.append('pytorch_model.bin')
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=model_dir(),
                      allow_patterns=patterns, ignore_patterns=['onnx/**'], max_workers=3)
    with weights.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != WEIGHTS_SHA256:
            raise RuntimeError('模型权重 SHA256 不匹配')
    (model_dir() / 'deployment.json').write_text(json.dumps({
        'model': MODEL_ID, 'revision': MODEL_REVISION, 'weights_sha256': WEIGHTS_SHA256,
    }, indent=2), encoding='utf-8')
    return {'model': MODEL_ID, 'revision': MODEL_REVISION, 'directory': str(model_dir())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['download', 'start', 'stop', 'status', 'index', 'search'])
    parser.add_argument('--document-id', type=int)
    parser.add_argument('--question')
    parser.add_argument('--top-k', type=int, default=5)
    args = parser.parse_args()
    if args.command == 'download':
        result = download()
    elif args.command == 'start':
        result = start()
    elif args.command == 'status':
        try:
            result = health()
        except UNAVAILABLE:
            result = {'status': 'stopped'}
    elif args.command == 'stop':
        result = stop()
    else:
        from storage.database import connect
        from retrieval.local_index import index_local_document, search_local
        with closing(connect()) as connection:
            if args.command == 'index':
                # Remove snapshots of deleted documents only in the BGE collection.
                from storage.vector_store import get_local_collection
                collection = get_local_collection()
                all_ids = {r['id'] for r in connection.execute('SELECT id FROM documents')}
                snapshots = collection.get(include=['metadatas'])
                stale = [key for key, meta in zip(snapshots['ids'], snapshots['metadatas'])
                         if meta['document_id'] not in all_ids]
                if stale:
                    collection.delete(ids=stale)
                ids = [args.document_id] if args.document_id is not None else sorted(all_ids)
                result = {'collection': COLLECTION_NAME, 'documents': []}
                for doc_id in ids:
                    def progress(done, total):
                        print(f'document {doc_id}: {done}/{total}', flush=True)
                    report = index_local_document(connection, doc_id, progress)
                    result['documents'].append(report)
                    print(json.dumps(report, ensure_ascii=False), flush=True)
            else:
                if not args.question:
                    parser.error('search 需要 --question')
                result = {'collection': COLLECTION_NAME, 'items': search_local(
                    connection, args.question, args.top_k, args.document_id)}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
