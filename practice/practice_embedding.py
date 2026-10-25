import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os

from dotenv import load_dotenv
from openai import OpenAI
from contextlib import closing

from storage.database import connect


load_dotenv(PROJECT_ROOT / ".env")

api_key = os.getenv("DASHSCOPE_API_KEY")
base_url = os.getenv("DASHSCOPE_BASE_URL")

if not api_key or not base_url:
    raise RuntimeError("请在 .env 配置百炼 API Key 和 base_url")

# 上次上传的 11111.md，文档 ID 为 5。
document_id = 5

with closing(connect()) as connection:

    rows = connection.execute(
        """
        SELECT id, document_id, chunk_index, content
        FROM document_chunks
        WHERE document_id = ?
        ORDER BY chunk_index
        """,
        (document_id,),
    ).fetchall()

chunks = [dict(row) for row in rows]

if not chunks:
    raise RuntimeError("该文档没有分块，请先调用分块接口")

texts = [chunk["content"] for chunk in chunks]

with OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=30.0,
    max_retries=0,
) as client:
    # text-embedding-v4 每次最多发送 10 条文本。
    for start in range(0, len(texts), 10):
        batch_texts = texts[start:start + 10]
        batch_chunks = chunks[start:start + 10]

        response = client.embeddings.create(
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
            input=batch_texts,
            dimensions=1024,
            encoding_format="float",
        )

        indices = sorted(item.index for item in response.data)
        if indices != list(range(len(batch_texts))):
            raise RuntimeError("返回的向量数量或编号与输入不一致")

        for item in sorted(response.data, key=lambda item: item.index):
            # index 是当前批次内的位置。
            chunk = batch_chunks[item.index]
            vector = item.embedding

            if len(vector) != 1024:
                raise RuntimeError("返回的向量维度不符合预期")

            print("文档 ID：", chunk["document_id"])
            print("分块 ID：", chunk["id"])
            print("块序号：", chunk["chunk_index"])
            print("正文预览：", chunk["content"][:60])
            print("向量维度：", len(vector))
            print("前 5 个数：", vector[:5])
            print()