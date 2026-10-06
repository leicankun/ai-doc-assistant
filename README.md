# 技术文档问答助手

基于 FastAPI 和 Vue 的文档信息管理练习项目，已实现文档信息的创建、查询、修改和删除，使用 SQLite 持久化保存数据，支持 Markdown 和文字型 PDF 文件上传、正文读取和基于 DeepSeek 的单篇文档问答。

## 环境准备（Windows PowerShell）

在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 配置 DeepSeek

在项目根目录创建 `.env`（已被 Git 忽略）：

```dotenv
DEEPSEEK_API_KEY=填写你的密钥
DEEPSEEK_MODEL=deepseek-flash
```

密钥只保存在后端。每次提问会将选中文档的全文、当前会话最近 5 轮问答和本次问题发送给 DeepSeek。修改配置后重启后端。

## 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

终端出现 `Application startup complete.` 后访问：

- 接口文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

保持服务终端运行，在接口文档中使用 Try it out 和 Execute 发送请求。

## 启动前端

保持后端服务运行，另开一个 PowerShell 终端，在项目目录执行：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

打开终端显示的前端地址（通常是 http://localhost:5173）。前端会请求 `http://127.0.0.1:8000/documents`；后端允许来自 `localhost:5173` 和 `127.0.0.1:5173` 的跨域请求。Node.js 版本需满足 `frontend/package.json` 中的 `engines` 要求。

页面会在打开时加载文档列表；可以新增、编辑、取消编辑、删除文档，也可以手动刷新列表。提交成功后会重新读取列表。新增、编辑和删除会修改 SQLite 中的数据；取消编辑不会发送请求。

删除文档会同步清理上传原文件、SQLite 分块、Chroma 中该文档的全部向量（含旧分块向量）、全文问答历史、RAG 历史和文档会话，不影响其他文档。原文件或向量已缺失时仍可删除。清理失败返回错误并回滚 SQLite，文档记录保留以便重试；文件系统和 Chroma 不参与 SQLite 事务，已经完成的外部清理不会回滚。删除选中的文档后，前端会清空对应全文问答及编辑状态。

文档列表和检索面板显示处理状态及分块数。列表/详情接口新增 `chunk_count`、`chunk_version`、`indexed_version`、`processing_status` 和 `processing_status_label`。无分块为“未分块”；有分块、尚无成功索引版本为“待入库”；两个版本相同为“已入库”；版本不同或索引写入中断为“索引过期”。重新分块时版本递增，向量写入、旧记录清理及校验成功后才记录索引版本。过期或待入库的 dense/hybrid 检索（含 RAG）返回 409，BM25 仍可读取当前分块。前端分块/入库后自动刷新状态。

启动后端时会自动为旧数据库补充版本字段，保留原数据；已有分块但没有版本记录的文档保守显示“待入库”，需重新入库一次。状态记录处理流程，不会在每次列表查询时扫描 Chroma；若手动删除/修改 Chroma 或上传文件，请重新生成分块并入库恢复一致性。

检查前端构建：

```powershell
cd frontend
npm.cmd run build
```

## 上传和问答

1. 同时启动前后端，打开 Vue 页面。
2. 在上传区选择 UTF-8 编码的 `.md` 或文字型 `.pdf` 文件，点击上传。
3. 在文档记录旁点击“提问”，选择已有会话或输入名称创建会话，再输入问题。
4. 点击“发送问题”，等待回答；成功后刷新当前会话历史，失败会显示错误提示。
5. 切换会话只展示该会话的记录；刷新页面后重新选择文档和会话可继续查看历史。

可上传写有“数据库使用 SQLite”的测试文档，询问数据库类型，再问文档未提及的开发者信息。后者应提示“文档中未提供”，模型回答仍需核对原文。

## 项目结构

```text
main.py                 创建 FastAPI 应用、注册文档路由和健康检查
config.py               项目根目录、SQLite、上传文件和 Chroma 路径
schemas.py              文档、会话、问答和搜索请求校验
database.py            初始化数据库、提供连接依赖
routers/__init__.py     路由包标记
routers/documents.py    聚合各业务路由，供 main.py 注册
routers/document_management.py  文档增删改查、上传和正文读取
routers/chunks.py       文档分块生成和读取
routers/search.py       BM25/dense/hybrid 检索和向量入库
routers/sessions.py     会话创建、列表和问答历史
routers/question_answering.py   全文普通问答和流式问答
routers/rag.py          单轮带引用的 RAG 问答
llm.py                  DeepSeek 调用封装
chunking.py             文本分块及字符位置
bm25.py                 中文分词、词频统计、BM25 排序
embedding.py            百炼向量调用，分批处理及结果校验
vector_store.py         本地 Chroma 集合配置
indexing.py             读取 SQLite 分块、生成向量并更新 Chroma
tests/test_indexing.py  入库、清理、隔离和失败处理回归测试
tests/test_retrieval.py  隔离数据库及模拟模型的回归测试
practice/practice_llm.py         独立模型调用练习
frontend/               Vue 前端及其独立的 package.json
practice/practice_db.py          sqlite3 插入与查询练习
practice/practice_orm.py         SQLAlchemy 会话、消息和 JOIN 练习
requirements.txt        当前项目虚拟环境的依赖版本
```

## 连接管理与路由组织

- 访问数据库的文档接口通过 Depends(get_connection) 获取独立的 SQLite 连接。
- get_connection 设置 sqlite3.Row，通过 yield 提供连接，并在 finally 中关闭连接。
- 创建、修改和删除接口仍自行调用 commit()；连接依赖不自动提交。
- 同步依赖配置 check_same_thread=False，以适应框架线程调度；不跨请求共享连接。
- APIRouter 使用 /documents 前缀，在接口文档中归入“文档”分组。
- main.py 通过 include_router 注册文档接口，schemas.py 独立负责输入校验。
- config.py 根据自身所在目录计算数据库绝对路径，避免依赖终端的当前目录。

## 当前接口

| 方法 | 路径 | 用途 | 成功状态码 |
| --- | --- | --- | --- |
| GET | /health | 检查服务状态 | 200 |
| GET | /documents | 查询全部文档信息，返回 items 和 total | 200 |
| POST | /documents | 创建文档信息，由 SQLite 分配 ID | 201 |
| GET | /documents/{document_id} | 根据 ID 查询单条文档 | 200 |
| PUT | /documents/{document_id} | 更新指定文档的标题和类型，保留 ID | 200 |
| DELETE | /documents/{document_id} | 删除文档，返回提示和被删除文档的 ID | 200 |
| POST | /documents/upload | 上传 Markdown / PDF 并关联数据库 | 201 |
| GET | /documents/{document_id}/content | 读取文档正文 | 200 |
| POST | /documents/{document_id}/ask | 根据文档回答问题 | 200 |

上传接口使用 `multipart/form-data`，字段名为 `file`。提问接口的 JSON 请求体示例为 `{"session_id":1,"question":"项目使用什么数据库？"}`，返回 `document_id`、`session_id`、`question` 和 `answer`。问题去除首尾空白后长度需为 1—1000 个字符；文档不得为空且最多 20000 个字符。

## 请求字段与校验

POST 和 PUT 均须提交以下 JSON 请求体：

```json
{
  "title": "Python 学习笔记",
  "file_type": "markdown"
}
```

- `title`：必填字符串。先去除首尾空白，再校验长度为 1—100 个字符。空字符串和纯空白标题会被拒绝。
- `file_type`：必填，只允许 `markdown` 或 `pdf`，区分大小写。
- `document_id`：路径参数，必须能解析为整数。
- PUT 需要同时提供标题和文件类型。

## 错误响应

- `404`：请求数据合法，但指定文档不存在。响应示例：`{"detail":"文档不存在"}`。
- `422`：请求数据校验失败，例如标题为空、类型不支持、缺少必填字段或 ID 不是整数。

模型问答额外返回：`502`（模型调用失败或空回答）、`503`（未配置密钥）、`504`（模型调用超时）。文档未关联文件或磁盘文件丢失时返回 `404`。

## 手动验证流程

1. POST 创建一条测试文档，记下返回的 ID，确认状态码为 201。
2. GET 按 ID 查询，确认内容一致。
3. PUT 修改标题和类型，再 GET 确认修改已生效且 ID 不变。
4. DELETE 删除，再 GET 查询，确认返回 404。
5. 使用合法请求体 PUT 修改已删除的文档，以及再次 DELETE，均应返回 404。
6. 再创建文档，确认 ID 不与当前已有记录重复。
7. 测试空标题、纯空格标题、不支持的类型和缺失字段，确认返回 422。
8. 修改后重启服务，确认记录的 ID 和内容保留；删除后重启服务，确认该记录仍返回 404。

## 数据存储

- 文档接口使用 Python 自带的 sqlite3；独立 ORM 练习使用 SQLAlchemy，安装 requirements.txt 时一并安装。
- 数据库文件由 config.py 的 DB_PATH 指定，位于项目目录下，名称为 documents.db，包含 documents 表及 id、title、file_type、stored_name 四列。
- 服务加载时调用 init_db()，创建不存在的数据库和表，并为旧表补充 stored_name 字段，不清空已有记录。
- 创建、修改和删除操作提交到数据库，服务重启或自动重载后仍然保留。
- id 为 INTEGER PRIMARY KEY，由 SQLite 在插入时自动分配；删除后的编号可能被复用。
- 本地数据库及事务日志由 .gitignore 忽略，仓库保留建表代码。

文件正文保存在 `uploads/`，通过数据库中的 `stored_name` 关联；旧的纯信息记录该字段为 NULL。上传文件目录已被 Git 忽略。

## 数据库练习

单独初始化数据库：

```powershell
.\.venv\Scripts\python.exe database.py
```

运行插入和查询练习：

```powershell
.\.venv\Scripts\python.exe practice/practice_db.py
```

每次运行 practice/practice_db.py 都会向同一个数据库新增一条练习记录，并查询表中全部记录。

## SQLAlchemy 练习

```powershell
.\.venv\Scripts\python.exe practice/practice_orm.py
```

使用同一个 documents.db，创建尚不存在的 chat_sessions 和 chat_messages 表。
每次运行新增一个聊天会话和两条消息，通过 session_id 外键关联，再用 JOIN 查询本次会话的标题、角色和消息内容。
引擎在新连接建立时开启 SQLite 外键检查，echo=True 会打印实际执行的 SQL。
聊天会话和消息目前仅用于独立脚本练习，尚未提供对应的 HTTP 接口。

## 依赖维护

requirements.txt 记录项目虚拟环境中的依赖版本，包含 FastAPI、Uvicorn、Pydantic、SQLAlchemy、OpenAI SDK、python-dotenv、python-multipart 及其依赖。OpenAI SDK 在本项目中用于调用 DeepSeek。
SQLite 的 sqlite3 和 pathlib 属于 Python 标准库，不需要用 pip 安装。

## 当前限制

- 文件上传和问答支持 UTF-8 Markdown 与文字型 PDF；不支持加密 PDF 和纯图片扫描件。
- 问答为单篇短文档全文问答，支持当前会话最近 5 轮上下文；已支持流式回答；另有独立的单轮非流式 RAG 接口，见下文。更早的记录仍可查看，但不会发送给模型。
- 当前删除接口只删除数据库记录，尚未同步删除磁盘文件。


## 文档会话管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /documents/{document_id}/sessions | 创建会话，返回 201 |
| GET | /documents/{document_id}/sessions | 查询会话列表，较新的在前 |
| GET | /documents/{document_id}/sessions/{session_id}/history | 查询指定会话的历史，按 ID 升序 |
| GET | /documents/{document_id}/history | 查询整篇文档的历史，包括未关联会话的旧记录 |

创建会话的请求体为 `{"title":"基础概念复习"}`。名称去掉首尾空白后需为 1—100 个字符。列表接口返回 `items` 和 `total`。

提问请求现在必须携带大于 0 的 `session_id`。后端先检查会话是否属于当前文档，不匹配返回 404；缺少或非法会话编号返回 422。成功回答写入 `question_answers`，记录文档、会话、问题、回答和创建时间。

`document_sessions` 保存会话信息。初始化数据库时自动为旧的 `question_answers` 表补充可空的 `session_id`；原记录保留为 NULL，通过文档历史接口可查看。时间在数据库中保存为 UTC，页面转换为本地时间。会话历史用于保存、展示和多轮问答上下文。

验收：同一文档创建 A、B 两个会话，分别提问并切换查看；每个会话只显示自己的记录。重新打开页面并选择会话后，记录仍可读取。未选择会话时页面禁用提问。

## 文档分块接口

`chunking.py` 的 `split_text_with_offsets()` 返回正文及字符位置，`split_text()` 保留纯文本列表接口供练习使用。每块最多 500 个字符，相邻窗口重叠 100 个字符，跳过纯空白片段。PDF 按页分块，不跨页拼接。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /documents/{document_id}/chunks | 无需请求体，重新生成并保存当前文档的分块，返回 document_id、total、message |
| GET | /documents/{document_id}/chunks | 按 chunk_index 升序查询，返回 document_id、items、total |

`document_chunks` 保存 id、document_id、chunk_index、content、source_name、page_number、start_char、end_char。查询额外返回 chunk_id（id 的别名）。PDF 页码从 1 开始，字符范围相对于该页提取文本；Markdown 页码为空，字符范围相对于整篇正文。范围均为左闭右开。旧表启动时自动补字段，旧记录来源字段为 NULL，需要重新生成分块。块序号从 1 开始，同一文档的块序号唯一；重新生成后数据库 ID 不保证不变。重新生成时，在同一事务中删除旧分块并插入新分块，失败会回滚。文档存在但未分块时查询返回空列表；文档不存在返回 404，空白正文生成返回 422。

分块接口负责保存分块，后续可用于 BM25、向量检索和 RAG；原全文问答仍保留。分块接口没有分页，删除文档也尚未级联清理分块；当前未声明分块表的外键约束。

验证（2026-10-03）：临时数据库 TestClient 测试通过，覆盖分块正文、重叠、排序、重复生成、内容缩短后替换、文档隔离、插入失败回滚、空白正文、未上传文件、非法或不存在的文档编号，以及再次初始化后数据保留。

## 多轮对话流程

前端继续发送 `session_id` 和 `question`，无需重复上传历史记录。后端按文档和会话编号查询最近 5 条问答，按从旧到新的顺序交给 `llm.py`。消息依次为系统说明、文档正文、历史用户问题与模型回答、当前问题。模型成功回答后，本轮问答才写入数据库。

不同会话的上下文相互隔离；未关联会话的旧记录不会被带入。每次请求重新构建上下文，并非模型服务自动记住对话。最近 5 轮按记录数量限制，尚未按 token 数动态裁剪。

手动验证：在同一会话先问“文档使用什么数据库？”，再问“我刚才问了什么？”，检查第二次回答是否引用前一问；切换新会话后，不应获得旧会话的问题。

本次验证（2026-10-01）：临时数据库接口测试通过，覆盖最近 5 轮截取与顺序、会话和文档隔离、参数校验、超时及空回答不入库；前端类型检查和构建通过。真实 DeepSeek 两轮问答及历史保存通过，测试期间曾出现连接错误，重试后成功。

## 流式回答与停止生成

`POST /documents/{document_id}/ask/stream` 接收与 `/ask` 相同的 JSON：`{"session_id":1,"question":"请概括文档"}`。原有非流式接口保留。

响应为 `text/event-stream`，每个事件使用 `data: JSON` 加空行分隔：

- `delta`：`content` 包含新增文字，页面逐段追加。
- `done`：完整回答已保存到历史，包含 `document_id` 和 `session_id`，页面自动刷新历史。
- `error`：`message` 包含错误提示；开始流式响应后的错误通过事件通知，不能再修改 HTTP 状态码。

点击“停止生成”通过 AbortController 中断请求，页面保留已收到的文字并刷新历史。后端检测断开后关闭生成器；正常完成前中止或出错的回答不保存。若停止发生在保存完成之后，已保存的记录不会撤销。

PDF 通过 pypdf 逐页提取正文，再复用同一套问答与会话逻辑。正文仍限制为 20000 字符，历史携带最近 5 轮。

验证（2026-10-03）：临时数据库及独立本地 HTTP 服务测试通过；真实 DeepSeek 两轮分别返回 29、16 个文字片段，连续追问及完整历史保存通过。模拟慢模型并真实断开 HTTP 连接，确认生成器关闭且半截回答不入库；中途错误事件测试通过。前端分段解析、停止请求逻辑测试及类型检查、生产构建通过。未进行浏览器点击验收。


## 练习脚本

独立练习脚本统一放在 `practice/`，正式业务模块保留在项目根目录。
从项目根目录运行，例如：

```powershell
.\.venv\Scripts\python.exe practice/practice_chunk.py
.\.venv\Scripts\python.exe practice/practice_embedding.py
```

练习脚本继续使用项目根目录的 `.env`、`uploads/` 和数据库。模型练习会调用真实 API，数据库练习会写入数据。


## BM25 检索

先上传文件，调用 `POST /documents/{document_id}/chunks` 保存分块，再调用 `POST /documents/{document_id}/search`：

```json
{"question":"SQLite 如何保存数据？","mode":"bm25","top_k":5}
```

支持 bm25、dense 和 hybrid，默认 bm25。top_k 为 1—20，默认 5。问题去除首尾空白后为 1—1000 字符。接口从 SQLite 读取指定文档的分块，返回 document_id、question、mode、items、total。每项包含正文、来源和从 1 开始的 rank；各模式的评分字段见下文。BM25 无词语匹配返回空列表；未分块返回 409，文档不存在返回 404，非法输入返回 422；BM25 模式下纯标点问题也返回 422。

中文使用 jieba 搜索分词，英文标识符转小写，使用正值 IDF 的 BM25 评分。统计结果按完整语料缓存，最多 8 组；分块变化时重新统计。评分只代表词语匹配程度，不代表资料足以回答问题。尚未配置停用词或专有名词词典。

## 百炼 Embedding 与 Chroma

在根目录 `.env` 增加：

```dotenv
DASHSCOPE_API_KEY=填写百炼密钥
DASHSCOPE_BASE_URL=填写对应地域的OpenAI兼容基础地址
EMBEDDING_MODEL=text-embedding-v4
```

基础地址以 `/compatible-mode/v1` 结尾，不追加 `/embeddings`，以账号控制台对应地域配置为准。不要将真实密钥提交到 Git。

`embedding.embed_texts(texts)` 接收非空白文本列表，每批最多 10 条，返回与输入顺序一致的 1024 维向量。空列表直接返回空列表。函数校验返回编号、数量、维度、有限数值及非零向量，当前固定支持 text-embedding-v4。调用有 30 秒超时配置，不自动重试；调用失败由调用方处理。

`vector_store.get_collection()` 打开 `chroma_data/` 下的持久化集合 `document_chunks_v4_1024`，使用 cosine 距离，禁用自动 Embedding，写入时需自行提供向量。这个目录已被 Git 忽略。

已完成 Embedding、Chroma 持久化、文档入库 HTTP 接口、BM25/dense/hybrid 检索、RRF 融合和前端检索展示。原会话问答仍使用全文；新增单轮 RAG 问答可将检索结果接入回答生成，并返回引用原文。练习脚本生成并打印向量，不自动写入 Chroma。

## 当前回归测试

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时数据库和模拟 Embedding 响应，不依赖真实付费模型。覆盖旧表迁移、重复初始化、上传与重复分块、字符来源、PDF 空白页页码（模拟解析器）、BM25 排名及文档隔离、错误输入，以及 Embedding 跨批顺序和异常响应。

验证（2026-10-04）：上述 3 个测试方法及 Python 语法检查通过；pip check 无依赖冲突。另行真实调用百炼，返回 2 个有效的 1024 维向量；在临时 Chroma 目录验证重复 upsert 不增加条数、独立进程重开后正文保留，以及按文档过滤的向量查询。这些 Chroma 检查使用合成向量，不代表正式入库流程已经实现。


## 文档向量入库函数

`indexing.index_document(document_id, connection)` 接收文档 ID 和设置了 `sqlite3.Row` 的 SQLite 连接。它读取该文档已保存的分块，调用 `embed_texts()` 生成向量，再通过 `get_collection()` 写入本地 Chroma。

- 记录 ID 为 `doc:{document_id}:chunk:{chunk_id}`，保存向量、正文以及分块来源。没有页码等信息时省略对应元数据字段。
- 每批最多写入 100 条；`upsert` 更新已有 ID 或新增记录，重复执行不会增加相同 ID 的记录，但会重新调用 Embedding API。
- 新记录全部写入后，清理仅属于当前文档且已经失效的旧块 ID，最后检查保存的 ID 集合。
- 返回 document_id、indexed_chunks、removed_chunks 和 message。文档不存在或没有分块时抛出 ValueError；API、数据库及 Chroma 失败向调用方传播。
- 同一文档需串行处理，入库期间不要重新分块。多批写入及删除不是整体事务；失败可能留下部分更新，应重试。同一时刻删除文档、多个调用并发入库以及向量索引版本切换尚未处理。
- 如果文档完全没有分块，函数报错，不自动删除旧向量。正式删除流程后续需要单独接入。

验证（2026-10-04）：完整回归套件 6 个测试方法通过。新增测试使用内存 SQLite、真实临时 Chroma 集合及模拟 Embedding，覆盖向量与正文、来源对应，重复入库，正文更新，旧块清理，文档隔离，缺失文档、无分块、Embedding 失败或数量错误、205 块跨批写入、写入失败后重试及跨批清理。

另行以只读方式读取项目数据库的文档 5，真实调用百炼，通过 index_document 将 3 个分块保存为 3 个 1024 维向量到临时 Chroma 目录。独立进程重新打开后读取成功，并通过自身向量查询命中对应块。测试未修改项目文档或正式 Chroma 数据；临时目录已清理。此验证是入库及持久化验证，不代表问题检索质量评测。

## 向量检索与前端操作

启动前后端后，在“文档检索”区域选择已上传的文档：

1. 点击“生成 / 更新分块”，保存当前文本分块。
2. 点击“向量入库”，调用 `POST /documents/{document_id}/index`。成功返回 indexed_chunks 和 removed_chunks；重复操作会再次调用 Embedding 模型。
3. 输入问题，选择 BM25、向量或混合检索，设置 Top-K（1—20），点击“开始检索”。
4. 查看原文、来源、页码、排名。BM25 分数越大越靠前；dense 余弦距离越小越靠前，距离不代表正确率。

向量检索请求示例：

```http
POST /documents/5/search
Content-Type: application/json

{"question":"FastAPI 如何使用依赖注入？","mode":"dense","top_k":3}
```

`embed_query()` 使用相同模型生成问题向量，`search_dense()` 按 document_id 限定 Chroma 检索范围。dense 结果包含 distance，BM25 结果包含 score 和 matched_terms。向量检索返回最近的分块，没有相关性阈值，因此不相关问题也可能返回结果。

未分块或 dense/hybrid 未入库返回 409；文档不存在返回 404；模型超时返回 504，模型 API 错误返回 502，内部错误返回 500。入库接口未分块返回 422。重新分块后需重新入库；当前没有自动检查索引是否过期。页面展示加载、失败和空结果提示，切换文档会清空旧结果。

### 验证（2026-10-05）

- 后端 unittest：16 项通过（包含复用的入库测试），覆盖真实内存 Chroma 的向量排序、Top-K、文档隔离、重复入库、旧记录清理和接口错误响应；Embedding 使用模拟数据，不调用付费模型。
- 前端 `cd frontend; npm run build`：Vue 类型检查及生产构建通过。
- Playwright / Edge 无头浏览器交互检查：分块、入库、BM25/dense 请求和结果、加载时禁用提交、原文 HTML 转义、409 提示、空结果、切换文档清空结果通过；390px 手机宽度无横向溢出，没有捕获到页面 JavaScript 异常。浏览器使用模拟 HTTP 响应，未进行真实模型端到端联调。

## Hybrid / RRF 混合检索

使用同一个 `POST /documents/{document_id}/search` 接口，将 mode 设置为 hybrid：

```json
{"question":"SQLite 如何在服务重启后保留数据？","mode":"hybrid","top_k":5}
```

`search_hybrid()` 先分别执行 BM25 和 dense，每路取 `max(20, top_k)` 个候选（实际结果受文档分块数量限制），再由 `fuse_rrf()` 按 `(document_id, chunk_id)` 合并去重，最后返回 Top-K。两路都限定当前文档。

RRF 根据排名而非原始分数融合，排名从 1 开始，平滑常数固定为 60：

```text
rrf_score = 1 / (60 + bm25_rank) + 1 / (60 + dense_rank)
```

未进入某一路候选时，该路贡献为 0。同一路重复分块只贡献一次；两路均命中则累加贡献。融合分数越大越靠前，同分按文档 ID 和分块 ID 排序。结果保留 rrf_score、bm25_rank、dense_rank、bm25_score、dense_distance、matched_terms，以及最终 rank 和原文来源。未命中一路时，该路排名和原始指标为 null。

前端“混合检索 · BM25 + 向量（RRF）”展示融合分数及两路排名、分数或距离。Hybrid 需要先完成向量入库；dense 失败时整个混合请求返回错误，不自动退回 BM25。RRF 分数不代表答案正确率，也没有无关结果过滤阈值。混合检索质量仍需用评测集与单路检索比较。

### Hybrid 验证（2026-10-05）

- 完整后端测试套件 24 项通过（包含复用入库测试）；覆盖 RRF 数值、去重、输入不被修改、单路候选、Top-K、候选数量、文档隔离、真实内存 Chroma 检索和未入库响应。
- 前端 Vue 类型检查和生产构建通过。
- 使用临时 SQLite 和持久化 Chroma，通过 Edge 无头浏览器完成真实 HTTP 和百炼 Embedding 联调：hybrid 返回 200，第一条结果包含预期 SQLite 内容，融合分数与两路排名计算公式一致，页面正确展示 RRF 与两路指标。随后 BM25 和 dense 请求均返回 200。
- 390px 手机宽度无横向溢出，未捕获页面 JavaScript 异常。测试未修改正式文档数据，测试后临时后端服务已停止。这是链路验证，不是全面检索质量评测。

## 单轮 RAG 问答与引用

新增 `POST /documents/{document_id}/ask-rag`，复用 SearchRequest：

```json
{"question":"SQLite 如何在服务重启后保留数据？","mode":"hybrid","top_k":3}
```

接口先执行所选 BM25、dense 或 hybrid 检索，再由 `llm.answer_with_chunks()` 将相关分块和问题交给 DeepSeek 回答模型。使用现有 DEEPSEEK_API_KEY 和 DEEPSEEK_MODEL 配置。本次分块按顺序分配 citation_id，模型在回答中使用 [1]、[2] 等标注；编号仅对本次回答有效，与数据库 chunk_id 不同。

响应包含 document_id、question、mode、answer、citations、retrieved_chunks。citations 仅返回回答实际使用的引用，包括 citation_id、document_id、chunk_id、source_name、page_number、start_char、end_char 和 content；来源及原文从后端检索结果取得，不由模型编造。

前端“文档检索与问答”中选择文档、检索方式和 Top-K，输入问题后点击“根据资料回答”。页面展示回答、检索分块数量及可展开的引用原文和来源；请求过程中禁用重复操作，错误时清空旧结果，切换文档时清空回答与引用。普通“开始检索”仍可用于查看检索结果。

没有检索结果时直接返回不足提示及空引用，不调用回答模型。有资料但缺少回答密钥返回 503；模型超时返回 504，API 失败返回 502；空回答、截断回答或不存在的引用编号返回 502。文档、分块及向量索引的前置错误沿用检索接口。

当前为单轮、非流式 RAG，不读取或保存会话历史。原来的流式全文问答及会话功能仍可使用。引用校验只检查编号是否存在，不能验证原文是否支持结论，也不强制每个回答都有引用；资料不足处理仍依赖模型遵循提示词。dense/hybrid 没有相关性阈值，非空检索结果不代表足以回答。

### RAG 验证（2026-10-05）

- 完整后端测试套件 32 项通过（包含复用的入库测试）。新增测试覆盖三种检索模式接入回答及引用来源、空结果不调用模型、缺少配置、非法引用、空/截断回答、请求校验和上游异常；自动测试使用隔离 SQLite、真实内存 Chroma 和模拟模型。
- 临时文档真实 HTTP 联调使用百炼 Embedding、持久化 Chroma 和 DeepSeek：SQLite 问题生成带合法引用的回答；火星温度问题提示资料不足。引用原文来自测试文档，未修改正式数据。
- Edge 浏览器真实问答链路通过，确认答案与引用展示、展开原文和加载禁用。另使用模拟响应验证错误清空回答及无引用状态，切换文档清空结果通过。
- Vue 类型检查及生产构建通过；390px 手机宽度无横向溢出，未捕获页面 JavaScript 异常。以上是链路和有限样例验证，不是全面的回答正确率或引用支持度评测。

## ?? RAG ????

`POST /documents/{document_id}/ask-rag/stream` ?? SearchRequest???? RAG ?????????????? SSE ?? JSON ???delta ? content ??????done ?? citations?document_id?question?mode ? retrieved_chunks?error ?? message??????????????????? done?????????? HTTP ????????????? error ?????

????????????????????????????????????????? done ?????????????????????????SSE ?????????? UTF-8 ???????? LF/CRLF ???????????????????????????????????????????

???2026-10-06???????? 39 ???????????done??????????????????????????????????????????????????????error??? done????????????? SQLite?Chroma ??????? hybrid ?????SQLite ???? 28 ? delta ?????????????????????????????????????????????????????????????????????????????? RAG???????????????????

## ?? RAG ?????

?????? RagChatRequest?? SearchRequest ? question?mode?top_k ????? session_id??????????????? ID?????

- `POST /documents/{document_id}/ask-rag/chat`??? JSON ???
- `POST /documents/{document_id}/ask-rag/chat/stream`?SSE ?????
- `GET /documents/{document_id}/sessions/{session_id}/rag-history`???????? RAG ??????

????????????? 5 ? RAG ???????? rewrite_question ???????????????????????????????????????????????????????????????????????????????

rag_turns ????? document_id?session_id?question?retrieval_question?answer?citations_json?mode ? created_at???????????init_db ??????????????????? meta????????? delta????????????? done??? ID???????????????????????????????? error ???????????????????????????????????????????????

?? RagChat.vue ????????????????RagTurn.vue ?????????????????????readSse.js ?? UTF-8 ??? SSE ???????????????????????????????????????????????????????????????????

???2026-10-06???????? 56 ???????????Vue ????????????SSE ?????? CRLF ????????????????????????????????????????? Embedding?Chroma?DeepSeek ??????????? 27 ??????? 29 ?????????????????????????SQLite ????????????????????????????????? done ????????????????????????????????????????????????

???????????????? HTTP??????? 8001 ??????????????????????????????????????????????????????????????????

## ??????

split_text_with_offsets ????????????????????Markdown ATX ???# ? ######?????????????????????????????????????????????????????????500 ?????????????????????????????????????????????????? Markdown ??????? Setext ???????????

overlap=100 ?????????????????????????????? 100 ??????????????????????????????? text[start_char:end_char]?????????????????PDF ?????????????????????????? PDF ???????

????????? 61 ??????????????????????????URL/?????????????????????????????????????????????????????????????????????????????
