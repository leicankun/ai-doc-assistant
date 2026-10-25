# 本地 BGE-M3 与百炼 Embedding API 实测

运行状态：complete；开始时间（北京时间）：2026-10-24T16:10:24.704805+08:00。

比较对象为 BGE-M3（本机 RTX 4050、FP16）与 text-embedding-v4（当前百炼 API、1024 维）。模型和部署方式同时不同，不能将差异全部解释为本地或云端部署造成。

## 评测设计

- 固定合成集：既有 60 题、20 份虚构运维文档，48 题可回答；其中 test 为 40 题、32 题可回答。保留既有 dev/test 标签，未根据本次结果调参；这些题曾用于历史评测，不是全新盲测。
- 当前文档：评测前固定 30 题（18 道原创 Markdown、6 道经典文献、6 道 Hello-Agents PDF），均标注明确原文依据。由助手编题，属于探索性样本，没有独立人工评分。
- 当前文档只使用双方均有且与 MySQL 正文一致的 3068 块；排除旧记录和只有一方有的测试分块。合成语料按生产 500＋100 切成相同的 183 块。
- 1024 维、cosine、相同 Chroma HNSW 配置，候选 30、最终 Top-5；混合检索复用相同 BM25，RRF k=60。未启用 rerank、MMR、查询改写、Agent、回答生成或自动评审，以观察 Embedding 本身的影响。
- 每题每方案单条请求重复 3 次，串行执行；题序固定种子打乱，两个方案交替先后。质量只计第一轮，重复请求只增加耗时样本，不将它们当作独立质量样本。
- 使用项目现有调用函数：API 每次建立 SDK 客户端，本地请求常驻模型服务。耗时包含调用开销及网络，不等于模型纯计算时间；这里没有尝试持久连接等额外优化。

## 检索质量

证据覆盖为每题必要证据组的命中比例；组内可替代来源任一命中即可，支持同文档原文区间并集。完整覆盖要求所有必要组都找到。Hit@5 为至少完整命中一组。MRR@5 为前缀首次完整覆盖任意一组的倒数排名。失败按 0 计，不从题数中删除；12 道拒答/澄清题不进入证据指标，也不据召回内容判断其回答行为。这些是原文区间代理指标，不是最终答案准确率，标注可能没有穷尽其他有效出处。

| 数据集 | 检索 | 方案 | 可回答题 | Hit@1 | Hit@5 | 平均证据覆盖@5 | 完整覆盖@5 | MRR@5 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| synthetic_all | dense | api | 48 | 75.00% | 93.75% | 85.07% | 37/48 | 0.83 |
| synthetic_all | dense | local | 48 | 56.25% | 85.42% | 76.39% | 33/48 | 0.69 |
| synthetic_all | hybrid | api | 48 | 75.00% | 100.00% | 94.44% | 43/48 | 0.86 |
| synthetic_all | hybrid | local | 48 | 68.75% | 95.83% | 90.80% | 40/48 | 0.81 |
| synthetic_dev | dense | api | 16 | 87.50% | 100.00% | 93.75% | 14/16 | 0.92 |
| synthetic_dev | dense | local | 16 | 68.75% | 100.00% | 96.88% | 15/16 | 0.82 |
| synthetic_dev | hybrid | api | 16 | 75.00% | 100.00% | 100.00% | 16/16 | 0.88 |
| synthetic_dev | hybrid | local | 16 | 75.00% | 100.00% | 100.00% | 16/16 | 0.88 |
| synthetic_test | dense | api | 32 | 68.75% | 90.62% | 80.73% | 23/32 | 0.78 |
| synthetic_test | dense | local | 32 | 50.00% | 78.12% | 66.15% | 18/32 | 0.62 |
| synthetic_test | hybrid | api | 32 | 75.00% | 100.00% | 91.67% | 27/32 | 0.86 |
| synthetic_test | hybrid | local | 32 | 65.62% | 93.75% | 86.20% | 24/32 | 0.77 |
| live_all | dense | api | 30 | 63.33% | 80.00% | 80.00% | 24/30 | 0.68 |
| live_all | dense | local | 30 | 76.67% | 80.00% | 80.00% | 24/30 | 0.78 |
| live_all | hybrid | api | 30 | 73.33% | 80.00% | 80.00% | 24/30 | 0.77 |
| live_all | hybrid | local | 30 | 76.67% | 80.00% | 80.00% | 24/30 | 0.78 |
| live_markdown | dense | api | 18 | 88.89% | 94.44% | 94.44% | 17/18 | 0.90 |
| live_markdown | dense | local | 18 | 100.00% | 100.00% | 100.00% | 18/18 | 1.00 |
| live_markdown | hybrid | api | 18 | 94.44% | 100.00% | 100.00% | 18/18 | 0.97 |
| live_markdown | hybrid | local | 18 | 100.00% | 100.00% | 100.00% | 18/18 | 1.00 |
| live_classics | dense | api | 6 | 50.00% | 66.67% | 66.67% | 4/6 | 0.53 |
| live_classics | dense | local | 6 | 50.00% | 50.00% | 50.00% | 3/6 | 0.50 |
| live_classics | hybrid | api | 6 | 33.33% | 50.00% | 50.00% | 3/6 | 0.42 |
| live_classics | hybrid | local | 6 | 33.33% | 50.00% | 50.00% | 3/6 | 0.42 |
| live_pdf | dense | api | 6 | 0.00% | 50.00% | 50.00% | 3/6 | 0.17 |
| live_pdf | dense | local | 6 | 33.33% | 50.00% | 50.00% | 3/6 | 0.42 |
| live_pdf | hybrid | api | 6 | 50.00% | 50.00% | 50.00% | 3/6 | 0.50 |
| live_pdf | hybrid | local | 6 | 50.00% | 50.00% | 50.00% | 3/6 | 0.50 |

## 单条查询耗时

单位毫秒；P95 使用 NumPy 线性百分位。预热调用不进入下表，失败次数单列，分位数只看成功请求。dense 组件和包含 Embedding 与 Chroma；hybrid 组件和额外加上共享 BM25 和融合耗时，不是两个独立 HTTP 端到端接口测量，不包括数据库校验、回答生成或 SSE。

| 方案 | 成功/尝试 | Embedding P50 | Embedding P95 | dense组件和 P50 | hybrid组件和 P50 |
|---|---:|---:|---:|---:|---:|
| api | 270/270 | 1959.50 | 3812.78 | 1961.00 | 1987.74 |
| local | 270/270 | 48.34 | 74.60 | 50.13 | 55.11 |

## 文档编码、资源和调用量

文档批量使用相同的 40 个真实分块（按字符长度分位取样），每批固定 4 条、串行请求。该配置控制单次输入数量，并非各服务最优吞吐配置；不代表冷启动或并发吞吐。

| 方案 | 文本数 | 总字符 | 编码秒数 | 文本/秒 | API或本地服务报告token |
|---|---:|---:|---:|---:|---:|
| api | 40 | 11772 | 21.01 | 1.90 | 5047 |
| local | 40 | 11772 | 0.99 | 40.29 | 5396 |

GPU 采样：694 次；整张显卡显存范围 2478～3223 MiB。包含桌面及其他应用，且 API 测试时本地模型仍常驻，不能当成两种方案各自的显存占用。

API 按供应商报告的 token 统计，不用未经核对的地域单价估算人民币；本地没有供应商调用费，但本次未测电费或硬件摊销。两种模型的 tokenizer 不同，其 token 数不能直接当成统一文本量。

```json
{
  "api": {
    "logical_calls": 328,
    "failed": 0,
    "reported_total_tokens": 19023,
    "by_phase": {
      "document_batch": {
        "calls": 10,
        "reported_total_tokens": 5047
      },
      "query": {
        "calls": 270,
        "reported_total_tokens": 5523
      },
      "synthetic_index": {
        "calls": 46,
        "reported_total_tokens": 8437
      },
      "warmup": {
        "calls": 2,
        "reported_total_tokens": 16
      }
    }
  },
  "local": {
    "logical_calls": 328,
    "failed": 0,
    "reported_total_tokens": 19116,
    "by_phase": {
      "document_batch": {
        "calls": 10,
        "reported_total_tokens": 5396
      },
      "query": {
        "calls": 270,
        "reported_total_tokens": 5550
      },
      "synthetic_index": {
        "calls": 46,
        "reported_total_tokens": 8149
      },
      "warmup": {
        "calls": 2,
        "reported_total_tokens": 21
      }
    }
  }
}
```

## 数据保护与局限

生产逻辑指纹（MySQL、上传文件、原 SQLite、两套集合正文/元数据/向量）前后一致：True。评测索引保存在本结果目录，未改生产配置或生产向量。

- 主结论应分别看固定 test 集和当前文档探索集，避免混合语料掩盖差异；样本规模有限，没有据此估计真实用户总体准确率。经典文献与 PDF 在生产中分块较长，未在评测中重新切分。
- 单机、单次运行、低并发、已加载 GPU 模型；网络、桌面 GPU 负载和服务商负载会影响耗时。未测 CPU、本地冷启动、大批量最大吞吐、完整 RAG 回答质量或长时间稳定性。
- 第一轮逐题排名见 results.jsonl，所有真实调用及 usage 见 calls.jsonl。数据和代码快照、向量及 SHA256 均保存在本目录，可使用 --audit 做只读复算。

## 两方案出现差异的题目（第一轮、完整证据覆盖）

- r008 / hybrid：Atlas 2.4 editor 可以上传和删除吗？；api=1.00，local=0.00。
- r017 / dense：Atlas 2.4 普通运行记录和操作审计记录分别过多久清理？；api=1.00，local=0.00。
- r018 / dense：Atlas 新版编辑角色能放入资料吗，有资格移除资料吗？；api=1.00，local=0.00。
- r025 / hybrid：Atlas 2.4 E201 要查什么，数据库版本要求及修复后的切流条件是什么？；api=1.00，local=0.67。
- r026 / dense：Atlas 2.4 备份如何暂停和恢复写入，恢复时的快照与校验要求是什么？；api=1.00，local=0.00。
- r029 / dense：Aster 4.2 入库失败后如何重试，以及恢复检索前需要哪些校验？；api=0.50，local=1.00。
- r030 / dense：Aster 4.2 Windows 的启动命令、数据目录，以及修改日志后是否重启？；api=1.00，local=0.00。
- r032 / dense：Orion 3.0 过期缓存重建步骤与满回源队列后的处理是什么？；api=0.50，local=1.00。
- r035 / dense：Aster 4.2 重新切块后索引什么时候才可用，向量维度和模型要求是什么？；api=1.00，local=0.50。
- r036 / dense：Aster 4.2 检索如何限制知识库范围，contributor 可以删除吗？；api=1.00，local=0.50。
- r045 / hybrid：Aster 2.0 与 4.2 的向量维度和知识库文档上限分别是什么？；api=1.00，local=0.75。
- l016 / dense：要求比较三篇文章时，全库 Top-K 是否能保证三篇都有结果，应该怎样处理？；api=0.00，local=1.00。
- l024 / dense：《中庸》提出的博学到笃行之间，完整的五个学习环节是什么？；api=1.00，local=0.00。
