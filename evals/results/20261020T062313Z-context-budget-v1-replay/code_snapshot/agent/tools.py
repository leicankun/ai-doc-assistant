"""Tool definitions, validation and dispatch."""
import logging
import os
import time
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from schemas import SearchRequest
from services.knowledge_base import require_base, search_base
from storage.document_status import DOCUMENT_SELECT, with_processing_status


# 三个工具的参数结构与校验规则。
class ListDocumentsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=10)
    mode: Literal["bm25", "dense", "hybrid"] = "hybrid"
    document_ids: list[int] | None = Field(default=None, min_length=1, max_length=50)


class ContextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chunk_id: int = Field(gt=0)
    before: int = Field(default=1, ge=0, le=2)
    after: int = Field(default=1, ge=0, le=2)


# 三个工具实际执行的 Python 函数。
def list_documents(connection, base_id, offset=0, limit=20):
    """知识库 ID 来自接口路径，模型不能指定或切换它。"""
    args = ListDocumentsArguments(offset=offset, limit=limit)
    require_base(connection, base_id)
    total = connection.execute(
        "SELECT COUNT(*) FROM knowledge_base_documents WHERE knowledge_base_id=?",
        (base_id,),
    ).fetchone()[0]
    rows = connection.execute(
        DOCUMENT_SELECT + """ JOIN knowledge_base_documents m ON m.document_id=d.id
        WHERE m.knowledge_base_id=? ORDER BY d.id LIMIT ? OFFSET ?""",
        (base_id, args.limit, args.offset),
    ).fetchall()
    documents = []
    for row in rows:
        doc = with_processing_status(row)
        documents.append({
            "document_id": doc["id"], "title": doc["title"],
            "file_type": doc["file_type"],
            "processing_status": doc["processing_status"],
            "processing_status_label": doc["processing_status_label"],
        })
    next_offset = args.offset + len(documents)
    return {"documents": documents, "total": total,
            "next_offset": next_offset if next_offset < total else None}


def search_knowledge_base(connection, base_id, query, top_k=5, mode="hybrid", document_ids=None,
                          timeout=None):
    return search_base(connection, base_id,
                       SearchRequest(question=query, top_k=top_k, mode=mode),
                       document_ids=document_ids, allow_partial=True,
                       min_dense_similarity=float(os.getenv('AGENT_MIN_SIMILARITY', '0.4')),
                       timeout=timeout)


def read_chunk_context(connection, base_id, chunk_id, before=1, after=1):
    """先验证中心块属于当前知识库，再按同一文档的块序号读取。"""
    require_base(connection, base_id)
    center = connection.execute("""
        SELECT c.document_id, c.chunk_index FROM document_chunks c
        JOIN knowledge_base_documents m ON m.document_id=c.document_id
        WHERE m.knowledge_base_id=? AND c.id=?
    """, (base_id, chunk_id)).fetchone()
    if center is None:
        raise HTTPException(404, "分块不存在或不属于当前知识库")
    rows = connection.execute("""
        SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.content,
               d.title AS source_name, c.page_number, c.start_char, c.end_char
        FROM document_chunks c JOIN documents d ON d.id=c.document_id
        WHERE c.document_id=? AND c.chunk_index BETWEEN ? AND ?
        ORDER BY c.chunk_index
    """, (center["document_id"], center["chunk_index"] - before,
          center["chunk_index"] + after)).fetchall()
    return {"items": [dict(row) for row in rows], "total": len(rows)}


# 统一生成提供给大模型的工具定义。
def tool_definition(name, description, arguments):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": arguments.model_json_schema(),
        },
    }


TOOLS = [
    tool_definition(
        "list_documents",
        "分页查看当前知识库的文档 ID、标题、类型和处理状态。"
        "用户询问有哪些资料、文档数量、文档是否就绪时使用。"
        "仅提供目录，不提供正文，不能据此回答文档内容问题。"
        "可按返回的 next_offset 继续读取下一页。",
        ListDocumentsArguments,
    ),
    tool_definition(
        "search_knowledge_base",
        "检索当前知识库正文。内容问题必须先检索；结果不足可换查询补查。"
        "可用 document_ids 限定已知文档 ID，比较版本时分别检索。"
        "默认混合检索；仅检索已就绪文档并返回 skipped_documents，必要时可用 BM25。返回带 citation_id 的依据。",
        SearchArguments,
    ),
    tool_definition(
        "read_chunk_context",
        "用检索得到的 chunk_id 读取同文档前后分块，补全文段的条件、步骤。"
        "返回带 citation_id 的原文及页码；不读取其他知识库。",
        ContextArguments,
    ),
]


# 后端注册表：工具名称对应参数校验类和执行函数。
TOOL_HANDLERS = {
    "list_documents": (ListDocumentsArguments, list_documents),
    "search_knowledge_base": (SearchArguments, search_knowledge_base),
    "read_chunk_context": (ContextArguments, read_chunk_context),
}


def execute_tool(connection, base_id, name, arguments, deadline=None):
    """Execute registered tools and return parameter errors to the model."""
    try:
        if name not in TOOL_HANDLERS:
            raise ValueError("Unsupported tool")
        schema, handler = TOOL_HANDLERS[name]
        args = schema.model_validate_json(arguments)
        kwargs = args.model_dump()
        if name == 'search_knowledge_base' and deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Agent timeout')
            kwargs['timeout'] = min(30.0, remaining)
        return handler(connection, base_id, **kwargs)
    except ValueError:
        return {"error": "工具名称或参数无效，请按工具参数定义修正"}
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    except TimeoutError:
        raise
    except Exception:
        logging.getLogger(__name__).exception("Agent tool failed: %s", name)
        return {"error": "工具执行失败，请稍后重试；不要把失败当成未找到资料"}
