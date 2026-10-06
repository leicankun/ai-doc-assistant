<script setup>
defineProps({ turn: { type: Object, required: true } })
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
    <p class="text answer">{{ turn.answer }}</p>
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
.turn { border: 1px solid #dce3ed; border-radius: 12px; padding: 18px; margin-top: 16px; }
.label { font-weight: 600; margin: 0 0 8px; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.question { margin-top: 0; }
.answer { background: #f5f8fc; border-radius: 8px; padding: 12px; }
.muted, .rewrite { color: #576579; font-size: 14px; }
.rewrite { margin-bottom: 16px; }
.citation { padding: 12px; border-top: 1px solid #dce3ed; }
summary { cursor: pointer; line-height: 1.6; overflow-wrap: anywhere; }
</style>
