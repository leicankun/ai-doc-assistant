<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import EmbeddingBackend from './EmbeddingBackend.vue'

const props = defineProps({ documents: { type: Array, required: true }, activeDocumentId: { type: [String, Number], default: '' }, embeddingProvider: { type: String, default: 'api' } })
const emit = defineEmits(['processing-updated', 'update:embeddingProvider'])
const documentId = ref('')
const question = ref('')
const mode = ref('bm25')
const embeddingProvider = computed({ get: () => props.embeddingProvider, set: value => emit('update:embeddingProvider', value) })
const localPreparing = ref(false)
const topK = ref(5)
const busy = ref('')
const error = ref('')
const notice = ref('')
const result = ref(null)
const ragResult = ref(null)
const streamStatus = ref('')
const modeNames = { bm25: 'BM25', dense: '向量检索', hybrid: '混合检索（RRF）' }
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
  ragResult.value = null
  streamStatus.value = ''
}

watch(documentId, reset)
watch(embeddingProvider, reset)
watch(() => props.activeDocumentId, id => { documentId.value = id }, { immediate: true })
watch(() => props.documents, () => {
  if (!selected.value) documentId.value = ''
})
onBeforeUnmount(reset)

async function run(action) {
  if (busy.value || localPreparing.value || !selected.value) return
  const isQuestion = action === 'search' || action === 'ask-rag' || action === 'ask-rag/stream'
  if (isQuestion && (!question.value.trim() || question.value.trim().length > 1000 || !Number.isInteger(topK.value) || topK.value < 1 || topK.value > 20)) {
    error.value = '请输入问题，返回数量应为 1 到 20 的整数。'
    return
  }
  const version = ++generation
  controller = new AbortController()
  busy.value = action
  error.value = ''
  notice.value = ''
  result.value = null
  ragResult.value = null
  streamStatus.value = ''
  try {
    const response = await fetch(`http://127.0.0.1:8000/documents/${documentId.value}/${action}`, {
      method: 'POST',
      signal: controller.signal,
      ...(isQuestion ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.value.trim(), mode: mode.value, top_k: topK.value, embedding_provider: embeddingProvider.value }),
      } : {}),
    })
    if (action === 'ask-rag/stream' && response.ok) {
      await readRagStream(response, version)
      return
    }
    const data = await response.json()
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail
        : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : `请求失败（${response.status}）`
      throw new Error(detail)
    }
    if (version !== generation) return
    if (action === 'search') result.value = data
    else if (action === 'ask-rag') ragResult.value = data
    else if (action === 'chunks') notice.value = `已生成 ${data.total} 个分块。使用向量检索前，请点击“向量入库”更新索引。`
    else notice.value = `向量入库成功：${data.indexed_chunks} 个分块，清理 ${data.removed_chunks} 条旧记录。`
    if (!isQuestion) emit('processing-updated')
  } catch (err) {
    if (version === generation) {
      if (err.name === 'AbortError') streamStatus.value = '已停止，回答未完成，引用未校验。'
      else {
        error.value = err.message || '请求失败，请稍后重试。'
        if (!isQuestion) emit('processing-updated')
        if (action === 'ask-rag/stream') streamStatus.value = '生成失败，回答未完成，引用未校验。'
      }
    }
  } finally {
    if (version === generation) busy.value = ''
  }
}

function stopStream() {
  controller?.abort()
}

async function readRagStream(response, version) {
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('服务未返回有效的流式响应。')
  }
  ragResult.value = { question: question.value.trim(), mode: mode.value, answer: '', citations: [], retrieved_chunks: null }
  streamStatus.value = '正在生成，引用将在完成后显示。'
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let complete = false
  function consume(block) {
    const payload = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    if (!payload) return
    const event = JSON.parse(payload)
    if (event.type === 'error') throw new Error(event.message || '生成失败')
    if (event.type === 'delta') {
      if (typeof event.content !== 'string') throw new Error('流式文本格式错误')
      ragResult.value.answer += event.content
    } else if (event.type === 'done') {
      if (!Array.isArray(event.citations)) throw new Error('引用格式错误')
      ragResult.value.citations = event.citations
      ragResult.value.retrieved_chunks = event.retrieved_chunks
      complete = true
      streamStatus.value = '回答已完成。'
    }
  }
  try {
    while (!complete) {
      const { value, done } = await reader.read()
      if (version !== generation) return
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let separator
      while (!complete && (separator = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator[0].length)
        consume(block)
      }
      if (done) break
    }
    if (!complete) throw new Error('连接提前结束，未收到完成事件，请重试。')
  } finally {
    try { await reader.cancel() } catch { /* Connection may already be closed. */ }
    reader.releaseLock()
  }
}

function metric(value) {
  return Number.isFinite(value) ? value.toFixed(4) : '—'
}
</script>

<template>
  <section class="search-panel" aria-labelledby="retrieval-heading" :aria-busy="Boolean(busy)">
    <h2 id="retrieval-heading">文档检索与问答</h2>
    <p class="hint">先生成分块；使用向量或混合检索时，再完成向量入库。检索结果展示相关原文。</p>
    <label class="field">
      <span>选择文档</span>
      <select v-model="documentId" :disabled="Boolean(busy)">
        <option value="" disabled>请选择文档</option>
        <option v-for="doc in documents" :key="doc.id" :value="doc.id">#{{ doc.id }} · {{ doc.title }}</option>
      </select>
    </label>
    <p v-if="!documents.length" class="hint">暂无文档，请先上传 Markdown 或 PDF 文件。</p>
    <EmbeddingBackend v-model="embeddingProvider" :document-id="documentId" :disabled="Boolean(busy)" @preparing="localPreparing = $event" />
    <p v-if="selected" class="hint" role="status">处理状态：{{ selected.processing_status_label }} · {{ selected.chunk_count }} 个分块。<span v-if="selected.processing_status === 'not_chunked'">请先生成分块。</span><span v-else-if="selected.processing_status !== 'indexed'">BM25 可用；向量或混合检索前请完成向量入库。</span><span v-else>可使用全部检索方式。</span></p>
    <div class="actions">
      <button type="button" :disabled="!selected || Boolean(busy) || localPreparing" @click="run('chunks')">{{ busy === 'chunks' ? '生成分块中…' : '生成 / 更新分块' }}</button>
      <button v-if="embeddingProvider === 'api'" type="button" :disabled="!selected || Boolean(busy)" @click="run('index')">{{ busy === 'index' ? '向量入库中…' : '向量入库' }}</button>
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
            <option value="hybrid">混合检索 · BM25 + 向量（RRF）</option>
          </select>
        </label>
        <label class="field"><span>返回数量（Top-K）</span>
          <input v-model.number="topK" type="number" min="1" max="20" step="1" required :disabled="Boolean(busy)" />
        </label>
        <button class="primary" type="submit" :disabled="!selected || Boolean(busy) || !question.trim()">{{ busy === 'search' ? '检索中…' : '开始检索' }}</button>
        <button class="primary" type="button" :disabled="!selected || Boolean(busy) || !question.trim()" @click="run('ask-rag')">{{ busy === 'ask-rag' ? '检索并生成回答中…' : '根据资料回答' }}</button>
        <button class="primary" type="button" :disabled="!selected || Boolean(busy) || !question.trim()" @click="run('ask-rag/stream')">{{ busy === 'ask-rag/stream' ? '流式生成中…' : '流式回答' }}</button>
        <button v-if="busy === 'ask-rag/stream'" type="button" @click="stopStream">停止生成</button>
      </div>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="success" role="status">{{ notice }}</p>
    <p v-if="streamStatus" class="hint" role="status">{{ streamStatus }}</p>
    <section v-if="ragResult" class="rag-result results" aria-live="polite">
      <h3>资料回答 · {{ modeNames[ragResult.mode] }}</h3>
      <p class="hint">问题：{{ ragResult.question }}<span v-if="ragResult.retrieved_chunks != null"> · 检索到 {{ ragResult.retrieved_chunks }} 个分块</span></p>
      <p class="original rag-answer">{{ ragResult.answer }}</p>
      <h4>引用原文</h4>
      <p v-if="!ragResult.citations.length && (!streamStatus || streamStatus === '回答已完成。')" class="hint">本次回答未提供引用。</p>
      <details v-for="citation in ragResult.citations" :key="citation.citation_id" class="result-card">
        <summary>[{{ citation.citation_id }}] {{ citation.source_name || selected?.title || '未知来源' }}<span v-if="citation.page_number != null"> · 第 {{ citation.page_number }} 页</span></summary>
        <p class="hint">分块 {{ citation.chunk_id }}<span v-if="citation.start_char != null"> · 字符 {{ citation.start_char }}–{{ citation.end_char }}</span></p>
        <p class="original">{{ citation.content }}</p>
      </details>
      <p class="hint">当前为单轮问答；回答中的 [编号] 对应上方引用原文。</p>
    </section>
    <div v-if="result" class="results" aria-live="polite">
      <h3>检索结果 · {{ result.total }} 条 · {{ modeNames[result.mode] }}</h3>
      <p class="hint">问题：{{ result.question }}</p>
      <p v-if="result.mode === 'dense'" class="hint">余弦距离越小，向量越接近；距离不代表答案正确率。</p>
      <p v-if="result.mode === 'hybrid'" class="hint">RRF 根据两路排名融合，分数越大越靠前；未进入某一路候选的分块，在该路不贡献分数。</p>
      <p v-if="!result.items.length" class="hint">未找到相关分块，请换个关键词重试。</p>
      <article v-for="item in result.items" :key="item.record_id || item.chunk_id" class="result-card">
        <div class="result-title"><strong>第 {{ item.rank }} 名 · 分块 {{ item.chunk_index ?? item.chunk_id }}</strong>
          <span v-if="result.mode === 'hybrid'">RRF 分数：{{ Number.isFinite(item.rrf_score) ? item.rrf_score.toFixed(6) : '—' }}</span>
          <span v-else>{{ result.mode === 'dense' ? '距离' : 'BM25 分数' }}：{{ metric(result.mode === 'dense' ? item.distance : item.score) }}</span>
        </div>
        <p v-if="result.mode === 'hybrid'" class="hint">BM25 排名：{{ item.bm25_rank ?? '未进入候选' }} · 分数：{{ metric(item.bm25_score) }}<br />向量排名：{{ item.dense_rank ?? '未进入候选' }} · 距离：{{ metric(item.dense_distance) }}</p>
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
.rag-answer { padding: 16px; background: #f5f8fc; border-radius: 10px; }
summary { cursor: pointer; overflow-wrap: anywhere; line-height: 1.6; }
@media (max-width: 600px) { .search-panel { padding: 16px; } .options { display: block; } .options button { margin-top: 16px; width: 100%; } }
</style>
