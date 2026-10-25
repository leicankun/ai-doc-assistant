"""验证评测计数，防止跨分块漏判或失败被排除导致结果虚高。"""
import unittest
from evals.run_agentic_benchmark import coverage, percentiles, summary, tokens


class BenchmarkMetricsTests(unittest.TestCase):
    def test_evidence_can_span_adjacent_chunks_but_not_gaps_or_documents(self):
        source={'document':'atlas-deploy-v2.md','quote':'Atlas 2.4 的默认监听端口是 8200'}
        from pathlib import Path
        text=(Path(__file__).resolve().parents[1]/'evals/agentic_benchmark/documents'/source['document']).read_text(encoding='utf-8')
        start=text.index(source['quote'])
        stop=start+len(source['quote'])
        chunks={1:{'document_id':7,'start_char':start,'end_char':start+10},
                2:{'document_id':7,'start_char':start+10,'end_char':stop}}
        items=[{'chunk_id':1},{'chunk_id':2}]
        self.assertEqual(coverage([source],items,{source['document']:7},chunks)['evidence_coverage'],1)
        chunks[2]['start_char']+=1
        self.assertEqual(coverage([source],items,{source['document']:7},chunks)['evidence_coverage'],0)
        chunks[2]['start_char']-=1
        chunks[2]['document_id']=8
        self.assertEqual(coverage([source],items,{source['document']:7},chunks)['evidence_coverage'],0)

    def test_request_and_judge_failure_remain_in_denominator(self):
        spec={'category':'direct','expected_behavior':'answer'}
        common={'arm':'rag','question_spec':spec,'seconds':2,'token_usage':{'total_tokens':100}}
        rows=[{**common,'status':200,'response':{},'judge':{'status':'scored','task_pass':True,'strict_pass':True,'point_coverage':1,'citation_errors':[],'unsupported_claims':[]}},
              {**common,'status':502,'response':{},'judge':{'status':'not_run_request_failed'}},
              {**common,'status':200,'response':{},'judge':{'status':'judge_failed'}}]
        result=summary(rows,[],[])['arms']['rag']
        self.assertEqual(result['questions'],3)
        self.assertEqual(result['request_success'],2)
        self.assertEqual(result['judge_success'],1)
        self.assertEqual(result['task_pass'],1)
        self.assertEqual(result['chat_tokens_total'],300)

    def test_vendor_usage_and_latency_are_not_estimated(self):
        calls=[{'kind':'chat','usage':{'prompt_tokens':12,'completion_tokens':8,'total_tokens':20}},
               {'kind':'embedding','usage':{'total_tokens':99}},
               {'kind':'chat','usage':None}]
        self.assertEqual(tokens(calls)['total_tokens'],20)
        self.assertEqual(percentiles([1,2,3,4,5])['p50'],3)
        self.assertIsNone(percentiles([]))


if __name__=='__main__':
    unittest.main()
