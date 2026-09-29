# 技术文档问答助手

基于 FastAPI 和 Vue 的文档信息管理练习项目，已实现文档信息的创建、查询、修改和删除，使用 SQLite 持久化保存数据，支持 Markdown 文件上传、正文读取和基于 DeepSeek 的单篇文档问答。

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

密钥只保存在后端。每次提问会将选中文档的全文和问题发送给 DeepSeek，不包含历史问题。修改配置后重启后端。

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

检查前端构建：

```powershell
cd frontend
npm.cmd run build
```

## 上传和问答

1. 同时启动前后端，打开 Vue 页面。
2. 在上传区选择 UTF-8 编码的 `.md` 文件，点击上传。
3. 在新增的文档记录旁点击“提问”，在页面下方输入问题。
4. 点击“发送问题”，等待回答；调用失败会显示错误提示。

可上传写有“数据库使用 SQLite”的测试文档，询问数据库类型，再问文档未提及的开发者信息。后者应提示“文档中未提供”，模型回答仍需核对原文。

## 项目结构

```text
main.py                 创建 FastAPI 应用、注册文档路由和健康检查
config.py               集中定义 BASE_DIR 和 DB_PATH
schemas.py              Document、QuestionRequest 请求模型和字段校验
database.py            初始化数据库、提供连接依赖
routers/__init__.py     路由包标记
routers/documents.py    文档增删改查、上传、读取正文和问答接口
llm.py                  DeepSeek 调用封装
practice_llm.py         独立模型调用练习
frontend/               Vue 前端及其独立的 package.json
practice_db.py          sqlite3 插入与查询练习
practice_orm.py         SQLAlchemy 会话、消息和 JOIN 练习
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
| POST | /documents/upload | 上传 Markdown 并关联数据库 | 201 |
| GET | /documents/{document_id}/content | 读取文档正文 | 200 |
| POST | /documents/{document_id}/ask | 根据文档回答问题 | 200 |

上传接口使用 `multipart/form-data`，字段名为 `file`。提问接口的 JSON 请求体示例为 `{"question":"项目使用什么数据库？"}`，返回 `document_id`、`question` 和 `answer`。问题去除首尾空白后长度需为 1—1000 个字符；文档不得为空且最多 20000 个字符。

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
.\.venv\Scripts\python.exe practice_db.py
```

每次运行 practice_db.py 都会向同一个数据库新增一条练习记录，并查询表中全部记录。

## SQLAlchemy 练习

```powershell
.\.venv\Scripts\python.exe practice_orm.py
```

使用同一个 documents.db，创建尚不存在的 chat_sessions 和 chat_messages 表。
每次运行新增一个聊天会话和两条消息，通过 session_id 外键关联，再用 JOIN 查询本次会话的标题、角色和消息内容。
引擎在新连接建立时开启 SQLite 外键检查，echo=True 会打印实际执行的 SQL。
聊天会话和消息目前仅用于独立脚本练习，尚未提供对应的 HTTP 接口。

## 依赖维护

requirements.txt 记录项目虚拟环境中的依赖版本，包含 FastAPI、Uvicorn、Pydantic、SQLAlchemy、OpenAI SDK、python-dotenv、python-multipart 及其依赖。OpenAI SDK 在本项目中用于调用 DeepSeek。
SQLite 的 sqlite3 和 pathlib 属于 Python 标准库，不需要用 pip 安装。

## 当前限制

- 真实文件上传和问答目前只支持 UTF-8 Markdown；PDF 仅可登记文档信息。
- 问答为单篇短文档全文问答，尚未实现 RAG、流式回答和聊天历史。
- 当前删除接口只删除数据库记录，尚未同步删除磁盘文件。
