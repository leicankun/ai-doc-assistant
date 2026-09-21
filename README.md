# 技术文档问答助手

使用 FastAPI 开发，后续增加文档上传和 AI 问答功能。

## 环境准备（Windows PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

## 当前接口

- GET /health：检查服务状态
- GET /documents：返回示例文档列表
- /docs：交互式接口文档

## 当前限制

文档信息写在代码中，尚未实现文件上传、数据库存储和 AI 问答。