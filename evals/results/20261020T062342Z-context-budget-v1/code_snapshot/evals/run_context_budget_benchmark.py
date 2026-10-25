"""检索后上下文超预算专项：历史轨迹、真实固定检索计划、真实自主 Agent 对照。"""
import argparse
import contextvars
import copy
import json
import os
import sqlite3
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from openai.resources.chat.completions import Completions
from openai.resources.embeddings import Embeddings
from agent import runner, context
from agent.state import AgentState
from agent.tools import TOOLS, execute_tool
from retrieval import mmr, reranker
from services import knowledge_base
from storage import database
from evals.agentic_dataset import DOCUMENTS
from evals.context_budget_baseline import legacy_fit_tool_result
from evals.run_agentic_benchmark import (CALLS, measured_sdk, measured_urlopen,
    dump, append, digest, official_data_fingerprint)
from evals.run_retrieval_benchmark import grade_answer

SOURCE=ROOT/'evals/results/20261013T114543Z-agentic'
VECTOR_SOURCE=ROOT/'evals/results/20261020T052604Z-retrieval-v1'
EVENTS=contextvars.ContextVar('budget_events',default=None)
REQUESTS=contextvars.ContextVar('budget_requests',default=None)


def read_rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines()]


def make_call(index,name,arguments):
    if not isinstance(arguments,str):
        arguments=json.dumps(arguments,ensure_ascii=False)
    return SimpleNamespace(id=f'budget_call_{index}',function=SimpleNamespace(name=name,arguments=arguments))


def append_call(state,call):
    state.messages.append({'role':'assistant','tool_calls':[{'id':call.id,'type':'function',
        'function':{'name':call.function.name,'arguments':call.function.arguments}}]})


def observed_fit(policy):
    def fit(result,state,call,question,history,summary,budget,tools):
        before=budget.used(state.messages,tools)
        probe=copy.deepcopy(state)
        registered=probe.register_evidence(copy.deepcopy(result))
        probe.tool_calls.append({'tool':call.function.name,'arguments':call.function.arguments,'result':registered})
        probe.messages.append({'role':'tool','tool_call_id':call.id,'content':json.dumps(registered,ensure_ascii=False)})
        raw_after=budget.used(probe.messages,tools)
        compacted,duplicates=context.compact_tool_messages(probe.messages)
        final_size=budget.used(context.final_messages(question,history,probe,summary))
        row={'tool':call.function.name,'call_id':call.id,'items_received':len(result.get('items',[])),
            'budget':budget.limit,'before_result_estimate':before,'raw_after_result_estimate':raw_after,
            'compact_after_result_estimate':budget.used(compacted,tools),'full_final_estimate':final_size,
            'raw_over_budget':raw_after>budget.limit,
            'crossed_after_retrieval':bool(result.get('items')) and before<=budget.limit<raw_after,
            'preview_duplicate_bodies':duplicates}
        try:
            fitted,omitted,exhausted=policy(result,state,call,question,history,summary,budget,tools)
            row.update(omitted_items=omitted,finalize=exhausted,
                retained_items=len(fitted.get('items',[])),
                action='finalize' if exhausted else 'continue')
            return fitted,omitted,exhausted
        except Exception as exc:
            row.update(action='error',error_type=type(exc).__name__)
            raise
        finally:
            if EVENTS.get() is not None:
                EVENTS.get().append(row)
    return fit


def observed_chat(original):
    measured=measured_sdk(original,'chat')
    def create(self,*args,**kwargs):
        requests=REQUESTS.get()
        if requests is not None:
            used=context.ContextBudget().used(kwargs['messages'],kwargs.get('tools'))
            requests.append({'estimated_units':used,'budget':24000,'within_budget':used<=24000,
                'tools_enabled':bool(kwargs.get('tools')),'messages':copy.deepcopy(kwargs['messages']),
                'tools':copy.deepcopy(kwargs.get('tools',[]))})
        return measured(self,*args,**kwargs)
    return create


def grade_spec(spec):
    return {**spec,'evidence_groups':[{'alternatives':[source]} for source in spec['evidence']]}


def summarize(rows):
    summary={}
    for phase in ['historical_replay','live_fixed_plan','live_agent']:
        summary[phase]={}
        for policy in ['before','after']:
            group=[r for r in rows if r['phase']==phase and r['policy']==policy]
            events=[e for r in group for e in r.get('events',[])]
            requests=[q for r in group for q in r.get('requests',[])]
            summary[phase][policy]={'cases':len(group),'success':sum(r['status']=='ok' for r in group),
                'cases_with_retrieval_crossing':sum(any(e['crossed_after_retrieval'] for e in r.get('events',[])) for r in group),
                'retrieval_crossing_events':sum(e['crossed_after_retrieval'] for e in events),
                'omitted_items':sum(e.get('omitted_items',0) for e in events),
                'early_finalizations':sum(e.get('finalize',False) for e in events),
                'deduplicated_bodies':sum(r.get('deduplicated_bodies',0) for r in group),
                'model_requests':len(requests),'requests_over_budget':sum(not q['within_budget'] for q in requests),
                'task_pass':sum(r.get('grade',{}).get('task_pass',False) for r in group),
                'judge_success':sum(r.get('grade',{}).get('status')=='scored' for r in group),
                'chat_tokens':sum(int((c.get('usage') or {}).get('total_tokens') or 0) for r in group for c in r.get('answer_calls',[]) if c['kind']=='chat'),
                'citation_identity_errors':sum(len(r.get('citation_identity_errors',[])) for r in group)}
    return summary


def write_report(output,rows,metadata):
    summary=summarize(rows)
    dump(output/'summary.json',summary)
    lines=['# 检索后上下文超预算专项','',
        f"状态：{metadata.get('status','running')}；预算固定24000个保守估算单位，包含2000输出预留，不是模型硬限制。",'',
        '| 场景 | 策略 | 完成 | 结果加入后跨过预算的案例 | 省略项 | 重复正文压缩 | 真实模型请求超预算 |',
        '|---|---|---:|---:|---:|---:|---:|']
    for phase,policies in summary.items():
        for policy,s in policies.items():
            lines.append(f"| {phase} | {policy} | {s['success']}/{s['cases']} | {s['cases_with_retrieval_crossing']} | {s['omitted_items']} | {s['deduplicated_bodies']} | {s['requests_over_budget']}/{s['model_requests']} |")
    examples=[(r,e) for r in rows for e in r.get('events',[]) if e['crossed_after_retrieval']]
    lines+=['','## 明确跨预算的证据','']
    for r,e in examples[:10]:
        lines.append(f"- {r['phase']} / {r['id']} / {r['policy']}：加入结果前 {e['before_result_estimate']}，加入后 {e['raw_after_result_estimate']} > {e['budget']}；去重后 {e['compact_after_result_estimate']}，完整最终证据 {e['full_final_estimate']}；省略 {e.get('omitted_items')} 项，结束工具阶段 {e.get('finalize')}。")
    lines+=['','## 方法与限制','',
        '- before保留修复前按新结果尾部删减的算法，不压缩工具正文；after使用当前生产修复。两组使用相同提示词、预算和检索配置。',
        '- 历史回放使用2026-10-13保存的真实检索原始轨迹，两个策略消费相同轨迹；不是重新自主检索或重新生成历史模型回答。',
        '- 固定计划使用本次真实Embedding查询和重排检索结果，两个策略复用完全相同结果，再分别真实生成最终回答；检索顺序由评测预先规定，不冒充自主Agent。',
        '- live_agent使用真实生产Agent自行选择工具；每组独立运行，轨迹可能不同，属于整体行为比较。',
        '- 超预算判据明确要求：结果加入前≤24000、加入完整检索结果后>24000。不是单纯因512安全余量触发，也不是超长问题直接超限。',
        '- 记录的原始结果加入后估算可以超过预算；实际发送给模型的每次请求仍必须在预算内。',
        '- 模型输出与评分API均为真实调用；同模型自动评分仅辅助观察，不代表独立人工准确率。',
        '- 向量复用此前真实模型生成的快照，在独立SQLite/内存Chroma恢复；未使用模拟向量。',
        f"- 正式数据库与上传文件保持一致：{metadata.get('official_data_unchanged')}。",
        '- 原始工具结果和来源留在执行记录，只有模型消息内的重复正文换成引用标记；不同来源和版本不按语义合并。',
        '- 单次试验；未扩大上下文预算、修改分块大小或更换重排/MMR配置，不声称解决所有独特证据本身过长的情况。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def replay(spec,events,policy,phase='historical_replay'):
    state=AgentState(1,messages=context.build_messages(spec['question']))
    budget=context.ContextBudget(limit=24000)
    observed=[]
    token=EVENTS.set(observed)
    row={'id':spec['id'],'question_spec':spec,'phase':phase,'policy':policy,'status':'running'}
    fitter=observed_fit(legacy_fit_tool_result if policy=='before' else context.fit_tool_result)
    try:
        budget.check(state.messages,TOOLS)
        for index,event in enumerate(events,1):
            if not state.can_call_tool:
                break
            current=make_call(index,event['tool'],event['arguments'])
            append_call(state,current)
            fitted,omitted,exhausted=fitter(copy.deepcopy(event['result']),state,current,spec['question'],[],None,budget,TOOLS)
            state.context_omitted+=omitted
            state.context_exhausted=exhausted
            result=state.register_evidence(fitted)
            state.tool_calls.append({'tool':current.function.name,'arguments':current.function.arguments,'result':result})
            state.messages.append({'role':'tool','tool_call_id':current.id,'content':json.dumps(result,ensure_ascii=False)})
            if policy=='after':
                state.messages,replaced=context.compact_tool_messages(state.messages)
                state.context_deduplicated+=replaced
            if state.can_call_tool:
                budget.check(state.messages,TOOLS)
        final=context.final_messages(spec['question'],[],state)
        budget.check(final)
        row.update(status='ok',final_messages=final,evidence=list(state.evidence.values()),
            tool_calls=state.tool_calls,deduplicated_bodies=state.context_deduplicated,
            final_estimate=budget.used(final),peak_request_estimate=budget.peak)
    except Exception as exc:
        row.update(status='failed',error_type=type(exc).__name__)
    finally:
        EVENTS.reset(token)
        row['events']=observed
    return row


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--direct',action='store_true')
    parser.add_argument('--replay-only',action='store_true')
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    if args.workers<1:
        parser.error('workers must be positive')
    load_dotenv(ROOT/'.env')
    if args.direct:
        os.environ.update(NO_PROXY='*',no_proxy='*')
    original=read_rows(SOURCE/'answers.jsonl')
    historic=[r for r in original if r['arm']=='agent' and r.get('response',{}).get('context_usage',{}).get('omitted_tool_items',0)>0]
    specs={r['id']:r['question_spec'] for r in original}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output=ROOT/'evals/results'/(stamp+'-context-budget-v1'+('-replay' if args.replay_only else ''))
    output.mkdir(parents=True,exist_ok=False)
    metadata={'started_at_utc':datetime.now(timezone.utc).isoformat(),'status':'running',
        'budget':24000,'output_reserve':2000,'margin':512,'historical_ids':[r['id'] for r in historic],
        'vector_source':str(VECTOR_SOURCE.relative_to(ROOT)),
        'source_hashes':{str(p.relative_to(ROOT)):digest(p) for p in
            [SOURCE/'answers.jsonl',VECTOR_SOURCE/'chunks.json',VECTOR_SOURCE/'vectors.json']},
        'code_hashes':{str(p.relative_to(ROOT)):digest(p) for folder in ['agent','evals'] for p in (ROOT/folder).glob('*.py')},
        'official_before':official_data_fingerprint(),'network':'direct' if args.direct else 'environment',
        'rerank_enabled':True,'mmr_enabled':True,'mmr_lambda':.8,
        'answer_model':os.getenv('DEEPSEEK_MODEL','deepseek-flash'),
        'design':'8个历史失败轨迹双策略回放；3个固定真实检索计划双策略；4道真实自主Agent双策略。'}
    dump(output/'metadata.json',metadata)
    for name in metadata['code_hashes']:
        target=output/'code_snapshot'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((ROOT/name).read_bytes())
    rows=[]
    def save(row):
        rows.append(row)
        append(output/'results.jsonl',row)
        write_report(output,rows,metadata)
        print(row['phase'],row['id'],row['policy'],row['status'],
              'crossed',sum(e['crossed_after_retrieval'] for e in row.get('events',[])),
              'omitted',sum(e.get('omitted_items',0) for e in row.get('events',[])),flush=True)
    try:
        with patch.dict(os.environ,{'AGENT_CONTEXT_TOKENS':'24000','AGENT_MAX_TOOL_CALLS':'6',
                'AGENT_TIMEOUT_SECONDS':'120','RERANK_ENABLED':'true','MMR_ENABLED':'true','MMR_LAMBDA':'0.8'}):
            for old in historic:
                for policy in ['before','after']:
                    save(replay(old['question_spec'],old['tool_events'],policy))
            if not args.replay_only:
                with tempfile.TemporaryDirectory(prefix='budget-eval-') as temporary, ExitStack() as stack:
                    db=Path(temporary)/'test.db'
                    client=chromadb.EphemeralClient()
                    collection=client.create_collection('context-'+uuid4().hex,embedding_function=None,
                        configuration={'hnsw':{'space':'cosine'}})
                    stack.callback(client.delete_collection,collection.name)
                    stack.enter_context(patch.object(database,'DB_PATH',db))
                    stack.enter_context(patch.object(knowledge_base,'get_collection',return_value=collection))
                    stack.enter_context(patch.object(mmr,'get_collection',return_value=collection))
                    stack.enter_context(patch.object(Embeddings,'create',measured_sdk(Embeddings.create,'embedding')))
                    stack.enter_context(patch.object(reranker,'urlopen',measured_urlopen(reranker.urlopen)))
                    stack.enter_context(patch.object(Completions,'create',observed_chat(Completions.create)))
                    chunks={int(k):v for k,v in json.loads((VECTOR_SOURCE/'chunks.json').read_text(encoding='utf-8')).items()}
                    vectors=json.loads((VECTOR_SOURCE/'vectors.json').read_text(encoding='utf-8'))
                    vector_map=dict(zip(vectors['ids'],vectors['embeddings']))
                    source_meta=json.loads((VECTOR_SOURCE/'metadata.json').read_text(encoding='utf-8'))
                    database.init_db()
                    with closing(sqlite3.connect(db)) as connection:
                        connection.row_factory=sqlite3.Row
                        connection.execute("INSERT INTO knowledge_bases(id,title) VALUES (1,'budget')")
                        for name,doc_id in source_meta['document_ids'].items():
                            connection.execute('INSERT INTO documents(id,title,file_type,chunk_version,indexed_version) VALUES (?,?,?,1,1)',(doc_id,name,'markdown'))
                            if name in DOCUMENTS:
                                connection.execute('INSERT INTO knowledge_base_documents VALUES (1,?)',(doc_id,))
                        for chunk in chunks.values():
                            connection.execute('INSERT INTO document_chunks(id,document_id,chunk_index,content,source_name,start_char,end_char) VALUES (?,?,?,?,?,?,?)',
                                tuple(chunk[k] for k in ['chunk_id','document_id','chunk_index','content','source_name','start_char','end_char']))
                        connection.commit()
                    ids=[f"doc:{c['document_id']}:chunk:{c['chunk_id']}" for c in chunks.values()]
                    collection.upsert(ids=ids,embeddings=[vector_map[i] for i in ids],
                        documents=[c['content'] for c in chunks.values()],
                        metadatas=[{k:c[k] for k in ['document_id','chunk_id','chunk_index','source_name','start_char','end_char']} for c in chunks.values()])
                    def search(query):
                        return {'tool':'search_knowledge_base','arguments':{'query':query,'top_k':10,'mode':'hybrid'}}
                    def neighbor(needle):
                        chunk=next(c for c in chunks.values() if needle in c['content'])
                        return {'tool':'read_chunk_context','arguments':{'chunk_id':chunk['chunk_id'],'before':2,'after':2}}
                    plans=[('repeat-backup','a009',[search(specs['a009']['question']) for _ in range(6)]),
                        ('complementary-launch','a006',[
                            search('Atlas 2.4 上线依赖启动顺序和就绪检查'),
                            search('Atlas 2.4 生产认证OIDC和权限'),
                            search('Atlas 2.4 上线完整备份数据库附件'),
                            search('Atlas 2.4 配置优先级和端口'),
                            search('Atlas 2.4 迁移前后校验'),search('Atlas 2.4 上线检查清单')]),
                        ('neighbor-rollback','a019',[search(specs['a019']['question']),
                            neighbor('回滚必须停止新版'),neighbor('恢复顺序是停止应用'),
                            neighbor('回滚必须停止新版'),neighbor('恢复顺序是停止应用'),
                            search('Atlas 1.8 回滚恢复数据和健康检查')])]
                    for identifier,spec_id,plan in plans:
                        live_events=[]
                        calls=[]
                        token=CALLS.set(calls)
                        try:
                            with closing(sqlite3.connect(db)) as connection:
                                connection.row_factory=sqlite3.Row
                                for entry in plan:
                                    result=execute_tool(connection,1,entry['tool'],json.dumps(entry['arguments'],ensure_ascii=False),deadline=time.monotonic()+120)
                                    if 'error' in result:
                                        raise RuntimeError(f"真实检索失败：{result.get('error')}")
                                    live_events.append({**entry,'result':result})
                        finally:
                            CALLS.reset(token)
                            append(output/'retrieval_calls.jsonl',{'id':identifier,'calls':calls,'events':live_events})
                        spec={**specs[spec_id],'id':identifier}
                        for policy in ['before','after']:
                            row=replay(spec,live_events,policy,phase='live_fixed_plan')
                            requests=[]
                            answer_calls=[]
                            rt=REQUESTS.set(requests)
                            ct=CALLS.set(answer_calls)
                            try:
                                if row['status']!='ok':
                                    raise RuntimeError('context_fit_failed')
                                with OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url='https://api.deepseek.com',timeout=60,max_retries=0) as api:
                                    response=api.chat.completions.create(model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'),
                                        messages=row['final_messages'],response_format={'type':'json_object'},
                                        max_tokens=2000,extra_body={'thinking':{'type':'disabled'}})
                                assert response.choices[0].finish_reason=='stop'
                                state=AgentState(1)
                                state.evidence={(x['document_id'],x['chunk_id']):x for x in row['evidence']}
                                state.tool_calls=row['tool_calls']
                                row['response']=state.validate_final(response.choices[0].message.content,spec['question'])
                            except Exception as exc:
                                row.update(status='failed',error_type=type(exc).__name__)
                            finally:
                                REQUESTS.reset(rt)
                                CALLS.reset(ct)
                                row.update(requests=requests,answer_calls=answer_calls)
                            if row['status']=='ok':
                                judge_calls=[]
                                ct=CALLS.set(judge_calls)
                                try:
                                    row['grade']=grade_answer(grade_spec(spec),row['response'])
                                finally:
                                    CALLS.reset(ct)
                                    row['judge_calls']=judge_calls
                            row['citation_identity_errors']=[c['citation_id'] for c in row.get('response',{}).get('citations',[])
                                if c['content']!=chunks[c['chunk_id']]['content'] or c['document_id']!=chunks[c['chunk_id']]['document_id']]
                            save(row)
                    def live_agent(spec,policy):
                        row={'id':spec['id'],'question_spec':spec,'phase':'live_agent','policy':policy}
                        calls,events,requests=[],[],[]
                        ct=CALLS.set(calls); et=EVENTS.set(events); rt=REQUESTS.set(requests)
                        started=time.perf_counter()
                        try:
                            with closing(sqlite3.connect(db)) as connection:
                                connection.row_factory=sqlite3.Row
                                response=runner.run_document_agent(connection,1,spec['question'])
                            row.update(status='ok',response=response,
                                deduplicated_bodies=response['context_usage']['deduplicated_tool_items'])
                        except Exception as exc:
                            row.update(status='failed',error_type=type(exc).__name__)
                        finally:
                            CALLS.reset(ct); EVENTS.reset(et); REQUESTS.reset(rt)
                            row.update(events=events,requests=requests,answer_calls=calls,seconds=time.perf_counter()-started)
                        if row['status']=='ok':
                            judge_calls=[]
                            ct=CALLS.set(judge_calls)
                            try:
                                row['grade']=grade_answer(grade_spec(spec),row['response'])
                            finally:
                                CALLS.reset(ct); row['judge_calls']=judge_calls
                            row['citation_identity_errors']=[c['citation_id'] for c in response.get('citations',[])
                                if c['content']!=chunks[c['chunk_id']]['content'] or c['document_id']!=chunks[c['chunk_id']]['document_id']]
                        return row
                    for policy in ['before','after']:
                        fitter=legacy_fit_tool_result if policy=='before' else context.fit_tool_result
                        compactor=(lambda messages:(messages,0)) if policy=='before' else context.compact_tool_messages
                        with patch.object(runner,'fit_tool_result',observed_fit(fitter)),patch.object(runner,'compact_tool_messages',compactor):
                            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                                for future in as_completed([pool.submit(live_agent,specs[i],policy) for i in ['a006','a009','a013','a019']]):
                                    save(future.result())
            metadata['status']='completed'
    except Exception as exc:
        metadata.update(status='failed',error_type=type(exc).__name__)
        raise
    finally:
        metadata['official_after']=official_data_fingerprint()
        metadata['official_data_unchanged']=metadata['official_before']==metadata['official_after']
        metadata['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        dump(output/'metadata.json',metadata)
        write_report(output,rows,metadata)
        print('RESULT='+str(output),flush=True)


if __name__=='__main__':
    main()
