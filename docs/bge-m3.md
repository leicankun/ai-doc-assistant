# 本地 BGE-M3

保留百炼 `text-embedding-v4` 和原集合 `document_chunks_v4_1024`。BGE-M3 使用独立环境
`.venv-bge`、本地服务 `http://127.0.0.1:8001/v1/embeddings` 和独立集合
`document_chunks_bge_m3_1024`，集合仍持久化在 `chroma_data/`。

主应用的上传、RAG、Agent 和前端目前继续使用百炼。本地索引和检索通过下面的命令显式调用，
用于后续对比。不会修改 MySQL 中属于原 API 索引的 `indexed_version`。

## 安装和下载

在项目根目录运行 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-bge
.\.venv-bge\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.\.venv-bge\Scripts\python.exe -m pip install -r requirements-bge.txt
.\.venv\Scripts\python.exe scripts/local_bge.py download
```

模型：[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3)，固定 revision
`5617a9f61b028005a4858fdac845db406aefb181`。下载时校验原始权重 SHA256，权重和分词器存于
`models/bge-m3/`；推理仅从本地路径加载，禁用联网取模型和远程代码。使用模型原有的 CLS pooling
和归一化，生成 1024 维 dense 向量。没有启用 BGE 的 sparse 或 ColBERT 分支。

本机为 RTX 4050 Laptop 6GB，配置使用 CUDA、FP16 和小批量。CPU 部署可将 `.env` 中
`BGE_DEVICE` 改为 `cpu`；CPU 环境可按 PyTorch 官方说明安装 CPU 版本。推理依赖与主项目隔离。

## 启动、查看与停止

```powershell
.\.venv\Scripts\python.exe scripts/local_bge.py start
.\.venv\Scripts\python.exe scripts/local_bge.py status
.\.venv\Scripts\python.exe scripts/local_bge.py stop
```

启动脚本将服务放到隐藏后台进程，模型加载和预热后报告 ready。服务只监听 `127.0.0.1`。
日志位于 `.local_bge/server.log`；停止命令核对本实例标识并使用本地管理凭据。
模型文件、环境和运行目录均已加入 Git 忽略。电脑重启后需要重新运行 `start`。

`.env` 可设置：

```dotenv
BGE_MODEL_DIR=models/bge-m3
BGE_DEVICE=cuda
BGE_BATCH_SIZE=4
BGE_PORT=8001
BGE_BASE_URL=http://127.0.0.1:8001/v1
```

改变端口时同步修改 `BGE_PORT` 和 `BGE_BASE_URL`。更改服务配置需先 stop 再 start。
接口接受 OpenAI 风格的 `model`、`input`、`dimensions`、`encoding_format` 参数；固定模型、
1024 维和 float 编码。单次最多 32 条文本；超过每条 8192 token 的输入返回错误，不静默截断。
同一服务的 GPU 推理串行执行，降低并发导致显存不足的可能性。

## 创建本地文档索引与检索

先确保 MySQL 和 BGE 服务已启动：

```powershell
.\.venv\Scripts\python.exe scripts/local_mysql.py start
.\.venv\Scripts\python.exe scripts/local_bge.py index
.\.venv\Scripts\python.exe scripts/local_bge.py search --question '上传文档之后如何生成向量索引？' --top-k 5

# 也可以只处理或检索指定文档
.\.venv\Scripts\python.exe scripts/local_bge.py index --document-id 11
.\.venv\Scripts\python.exe scripts/local_bge.py search --document-id 11 --question '上传后的处理流程是什么？'
```

读取 MySQL 中已有的分块，不重新分块、不调用百炼。每批成功后持久化，重复运行会复用分块版本、
正文哈希和来源元数据均未变化的向量，可以接着完成中断的索引。搜索前校验当前文档所有分块的 ID、
版本和内容哈希；缺失、部分完成或过期时拒绝检索，避免混用旧向量。

本地集合是手动更新的对比快照：新增文档、重新分块或删除文档后，重新运行 `index` 同步。
搜索范围只包含 MySQL 中仍存在的文档，`index` 也会清除已删除文档残留的本地快照。
原 API 的文档删除流程目前不自动维护这个实验集合。向量距离用于排序，不能当成正确概率。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_local_bge.py -v
.\.venv\Scripts\python.exe scripts/verify_local_bge.py
```

离线检查包括原 API 索引状态不被改写、重复运行复用、内容变化失效、半成品索引拒绝查询和已删除文档排除。
第二条命令实际调用本地服务，验证维度、归一化、相关文本排序、错误模型拒绝及所有当前分块的索引完整性，
报告写入 `artifacts/bge-deployment.json`。如果存在部署前保存的 `.local_bge/api-baseline.json`，
还会核对原 API 集合记录和 MySQL 索引状态；日常新增或修改文档后，该旧基线会自然失效。
