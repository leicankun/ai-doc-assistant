"""索引与检索接口。"""
import sqlite3
import logging
from fastapi import APIRouter, Depends, HTTPException
from openai import APIError, APITimeoutError
from database import get_connection
from schemas import SearchRequest
from bm25 import search_bm25, tokenize
from retrieval import search_dense, search_hybrid
from indexing import index_document
from routers.chunks import list_document_chunks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["索引与检索"])


# 按 BM25、dense 或 hybrid 模式检索指定文档，返回相关分块和评分排名。
@router.post("/{document_id}/search")
def search_document(
    document_id: int,
    request: SearchRequest,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 检查文档是否存在，并读取分块。
    # 文档不存在时，list_document_chunks 会抛出 404。
    chunk_result = list_document_chunks(document_id, connection)
    chunks = chunk_result["items"]

    if not chunks:
        raise HTTPException(
            status_code=409,
            detail="该文档尚未生成分块，请先生成分块",
        )

    if request.mode == "bm25":
        # BM25 依赖分词后的词语匹配。
        if not tokenize(request.question):
            raise HTTPException(
                status_code=422,
                detail="问题中没有可检索的词语",
            )

        items = search_bm25(
            chunks=chunks,
            question=request.question,
            top_k=request.top_k,
        )

    # 3. dense 或 hybrid：需要调用向量检索。
    else:
        state = connection.execute(
            "SELECT chunk_version, indexed_version FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if state['indexed_version'] is None or state['indexed_version'] != state['chunk_version']:
            raise HTTPException(status_code=409, detail="向量索引未就绪或已过期，请先重新向量入库")
        try:
            if request.mode == "dense":
                items = search_dense(
                    document_id=document_id,
                    question=request.question,
                    top_k=request.top_k,
                )
            else:
                # SearchRequest 限定了三种模式，
                # 排除 bm25 和 dense 后，这里就是 hybrid。
                items = search_hybrid(
                    document_id=document_id,
                    chunks=chunks,
                    question=request.question,
                    top_k=request.top_k,
                )

        except APITimeoutError as exc:
            raise HTTPException(
                status_code=504,
                detail="问题向量生成超时，请稍后重试",
            ) from exc

        except APIError as exc:
            raise HTTPException(
                status_code=502,
                detail="向量模型调用失败，请检查模型服务配置",
            ) from exc

        except ValueError as exc:
            if str(exc) == "该文档尚未向量入库，请先调用入库接口":
                raise HTTPException(
                    status_code=409,
                    detail=str(exc),
                ) from exc

            logger.exception(
                "检索参数或配置错误，document_id=%s，mode=%s",
                document_id,
                request.mode,
            )
            raise HTTPException(
                status_code=500,
                detail="检索失败，请查看后端日志",
            ) from exc

        except Exception as exc:
            logger.exception(
                "检索失败，document_id=%s，mode=%s",
                document_id,
                request.mode,
            )
            raise HTTPException(
                status_code=500,
                detail="检索失败，请查看后端日志",
            ) from exc

    # 4. 统一返回结构。
    return {
        "document_id": document_id,
        "question": request.question,
        "mode": request.mode,
        "items": items,
        "total": len(items),
    }


# 将文档分块转换成向量写入 Chroma，更新已有记录并清理失效旧记录。
@router.post("/{document_id}/index")
def index_document_vectors(
    document_id: int,
    connection: sqlite3.Connection = Depends(get_connection),
):
    # 1. 检查文档是否存在。
    document = connection.execute(
        "SELECT id FROM documents WHERE id = ?",
        (document_id,),
    ).fetchone()

    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 2. 检查是否已经生成分块。
    chunk = connection.execute(
        "SELECT id FROM document_chunks WHERE document_id = ? LIMIT 1",
        (document_id,),
    ).fetchone()

    if chunk is None:
        raise HTTPException(
            status_code=422,
            detail="该文档尚未生成分块，请先调用分块接口",
        )

    # 3. 调用入库函数，生成向量并保存到 Chroma。
    try:
        result = index_document(document_id, connection)
    except APITimeoutError as exc:
        raise HTTPException(
            status_code=504,
            detail="向量生成超时，请稍后重试",
        ) from exc
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail="向量模型调用失败，请检查模型服务配置",
        ) from exc
    except Exception as exc:
        logger.exception("文档向量入库失败，document_id=%s", document_id)
        raise HTTPException(
            status_code=500,
            detail="文档向量入库失败，请查看后端日志",
        ) from exc

    return result
