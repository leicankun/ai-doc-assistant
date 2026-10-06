import unittest
from chunking import split_text_with_offsets, split_text


class ChunkingTests(unittest.TestCase):
    def check(self, text, size, overlap):
        chunks = split_text_with_offsets(text, size, overlap)
        covered = set()
        for chunk in chunks:
            a, b = chunk['start_char'], chunk['end_char']
            self.assertEqual(chunk['content'], text[a:b])
            self.assertLessEqual(b-a, size)
            covered.update(range(a, b))
        self.assertTrue(all(i in covered for i,c in enumerate(text) if not c.isspace()))
        for left, right in zip(chunks, chunks[1:]):
            self.assertGreater(right['start_char'], left['start_char'])
            self.assertLessEqual(left['end_char']-right['start_char'], overlap)
        return chunks

    def test_headings_and_offsets(self):
        text = '# 第一节\r\n第一节内容。\r\n\r\n## 第二节\r\n第二节内容。'
        chunks = self.check(text, 100, 20)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[1]['content'].startswith('## 第二节'))
        self.assertEqual(chunks[0]['end_char'], chunks[1]['start_char'])

    def test_paragraph_priority_and_sentence_fallback(self):
        text = '甲'*15 + '。\n\n' + '乙'*25 + '。'
        chunks = self.check(text, 30, 0)
        self.assertEqual(chunks[0]['content'], '甲'*15+'。\n\n')
        text = '第一句话。第二句话。第三句话。第四句话。'
        chunks = self.check(text, 12, 5)
        self.assertTrue(all(c['content'].endswith('。') for c in chunks))

    def test_long_units_fences_urls_and_inline_code(self):
        self.check('甲'*1000, 50, 10)
        text = '# 章节\n```python\n# 不是标题\n\nprint("hello!")\n```\n正文。'
        self.assertEqual(len(self.check(text, 200, 20)), 1)
        self.check('网址 https://example.com/a.b 版本 1.23。`a!b` 后续文本。'*20, 50, 10)

    def test_empty_validation_and_compatibility(self):
        self.assertEqual(split_text_with_offsets(''), [])
        self.assertEqual(split_text_with_offsets(' \n\t'), [])
        for size, overlap in [(0, 0), (5, -1), (5, 5)]:
            with self.assertRaises(ValueError):
                split_text_with_offsets('abc', size, overlap)
        self.assertEqual(split_text('短文。'), ['短文。'])

    def test_many_sizes_no_loss_or_infinite_loop(self):
        text = '# 标题\n短句。\n\n' + '长'*80 + '。\n\n## 次节\n结尾。'
        for size in [1, 2, 7, 20, 50]:
            for overlap in [0, size-1]:
                self.check(text, size, overlap)
