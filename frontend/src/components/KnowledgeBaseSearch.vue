<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiError } from '../utils/apiError'
const props = defineProps({ base: { type: Object, required: true } })
const question = ref('')
const mode = ref('hybrid')
const topK = ref(5)
const result = ref(null)
const error = ref('')
const busy = ref(false)
let controller
let generation = 0
onBeforeUnmount(() => { generation++; controller?.abort() })
watch(() => props.base.id, () => { generation++; controller?.abort(); result.value = null; error.value = ''; busy.value = false })
async function search() {
  const current = ++generation
  controller = new AbortController(); busy.value = true; error.value = ''; result.value = null
  try {
    const response = await fetch(`http://127.0.0.1:8000/knowledge-bases/${props.base.id}/search`, { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: question.value.trim(), mode: mode.value, top_k: topK.value }) })
    const data = await response.json()
    if (!response.ok) throw new Error(apiError(data, response.status))
    if (current === generation) result.value = data
  } catch (err) { if (current === generation && err.name !== 'AbortError') error.value = err.message } finally { if (current === generation) busy.value = false }
}
</script>
<template><section class="kb-search"><h2>{{ base.title }} · 跨文档检索</h2><p>Top-K 是整个知识库的返回总数。成员文档需全部就绪。</p><form @submit.prevent="search"><label>检索问题<input v-model="question" maxlength="1000" required :disabled="busy" /></label><label>检索方式<select v-model="mode" :disabled="busy"><option value="bm25">BM25</option><option value="dense">向量检索</option><option value="hybrid">混合检索</option></select></label><label>Top-K<input v-model.number="topK" type="number" min="1" max="20" required :disabled="busy" /></label><button :disabled="busy || !question.trim()">{{ busy ? '检索中…' : '检索知识库' }}</button></form><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="result">检索到 {{ result.total }} 个分块</p><details v-for="item in result?.items || []" :key="`${item.document_id}:${item.chunk_id}`"><summary>{{ item.rank }} · {{ item.source_name }}<span v-if="item.page_number"> · 第 {{ item.page_number }} 页</span></summary><p class="content">{{ item.content }}</p></details></section></template>
<style scoped>.kb-search{padding:20px 0}form{display:flex;flex-wrap:wrap;gap:12px;align-items:end}label{display:grid;gap:6px}input,select,button{padding:9px;border:1px solid #d9e1d9;border-radius:7px;font:inherit}details{border-top:1px solid #e3e8e1;padding:14px 0;margin-top:10px}summary{cursor:pointer}.content{white-space:pre-wrap;overflow-wrap:anywhere}.error{color:#ad3939}</style>
