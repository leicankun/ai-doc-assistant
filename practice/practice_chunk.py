import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from retrieval.chunking import split_text




# 找到项目中的测试文档
BASE_DIR = PROJECT_ROOT
file_path = BASE_DIR / "uploads" / "test.md"

# 读取正文
text = file_path.read_text(encoding="utf-8")

# 对正文进行分块
chunks = split_text(
    text,
    chunk_size=500,
    overlap=100,
)

print(f"正文共 {len(text)} 个字符")
print(f"分成了 {len(chunks)} 块")

for number, chunk in enumerate(chunks, start=1):
    print(f"\n========== 第 {number} 块 ==========")
    print(f"长度：{len(chunk)} 个字符")
    print(chunk)