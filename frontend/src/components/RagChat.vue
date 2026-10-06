<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import RagTurn from './RagTurn.vue'
import { readSse } from '../utils/readSse'

const props = defineProps({ documents: { type: Array, required: true } })
const documentId = ref('')
const sessionId = ref('')
const sessions = ref([])
const turns = ref([])
const title = ref('')
const question = ref('')
const mode = ref('hybrid')
const topK = ref(5)
const busy = ref('')
const error = ref('')
const pending = ref(null)
const selected = computed(() => props.documents.some(doc => doc.id === documentId.value))
let version = 0
let controller

function invalidate() {
  version += 1
  controller?.abort()
  busy.value = ''
  error.value = ''
  pending.value = null
}

async function request(path, body, signal) {
  const response = await fetch(`http://127.0.0.1:8000${path}`, {
    signal,
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail
      : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : `请求失败（${response.status}）`)
  }
  return data
}

async function operate(action, work) {
  if (busy.value) return
  const current = ++version
  controller = new AbortController()
  busy.value = action
  error.value = ''
  try {
    await work(controller.signal, () => current === version)
  } catch (err) {
    if (current === version) {
      if (err.name !== 'AbortError') error.value = err.message || '请求失败，请稍后重试。'
      if (pending.value) pending.value.status = err.name === 'AbortError' ? '已停止，回答未完成。重新加载历史可确认是否已保存。' : '生成失败，本轮未确认保存。'
    }
  } finally {
    if (current === version) busy.value = ''
  }
}

async function loadSessions() {
  if (!selected.value) return
  await operate('sessions', async (signal, active) => {
    const data = await request(`/documents/${documentId.value}/sessions`, null, signal)
    if (active()) sessions.value = data.items
  })
}

async function loadHistory() {
  if (!selected.value || !sessionId.value) return
  turns.value = []
  await operate('history', async (signal, active) => {
    const data = await request(`/documents/${documentId.value}/sessions/${sessionId.value}/rag-history`, null, signal)
    if (active()) turns.value = data.items
  })
}

async function createSession() {
  if (!selected.value || !title.value.trim() || busy.value) return
  await operate('create', async (signal, active) => {
    const data = await request(`/documents/${documentId.value}/sessions`, { title: title.value.trim() }, signal)
    if (active()) {
      sessions.value.unshift(data)
      title.value = ''
      sessionId.value = data.id
    }
  })
}

async function ask() {
  if (!selected.value || !sessionId.value || busy.value) return
  if (!question.value.trim() || question.value.trim().length > 1000 || !Number.isInteger(topK.value) || topK.value < 1 || topK.value > 20) {
    error.value = '请输入 1–1000 字符的问题，Top-K 应为 1–20 的整数。'
    return
  }
  await operate('ask', async (signal, active) => {
    pending.value = { question: question.value.trim(), answer: '', citations: [], status: '正在生成，引用将在完成后显示。' }
    const response = await fetch(`http://127.0.0.1:8000/documents/${documentId.value}/ask-rag/chat/stream`, {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question.value.trim(), session_id: sessionId.value, mode: mode.value, top_k: topK.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}）`)
    }
    await readSse(response, event => {
      if (!active()) return
      if (event.type === 'meta') pending.value.retrieval_question = event.retrieval_question
      else if (event.type === 'delta') pending.value.answer += event.content
      else if (event.type === 'done') {
        turns.value.push({ ...pending.value, ...event, status: null })
        pending.value = null
        question.value = ''
      }
    })
  })
}

watch(documentId, async () => {
  invalidate()
  sessions.value = []
  sessionId.value = ''
  turns.value = []
  question.value = ''
  title.value = ''
  await loadSessions()
})
watch(sessionId, async () => {
  invalidate()
  turns.value = []
  question.value = ''
  await loadHistory()
})
watch(() => props.documents, () => {
  if (!selected.value) documentId.value = ''
})
onBeforeUnmount(invalidate)
</script>

<template>
  <section class="rag-chat" aria-labelledby="rag-chat-heading" :aria-busy="Boolean(busy)">
    <h2 id="rag-chat-heading">多轮 RAG 对话</h2>
    <p class="muted">每轮重新检索资料，结合最近 5 轮理解追问，逐段显示回答。引用编号仅对应所在轮次。</p>
    <p class="muted">使用向量或混合检索前，请在上方完成分块和向量入库。</p>
    <div class="grid">
      <label>文档<select v-model="documentId" :disabled="Boolean(busy)"><option value="" disabled>请选择文档</option><option v-for="doc in documents" :key="doc.id" :value="doc.id">#{{ doc.id }} · {{ doc.title }}</option></select></label>
      <label>RAG 会话<select v-model="sessionId" :disabled="Boolean(busy) || !sessions.length"><option value="" disabled>请选择会话</option><option v-for="session in sessions" :key="session.id" :value="session.id">{{ session.title }}</option></select></label>
    </div>
    <p v-if="selected && !sessions.length && !busy" class="muted">暂无会话，请创建一个新会话。</p>
    <form class="actions" @submit.prevent="createSession">
      <label class="grow">新会话名称<input v-model="title" maxlength="100" placeholder="例如：SQLite 学习" :disabled="Boolean(busy) || !selected" required /></label>
      <button :disabled="Boolean(busy) || !selected || !title.trim()">{{ busy === 'create' ? '创建中…' : '创建 RAG 会话' }}</button>
    </form>
    <div class="actions"><button type="button" :disabled="Boolean(busy) || !selected" @click="loadSessions">刷新会话</button><button type="button" :disabled="Boolean(busy) || !sessionId" @click="loadHistory">重新加载历史</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy" class="muted" role="status">{{ busy === 'ask' ? '正在理解问题、检索资料并生成回答…' : '正在加载或创建会话…' }}</p>
    <div class="conversation" aria-live="polite">
      <RagTurn v-for="turn in turns" :key="turn.id" :turn="turn" />
      <RagTurn v-if="pending" :turn="pending" />
      <p v-if="sessionId && !turns.length && !busy && !error" class="muted">这个会话还没有 RAG 问答，输入第一个问题开始吧。</p>
    </div>
    <form @submit.prevent="ask">
      <label>问题或追问<textarea v-model="question" rows="3" maxlength="1000" placeholder="例如：它有什么限制？" :disabled="Boolean(busy) || !sessionId" required /></label>
      <div class="actions">
        <label class="grow">检索方式<select v-model="mode" :disabled="Boolean(busy)"><option value="bm25">BM25</option><option value="dense">向量检索</option><option value="hybrid">混合检索（RRF）</option></select></label>
        <label>Top-K<input v-model.number="topK" type="number" min="1" max="20" step="1" required :disabled="Boolean(busy)" /></label>
        <button class="primary" :disabled="Boolean(busy) || !sessionId || !question.trim()">{{ busy === 'ask' ? '回答中…' : '发送问题' }}</button>
        <button v-if="busy === 'ask'" type="button" @click="controller?.abort()">停止生成</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.rag-chat { margin-top: 24px; padding: 24px; border: 1px solid #dce3ed; border-radius: 16px; background: #fff; color: #243247; }
h2 { margin-top: 0; }
.muted { color: #576579; line-height: 1.6; font-size: 14px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
label { display: flex; flex-direction: column; gap: 8px; margin: 12px 0; min-width: 0; }
select, input, textarea { box-sizing: border-box; width: 100%; padding: 10px 12px; border: 1px solid #bac6d6; border-radius: 8px; font: inherit; background: white; color: #243247; }
textarea { resize: vertical; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; margin: 12px 0; }
.grow { flex: 1; min-width: 160px; }
button { padding: 10px 16px; border: 1px solid #bac6d6; border-radius: 8px; background: #f5f8fc; font: inherit; cursor: pointer; color: #243247; }
.primary { background: #245acc; color: white; }
button:disabled { opacity: .55; cursor: not-allowed; }
.error { color: #a22323; background: #fff0f0; padding: 12px; border-radius: 8px; overflow-wrap: anywhere; }
input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible { outline: 3px solid #91b5ff; outline-offset: 2px; }
@media(max-width:600px) { .rag-chat { padding: 16px; } .grid { grid-template-columns: 1fr; } .actions > * { width: 100%; } }
</style>
