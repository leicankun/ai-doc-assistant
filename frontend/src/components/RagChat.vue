<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import RagTurn from './RagTurn.vue'
import { readSse } from '../utils/readSse'
import { apiError } from '../utils/apiError'
import { vClosePopover } from '../utils/closePopover'
const sessionPopover = ref(null)
const settingsPopover = ref(null)

const props = defineProps({ documents: { type: Array, required: true }, activeDocumentId: { type: [String, Number], default: '' }, knowledgeBase: { type: Object, default: null } })
const emit = defineEmits(['open-tools'])
const conversation = ref(null)
const selectedDocument = computed(() => props.knowledgeBase ? { ...props.knowledgeBase, processing_status_label: '知识库', processing_status: 'indexed' } : props.documents.find(doc => doc.id === documentId.value))
const basePath = computed(() => props.knowledgeBase ? `/knowledge-bases/${props.knowledgeBase.id}` : `/documents/${documentId.value}`)
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
const selected = computed(() => Boolean(props.knowledgeBase) || props.documents.some(doc => doc.id === documentId.value))
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
    throw new Error(apiError(data, response.status))
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
    const data = await request(`${basePath.value}/sessions`, null, signal)
    if (active()) {
      sessions.value = data.items
      if (!sessionId.value && data.items.length) sessionId.value = data.items[0].id
    }
  })
}

async function loadHistory() {
  if (!selected.value || !sessionId.value) return
  turns.value = []
  await operate('history', async (signal, active) => {
    const data = await request(`${basePath.value}/sessions/${sessionId.value}/rag-history`, null, signal)
    if (active()) turns.value = data.items
  })
}

async function createSession() {
  if (!selected.value || !title.value.trim() || busy.value) return
  await operate('create', async (signal, active) => {
    const data = await request(`${basePath.value}/sessions`, { title: title.value.trim() }, signal)
    if (active()) {
      sessions.value.unshift(data)
      title.value = ''
      if (sessionPopover.value) sessionPopover.value.open = false
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
    const response = await fetch(`http://127.0.0.1:8000${basePath.value}/ask-rag/chat/stream`, {
      method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question.value.trim(), session_id: sessionId.value, mode: mode.value, top_k: topK.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(apiError(data, response.status))
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

watch(() => props.activeDocumentId, id => { documentId.value = id }, { immediate: true })
watch(() => [turns.value.length, pending.value?.answer], async () => {
  await nextTick()
  if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
})
watch(basePath, async () => {
  invalidate()
  sessions.value = []
  sessionId.value = ''
  turns.value = []
  question.value = ''
  title.value = ''
  if (sessionPopover.value) sessionPopover.value.open = false
  await loadSessions()
})
watch(sessionId, async () => {
  invalidate()
  turns.value = []
  question.value = ''
  await loadHistory()
})
watch(() => props.documents, () => {
  if (!props.knowledgeBase && !selected.value) documentId.value = ''
})
onBeforeUnmount(invalidate)
</script>

<template>
  <section class="rag-chat" aria-labelledby="rag-chat-heading" :aria-busy="Boolean(busy)">
    <header class="chat-header">
      <div><h1 id="rag-chat-heading">{{ selectedDocument?.title || '与你的资料聊一聊' }}</h1><p class="muted">{{ selectedDocument ? `${selectedDocument.processing_status_label} · 回答附原文引用` : '从侧边栏选择文档，开始探索。' }}</p></div>
      <button v-if="selectedDocument && selectedDocument.processing_status !== 'indexed'" class="prepare" @click="emit('open-tools')">处理文档</button>
    </header>
    <div v-if="selected" class="session-bar">
      <label class="session-picker"><span>会话</span><select v-model="sessionId" :disabled="Boolean(busy) || !sessions.length"><option value="" disabled>新建会话后开始</option><option v-for="session in sessions" :key="session.id" :value="session.id">{{ session.title }}</option></select></label>
      <details ref="sessionPopover" v-close-popover class="new-session"><summary>＋ 新对话</summary><form @submit.prevent="createSession"><input v-model="title" maxlength="100" placeholder="会话名称" aria-label="新会话名称" :disabled="Boolean(busy)" required /><button :disabled="Boolean(busy) || !title.trim()">创建</button><button type="button" @click="sessionPopover.open = false">取消</button></form></details>
      <details ref="settingsPopover" v-close-popover class="chat-settings"><summary>设置</summary><div class="settings-content"><label>检索方式<select v-model="mode" :disabled="Boolean(busy)"><option value="bm25">BM25</option><option value="dense">向量检索</option><option value="hybrid">混合检索（RRF）</option></select></label><label>返回分块数<input v-model.number="topK" type="number" min="1" max="20" step="1" :disabled="Boolean(busy)" /></label><button :disabled="Boolean(busy)" @click="loadSessions">刷新会话</button><button :disabled="Boolean(busy) || !sessionId" @click="loadHistory">重新加载历史</button><button @click="settingsPopover.open = false">完成</button></div></details>
    </div>
    <div ref="conversation" class="conversation" aria-live="polite">
      <div v-if="!turns.length && !pending" class="welcome"><span class="welcome-icon">文</span><h2>{{ selected ? '从一个问题开始' : '把文档变成可以对话的知识' }}</h2><p>{{ !selected ? '上传 Markdown 或 PDF，选择文档后开始问答。' : !sessionId ? '点击「新对话」创建会话，或选择已有会话。' : '你可以问文档里的概念，也可以继续追问。' }}</p><p class="welcome-note">答案来自资料，引用原文可展开核对。</p></div>
      <div class="turns"><RagTurn v-for="turn in turns" :key="turn.id" :turn="turn" /><RagTurn v-if="pending" :turn="pending" /></div>
    </div>
    <div class="composer-area">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="busy" class="muted" role="status">{{ busy === 'ask' ? '正在检索资料并生成回答…' : '正在加载或创建会话…' }}</p>
      <form class="composer" @submit.prevent="ask"><label class="sr-only" for="rag-question">问题或追问</label><textarea id="rag-question" v-model="question" rows="2" maxlength="1000" placeholder="问问这份资料，或继续追问…" :disabled="Boolean(busy) || !sessionId" required /><div class="composer-footer"><span>{{ mode === 'hybrid' ? '混合检索' : mode === 'dense' ? '向量检索' : 'BM25' }} · {{ topK }} 个分块</span><button v-if="busy === 'ask'" type="button" @click="controller?.abort()">停止生成</button><button v-else class="primary" :disabled="Boolean(busy) || !sessionId || !question.trim()">发送 ↑</button></div></form>
      <p class="footnote">回答仅供参考，请核对引用资料。</p>
    </div>
  </section>
</template>
<style scoped>
.rag-chat{display:flex;flex-direction:column;flex:1;min-height:0;background:white;color:#25352f}.chat-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:24px 40px 12px}.chat-header h1{font-size:19px;font-weight:600;margin:0;overflow-wrap:anywhere}.muted{font-size:12px;color:#829084;margin:5px 0}.session-bar{display:flex;align-items:center;gap:16px;padding:0 40px 14px;border-bottom:1px solid #edf0eb;font-size:12px;flex-wrap:wrap}.session-picker{display:flex;align-items:center;gap:8px;min-width:0}.session-picker select{max-width:260px}.new-session,.chat-settings{position:relative}summary{cursor:pointer;list-style:none;color:#65766a}.new-session form,.settings-content{position:absolute;top:28px;right:0;z-index:5;min-width:230px;padding:16px;background:white;box-shadow:0 4px 24px #0002;border:1px solid #e4e9e2;border-radius:10px}.new-session form{display:flex;gap:8px}.settings-content{display:grid;gap:12px}.settings-content label{display:grid;gap:5px}
.conversation{flex:1;min-height:100px;overflow:auto;padding:20px 40px;scroll-behavior:smooth}.turns{max-width:820px;margin:auto}.welcome{display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:280px;height:100%;padding:20px}.welcome-icon{display:grid;place-items:center;background:#edf2e9;color:#315e4b;width:56px;height:56px;border-radius:16px;font-size:24px}.welcome h2{font-size:24px;font-weight:500;margin:22px 0 8px}.welcome p{color:#7e8b80;font-size:14px;margin:6px 0}.welcome .welcome-note{font-size:12px}.composer-area{width:100%;max-width:900px;margin:0 auto;padding:12px 40px 8px}.composer{border:1px solid #dbe3d8;border-radius:16px;padding:14px 16px;background:#fafbf8;box-shadow:0 3px 12px #21351e05}.composer textarea{width:100%;resize:vertical;border:0;background:transparent;padding:0;font:inherit;font-size:14px;min-height:50px;max-height:180px}.composer-footer{display:flex;align-items:center;justify-content:space-between;gap:10px}.composer-footer span{font-size:11px;color:#859182}.footnote{text-align:center;font-size:11px;color:#95a08f;margin:8px 0}.error{color:#b33e3e;font-size:13px;overflow-wrap:anywhere}button{padding:8px 12px;border:1px solid #dbe3d8;background:white;border-radius:8px;cursor:pointer;font:inherit;font-size:12px}.primary{background:#315e4b;color:white;border-color:#315e4b}button:disabled,textarea:disabled{opacity:.5;cursor:not-allowed}select,input{font:inherit;border:1px solid #dbe3d8;border-radius:6px;padding:7px;max-width:100%;background:white;color:inherit}button:focus-visible,textarea:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid #477e63;outline-offset:3px}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.new-session form{width:320px;min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center}.new-session input{min-width:0;width:100%}
@media(max-width:760px){.chat-header{padding:20px 18px 12px}.session-bar{padding:0 18px 12px;gap:12px}.conversation{padding:12px 18px}.composer-area{padding:10px 16px}.welcome h2{font-size:20px}.session-picker select{max-width:160px}.welcome{min-height:180px}.new-session form,.settings-content{position:fixed;top:190px;right:16px;left:16px;width:auto;min-width:0}.chat-header h1{font-size:16px}}
</style>
