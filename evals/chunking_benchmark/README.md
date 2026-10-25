# 300＋60 / 500＋100 / 800＋160 分块参数对照

核心指标新增 [分阶段质量复算报告](../results/20261021T061728Z-retrieval-quality-v1/RETRIEVAL_QUALITY.md)，覆盖候选池、重排、MMR和预算筛选的丢失/补回。后续真实运行会自动生成同类 `RETRIEVAL_QUALITY.md`，指标口径见 [说明](../retrieval_quality/README.md)。

已完成真实运行：[评测报告](../results/20261021T055048Z-chunking-v1/REPORT.md)、[逐题复核](../results/20261021T055048Z-chunking-v1/REVIEW_NOTES.md)。240 次检索、80 次回答；建议暂时保留当前 `500＋100`，生产参数未改。

比较当前按标题、段落、句子优先切分的算法在三个参数下的效果，基线为当前 `500＋100`。不改生产分块参数，不修改正式数据库或向量库。

## 为什么分两类语料

既有 `retrieval_benchmark` 的 20 份合成 Markdown 被标题分成短小节。三组参数均生成完全相同的 183 块，最长只有 178 字符。仅在这套结构上跑三遍，无法测出分块参数的作用。

因此保留两个独立口径：

1. **原始结构核验**：三组分块逐块一致，共用一次真实检索和回答运行，明确记录三组指向同一个结果；不把它算作三次独立实测。
2. **同事实长章节对照**：只把 `## 小节标题` 变成普通段落标签，保留文档一级标题、全部正文、事实顺序、问题和标准答案。根据保留的原文重新计算证据区间。三组分别生成 54 / 38 / 22 块，平均长度约 244 / 371 / 591 字符。

原始结构与长章节结构分别报告。跨结构差异不能直接归因为 chunk_size。长章节版本仍来自这批短运维手册，不代表长 PDF、代码仓库或真实企业文档总体。

## 控制变量

- 固定原有 60 题、20 份文档、dev/test 标签和预选的 20 题回答检查子集。不根据本次结果改参数；已有 test 集曾用于历史评测，不宣称它是全新未知验收集。
- 固定 Hybrid、RRF、候选池 30、保守清理、同一 Rerank 模型、MMR lambda=0.8、Top-5、同一回答模型和提示词。
- 实际模型名、调用 usage、失败、耗时及代码哈希保存在结果目录。不替换模型输出，不用随机或模拟向量运行正式评测。
- 相同文本的真实 Embedding 结果缓存复用；不同参数共享同一问题向量。检索计时不包含共用查询向量的 API 延迟，索引调用单独记录。
- 以临时 SQLite 和独立内存 Chroma 执行。正式 MySQL 全表内容、旧 SQLite、上传文件、持久化 Chroma 文件均在运行前后核对指纹。

## 指标

- 必要证据组平均覆盖率、全部证据覆盖题数、MRR@5、候选池覆盖。证据按原文区间并集计算，支持跨相邻分块拼接。
- Top-5 总字符数、同一文档原文区间重叠比例。
- **相同 2500 原文字符预算下的证据覆盖**：按最终排名保留可装入的完整片段，不截断正文，也不另外补充 Top-5 以外的片段。这是检索辅助指标；实际回答仍使用生产 Top-5。
- 固定 20 题的请求成功、答案点覆盖、任务通过、严格通过、真实输入 token 用量和引用身份。
- 同模型自动评分仅作辅助。原始模型输出保留在调用记录中，需结合原文复核；确定性审计不等于独立人工语义评分。

## 运行

```powershell
# 离线结构检查，无模型调用
.\.venv\Scripts\python.exe evals/run_chunking_benchmark.py --validate-only

# 正式真实服务评测，会产生模型调用费用
.\.venv\Scripts\python.exe evals/run_chunking_benchmark.py --direct --workers 3

# 只读复算布局、指标、MMR 和引用身份
.\.venv\Scripts\python.exe evals/audit_chunking_benchmark.py evals/results/<运行目录>

# 离线评测器测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_chunking_benchmark.py
```

`--direct` 只对评测进程禁用环境代理，不改系统代理。`--limit N` / `--skip-answers` 仅供预检，不将部分结果冒充完整评测。结果目录保存 REPORT、summary、metadata、全部分块和向量、检索与回答轨迹、原始调用输出、语料与代码快照。
