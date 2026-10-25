"""Loopback-only, offline BGE-M3 embedding service, run in .venv-bge."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from retrieval.local_embedding import MODEL_ID, MODEL_REVISION, DIMENSIONS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    parser.add_argument('--batch-size', type=int, default=4)
    args = parser.parse_args()
    # Loading from a local directory must never download model files at runtime.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch
    from sentence_transformers import SentenceTransformer
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA 不可用；安装 CUDA PyTorch，或设置 BGE_DEVICE=cpu')
    model_dir = Path(args.model_dir)
    provenance = json.loads((model_dir / 'deployment.json').read_text(encoding='utf-8'))
    if provenance['model'] != MODEL_ID or provenance['revision'] != MODEL_REVISION:
        raise RuntimeError('模型版本与本地集合不匹配')
    print(f'Loading {MODEL_ID} on {args.device}...', flush=True)
    model = SentenceTransformer(str(model_dir), device=args.device, local_files_only=True,
                                trust_remote_code=False,
                                model_kwargs={'dtype': torch.float16 if args.device == 'cuda' else torch.float32})
    model.max_seq_length = 8192
    model.encode(['本地模型就绪'], normalize_embeddings=True, show_progress_bar=False)
    lock = threading.Lock()
    deployment_id = os.environ['BGE_DEPLOYMENT_ID']
    management_token = os.environ['BGE_MANAGEMENT_TOKEN']

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path not in ('/health', '/v1/models'):
                return self.reply(404, {'error': 'Not found'})
            if self.path == '/v1/models':
                return self.reply(200, {'object': 'list', 'data': [
                    {'id': MODEL_ID, 'object': 'model', 'owned_by': 'local'}]})
            self.reply(200, {'status': 'ready', 'model': MODEL_ID, 'revision': MODEL_REVISION,
                             'dimensions': DIMENSIONS, 'device': args.device,
                             'gpu': torch.cuda.get_device_name(0) if args.device == 'cuda' else None,
                             'max_tokens': model.max_seq_length, 'deployment_id': deployment_id})

        def do_POST(self):
            if self.path == '/shutdown':
                import hmac
                if not hmac.compare_digest(self.headers.get('X-Management-Token', ''), management_token):
                    return self.reply(403, {'error': 'Forbidden'})
                self.reply(200, {'status': 'stopping'})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path != '/v1/embeddings':
                return self.reply(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('请求体大小无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('请求体必须是 JSON 对象')
                texts = body.get('input')
                if isinstance(texts, str):
                    texts = [texts]
                if (not isinstance(texts, list) or not 1 <= len(texts) <= 32
                        or any(not isinstance(t, str) or not t.strip() for t in texts)):
                    raise ValueError('input 必须是非空字符串或 1～32 条非空字符串列表')
                if body.get('model', MODEL_ID) != MODEL_ID:
                    raise ValueError('仅支持 BAAI/bge-m3')
                if body.get('dimensions', DIMENSIONS) != DIMENSIONS:
                    raise ValueError('仅支持 1024 维')
                if body.get('encoding_format', 'float') != 'float':
                    raise ValueError('仅支持 float 编码')
                started = time.perf_counter()
                # Serialize GPU work. Reject oversized inputs instead of silently truncating.
                with lock:
                    lengths = [len(ids) for ids in model.tokenizer(
                        texts, truncation=False, padding=False)['input_ids']]
                    if max(lengths) > model.max_seq_length:
                        raise ValueError('单条文本超过 BGE-M3 的 8192 token 上限，请先分块')
                    vectors = model.encode(texts, batch_size=args.batch_size,
                                           normalize_embeddings=True, show_progress_bar=False).tolist()
                self.reply(200, {'object': 'list', 'model': MODEL_ID,
                                 'data': [{'object': 'embedding', 'index': i, 'embedding': v}
                                          for i, v in enumerate(vectors)],
                                 'usage': {'prompt_tokens': sum(lengths), 'total_tokens': sum(lengths)},
                                 'elapsed_seconds': round(time.perf_counter() - started, 4)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                import traceback
                traceback.print_exc()
                if args.device == 'cuda':
                    torch.cuda.empty_cache()
                self.reply(500, {'error': '本地推理失败，请查看 .local_bge/server.log'})

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = False
    print(f'Ready: http://127.0.0.1:{args.port}/v1/embeddings', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
