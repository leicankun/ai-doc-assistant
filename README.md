# 技术文档问答助手

基于 FastAPI 和 Vue 的文档信息管理练习项目，已实现文档信息的创建、查询、修改和删除，使用 SQLite 持久化保存数据，后续增加文件上传和 AI 问答。

## 环境准备（Windows PowerShell）

在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

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

## 项目结构

```text
main.py                 创建 FastAPI 应用、注册文档路由和健康检查
config.py               集中定义 BASE_DIR 和 DB_PATH
schemas.py              Document 请求模型及标题、文件类型校验
database.py            初始化数据库、提供连接依赖
routers/__init__.py     路由包标记
routers/documents.py    文档增删改查接口
frontend/               Vue 前端及其独立的 package.json
practice_db.py          sqlite3 插入与查询练习
practice_orm.py         SQLAlchemy 会话、消息和 JOIN 练习
requirements.txt        当前项目虚拟环境的依赖版本
```

## 连接管理与路由组织

- 五个文档接口通过 Depends(get_connection) 获取独立的 SQLite 连接。
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
- 数据库文件由 config.py 的 DB_PATH 指定，位于项目目录下，名称为 documents.db，包含 documents 表及 id、title、file_type 三列。
- 服务加载时调用 init_db()，只创建不存在的数据库和表，不清空已有记录。
- 创建、修改和删除操作提交到数据库，服务重启或自动重载后仍然保留。
- id 为 INTEGER PRIMARY KEY，由 SQLite 在插入时自动分配；删除后的编号可能被复用。
- 本地数据库及事务日志由 .gitignore 忽略，仓库保留建表代码。

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

requirements.txt 记录项目虚拟环境中的依赖版本，包含 FastAPI、Uvicorn、Pydantic 和 SQLAlchemy 及其依赖。
SQLite 的 sqlite3 和 pathlib 属于 Python 标准库，不需要用 pip 安装。

## 当前限制

- 仅管理文档的 ID、标题和文件类型，尚未保存实际 PDF 或 Markdown 文件。
- 尚未接入 AI 问答。
