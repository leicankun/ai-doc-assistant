# 本地 BGE-M3 与百炼 API 对照

比较实际部署的 `BAAI/bge-m3`（CUDA FP16）与 `text-embedding-v4` API，两者均使用 1024 维。
模型和运行位置同时不同，结果不代表部署位置的单独因果效应。

已完成的真实评测：[结果解读与原文复核](../results/20261024T081024Z-embedding-comparison/REVIEW_NOTES.md)、
[完整报告](../results/20261024T081024Z-embedding-comparison/REPORT.md)。

## 数据与口径

- 复用既有 `retrieval_benchmark` 的 60 题和固定 dev/test 标签；48 题有必要证据，12 题拒答或澄清不参与证据分母。
- `live_questions.json` 在模型调用前固定 30 道当前资料问题及原文依据：18 道原创测试 Markdown、6 道经典文献、6 道 Hello-Agents PDF。
- PDF 的兼容汉字、换行和空格仅在查找标注出处时规范化；存储和检索仍使用原始分块正文，评分使用映射回原文的字符区间。
- 当前文档只使用两套生产索引均存在、且内容与 MySQL 一致的共同分块；保留部署时已有的两套向量，不覆盖生产索引。
- 合成 20 份文档使用生产 `500＋100` 切分，分别重新请求两个模型生成真实向量；两套评测索引都写在独立结果目录。
- cosine、相同 HNSW 设置、候选30、Top-5，分别比较 dense 和 BM25＋dense 的 RRF；BM25 排名在两方案间共享，RRF k=60。
- 关闭与本次目标无关的 rerank、MMR、查询改写、Agent 和回答模型；不把检索指标当成答案准确率。
- 查询串行，种子固定，两个后端交替先后；默认每题每方案3次，质量只计第一轮，耗时统计全部成功请求并单列失败。
- 固定40个真实分块，按长度分位取样；每批4条测文档编码，控制输入大小，不宣称最大吞吐。
- 使用现有 `embed_texts` 与 `embed_local_texts` 函数，包含实际客户端和网络开销。API当前每次创建 SDK 客户端；模型服务已预热，未包含本地冷启动。

证据组允许替代出处，按原文区间并集判断。统计 Hit@1/5、必要证据组平均覆盖、完整覆盖题数和 MRR@5。
失败计0；拒答/澄清题没有证据 gold，不因为召回文本就判错。源码与题目同由助手维护，新增30题属于探索性样本，没有独立人工标注。

## 运行

```powershell
# 检查题目、依据、共同分块；不请求 Embedding API
.\.venv\Scripts\python.exe evals/run_embedding_comparison.py --validate-only

# 先启动项目 MySQL 和本地 BGE 服务，再进行真实模型评测（产生 API 费用）
.\.venv\Scripts\python.exe scripts/local_mysql.py start
.\.venv\Scripts\python.exe scripts/local_bge.py start
.\.venv\Scripts\python.exe evals/run_embedding_comparison.py --rounds 3

# 只读核对产物哈希，重算所有逐题证据指标与汇总；不请求模型或生产数据库
.\.venv\Scripts\python.exe evals/run_embedding_comparison.py --audit evals/results/<运行目录>

# 离线精确余弦核对，与实际 Chroma Top-5 比较；不请求模型
.\.venv\Scripts\python.exe evals/audit_embedding_vectors.py evals/results/<运行目录>

# 主实验结束后才能运行的10题补充延迟实验（额外21次API调用）
.\.venv\Scripts\python.exe evals/probe_embedding_connections.py evals/results/<运行目录>
.\.venv\Scripts\python.exe evals/report_embedding_comparison.py evals/results/<运行目录>

# 离线单元测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_embedding_comparison.py -v
```

`--direct` 仅让当前评测进程绕过环境代理，记录在 metadata 中，不改系统代理。
结果目录包括 REPORT、summary、metadata、真实调用记录、逐题排名、分块和题目快照、原始向量、GPU采样及源码快照。
运行前后核对 MySQL 各表、上传文件、原 SQLite 和两套生产集合的逻辑指纹；不改生产配置。

GPU 数值为整张显卡读数，包含桌面和其他应用；API测时本地模型仍常驻，因此不把GPU采样当成每种方案各自的资源占用。
API只报告实际 token usage，不使用未经核对的地域单价估算金额；未测硬件摊销、电费、CPU推理、高并发和完整回答质量。
