# 技术文档问答助手

基于 FastAPI 的文档信息管理练习项目，已实现文档信息的创建、查询、修改和删除，后续增加数据库存储、文件上传和 AI 问答。

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

## 当前接口

| 方法 | 路径 | 用途 | 成功状态码 |
| --- | --- | --- | --- |
| GET | /health | 检查服务状态 | 200 |
| GET | /documents | 查询全部文档信息，返回 items 和 total | 200 |
| POST | /documents | 创建文档信息，由后端生成 ID | 201 |
| GET | /documents/{document_id} | 根据 ID 查询单条文档 | 200 |
| PUT | /documents/{document_id} | 更新指定文档的标题和类型，保留 ID | 200 |
| DELETE | /documents/{document_id} | 删除文档，返回提示和被删除的记录 | 200 |

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

测试期间避免修改代码，自动重载会重置内存数据。

## 当前限制

- 仅管理文档的 ID、标题和文件类型，尚未保存实际 PDF 或 Markdown 文件。
- 数据保存在进程内存中，服务重启或自动重载后恢复为代码中的示例数据。
- ID 按现有最大值加 1 生成，删除最大 ID 后可能复用该 ID；该实现仅用于单进程练习。
- 尚未连接数据库，也未接入 AI 问答。
