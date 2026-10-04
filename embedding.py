import math
import os

from dotenv import load_dotenv
from openai import OpenAI

from config import BASE_DIR


load_dotenv(BASE_DIR / ".env")

EMBEDDING_DIMENSIONS = 1024
BATCH_SIZE = 10


def embed_texts(texts: list[str]) -> list[list[float]]:
    # 没有文本时，不调用 API。
    if not texts:
        return []

    # 空白文本不能用于生成有效的文档向量。
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("输入必须是非空白字符串列表")

    api_key = os.getenv("DASHSCOPE_API_KEY")
    base_url = os.getenv("DASHSCOPE_BASE_URL")
    model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")

    if not api_key or not base_url:
        raise RuntimeError("请配置百炼 API Key 和 base_url")

    # 当前批量大小、维度和后续集合均按这个模型配置。
    if model != "text-embedding-v4":
        raise ValueError("当前仅支持 text-embedding-v4")

    vectors = []

    with OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=30.0,
        max_retries=0,
    ) as client:
        # 每批最多发送 10 条文本。
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start:start + BATCH_SIZE]

            response = client.embeddings.create(
                model=model,
                input=batch,
                dimensions=EMBEDDING_DIMENSIONS,
                encoding_format="float",
            )

            # 按输入位置排列返回结果。
            items = sorted(
                response.data,
                key=lambda item: item.index,
            )

            # 确认没有缺失、重复或错误的编号。
            indices = [item.index for item in items]

            if indices != list(range(len(batch))):
                raise RuntimeError("返回的向量数量或编号与输入不一致")

            for item in items:
                vector = item.embedding

                if len(vector) != EMBEDDING_DIMENSIONS:
                    raise RuntimeError("返回的向量维度不符合预期")

                if not all(math.isfinite(value) for value in vector):
                    raise RuntimeError("向量包含无效数值")

                if not any(value != 0 for value in vector):
                    raise RuntimeError("返回了全零向量")

                vectors.append(vector)

    return vectors