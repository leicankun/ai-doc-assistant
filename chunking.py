import bisect
import re


def _boundaries(text):
    """?????????????? Markdown ?????"""
    headings, paragraphs, sentences = [], [], []
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            offset += len(line)
            continue
        if marker:
            fence = marker[1]
            offset += len(line)
            continue
        if re.match(r"^ {0,3}#{1,6}[ \t]+\S", line):
            headings.append(offset)
        if not line.strip():
            paragraphs.append(offset + len(line))
        else:
            # ??????? URL??????????????????
            protected = [(m.start(), m.end()) for m in re.finditer(r"`+[^`\n]*`+|https?://\S+", line)]
            for match in re.finditer(r'[。！？!?]+[”’"\')）]*|\.(?=\s|$)', line):
                if not any(a <= match.start() < b for a, b in protected):
                    sentences.append(offset + match.end())
        offset += len(line)
    return headings, sorted(set(paragraphs)), sorted(set(sentences))


def _last_between(points, low, high):
    index = bisect.bisect_right(points, high) - 1
    return points[index] if index >= 0 and points[index] > low else None


def split_text_with_offsets(text: str, chunk_size: int = 500, overlap: int = 100):
    """Prefer headings, paragraphs, sentences; hard-split oversized units.

    Lengths are Python characters. Heading sections never share overlap.
    Original slices and offsets are preserved. Overlap is at most the requested
    length, aligned to a complete paragraph/sentence when possible.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0，并且小于 chunk_size")

    headings, paragraphs, sentences = _boundaries(text)
    sections = sorted(set([0, *headings, len(text)]))
    chunks = []
    for section_start, section_end in zip(sections, sections[1:]):
        start = section_start
        previous_end = start
        while start < section_end:
            limit = min(start + chunk_size, section_end)
            if limit == section_end:
                end = section_end
            else:
                # ??????????????????????
                low = max(start, previous_end)
                end = (_last_between(paragraphs, low, limit)
                       or _last_between(sentences, low, limit)
                       or limit)
            content = text[start:end]
            if content.strip():
                chunks.append({"content": content, "start_char": start, "end_char": end})
            if end == section_end:
                break
            # ??????????????????
            floor = max(start + 1, end - overlap)
            next_start = end
            if overlap:
                for boundaries in (paragraphs, sentences):
                    index = bisect.bisect_left(boundaries, floor)
                    if index < len(boundaries) and boundaries[index] < end:
                        next_start = boundaries[index]
                        break
                else:
                    # ????????????????????????
                    if end == limit:
                        next_start = floor
            previous_end = end
            start = next_start
    return chunks


def split_text(text: str, chunk_size: int = 500, overlap: int = 100):
    """保留练习脚本使用的纯文本列表接口。"""
    return [chunk["content"] for chunk in split_text_with_offsets(text, chunk_size, overlap)]
