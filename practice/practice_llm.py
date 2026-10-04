import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import os

from dotenv import load_dotenv
from openai import OpenAI


# 从项目根目录读取 .env
load_dotenv(PROJECT_ROOT / ".env")

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