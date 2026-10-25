"""Scoring and quote mapping tests; no model or production database calls."""
import unittest
from evals.embedding_comparison import quote_span, score_question, aggregate


class ComparisonTests(unittest.TestCase):
    def test_pdf_normalization_maps_back_to_original_offsets(self):
        text = '前缀：⼤语\n⾔模型（ LLM ）的应⽤。后缀'
        span = quote_span(text, '大语言模型(LLM)的应用。')
        self.assertIsNotNone(span)
        self.assertEqual(text[slice(*span)], '⼤语\n⾔模型（ LLM ）的应⽤。')
        self.assertIsNone(quote_span(text, '另一个概念'))

    def test_failures_remain_in_metric_denominator(self):
        spec = {'expected_behavior': 'answer', 'evidence_groups': [
            {'alternatives': [{'document': 'a', 'start_char': 0, 'end_char': 5}]}]}
        item = {'document': 'a', 'start_char': 0, 'end_char': 5, 'chunk_id': 1, 'content': 'hello'}
        summary = aggregate([score_question(spec, [item]), score_question(spec, [], False)])
        self.assertEqual(summary['answerable_questions'], 2)
        self.assertEqual(summary['failed_questions'], 1)
        self.assertEqual(summary['hit_at_5'], .5)
        self.assertEqual(summary['mrr_at_5'], .5)

    def test_refusal_is_excluded_and_multihop_requires_all_groups(self):
        spec = {'expected_behavior': 'answer', 'evidence_groups': [
            {'alternatives': [{'document': 'a', 'start_char': 0, 'end_char': 5}]},
            {'alternatives': [{'document': 'b', 'start_char': 0, 'end_char': 5}]}]}
        item = {'document': 'a', 'start_char': 0, 'end_char': 5, 'chunk_id': 1, 'content': 'hello'}
        partial = score_question(spec, [item])
        refusal = score_question({'expected_behavior': 'refuse', 'evidence_groups': []}, [item])
        summary = aggregate([partial, refusal])
        self.assertEqual(summary['questions'], 2)
        self.assertEqual(summary['answerable_questions'], 1)
        self.assertEqual(summary['evidence_coverage_at_5'], .5)
        self.assertEqual(summary['full_evidence_at_5'], 0)

    def test_reciprocal_rank_uses_first_complete_group(self):
        spec = {'expected_behavior': 'answer', 'evidence_groups': [
            {'alternatives': [{'document': 'a', 'start_char': 0, 'end_char': 10}]}]}
        items = [
            {'document': 'a', 'start_char': 0, 'end_char': 5, 'chunk_id': 1, 'content': 'hello'},
            {'document': 'a', 'start_char': 5, 'end_char': 10, 'chunk_id': 2, 'content': 'world'},
        ]
        result = score_question(spec, items)
        self.assertEqual(result['reciprocal_rank_at_5'], .5)
        self.assertEqual(result['hit_at_1'], False)
        self.assertEqual(result['hit_at_5'], True)


if __name__ == '__main__':
    unittest.main()
