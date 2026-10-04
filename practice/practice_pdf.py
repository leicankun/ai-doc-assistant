import sys
from pathlib import Path

# 支持直接运行 practice 目录中的脚本，并导入项目根目录模块。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from pypdf import PdfReader


# 找到项目目录下的 uploads/test.pdf
BASE_DIR = PROJECT_ROOT
pdf_path = BASE_DIR / "uploads" / "test.pdf"

# 读取 PDF
reader = PdfReader(pdf_path)

print(f"这份 PDF 一共有 {len(reader.pages)} 页")

# 逐页提取文字
for page_number, page in enumerate(reader.pages, start=1):
    text = page.extract_text()

    print(f"\n========== 第 {page_number} 页 ==========")

    if text and text.strip():
        print(text)
    else:
        print("这一页没有提取到文字，可能是空白页或图片。")