import copy
import json
from pathlib import Path
import tempfile
import unittest

from evals import retrieval_quality as quality
from evals import report_retrieval_quality as reporter


def item(chunk_id,start,end,document='a'):
    return {'chunk_id':chunk_id,'document_id':1,'document':document,'content':'x'*(end-start),
            'start_char':start,'end_char':end,'heading_only':False,'redundancy_group':None}


def spec(groups):
    return {'id':'q','split':'test','question':'question','expected_behavior':'answer','evidence_groups':[
        {'alternatives':[{'document':doc,'start_char':start,'end_char':end}]} for doc,start,end in groups]}


class RetrievalQualityTests(unittest.TestCase):
    def test_equivalent_sources_fragment_union_duplicates_and_wrong_document(self):
        question=spec([('a',10,20)])
        question['evidence_groups'][0]['alternatives'].append({'document':'b','start_char':0,'end_char':5})
        metrics=quality.evaluate_stage(question,[item(1,10,15),item(2,15,20)])
        self.assertEqual(metrics['evidence_hits'],[True])
        self.assertEqual(metrics['reciprocal_rank_at_5'],.5)
        self.assertEqual(quality.evaluate_stage(question,[item(3,0,5,'b')])['evidence_coverage'],1)
        self.assertEqual(quality.evaluate_stage(question,[item(3,0,5,'c')])['evidence_coverage'],0)
        self.assertEqual(quality.evaluate_stage(question,[item(1,10,20),item(2,10,20)])['evidence_hits'],[True])

    def test_mrr_is_top5_even_when_candidate_pool_eventually_has_evidence(self):
        question=spec([('a',100,110)])
        items=[item(i,0,5) for i in range(1,6)]+[item(6,100,110)]
        metrics=quality.evaluate_stage(question,items)
        self.assertEqual(metrics['evidence_coverage'],1)
        self.assertEqual(metrics['reciprocal_rank_at_5'],0)

    def test_equal_net_coverage_still_reports_loss_and_gain(self):
        question=spec([('a',0,5),('a',10,15)])
        before=quality.evaluate_stage(question,[item(1,0,5)])
        after=quality.evaluate_stage(question,[item(2,10,15)])
        change=quality.transition(before,after)
        self.assertEqual(before['evidence_coverage'],after['evidence_coverage'])
        self.assertEqual(change['lost_groups'],[1])
        self.assertEqual(change['gained_groups'],[2])

    def test_complete_coverage_can_fall_even_when_first_hit_stays_first(self):
        question=spec([('a',0,5),('a',10,15)])
        before=quality.evaluate_stage(question,[item(1,0,5),item(2,10,15)])
        after=quality.evaluate_stage(question,[item(1,0,5)])
        self.assertEqual(before['reciprocal_rank_at_5'],after['reciprocal_rank_at_5'])
        self.assertTrue(quality.transition(before,after)['complete_to_incomplete'])

    def test_budget_retains_whole_items_and_exposes_lost_evidence(self):
        question=spec([('a',10,15),('a',30,35)])
        pool=[item(1,10,15),item(2,30,35)]
        source={'id':'q','question_spec':question,'status':'ok','pool':pool,'scored':pool,'items':pool}
        original=copy.deepcopy(source)
        result=quality.evaluate_record(source,budget_chars=5)
        self.assertEqual(result['stages']['budget_top5']['chunk_ids'],[1])
        self.assertEqual(result['transitions']['mmr_top5->budget_top5']['lost_groups'],[2])
        self.assertEqual(source,original)

    def test_errors_stay_in_denominator_and_negatives_do_not_enter_evidence_metrics(self):
        question=spec([('a',10,15)])
        good={'id':'q','question_spec':question,'status':'ok','pool':[item(1,10,15)],'scored':[item(1,10,15)],'items':[item(1,10,15)]}
        failed={**good,'id':'failed','status':'failed'}
        failed.pop('items')
        failed.pop('scored')
        negative={**good,'id':'negative','question_spec':{'question':'missing','split':'test','expected_behavior':'refuse','evidence_groups':[]}}
        rows=[quality.evaluate_record(r) for r in (good,failed,negative)]
        summary=quality.aggregate(rows)
        final=summary['stages']['budget_top5']
        self.assertEqual(summary['answerable_questions'],2)
        self.assertEqual(summary['excluded_refuse_or_clarify'],1)
        self.assertEqual(final['mean_evidence_coverage'],.5)
        self.assertEqual(final['failed_questions'],1)
        self.assertEqual(final['full_evidence_questions'],1)
        empty=quality.transition(quality.evaluate_stage(question,[]),quality.evaluate_stage(question,[]))
        self.assertEqual(empty['before_hit_groups'],0)

    def test_both_saved_record_formats_and_outputs(self):
        question=spec([('a',0,5)])
        items=[item(1,0,5)]
        old={'id':'q','question_spec':question,'pool':items,'scored_candidates':items,
             'arms':{'C':{'status':'ok','items':items},'D':{'status':'ok','items':items}}}
        with tempfile.TemporaryDirectory() as folder:
            rows,summary=quality.write_outputs(folder,[old])
            self.assertEqual(rows[0]['stages']['mmr_top5']['evidence_coverage'],1)
            self.assertTrue((Path(folder)/'RETRIEVAL_QUALITY.md').exists())
            self.assertEqual(summary['original_500_100']['test']['stages']['candidate_pool']['missing_groups'],0)

    def test_offline_report_replays_and_detects_changed_source(self):
        question=spec([('a',0,5)])
        items=[item(1,0,5)]
        record={'id':'q','question_spec':question,'status':'ok','pool':items,'scored':items,'items':items}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/'source'
            source.mkdir()
            (source/'metadata.json').write_text(json.dumps({'code_hashes':{
                'retrieval/reranker.py':reporter.digest(reporter.ROOT/'retrieval/reranker.py')}}),encoding='utf-8')
            (source/'retrieval.jsonl').write_text(json.dumps(record)+'\n',encoding='utf-8')
            (source/'chunks.json').write_text('{}',encoding='utf-8')
            before=reporter.source_hashes(source)
            output=reporter.create_report(source,root/'report')
            self.assertEqual(reporter.audit(output)['issues'],[])
            self.assertEqual(reporter.source_hashes(source),before)
            (source/'chunks.json').write_text('{"changed":true}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'source_hashes'):
                reporter.audit(output)


if __name__=='__main__':
    unittest.main()
