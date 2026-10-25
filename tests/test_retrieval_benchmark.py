"""专项题集和评分口径的离线检查；不调用模型。"""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from evals import run_retrieval_benchmark as benchmark
from evals.run_retrieval_benchmark import read_dataset, retrieval_metrics, summarize


class RetrievalBenchmarkTests(unittest.TestCase):
    def test_fixed_dataset_has_disjoint_splits_and_complete_annotations(self):
        manifest,questions,texts,sections=read_dataset()
        self.assertEqual(len(questions),60)
        self.assertEqual(len(texts),20)
        self.assertGreaterEqual(len(sections),150)
        self.assertEqual(manifest['split'],{'dev':20,'test':40})
        self.assertEqual(sum(q['answer_check'] for q in questions),20)

    def test_alternative_sources_cross_chunk_coverage_and_wrong_document(self):
        spec={'evidence_groups':[{'alternatives':[
            {'document':'a','start_char':10,'end_char':20},
            {'document':'b','start_char':0,'end_char':5}]}]}
        def item(doc,start,end,group):
            return {'document':doc,'start_char':start,'end_char':end,
                    'redundancy_group':group,'heading_only':False}
        items=[item('a',10,15,'one'),item('a',15,20,'two')]
        result=retrieval_metrics(spec,items)
        self.assertEqual(result['evidence_coverage'],1)
        self.assertEqual(result['reciprocal_rank'],.5)
        items[1]['start_char']=16
        self.assertEqual(retrieval_metrics(spec,items)['evidence_coverage'],0)
        self.assertEqual(retrieval_metrics(spec,[item('b',0,5,'other')])['evidence_coverage'],1)
        self.assertEqual(retrieval_metrics(spec,[item('wrong',10,20,'wrong')])['evidence_coverage'],0)

    def test_redundancy_and_headings_do_not_create_evidence(self):
        spec={'evidence_groups':[{'alternatives':[{'document':'a','start_char':10,'end_char':20}]}]}
        items=[{'document':'a','start_char':0,'end_char':5,'redundancy_group':None,'heading_only':True},
               {'document':'a','start_char':10,'end_char':20,'redundancy_group':'same','heading_only':False},
               {'document':'a','start_char':10,'end_char':20,'redundancy_group':'same','heading_only':False}]
        result=retrieval_metrics(spec,items)
        self.assertEqual(result['heading_only_count'],1)
        self.assertEqual(result['redundant_count'],1)
        self.assertEqual(result['evidence_coverage'],1)
        self.assertEqual(result['reciprocal_rank'],.5)
        self.assertEqual(retrieval_metrics({'evidence_groups':[]},items)['evidence_coverage'],None)

    def test_failures_stay_in_denominator_and_negatives_are_excluded(self):
        positive={'split':'test','category':'direct','expected_behavior':'answer'}
        negative={'split':'test','category':'unanswerable','expected_behavior':'refuse'}
        metrics={'evidence_coverage':1,'any_evidence_hit':True,'full_evidence_coverage':True,
                 'reciprocal_rank':1,'returned_count':5,'heading_only_count':0,'redundant_count':0}
        rows=[{'question_spec':positive,'arms':{'A':{'status':'ok','metrics':metrics,'composed_seconds':1}}},
              {'question_spec':copy.deepcopy(positive),'arms':{'A':{'status':'failed'}}},
              {'question_spec':negative,'arms':{'A':{'status':'ok','metrics':metrics,'composed_seconds':1}}}]
        result=summarize(rows,[])['retrieval']['test']['A']
        self.assertEqual(result['questions'],3)
        self.assertEqual(result['answerable_questions'],2)
        self.assertEqual(result['success'],2)
        self.assertEqual(result['mean_evidence_coverage'],.5)

    def test_isolated_runner_end_to_end_without_external_models(self):
        def respond(request, **kwargs):
            payload=json.loads(request.data)
            count=payload['parameters']['top_n']
            return io.BytesIO(json.dumps({'output':{'results':[
                {'index':i,'relevance_score':.99-i/100} for i in range(count)]},
                'usage':{'total_tokens':12}}).encode())
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), \
                patch.object(benchmark,'ROOT',Path(folder)), \
                patch.object(benchmark,'official_data_fingerprint',return_value={'sentinel':'same'}), \
                patch.object(benchmark.indexing,'embed_texts',side_effect=lambda texts:[[1.,0.]]*len(texts)), \
                patch.object(benchmark.knowledge_base,'embed_query',return_value=[1.,0.]), \
                patch.object(benchmark.reranker,'urlopen',side_effect=respond), \
                patch.dict('os.environ',{'RERANK_MODEL':'gte-rerank-v2','RERANK_API_KEY':'test',
                                       'RERANK_URL':'https://test.invalid/rerank'}), \
                patch('sys.argv',['benchmark','--limit','1','--skip-answers']):
            benchmark.main()
            output=next((Path(folder)/'evals/results').iterdir())
            summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
            metadata=json.loads((output/'metadata.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['official_data_unchanged'])
            self.assertGreaterEqual(metadata['body_chunks'],150)
            for arm in benchmark.ARMS:
                self.assertEqual(summary['retrieval']['all'][arm]['success'],1)
            self.assertEqual(summary['retrieval']['all']['D']['mmr_applied'],1)
