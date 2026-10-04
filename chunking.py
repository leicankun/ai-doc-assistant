def split_text_with_offsets(
    text: str,
    chunk_size: int = 500,
    overlap: int = 100,
):
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0，并且小于 chunk_size")

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]

        if chunk.strip():
            chunks.append({"content": chunk, "start_char": start, "end_char": end})

        if end >= len(text):
            break

        start = end - overlap

    return chunks


def split_text(text: str, chunk_size: int = 500, overlap: int = 100):
    """保留练习脚本使用的纯文本列表接口。"""
    return [chunk["content"] for chunk in split_text_with_offsets(text, chunk_size, overlap)]
