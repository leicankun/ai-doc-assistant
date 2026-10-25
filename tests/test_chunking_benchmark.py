import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals import run_chunking_benchmark as bench


class ChunkingBenchmarkTests(unittest.TestCase):
    def test_original_layouts_identical_and_transformed_facts_preserved(self):
        _,questions,texts,sections = bench.read_dataset()
        layouts = [bench.layouts(texts,*config) for config in bench.CONFIGS]
        self.assertEqual(layouts[0],layouts[1])
        self.assertEqual(layouts[1],layouts[2])
        new_texts,new_sections,new_questions = bench.transformed_corpus(texts,sections,questions)
        self.assertEqual({s['id']:s['text'] for s in sections},{s['id']:s['text'] for s in new_sections})
        counts = [sum(len(c) for c in bench.layouts(new_texts,*config).values()) for config in bench.CONFIGS]
        self.assertEqual(len(set(counts)),3)
        for before,after in zip(questions,new_questions):
            self.assertEqual(before['question'],after['question'])
            self.assertEqual(before['expected_points'],after['expected_points'])
            for group in after['evidence_groups']:
                for alt in group['alternatives']:
                    self.assertEqual(new_texts[alt['document']][alt['start_char']:alt['end_char']],alt['quote'])

    def test_budget_keeps_whole_chunks_and_can_skip_to_a_smaller_one(self):
        items = [{'content':'a'*1500},{'content':'b'*1200},{'content':'c'*900}]
        original = copy.deepcopy(items)
        self.assertEqual(bench.within_budget(items),[items[0],items[2]])
        self.assertEqual(items,original)

    def test_overlap_is_counted_by_source_intervals_not_duplicate_facts(self):
        spec = {'evidence_groups':[{'alternatives':[{'document':'a','start_char':90,'end_char':110}]}]}
        def item(start,end):
            return {'document':'a','start_char':start,'end_char':end,'content':'x'*(end-start),'redundancy_group':None,'heading_only':False}
        metrics = bench.extra_metrics(spec,[item(0,100),item(80,130)])
        self.assertEqual(metrics['evidence_coverage'],1)
        self.assertEqual(metrics['unique_source_characters'],130)
        self.assertAlmostEqual(metrics['source_overlap_ratio'],20/150)

    def test_failures_remain_in_denominator(self):
        spec = {'split':'test','expected_behavior':'answer'}
        metric = {'evidence_coverage':1,'full_evidence_coverage':True,'reciprocal_rank':1,'context_characters':100,'source_overlap_ratio':0}
        records = [{'group':'g','question_spec':spec,'status':'ok','metrics':metric,'budget_metrics':metric,'seconds':1},
                   {'group':'g','question_spec':spec,'status':'failed'}]
        result = bench.summarize(records,[],{'g':{}})['g']['retrieval']['test']
        self.assertEqual(result['questions'],2)
        self.assertEqual(result['success'],1)
        self.assertEqual(result['metrics']['mean_evidence_coverage'],.5)

    def test_isolated_runner_with_concurrent_groups(self):
        def response(request,**kwargs):
            count = json.loads(request.data)['parameters']['top_n']
            return io.BytesIO(json.dumps({'output':{'results':[{'index':i,'relevance_score':.99-i/100} for i in range(count)]}}).encode())
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()), \
             patch.object(bench,'ROOT',Path(folder)), \
             patch.object(bench,'official_state',return_value={'sentinel':'same'}), \
             patch.object(bench.embedding,'embed_texts',side_effect=lambda texts:[[1.,0.,0.] for _ in texts]), \
             patch.object(bench.reranker,'urlopen',side_effect=response), \
             patch.dict('os.environ',{'RERANK_MODEL':'gte-rerank-v2','RERANK_API_KEY':'test','RERANK_URL':'https://test.invalid'}), \
             patch('sys.argv',['benchmark','--limit','1','--skip-answers','--workers','2']):
            bench.main()
            output = next((Path(folder)/'evals/results').iterdir())
            metadata = json.loads((output/'metadata.json').read_text(encoding='utf-8'))
            summary = json.loads((output/'summary.json').read_text(encoding='utf-8'))
            self.assertTrue(metadata['official_data_unchanged'])
            self.assertEqual(len(summary),4)
            for group in summary.values():
                self.assertEqual(group['retrieval']['all']['success'],1)


if __name__ == '__main__':
    unittest.main()
