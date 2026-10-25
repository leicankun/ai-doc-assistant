"""三组分块参数的真实服务评测；原始结构核验＋同事实长章节对照。"""
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, closing
import contextvars
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import chromadb
from openai.resources.embeddings import Embeddings
from openai.resources.chat.completions import Completions
from evals.run_retrieval_benchmark import read_dataset, retrieval_metrics, grade_answer
from evals.run_agentic_benchmark import CALLS, measured_sdk, measured_urlopen, dump, append, digest, percentiles
from retrieval import embedding, mmr, reranker
from retrieval.chunking import split_text_with_offsets
from services import knowledge_base, llm
from storage import database
from storage.schema import metadata as schema
from schemas import SearchRequest

CONFIGS = [(300, 60), (500, 100), (800, 160)]
COLLECTION = contextvars.ContextVar("chunking_collection")


def measured_chat(original):
    observed = measured_sdk(original,"chat")
    def call(self,*args,**kwargs):
        response = observed(self,*args,**kwargs)
        log = CALLS.get()
        if log:
            log[-1]["raw_completion"] = response.model_dump()
        return response
    return call


def transformed_corpus(texts, sections, questions):
    """只改变标题标记，不改变正文事实、顺序、问题和答案点。"""
    result, annotations = {}, []
    for name, original in texts.items():
        doc_sections = sorted((s for s in sections if s["document"] == name), key=lambda s:s["start_char"])
        content = original[:doc_sections[0]["start_char"]]
        for section in doc_sections:
            segment = original[section["start_char"]:section["end_char"]]
            relative_body = section["body_start"] - section["start_char"]
            prefix = segment[:relative_body]
            # 小节仍有可读标签，但不再是分块器识别的 ## 标题。
            assert prefix.startswith("## ")
            updated_prefix = prefix.replace("## ", "", 1)
            new_section = {**section, "start_char":len(content),
                           "body_start":len(content) + len(updated_prefix)}
            content += updated_prefix + segment[relative_body:]
            new_section["end_char"] = len(content)
            assert content[new_section["body_start"]:new_section["body_start"] + len(section["text"])] == section["text"]
            annotations.append(new_section)
        result[name] = content
    question_copy = copy.deepcopy(questions)
    by_section = {s["id"]:s for s in annotations}
    for question in question_copy:
        for group in question["evidence_groups"]:
            for alt in group["alternatives"]:
                section = by_section[f"{alt['document']}:{alt['section']}"]
                start = section["body_start"] + section["text"].index(alt["quote"])
                alt.update(start_char=start, end_char=start + len(alt["quote"]))
                assert result[alt["document"]][start:alt["end_char"]] == alt["quote"]
    return result, annotations, question_copy


def layouts(texts, size, overlap):
    return {name:split_text_with_offsets(text, size, overlap) for name, text in texts.items()}


def within_budget(items, limit=2500):
    """按最终排名取可装入的完整块，最多保留已有 Top-5，不截正文。"""
    selected, used = [], 0
    for item in items:
        if used + len(item["content"]) <= limit:
            selected.append(item)
            used += len(item["content"])
    return selected


def extra_metrics(spec, items):
    metrics = retrieval_metrics(spec, items)
    # 不使用旧的“完整小节重复组”口径比较不同大小的块。
    for key in ("redundant_count", "redundancy_ratio"):
        metrics.pop(key)
    raw_chars = sum(len(x["content"]) for x in items)
    by_doc = defaultdict(list)
    for item in items:
        by_doc[item["document"]].append((item["start_char"], item["end_char"]))
    unique = 0
    for ranges in by_doc.values():
        end = -1
        for start, stop in sorted(ranges):
            unique += max(0, stop - max(start, end))
            end = max(end, stop)
    return {**metrics, "context_characters":raw_chars, "unique_source_characters":unique,
            "source_overlap_ratio":(raw_chars - unique) / raw_chars if raw_chars else 0.0}


def official_state():
    files = [ROOT / "documents.db"]
    for folder in ("uploads", "chroma_data"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    result = {"files":{str(p.relative_to(ROOT)):digest(p) for p in files if p.is_file()}, "mysql":{}}
    with closing(database.connect()) as db:
        for name, table in schema.tables.items():
            order = ",".join(f"`{c.name}`" for c in table.primary_key)
            rows = [dict(r) for r in db.execute(f"SELECT * FROM `{name}` ORDER BY {order}")]
            result["mysql"][name] = {"rows":len(rows), "sha256":hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    return result


def summarize(records, answers, groups):
    output = {}
    for key in groups:
        group_result = {"retrieval":{}, "answers":{}}
        for split in ("dev", "test", "all"):
            rows = [r for r in records if r["group"] == key and (split == "all" or r["question_spec"]["split"] == split)]
            gold = [r for r in rows if r["question_spec"]["expected_behavior"] == "answer"]
            valid = [r for r in rows if r["status"] == "ok"]
            modes = {}
            for mode in ("metrics", "budget_metrics"):
                average = lambda field:round(statistics.mean((r.get(mode) or {}).get(field) or 0 for r in gold), 4) if gold else None
                modes[mode] = {"mean_evidence_coverage":average("evidence_coverage"),
                    "full_evidence_questions":sum(bool((r.get(mode) or {}).get("full_evidence_coverage")) for r in gold),
                    "mrr_at_5":average("reciprocal_rank")}
            group_result["retrieval"][split] = {"questions":len(rows), "success":len(valid),
                "answerable_questions":len(gold), **modes,
                "mean_context_characters":round(statistics.mean(r["metrics"]["context_characters"] for r in valid), 1) if valid else None,
                "mean_overlap_ratio":round(statistics.mean(r["metrics"]["source_overlap_ratio"] for r in valid), 4) if valid else None,
                "latency_seconds":percentiles([r["seconds"] for r in valid])}
        rows = [a for a in answers if a["group"] == key]
        scored = [a for a in rows if a.get("grade",{}).get("status") == "scored"]
        positive = [a for a in scored if a["question_spec"]["expected_behavior"] == "answer"]
        group_result["answers"] = {"questions":len(rows), "success":sum(a["status"] == "ok" for a in rows),
            "judge_success":len(scored), "task_pass":sum(a["grade"]["task_pass"] for a in scored),
            "strict_pass":sum(a["grade"]["strict_pass"] for a in scored),
            "mean_point_coverage":round(statistics.mean(a["grade"]["point_coverage"] for a in positive), 4) if positive else None,
            "source_identity_errors":sum(len(a.get("source_identity_errors",[])) for a in rows),
            "reported_answer_prompt_tokens":sum(int((c.get("usage") or {}).get("prompt_tokens") or 0) for a in rows for c in a.get("answer_calls",[]) if c["kind"] == "chat")}
        output[key] = group_result
    return output


def write_report(output, summary, metadata):
    lines = ["# 分块参数真实模型对照", "", f"状态：{metadata['run_status']}。", "",
        "固定原有 60 题（dev 20、test 40），沿用预选的 20 题回答验证。资料为合成运维手册。",
        "原始 Markdown 的三种参数均得到相同的 183 块，最长 178 字符，因此共用一次原始结构运行，不能冒充三次独立试验。",
        "长章节版本只将二级标题标记改为普通标签，正文、事实顺序、问题和答案点保持不变，重新定位原文证据区间。", "",
        "## test 集检索（只对可回答题计算覆盖）", "",
        "| 语料/参数 | 块数 | 成功 | 平均证据覆盖 | 完整覆盖 | MRR@5 | 平均上下文字符 | 2500字符预算覆盖 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, value in summary.items():
        row = value["retrieval"]["test"]
        pct = lambda x:f"{100*x:.2f}%" if x is not None else "—"
        lines.append(f"| {key} | {metadata['groups'][key]['chunks']} | {row['success']}/{row['questions']} | {pct(row['metrics']['mean_evidence_coverage'])} | {row['metrics']['full_evidence_questions']}/{row['answerable_questions']} | {row['metrics']['mrr_at_5']} | {row['mean_context_characters']} | {pct(row['budget_metrics']['mean_evidence_coverage'])} |")
    lines += ["", "## 固定 20 题回答验证", "", "| 语料/参数 | 回答成功 | 评分成功 | 任务通过 | 严格通过 | 提示词token总数 | 来源身份错误 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for key, value in summary.items():
        row = value["answers"]
        lines.append(f"| {key} | {row['success']}/{row['questions']} | {row['judge_success']}/{row['questions']} | {row['task_pass']}/{row['questions']} | {row['strict_pass']}/{row['questions']} | {row['reported_answer_prompt_tokens']} | {row['source_identity_errors']} |")
    lines += ["", "## 方法和边界", "",
        "- 长章节三组均使用同一 Hybrid/RRF 候选30、保守清理、真实 Rerank、MMR lambda0.8、最终Top-5；不修改生产参数。",
        "- 原始结构与长章节结构分别比较；不能将跨结构差异归因为分块长度。已有test集曾用于之前的评测，不宣称全新未接触验收集。",
        "- 文档块和查询向量由真实 Embedding API 生成并按精确文本缓存复用；三组共享同一查询向量。保存完整向量与调用usage。",
        "- Top-5 下大块可能提供更多正文，报告额外给出固定2500原文字符预算下的覆盖；按最终排名保留完整可装入片段，不截断。此预算检查是检索指标，回答仍使用生产Top-5。",
        "- 证据覆盖按必要证据组和原文字符区间并集计算；重叠不重复计分。冗余报告同文档字符区间重叠比例，不将重复提及事实等同语义重复。",
        "- 检索耗时不包括共用查询向量的生成时间；索引和API usage单独记录。",
        "- 评分使用相同回答模型盲评，单次、小样本、合成资料，需复核原始答案，不代表独立人工准确率。",
        f"- 正式MySQL、旧SQLite、上传文件及Chroma指纹不变：{metadata.get('official_data_unchanged')}。",
        "- 失败保留在题数中，不用重试成功覆盖失败；不运行Agent或SSE容量测试。",
        "", "完整数据：metadata.json、summary.json、retrieval.jsonl、answers.jsonl、chunks.json、embedding_cache.json、calls.jsonl。"]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--direct", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-answers", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1 or (args.limit is not None and args.limit < 1):
        parser.error("workers/limit 必须为正整数")
    manifest, questions, texts, sections = read_dataset()
    long_texts, long_sections, long_questions = transformed_corpus(texts, sections, questions)
    original_layouts = [layouts(texts, *config) for config in CONFIGS]
    assert original_layouts[0] == original_layouts[1] == original_layouts[2], "原始结构布局发生变化，需重新设计共享基线"
    groups = {"original_shared": {"texts":texts, "sections":sections, "questions":questions, "layout":original_layouts[1], "config":[500,100]}}
    for size, overlap in CONFIGS:
        groups[f"long_{size}_{overlap}"] = {"texts":long_texts, "sections":long_sections,
            "questions":long_questions, "layout":layouts(long_texts, size, overlap), "config":[size,overlap]}
    group_info = {key:{"config":g["config"], "documents":len(g["texts"]),
        "chunks":sum(len(items) for items in g["layout"].values()),
        "mean_chunk_characters":round(statistics.mean(len(x["content"]) for items in g["layout"].values() for x in items),1)} for key,g in groups.items()}
    if args.validate_only:
        print(json.dumps(group_info, ensure_ascii=False, indent=2))
        return
    if args.direct:
        os.environ.update(NO_PROXY="*", no_proxy="*")
    output = ROOT / "evals/results" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-chunking-v1" + (f"-pilot{args.limit}" if args.limit else ""))
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"run_status":"running", "phase":"preflight", "started_at_utc":datetime.now(timezone.utc).isoformat(),
        "dataset":manifest, "groups":group_info, "top_k":5, "candidate_count":30, "mmr_lambda":0.8,
        "embedding_model":os.getenv("EMBEDDING_MODEL","text-embedding-v4"),
        "rerank_model":os.getenv("RERANK_MODEL","gte-rerank-v2"), "answer_model":os.getenv("DEEPSEEK_MODEL","deepseek-flash"),
        "workers":args.workers, "network_mode":"direct" if args.direct else "environment", "limit":args.limit,
        "original_aliases":{f"{s}_{o}":"original_shared" for s,o in CONFIGS},
        "code_hashes":{str(p.relative_to(ROOT)):digest(p) for folder in ("retrieval","services","storage","evals") for p in (ROOT/folder).glob("*.py")}}
    records, answers, chunks, calls = [], [], {}, []
    def save():
        summary = summarize(records, answers, groups)
        metadata["usage"] = {kind:{"calls":sum(c["kind"]==kind for c in calls),
            "failed":sum(c["kind"]==kind and not c.get("success") for c in calls),
            "reported_total_tokens":sum(int((c.get("usage") or {}).get("total_tokens") or 0) for c in calls if c["kind"]==kind)} for kind in ("embedding","rerank","chat")}
        dump(output / "metadata.json", metadata)
        dump(output / "summary.json", summary)
        write_report(output, summary, metadata)
    def log_calls(phase, identifier, values):
        for value in values:
            row = {"phase":phase,"id":identifier,**value}
            calls.append(row)
            append(output / "calls.jsonl", row)
    metadata["official_data_before"] = official_state()
    for name in metadata["code_hashes"]:
        target = output / "code_snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    for key, group in groups.items():
        for name, text in group["texts"].items():
            target = output / "dataset_snapshot" / key / "documents" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        dump(output / "dataset_snapshot" / key / "questions.json", group["questions"])
        dump(output / "dataset_snapshot" / key / "sections.json", group["sections"])
    metadata["dataset_snapshot_hashes"] = {str(p.relative_to(output)):digest(p) for p in (output / "dataset_snapshot").rglob("*") if p.is_file()}
    save()
    try:
        with ExitStack() as stack, tempfile.TemporaryDirectory(prefix="chunking-eval-") as temporary:
            stack.enter_context(patch.dict(os.environ,{"RERANK_ENABLED":"true","RERANK_CANDIDATES":"30","MMR_ENABLED":"true","MMR_LAMBDA":"0.8"}))
            stack.enter_context(patch.object(Embeddings,"create",measured_sdk(Embeddings.create,"embedding")))
            stack.enter_context(patch.object(Completions,"create",measured_chat(Completions.create)))
            stack.enter_context(patch.object(reranker,"urlopen",measured_urlopen(reranker.urlopen)))
            db = Path(temporary) / "eval.db"
            database.init_test_db(db)
            metadata["phase"] = "embeddings"
            unique_texts = list(dict.fromkeys([x["content"] for g in groups.values() for items in g["layout"].values() for x in items] + [q["question"].strip() for q in questions[:args.limit]]))
            vectors = {}
            for offset in range(0,len(unique_texts),10):
                batch = unique_texts[offset:offset+10]
                current_calls = []
                token = CALLS.set(current_calls)
                try:
                    values = embedding.embed_texts(batch)
                    vectors.update(zip(batch,values))
                finally:
                    CALLS.reset(token)
                    log_calls("embedding",str(offset),current_calls)
                print("EMBED",min(offset+10,len(unique_texts)),"/",len(unique_texts),flush=True)
            dump(output / "embedding_cache.json", [{"text":text,"vector":value} for text,value in vectors.items()])
            metadata["unique_embedding_texts"] = len(vectors)
            client = chromadb.EphemeralClient()
            for key, group in groups.items():
                collection = client.create_collection("chunk-" + uuid4().hex, embedding_function=None, configuration={"hnsw":{"space":"cosine"}})
                stack.callback(client.delete_collection,collection.name)
                group["collection"] = collection
                with closing(database.connect_test_db(db)) as connection:
                    group["base_id"] = connection.execute("INSERT INTO knowledge_bases(title) VALUES (?)",(key,)).lastrowid
                    for name, doc_chunks in group["layout"].items():
                        doc_id = connection.execute("INSERT INTO documents(title,file_type,chunk_version,indexed_version) VALUES (?,'markdown',1,1)",(name,)).lastrowid
                        connection.execute("INSERT INTO knowledge_base_documents VALUES (?,?)",(group["base_id"],doc_id))
                        for index, chunk in enumerate(doc_chunks,1):
                            chunk_id = connection.execute("INSERT INTO document_chunks(document_id,chunk_index,content,source_name,start_char,end_char) VALUES (?,?,?,?,?,?)",
                                (doc_id,index,chunk["content"],name,chunk["start_char"],chunk["end_char"])).lastrowid
                            meta = {"document_id":doc_id,"chunk_id":chunk_id,"chunk_index":index,"source_name":name,
                                    "start_char":chunk["start_char"],"end_char":chunk["end_char"]}
                            chunks[chunk_id] = {**chunk,**meta,"document":name,"group":key,"redundancy_group":None,
                                "heading_only":not bool(reranker.clean_candidates([{"content":chunk["content"]}]))}
                            collection.add(ids=[f"doc:{doc_id}:chunk:{chunk_id}"], embeddings=[vectors[chunk["content"]]], documents=[chunk["content"]], metadatas=[meta])
                    connection.commit()
            dump(output / "chunks.json", chunks)
            stack.enter_context(patch.object(knowledge_base,"get_collection",lambda:COLLECTION.get()))
            stack.enter_context(patch.object(mmr,"get_collection",lambda:COLLECTION.get()))
            stack.enter_context(patch.object(knowledge_base,"embed_query",lambda question,**kwargs:vectors[question.strip()]))
            stack.enter_context(patch.object(knowledge_base,"candidate_count",lambda top_k:30))
            stack.enter_context(patch.object(knowledge_base,"rerank",lambda question,items,top_k,**kwargs:items))
            def retrieve(key, spec):
                row = {"group":key,"id":spec["id"],"question_spec":spec}
                values = []
                token = CALLS.set(values)
                collection_token = COLLECTION.set(groups[key]["collection"])
                start = time.perf_counter()
                try:
                    with closing(database.connect_test_db(db)) as connection:
                        pool = knowledge_base.search_base(connection,groups[key]["base_id"],SearchRequest(question=spec["question"],mode="hybrid",top_k=5))["items"]
                    pool = [{**chunks[x["chunk_id"]],**x} for x in pool]
                    row.update(pool=pool,pool_metrics=extra_metrics(spec,pool))
                    cleaned = reranker.clean_candidates(pool)
                    scored = reranker.rerank(spec["question"],cleaned,len(cleaned))
                    items = mmr.apply_mmr(scored,5,.8) if len(scored)>5 else scored[:5]
                    row.update(status="ok",scored=scored,items=items,metrics=extra_metrics(spec,items),
                               budget_items=within_budget(items),budget_metrics=extra_metrics(spec,within_budget(items)))
                except Exception as exc:
                    row.update(status="failed",error_type=type(exc).__name__,error_detail=getattr(exc,"detail",None))
                finally:
                    row["seconds"] = round(time.perf_counter()-start,4)
                    row["calls"] = values
                    CALLS.reset(token)
                    COLLECTION.reset(collection_token)
                return row
            metadata["phase"] = "retrieval"
            jobs = [(key,spec) for key,group in groups.items() for spec in group["questions"][:args.limit]]
            # 固定交错顺序提交，减少某一参数一直在最早/最晚时段运行的影响。
            jobs.sort(key=lambda x:(x[1]["id"], x[0]))
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                for future in as_completed([executor.submit(retrieve,*job) for job in jobs]):
                    row = future.result()
                    records.append(row)
                    append(output / "retrieval.jsonl",row)
                    log_calls("retrieval",row["group"]+"/"+row["id"],row["calls"])
                    save()
                    print("RETRIEVAL",len(records),"/",len(jobs),row["group"],row["id"],row["status"],flush=True)
            def answer_case(record):
                spec = record["question_spec"]
                row = {"group":record["group"],"id":spec["id"],"question_spec":spec}
                values = []
                token = CALLS.set(values)
                start = time.perf_counter()
                try:
                    if record["status"] != "ok":
                        raise ValueError("retrieval_failed")
                    response = llm.answer_with_chunks(spec["question"],record["items"])
                    errors = []
                    for citation in response.get("citations",[]):
                        source = chunks.get(citation["chunk_id"])
                        if source is None or source["document_id"] != citation["document_id"] or source["content"] != citation["content"]:
                            errors.append(citation["citation_id"])
                    row.update(status="ok",response=response,source_identity_errors=errors,answer_calls=list(values))
                    values.clear()
                    row["grade"] = grade_answer(spec,response)
                    row["judge_calls"] = list(values)
                except Exception as exc:
                    row.update(status="failed",error_type=type(exc).__name__,answer_calls=list(values))
                finally:
                    row["seconds"] = round(time.perf_counter()-start,4)
                    CALLS.reset(token)
                return row
            if not args.skip_answers:
                metadata["phase"] = "answers"
                selected = sorted([r for r in records if r["question_spec"]["answer_check"]],key=lambda r:(r["id"],r["group"]))
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    for future in as_completed([executor.submit(answer_case,row) for row in selected]):
                        row = future.result()
                        answers.append(row)
                        append(output / "answers.jsonl",row)
                        log_calls("answer",row["group"]+"/"+row["id"],row.get("answer_calls",[]))
                        log_calls("judge",row["group"]+"/"+row["id"],row.get("judge_calls",[]))
                        save()
                        print("ANSWER",len(answers),"/",len(selected),row["group"],row["id"],row["status"],row.get("grade",{}).get("task_pass"),flush=True)
            metadata.update(run_status="completed",phase="finished")
    except Exception as exc:
        metadata.update(run_status="failed",failure_type=type(exc).__name__)
        raise
    finally:
        metadata["official_data_after"] = official_state()
        metadata["official_data_unchanged"] = metadata["official_data_before"] == metadata["official_data_after"]
        metadata["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save()
        print("RESULT="+str(output),flush=True)


if __name__ == "__main__":
    main()
