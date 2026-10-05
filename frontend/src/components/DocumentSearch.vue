<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({ documents: { type: Array, required: true } })
const documentId = ref('')
const question = ref('')
const mode = ref('bm25')
const topK = ref(5)
const busy = ref('')
const error = ref('')
const notice = ref('')
const result = ref(null)
const selected = computed(() => props.documents.find(doc => doc.id === documentId.value))
let controller
let generation = 0

function reset() {
  generation += 1
  controller?.abort()
  busy.value = ''
  error.value = ''
  notice.value = ''
  result.value = null
}

watch(documentId, reset)
watch(() => props.documents, () => {
  if (!selected.value) documentId.value = ''
})
onBeforeUnmount(reset)

async function run(action) {
  if (busy.value || !selected.value) return
  if (action === 'search' && (!question.value.trim() || !Number.isInteger(topK.value) || topK.value < 1 || topK.value > 20)) {
    error.value = '请输入问题，返回数量应为 1 到 20 的整数。'
    return
  }
  const version = ++generation
  controller = new AbortController()
  busy.value = action
  error.value = ''
  notice.value = ''
  result.value = null
  try {
    const response = await fetch(`http://127.0.0.1:8000/documents/${documentId.value}/${action}`, {
      method: 'POST',
      signal: controller.signal,
      ...(action === 'search' ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.value.trim(), mode: mode.value, top_k: topK.value }),
      } : {}),
    })
    const data = await response.json()
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail
        : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : `请求失败（${response.status}）`
      throw new Error(detail)
    }
    if (version !== generation) return
    if (action === 'search') result.value = data
    else if (action === 'chunks') notice.value = `已生成 ${data.total} 个分块。使用向量检索前，请点击“向量入库”更新索引。`
    else notice.value = `向量入库成功：${data.indexed_chunks} 个分块，清理 ${data.removed_chunks} 条旧记录。`
  } catch (err) {
    if (version === generation && err.name !== 'AbortError') error.value = err.message || '请求失败，请稍后重试。'
  } finally {
    if (version === generation) busy.value = ''
  }
}

function metric(value) {
  return Number.isFinite(value) ? value.toFixed(4) : '—'
}
</script>

<template>
  <section class="search-panel" aria-labelledby="retrieval-heading" :aria-busy="Boolean(busy)">
    <h2 id="retrieval-heading">文档检索</h2>
    <p class="hint">先生成分块；使用向量检索时，再完成向量入库。检索结果展示相关原文。</p>
    <label class="field">
      <span>选择文档</span>
      <select v-model="documentId" :disabled="Boolean(busy)">
        <option value="" disabled>请选择文档</option>
        <option v-for="doc in documents" :key="doc.id" :value="doc.id">#{{ doc.id }} · {{ doc.title }}</option>
      </select>
    </label>
    <p v-if="!documents.length" class="hint">暂无文档，请先上传 Markdown 或 PDF 文件。</p>
    <div class="actions">
      <button type="button" :disabled="!selected || Boolean(busy)" @click="run('chunks')">{{ busy === 'chunks' ? '生成分块中…' : '生成 / 更新分块' }}</button>
      <button type="button" :disabled="!selected || Boolean(busy)" @click="run('index')">{{ busy === 'index' ? '向量入库中…' : '向量入库' }}</button>
    </div>
    <form @submit.prevent="run('search')">
      <label class="field">
        <span>检索问题</span>
        <textarea v-model="question" rows="3" maxlength="1000" required placeholder="输入想在这篇文档中查找的问题" :disabled="Boolean(busy)" />
      </label>
      <div class="options">
        <label class="field"><span>检索方式</span>
          <select v-model="mode" :disabled="Boolean(busy)">
            <option value="bm25">BM25 · 词语匹配</option>
            <option value="dense">向量检索 · 语义匹配</option>
          </select>
        </label>
        <label class="field"><span>返回数量（Top-K）</span>
          <input v-model.number="topK" type="number" min="1" max="20" step="1" required :disabled="Boolean(busy)" />
        </label>
        <button class="primary" type="submit" :disabled="!selected || Boolean(busy) || !question.trim()">{{ busy === 'search' ? '检索中…' : '开始检索' }}</button>
      </div>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="success" role="status">{{ notice }}</p>
    <div v-if="result" class="results" aria-live="polite">
      <h3>检索结果 · {{ result.total }} 条 · {{ result.mode === 'dense' ? '向量检索' : 'BM25' }}</h3>
      <p class="hint">问题：{{ result.question }}</p>
      <p v-if="result.mode === 'dense'" class="hint">余弦距离越小，向量越接近；距离不代表答案正确率。</p>
      <p v-if="!result.items.length" class="hint">未找到相关分块，请换个关键词重试。</p>
      <article v-for="item in result.items" :key="item.record_id || item.chunk_id" class="result-card">
        <div class="result-title"><strong>第 {{ item.rank }} 名 · 分块 {{ item.chunk_index ?? item.chunk_id }}</strong>
          <span>{{ result.mode === 'dense' ? '距离' : 'BM25 分数' }}：{{ metric(result.mode === 'dense' ? item.distance : item.score) }}</span>
        </div>
        <p class="hint">来源：{{ item.source_name || selected?.title || '未知来源' }}<span v-if="item.page_number != null"> · 第 {{ item.page_number }} 页</span><span v-if="item.start_char != null"> · 字符 {{ item.start_char }}–{{ item.end_char }}</span></p>
        <p v-if="item.matched_terms?.length" class="hint">命中词：{{ item.matched_terms.join('、') }}</p>
        <p class="original">{{ item.content }}</p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.search-panel { margin-top: 24px; padding: 24px; background: #fff; border: 1px solid #dce3ed; border-radius: 16px; color: #243247; }
h2, h3 { margin: 0 0 12px; }
.hint { color: #576579; font-size: 14px; line-height: 1.6; }
.field { display: flex; flex-direction: column; gap: 8px; margin: 16px 0; }
select, textarea, input { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #bac6d6; border-radius: 8px; font: inherit; background: #fff; color: #243247; }
textarea { resize: vertical; }
.actions, .options, .result-title { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.options { align-items: end; }
.options .field { flex: 1; min-width: 160px; margin-bottom: 0; }
button { padding: 10px 16px; border: 1px solid #bac6d6; border-radius: 8px; background: #f5f8fc; color: #243247; cursor: pointer; font: inherit; }
button.primary { background: #245acc; color: white; border-color: #245acc; }
button:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, select:focus-visible, input:focus-visible, textarea:focus-visible { outline: 3px solid #91b5ff; outline-offset: 2px; }
.error, .success { padding: 12px; border-radius: 8px; overflow-wrap: anywhere; }
.error { color: #a22323; background: #fff0f0; }
.success { color: #21633c; background: #eef9f1; }
.results { margin-top: 24px; }
.result-card { border: 1px solid #dce3ed; border-radius: 10px; padding: 16px; margin-top: 12px; }
.result-title { justify-content: space-between; }
.original { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; margin-bottom: 0; }
@media (max-width: 600px) { .search-panel { padding: 16px; } .options { display: block; } .options button { margin-top: 16px; width: 100%; } }
</style>
