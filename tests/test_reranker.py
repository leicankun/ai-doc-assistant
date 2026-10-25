"""重排适配器及真实 SQLite/Chroma 检索链路，外部重排请求用固定响应。"""
import copy
import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from fastapi import HTTPException
from retrieval import reranker
from retrieval import engine
from retrieval import mmr
from agent.tools import search_knowledge_base
from services import knowledge_base
from routers import search
from test_knowledge_bases import KnowledgeBaseTests


class RerankerTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'RERANK_ENABLED': 'true', 'RERANK_CANDIDATES': '30',
                         'RERANK_API_KEY': 'test', 'RERANK_URL': 'https://test.invalid/rerank',
                         'RERANK_MODEL': 'gte-rerank-v2', 'MMR_ENABLED': 'false'})
        env.start()
        self.addCleanup(env.stop)
        self.items = [{'document_id': 1, 'chunk_id': i, 'content': str(i), 'rank': i + 1}
                      for i in range(3)]

    def respond(self, results):
        return patch.object(reranker, 'urlopen', return_value=BytesIO(
            json.dumps({'output': {'results': results}}).encode()))

    def test_mapping_and_original_preservation(self):
        original = copy.deepcopy(self.items)
        with self.respond([{'index': 2, 'relevance_score': .9}, {'index': 0, 'relevance_score': .6}]) as request:
            result = reranker.rerank('question', self.items, 2)
        self.assertEqual([r['chunk_id'] for r in result], [2, 0])
        self.assertEqual([r['rank'] for r in result], [1, 2])
        self.assertEqual(result[0]['retrieval_rank'], 3)
        self.assertEqual(self.items, original)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['input']['documents'], ['0', '1', '2'])
        self.assertEqual(payload['parameters']['top_n'], 2)

    def test_heading_filter_is_conservative(self):
        headings = ['# 标题', ' \n# 一级\n\n## 二级\n', '标题\n===', '标题\n---']
        bodies = ['# 标题\n正文', '    # Python comment', '```python\n# comment\n```',
                  '#hashtag', '正文\n\n---', '1. 步骤\n---', '普通 PDF 标题']
        items = [{'document_id': 1, 'chunk_id': i, 'content': text}
                 for i, text in enumerate([' \n\t', *headings, *bodies])]
        self.assertEqual([x['content'] for x in reranker.clean_candidates(items)], bodies)

    def test_dedup_preserves_sources_code_spacing_and_near_matches(self):
        texts = [(1, 'shared\r\nbody\r\n'), (1, '\nshared\nbody\n'),
                 (2, 'shared\nbody'), (1, 'shared\nbody with extra fact'),
                 (1, '    shared\nbody'), (None, 'shared\nbody'), (None, 'shared\nbody')]
        items = [{'document_id': doc, 'chunk_id': i, 'content': text, 'page_number': i + 1}
                 for i, (doc, text) in enumerate(texts)]
        original = copy.deepcopy(items)
        result = reranker.clean_candidates(items)
        self.assertEqual([x['chunk_id'] for x in result], [0, 2, 3, 4, 5, 6])
        self.assertEqual(result[0]['page_number'], 1)
        self.assertEqual(items, original)

    def test_cleaning_precedes_api_and_indices_map_to_original_chunks(self):
        items = [dict(self.items[0], content='# 标题'),
                 dict(self.items[1], content='正文'),
                 dict(self.items[2], content='正文'),
                 {'document_id': 2, 'chunk_id': 9, 'content': '正文', 'rank': 4}]
        original = copy.deepcopy(items)
        with self.respond([{'index': 1, 'relevance_score': .9},
                           {'index': 0, 'relevance_score': .8}]) as request:
            result = reranker.rerank('q', items, 5)
        self.assertEqual([x['chunk_id'] for x in result], [9, 1])
        self.assertEqual([x['retrieval_rank'] for x in result], [4, 2])
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['input']['documents'], ['正文', '正文'])
        self.assertEqual(payload['parameters']['top_n'], 2)
        self.assertEqual(items, original)

    def test_cleaning_with_disabled_rerank_and_empty_cleaned_pool(self):
        items = [dict(self.items[0], content='# 标题'),
                 dict(self.items[1], content='正文'), dict(self.items[2], content='正文')]
        with patch.object(reranker, 'urlopen') as request:
            self.assertEqual(reranker.rerank('q', items[:1], 5), [])
            with patch.dict(os.environ, {'RERANK_ENABLED': 'false'}):
                result = reranker.rerank('q', items, 5)
            self.assertEqual([x['chunk_id'] for x in result], [1])
            self.assertEqual(result[0]['rank'], 1)
            request.assert_not_called()

    def test_invalid_results_are_rejected(self):
        for rows in [[], [{'index': 8, 'relevance_score': .5}],
                     [{'index': 0, 'relevance_score': float('nan')}],
                     [{'index': True, 'relevance_score': .5}]]:
            with self.respond(rows), self.assertRaises(HTTPException) as exc:
                reranker.rerank('q', self.items, 1)
            self.assertEqual(exc.exception.status_code, 502)
        with self.respond([{'index': 0, 'relevance_score': .8}] * 2), self.assertRaises(HTTPException):
            reranker.rerank('q', self.items, 2)

    def test_disabled_and_empty_skip_api(self):
        with patch.object(reranker, 'urlopen') as request:
            self.assertEqual(reranker.rerank('q', [], 5), [])
            with patch.dict(os.environ, {'RERANK_ENABLED': 'false'}):
                self.assertEqual(reranker.candidate_count(5), 5)
                self.assertEqual(reranker.rerank('q', self.items, 1), self.items[:1])
            request.assert_not_called()

    def test_timeout_is_explicit(self):
        with patch.object(reranker, 'urlopen', side_effect=TimeoutError), self.assertRaises(HTTPException) as exc:
            reranker.rerank('q', self.items, 1)
        self.assertEqual(exc.exception.status_code, 504)

    def test_qwen_response(self):
        response = BytesIO(b'{"results":[{"index":1,"relevance_score":0.9}]}')
        with patch.dict(os.environ, {'RERANK_MODEL': 'qwen3-rerank'}), \
                patch.object(reranker, 'urlopen', return_value=response) as request:
            result = reranker.rerank('q', self.items, 1)
        self.assertEqual(result[0]['chunk_id'], 1)
        self.assertEqual(json.loads(request.call_args.args[0].data)['query'], 'q')

    def test_mmr_scores_full_pool_before_selecting_final_top_k(self):
        records = {'ids': ['doc:1:chunk:2', 'doc:1:chunk:0', 'doc:1:chunk:1'],
                   'documents': ['2', '0', '1'],
                   'metadatas': [{'document_id': 1, 'chunk_id': i} for i in [2, 0, 1]],
                   'embeddings': [[0., 1.], [1., 0.], [1., 0.]]}
        original = copy.deepcopy(self.items)
        with patch.dict(os.environ, {'MMR_ENABLED': 'true', 'MMR_LAMBDA': '0.8'}), \
                patch.object(mmr, 'get_collection') as collection, \
                self.respond([{'index': i, 'relevance_score': score}
                              for i, score in enumerate([.99, .98, .9])]) as request:
            collection.return_value.get.return_value = records
            result = reranker.rerank('question', self.items, 2)
        self.assertEqual(json.loads(request.call_args.args[0].data)['parameters']['top_n'], 3)
        self.assertEqual([x['chunk_id'] for x in result], [0, 2])
        self.assertEqual([x['rank'] for x in result], [1, 2])
        self.assertEqual(result[1]['rerank_rank'], 3)
        self.assertEqual(result[1]['retrieval_rank'], 3)
        self.assertEqual(result[1]['rerank_score'], .9)
        self.assertAlmostEqual(result[1]['mmr_score'], .72)
        self.assertEqual(self.items, original)

    def test_mmr_qwen_full_pool_and_lambda_one_bypass(self):
        with patch.dict(os.environ, {'MMR_ENABLED': 'true', 'MMR_LAMBDA': '0.8',
                                   'RERANK_MODEL': 'qwen3-rerank'}), \
                patch.object(reranker, 'apply_mmr', side_effect=lambda rows, k, w: rows[:k]) as select, \
                patch.object(reranker, 'urlopen', return_value=BytesIO(json.dumps({'results': [
                    {'index': i, 'relevance_score': .9 - i / 10} for i in range(3)]}).encode())) as request:
            result = reranker.rerank('q', self.items, 2)
        self.assertEqual(json.loads(request.call_args.args[0].data)['top_n'], 3)
        self.assertEqual(len(result), 2)
        select.assert_called_once()
        with patch.dict(os.environ, {'MMR_ENABLED': 'true', 'MMR_LAMBDA': '1'}), \
                patch.object(reranker, 'apply_mmr') as select, \
                self.respond([{'index': 0, 'relevance_score': .9}]):
            self.assertEqual(len(reranker.rerank('q', self.items, 1)), 1)
            select.assert_not_called()


class RerankIntegrationTests(unittest.TestCase):
    def setUp(self):
        # 使用现有隔离数据库夹具；准备资料时不调用重排服务。
        self.fixture = KnowledgeBaseTests()
        with patch.dict(os.environ, {'RERANK_ENABLED': 'false'}):
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for mocker in [patch.object(engine, 'get_collection', return_value=self.fixture.collection),
                       patch.object(engine, 'embed_query', return_value=[1., 0.])]:
            mocker.start()
            self.addCleanup(mocker.stop)

    @staticmethod
    def choose_last(question, items, top_k, **kwargs):
        return [{**item, 'rank': i + 1, 'rerank_score': .9}
                for i, item in enumerate(list(reversed(items))[:top_k])]

    def test_kb_and_single_document_candidate_pool(self):
        f = self.fixture
        with patch.dict(os.environ, {'RERANK_ENABLED': 'true', 'RERANK_CANDIDATES': '30'}):
            for mode in ['bm25', 'dense', 'hybrid']:
                with patch.object(knowledge_base, 'rerank', side_effect=self.choose_last) as rank:
                    response = f.client.post(f.url + '/search', json={'question': 'shared', 'mode': mode, 'top_k': 1})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['total'], 1)
                    pool = rank.call_args.args[1]
                    self.assertEqual(len(pool), 2)
                    self.assertEqual({x['document_id'] for x in pool}, set(f.ids[:2]))
                with patch.object(search, 'rerank', side_effect=self.choose_last) as rank:
                    response = f.client.post(f'/documents/{f.ids[0]}/search', json={'question': 'shared', 'mode': mode, 'top_k': 1})
                    self.assertEqual(response.status_code, 200, response.text)
                    rank.assert_called_once()
                    self.assertEqual(response.json()['items'][0]['rerank_score'], .9)

    def test_candidate_cleaning_in_single_kb_and_agent_search(self):
        f = self.fixture
        next_index = f.db.execute(
            'SELECT MAX(chunk_index) + 1 FROM document_chunks WHERE document_id = ?', (f.ids[0],)
        ).fetchone()[0]
        for chunk_index, content in enumerate(
                ['shared alpha fact.', '# shared', 'shared extra fact.'], start=next_index):
            f.db.execute(
                'INSERT INTO document_chunks (document_id, chunk_index, content, source_name) VALUES (?, ?, ?, ?)',
                (f.ids[0], chunk_index, content, 'a.md'))
        f.db.commit()
        self.assertEqual(f.client.post(f'/documents/{f.ids[0]}/index').status_code, 200)

        def respond(request, **kwargs):
            payload = json.loads(request.data)
            limit = payload['parameters']['top_n']
            return BytesIO(json.dumps({'output': {'results': [
                {'index': i, 'relevance_score': 1 - i / 10} for i in range(limit)]}}).encode())

        with patch.object(engine, 'get_collection', return_value=f.collection), \
                patch.object(engine, 'embed_query', return_value=[1., 0.]), \
                patch.object(reranker, 'urlopen', side_effect=respond), \
                patch.dict(os.environ, {'RERANK_API_KEY': 'test',
                                       'RERANK_URL': 'https://test.invalid/rerank',
                                       'RERANK_MODEL': 'gte-rerank-v2'}):
            for enabled in ['true', 'false']:
                with patch.dict(os.environ, {'RERANK_ENABLED': enabled}):
                    for mode in ['bm25', 'dense', 'hybrid']:
                        for url, expected in [(f'/documents/{f.ids[0]}/search', 2),
                                              (f.url + '/search', 3)]:
                            with self.subTest(enabled=enabled, mode=mode, url=url):
                                response = f.client.post(url, json={'question': 'shared', 'mode': mode, 'top_k': 20})
                                self.assertEqual(response.status_code, 200, response.text)
                                rows = response.json()['items']
                                self.assertEqual(len(rows), expected)
                                self.assertTrue(all(not x['content'].startswith('#') for x in rows))
                                self.assertEqual(len({(x['document_id'], x['content']) for x in rows}), expected)
                                self.assertEqual([x['rank'] for x in rows], list(range(1, expected + 1)))
                    result = search_knowledge_base(f.db, f.base, 'shared', top_k=10)
                    self.assertEqual(result['total'], 3)

    def test_mmr_with_real_chroma_in_single_kb_and_agent(self):
        f = self.fixture
        # 同一文档三个不同正文；前两块向量相同，第三块提供另一方向的信息。
        f.db.execute('INSERT INTO document_chunks (document_id, chunk_index, content) VALUES (?, 2, ?)',
                     (f.ids[0], 'shared repeated detail.'))
        cursor = f.db.execute('INSERT INTO document_chunks (document_id, chunk_index, content) VALUES (?, 3, ?)',
                              (f.ids[0], 'shared distinct detail.'))
        distinct_id = cursor.lastrowid
        f.db.commit()
        self.assertEqual(f.client.post(f'/documents/{f.ids[0]}/index').status_code, 200)
        f.collection.update(ids=[f'doc:{f.ids[0]}:chunk:{distinct_id}'], embeddings=[[0., 1.]])

        def respond(request, **kwargs):
            payload = json.loads(request.data)
            scores = {'shared alpha fact.': .99, 'shared beta fact.': .98,
                      'shared repeated detail.': .97, 'shared distinct detail.': .9}
            rows = [{'index': i, 'relevance_score': scores[text]}
                    for i, text in enumerate(payload['input']['documents'])]
            self.assertEqual(payload['parameters']['top_n'], len(rows))
            return BytesIO(json.dumps({'output': {'results': rows}}).encode())

        with patch.dict(os.environ, {'RERANK_ENABLED': 'true', 'MMR_ENABLED': 'true',
                                   'MMR_LAMBDA': '0.8', 'RERANK_API_KEY': 'test',
                                   'RERANK_URL': 'https://test.invalid/rerank',
                                   'RERANK_MODEL': 'gte-rerank-v2'}), \
                patch.object(mmr, 'get_collection', return_value=f.collection), \
                patch.object(reranker, 'urlopen', side_effect=respond):
            for mode in ['bm25', 'dense', 'hybrid']:
                for url in [f'/documents/{f.ids[0]}/search', f.url + '/search']:
                    with self.subTest(mode=mode, url=url):
                        response = f.client.post(url, json={'question': 'shared', 'mode': mode, 'top_k': 2})
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual([x['content'] for x in response.json()['items']],
                                         ['shared alpha fact.', 'shared distinct detail.'])
                        self.assertTrue(all('mmr_score' in x for x in response.json()['items']))
            result = search_knowledge_base(f.db, f.base, 'shared', top_k=2)
            self.assertEqual([x['chunk_id'] for x in result['items']][1], distinct_id)


if __name__ == '__main__':
    unittest.main()
