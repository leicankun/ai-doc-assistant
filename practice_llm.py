import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# 从当前脚本所在目录读取 .env
load_dotenv(Path(__file__).resolve().parent / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    raise RuntimeError("请在 .env 中填写 DEEPSEEK_API_KEY")

# 创建调用 DeepSeek 的客户端
client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com",
    timeout=60.0,
    max_retries=0,
)

# 发送一条问题
response = client.chat.completions.create(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    messages=[
        {"role": "system", "content": "你是一位耐心的 Python 老师。"},
        {"role": "user", "content": "用一句话解释什么是 FastAPI。"},
    ],
    extra_body={"thinking": {"type": "disabled"}},
    max_tokens=300,
)

# 打印模型回答
print(response.choices[0].message.content)