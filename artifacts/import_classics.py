"""下载公开古籍并通过现有接口导入；保存来源与导入进度。"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from storage import database
from routers.documents import router

SOURCES = [(23839, '论语'), (7375, '大学章句'), (7376, '中庸章句')]
FOLDER = ROOT/'artifacts/classics'
FOLDER.mkdir(parents=True, exist_ok=True)
MANIFEST = FOLDER/'manifest.json'
manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'documents': []}


def save():
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def checked(response):
    response.raise_for_status()
    return response.json()


database.init_db()
app = FastAPI()
app.include_router(router)
with TestClient(app) as client:
    bases = checked(client.get('/knowledge-bases'))['items']
    base = next((b for b in bases if b['title'] == '儒家经典'), None)
    if base is None:
        base = checked(client.post('/knowledge-bases', json={'title': '儒家经典'}))
    manifest['knowledge_base_id'] = base['id']
    save()
    for ebook, title in SOURCES:
        url = f'https://www.gutenberg.org/cache/epub/{ebook}/pg{ebook}.txt'
        archive = FOLDER/f'pg{ebook}.txt'
        if not archive.exists():
            response = httpx.get(url, follow_redirects=True, timeout=60)
            response.raise_for_status()
            archive.write_bytes(response.content)
        raw = archive.read_text(encoding='utf-8-sig')
        match = re.search(r'\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK .*?\*\*\*(.*?)\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK', raw, re.S)
        if not match:
            raise ValueError(f'Cannot locate ebook body: {ebook}')
        body = match.group(1).strip()
        if len(body) < 3000:
            raise ValueError(f'Unexpectedly short source: {ebook}')
        text = f'# {title}\n\n来源：Project Gutenberg，https://www.gutenberg.org/ebooks/{ebook}\n\n版本：繁体中文原文；下载原件及完整许可保存在 artifacts/classics/pg{ebook}.txt。\n\n{body}\n'
        path = FOLDER/f'{title}.md'
        path.write_text(text, encoding='utf-8')
        entry = next((d for d in manifest['documents'] if d['ebook_id'] == ebook), None)
        if entry is None:
            entry = {'ebook_id': ebook, 'title': title, 'source_url': url, 'characters': len(body),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            manifest['documents'].append(entry)
        if not entry.get('document_id') or client.get(f'/documents/{entry["document_id"]}').status_code == 404:
            uploaded = checked(client.post('/documents/upload', files={'file': (path.name, path.read_bytes(), 'text/markdown')}))
            entry['document_id'] = uploaded['id']
            save()
        doc = entry['document_id']
        checked(client.post(f'/knowledge-bases/{base["id"]}/documents', json={'document_ids': [doc]}))
        detail = checked(client.get(f'/documents/{doc}'))
        if not detail['chunk_count']:
            checked(client.post(f'/documents/{doc}/chunks'))
        detail = checked(client.get(f'/documents/{doc}'))
        entry['chunks'] = detail['chunk_count']
        save()
        print(title, 'uploaded', doc, 'characters', len(body), 'chunks', entry['chunks'], flush=True)
        if detail['processing_status'] != 'indexed':
            checked(client.post(f'/documents/{doc}/index'))
        entry['status'] = checked(client.get(f'/documents/{doc}'))['processing_status']
        save()
        print(title, entry['status'], flush=True)
    result = checked(client.post(f'/knowledge-bases/{base["id"]}/search', json={'question': '修身与学习有什么关系？', 'mode': 'hybrid', 'top_k': 5}))
    manifest['verification'] = {'mode': 'hybrid', 'top_k': 5, 'total': result['total'],
                                'source_names': sorted({r['source_name'] for r in result['items']})}
    save()
    print('Completed:', MANIFEST, flush=True)
