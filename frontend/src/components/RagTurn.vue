<script setup>
defineProps({ turn: { type: Object, required: true } })
const toolNames = { list_documents: '查看文档目录', search_knowledge_base: '检索知识库', read_chunk_context: '读取相邻上下文' }
</script>

<template>
  <article class="turn">
    <p class="label">用户问题</p>
    <p class="text question">{{ turn.question }}</p>
    <details v-if="turn.retrieval_question && turn.retrieval_question !== turn.question" class="rewrite">
      <summary>查看本轮检索问题</summary>
      <p class="text">{{ turn.retrieval_question }}</p>
    </details>
    <p class="label">资料回答</p>
    <p v-if="turn.embedding_provider === 'local' || turn.citations?.some(source => source.embedding_provider === 'local')" class="muted">本地 BGE-M3 检索</p>
    <p class="text answer">{{ turn.answer }}</p>
    <details v-if="turn.tool_calls?.length" class="tool-trace">
      <summary>工具执行记录 · {{ turn.tool_calls.length }} 次</summary>
      <ol>
        <li v-for="(call, index) in turn.tool_calls" :key="index">
          <strong>{{ toolNames[call.tool] || call.tool }}</strong>
          <p class="muted">{{ call.result?.error ? '执行失败' : '执行完成' }}<span v-if="call.result?.total != null"> · {{ call.result.total }} 条结果</span></p>
          <details><summary>查看参数和结果</summary><pre class="text tool-data">{{ JSON.stringify({ arguments: call.arguments, result: call.result }, null, 2) }}</pre></details>
        </li>
      </ol>
    </details>
    <p v-if="turn.status" class="muted" role="status">{{ turn.status }}</p>
    <p v-else-if="!turn.citations?.length" class="muted">本轮未提供引用。</p>
    <details v-for="source in turn.citations || []" :key="source.citation_id" class="citation">
      <summary>[{{ source.citation_id }}] {{ source.source_name || '未知来源' }}<span v-if="source.page_number != null"> · 第 {{ source.page_number }} 页</span></summary>
      <p class="muted">分块 {{ source.chunk_id }}<span v-if="source.start_char != null"> · 字符 {{ source.start_char }}–{{ source.end_char }}</span></p>
      <p class="text">{{ source.content }}</p>
    </details>
  </article>
</template>

<style scoped>
.turn { padding: 12px 0 24px; margin-top: 12px; }
.label { font-weight: 600; margin: 0 0 8px; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.question { margin: 0 0 24px auto; max-width: 85%; width: fit-content; background: #edf2e9; border-radius: 14px; padding: 12px 18px; }
.answer { margin: 8px 0; padding: 0; }
.muted, .rewrite { color: #576579; font-size: 14px; }
.rewrite { margin-bottom: 16px; }
.citation { padding: 12px; border-top: 1px solid #dce3ed; }
.tool-trace { margin: 14px 0; padding: 12px; background: #f6f8f4; border-radius: 10px; }
.tool-trace li { margin: 12px 0; }
.tool-data { font-size: 12px; max-height: 260px; overflow: auto; }
summary { cursor: pointer; line-height: 1.6; overflow-wrap: anywhere; }
</style>
