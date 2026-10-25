<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import DocumentSearch from './components/DocumentSearch.vue'
import RagChat from './components/RagChat.vue'
import KnowledgeBases from './components/KnowledgeBases.vue'
import KnowledgeBaseSearch from './components/KnowledgeBaseSearch.vue'
import { vClosePopover } from './utils/closePopover'
const metadataForm = ref(null)
const activeBase = ref(null)

const documents = ref([])
const activeDocumentId = ref('')
const embeddingProvider = ref('api')
const sidebarOpen = ref(false)
const sidebarWidth = ref(280)
const resizingSidebar = ref(false)
const sidebarMaxWidth = ref(520)
let sidebarDrag = null

function setSidebarWidth(width) {
  sidebarWidth.value = Math.round(Math.max(220, Math.min(sidebarMaxWidth.value, width)))
}

function saveSidebarWidth() {
  try { localStorage.setItem('document-sidebar-width', String(sidebarWidth.value)) } catch { /* Storage may be disabled. */ }
}

function fitSidebarWidth() {
  sidebarMaxWidth.value = Math.max(220, Math.min(520, Math.floor(window.innerWidth * 0.45)))
  setSidebarWidth(sidebarWidth.value)
}

function startSidebarResize(event) {
  if (event.button !== 0) return
  event.preventDefault()
  sidebarDrag = { x: event.clientX, width: sidebarWidth.value }
  resizingSidebar.value = true
  event.currentTarget.setPointerCapture(event.pointerId)
}

function resizeSidebar(event) {
  if (sidebarDrag) setSidebarWidth(sidebarDrag.width + event.clientX - sidebarDrag.x)
}

function stopSidebarResize() {
  if (!sidebarDrag) return
  sidebarDrag = null
  resizingSidebar.value = false
  saveSidebarWidth()
}

function resizeSidebarWithKeyboard(event) {
  const widths = { ArrowLeft: sidebarWidth.value - 20, ArrowRight: sidebarWidth.value + 20, Home: 220, End: sidebarMaxWidth.value }
  if (!(event.key in widths)) return
  event.preventDefault()
  setSidebarWidth(widths[event.key])
  saveSidebarWidth()
}

onMounted(() => {
  try {
    const savedWidth = Number(localStorage.getItem('document-sidebar-width'))
    if (savedWidth > 0 && Number.isFinite(savedWidth)) sidebarWidth.value = savedWidth
  } catch { /* Use the default width when storage is unavailable. */ }
  fitSidebarWidth()
  window.addEventListener('resize', fitSidebarWidth)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', fitSidebarWidth)
  stopSidebarResize()
})
const toolsOpen = ref(false)
const loading = ref(false)
const errorMessage = ref('')
const title = ref('')
const fileType = ref('markdown')
const saving = ref(false)
const formError = ref('')
const editingId = ref(null)
const deletingId = ref(null)
const selectedFile = ref(null)
const uploading = ref(false)
const uploadError = ref('')
const uploadSuccess = ref('')
const askingDocument = ref(null)
const question = ref('')
const answer = ref('')
const asking = ref(false)
const askError = ref('')
const streamStatus = ref('')
let askController = null
const history = ref([])
const historyLoading = ref(false)
const historyError = ref('')
let historyRequestId = 0
const sessions = ref([])
const selectedSessionId = ref(null)
const sessionTitle = ref('')
const sessionsLoading = ref(false)
const creatingSession = ref(false)
const sessionError = ref('')
let sessionsRequestId = 0

async function loadDocuments() {
  loading.value = true
  errorMessage.value = ''

  try {
    const response = await fetch('http://127.0.0.1:8000/documents')
    console.log('响应对象：', response)

    if (!response.ok) {
      throw new Error(`请求失败，状态码：${response.status}`)
    }

    const data = await response.json()
    documents.value = data.items
    if (!documents.value.some(doc => doc.id === activeDocumentId.value)) activeDocumentId.value = documents.value[0]?.id ?? ''
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

async function submitDocument() {
  formError.value = ''

  if (!title.value.trim()) {
    formError.value = '请输入文档标题'
    return
  }

  saving.value = true
  const isEditing = editingId.value !== null

  try {
    const url = isEditing
      ? `http://127.0.0.1:8000/documents/${editingId.value}`
      : 'http://127.0.0.1:8000/documents'

    const response = await fetch(url, {
      method: isEditing ? 'PUT' : 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        title: title.value,
        file_type: fileType.value,
      }),
    })

    if (!response.ok) {
      throw new Error(
        `${isEditing ? '修改' : '创建'}失败，状态码：${response.status}`,
      )
    }

    title.value = ''
    fileType.value = 'markdown'
    editingId.value = null
    if (metadataForm.value) metadataForm.value.open = false
    await loadDocuments()
  } catch (error) {
    formError.value = error.message
  } finally {
    saving.value = false
  }
}

async function deleteDocument(id) {
  errorMessage.value = ''
  deletingId.value = id

  try {
    const response = await fetch(
      `http://127.0.0.1:8000/documents/${id}`,
      { method: 'DELETE' },
    )

    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data.detail || `删除失败，状态码：${response.status}`)
    }

    if (askingDocument.value?.id === id) {
      stopGeneration()
      askingDocument.value = null
      ++sessionsRequestId
      sessions.value = []
      selectedSessionId.value = null
      resetConversation()
    }
    if (editingId.value === id) cancelEdit()
    await loadDocuments()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    deletingId.value = null
  }
}

function startEdit(document) {
  editingId.value = document.id
  title.value = document.title
  fileType.value = document.file_type
}

function cancelEdit() {
  editingId.value = null
  title.value = ''
  fileType.value = 'markdown'
  formError.value = ''
}

function selectFile(event) {
  selectedFile.value = event.target.files[0] ?? null
  uploadError.value = ''
  uploadSuccess.value = ''
  if (selectedFile.value && !/\.(md|pdf)$/i.test(selectedFile.value.name)) {
    uploadError.value = '请选择 .md 或 .pdf 文件'
    selectedFile.value = null
    event.target.value = ''
  }
}

async function uploadDocument() {
  if (uploading.value) return
  uploadError.value = ''
  uploadSuccess.value = ''

  if (!selectedFile.value) {
    uploadError.value = '请先选择 Markdown 或 PDF 文件'
    return
  }

  uploading.value = true

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    const response = await fetch(
      `http://127.0.0.1:8000/documents/upload?embedding_provider=${embeddingProvider.value}`,
      {
        method: 'POST',
        body: formData,
      },
    )

    if (!response.ok) {
      const errorData = await response.json()
      throw new Error(
        typeof errorData.detail === 'string'
          ? errorData.detail
          : `上传失败，状态码：${response.status}`,
      )
    }

    const data = await response.json()
    uploadSuccess.value = data.processing_completed
      ? `上传并${data.embedding_provider === 'local' ? '本地' : 'API'}入库完成：${data.title}（${data.chunk_count} 个分块）`
      : `文件已上传：${data.title}`
    if (data.processing_error) uploadError.value = data.processing_error

    await loadDocuments()
  } catch (error) {
    uploadError.value = error.message
  } finally {
    uploading.value = false
  }
}

async function startAsk(document) {
  if (asking.value || creatingSession.value) return
  askingDocument.value = document
  sessions.value = []
  selectedSessionId.value = null
  sessionTitle.value = ''
  resetConversation()
  await loadSessions()
}

function resetConversation() {
  streamStatus.value = ''
  ++historyRequestId
  historyLoading.value = false
  historyError.value = ''
  question.value = ''
  answer.value = ''
  askError.value = ''
  history.value = []
}

async function changeSession() {
  resetConversation()
  await loadHistory()
}

async function loadSessions() {
  if (!askingDocument.value) return
  const documentId = askingDocument.value.id
  const requestId = ++sessionsRequestId
  sessionsLoading.value = true
  sessionError.value = ''
  try {
    const response = await fetch(`http://127.0.0.1:8000/documents/${documentId}/sessions`)
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '会话列表加载失败')
    if (requestId !== sessionsRequestId) return
    sessions.value = data.items
    if (!sessions.value.some(item => item.id === selectedSessionId.value)) {
      selectedSessionId.value = sessions.value[0]?.id ?? null
    }
    await changeSession()
  } catch (error) {
    if (requestId === sessionsRequestId) sessionError.value = error.message
  } finally {
    if (requestId === sessionsRequestId) sessionsLoading.value = false
  }
}

async function createSession() {
  if (!askingDocument.value || creatingSession.value || asking.value || sessionsLoading.value) return
  sessionError.value = ''
  if (!sessionTitle.value.trim()) {
    sessionError.value = '请输入会话名称'
    return
  }
  creatingSession.value = true
  try {
    const response = await fetch(`http://127.0.0.1:8000/documents/${askingDocument.value.id}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: sessionTitle.value.trim() }),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '创建会话失败')
    sessions.value.unshift(data)
    selectedSessionId.value = data.id
    sessionTitle.value = ''
    await changeSession()
  } catch (error) {
    sessionError.value = error.message
  } finally {
    creatingSession.value = false
  }
}

async function loadHistory() {
  if (!askingDocument.value || !selectedSessionId.value) return
  const documentId = askingDocument.value.id
  const requestId = ++historyRequestId
  historyLoading.value = true
  historyError.value = ''

  try {
    const response = await fetch(
      `http://127.0.0.1:8000/documents/${documentId}/sessions/${selectedSessionId.value}/history`,
    )
    const data = await response.json()
    if (!response.ok) {
      throw new Error(
        typeof data.detail === 'string' ? data.detail : '历史记录加载失败',
      )
    }
    // 快速切换文档时，忽略较早请求的结果
    if (requestId === historyRequestId) history.value = data.items
  } catch (error) {
    if (requestId === historyRequestId) historyError.value = error.message
  } finally {
    if (requestId === historyRequestId) historyLoading.value = false
  }
}

function formatHistoryTime(value) {
  // SQLite CURRENT_TIMESTAMP 保存的是 UTC，展示为本地时间
  const date = new Date(value.replace(' ', 'T') + 'Z')
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

async function askDocument() {
  if (!askingDocument.value || asking.value || creatingSession.value || sessionsLoading.value) return

  askError.value = ''
  answer.value = ''
  streamStatus.value = ''

  if (!selectedSessionId.value) {
    askError.value = '请先创建或选择会话'
    return
  }

  if (!question.value.trim()) {
    askError.value = '请输入问题'
    return
  }

  asking.value = true
  const controller = new AbortController()
  askController = controller
  let reader
  let completed = false

  try {
    const response = await fetch(
      `http://127.0.0.1:8000/documents/${askingDocument.value.id}/ask/stream`,
      {
        method: 'POST',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          question: question.value,
          session_id: selectedSessionId.value,
        }),
      },
    )

    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(
        typeof data.detail === 'string'
          ? data.detail
          : `提问失败，状态码：${response.status}`,
      )
    }

    if (!response.body) throw new Error('浏览器无法读取流式响应')
    reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    // 网络分段不等于 SSE 事件分段：缓存到空行后再解析。
    function consumeEvents() {
      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const data = block.split(/\r?\n/)
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).replace(/^ /, ''))
          .join('\n')
        if (!data) continue
        const event = JSON.parse(data)
        if (event.type === 'delta') {
          answer.value += event.content
        } else if (event.type === 'error') {
          throw new Error(event.message || '生成失败，请重试')
        } else if (event.type === 'done') {
          completed = true
          return
        }
      }
    }

    while (!completed) {
      const { value, done } = await reader.read()
      if (controller.signal.aborted) throw new DOMException('已停止', 'AbortError')
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      consumeEvents()
      if (done) break
    }
    if (!completed) throw new Error('连接提前结束，回答可能不完整，请重试')
    streamStatus.value = '回答已完成并保存'
    askController = null
    await loadHistory()
  } catch (error) {
    if (controller.signal.aborted) {
      streamStatus.value = '已停止接收，已显示的文字保留；历史记录以刷新结果为准'
      await loadHistory()
    } else {
      askError.value = error.message
      streamStatus.value = answer.value ? '回答未完成或保存失败' : ''
    }
  } finally {
    if (reader) {
      await reader.cancel().catch(() => {})
      reader.releaseLock()
    }
    if (askController === controller) askController = null
    asking.value = false
  }
}

function stopGeneration() {
  askController?.abort()
}

onBeforeUnmount(stopGeneration)
onMounted(loadDocuments)
</script>

<template>
  <div class="app-shell" :class="{ 'resizing-sidebar': resizingSidebar }" :style="{ '--sidebar-width': `${sidebarWidth}px` }">
    <aside class="sidebar" :class="{ expanded: sidebarOpen }" aria-label="文档侧边栏">
      <header class="brand"><span class="brand-icon">文</span><div><strong>文档问答</strong><small>让资料成为对话</small></div><button class="mobile-close" @click="sidebarOpen = false" aria-label="收起侧边栏">×</button></header>
      <section class="upload-area">
        <label class="upload-label" for="document-file">＋ 添加文档</label>
        <input id="document-file" type="file" accept=".md,.pdf" :disabled="uploading" @change="selectFile" />
        <p class="muted">Markdown / 文字型 PDF</p>
        <label class="muted">向量来源 <select v-model="embeddingProvider" :disabled="uploading"><option value="api">百炼 API（默认）</option><option value="local">本地 BGE-M3</option></select></label>
        <button v-if="selectedFile" class="primary wide" :disabled="uploading" @click="uploadDocument">{{ uploading ? '上传并处理中…' : '上传并自动入库' }}</button>
        <p v-if="uploadError" class="error" role="alert">{{ uploadError }}</p>
        <p v-if="uploadSuccess" class="success" role="status">{{ uploadSuccess }}</p>
      </section>
      <div class="list-heading"><h2>我的文档 <small>{{ documents.length }}</small></h2><button :disabled="loading" @click="loadDocuments" aria-label="刷新文档列表">↻</button></div>
      <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
      <nav class="document-list" aria-label="选择问答文档">
        <div v-for="document in documents" :key="document.id" class="document-row" :class="{ active: !activeBase && activeDocumentId === document.id }">
          <button class="document-select" :aria-pressed="!activeBase && activeDocumentId === document.id" @click="activeDocumentId = document.id; activeBase = null; sidebarOpen = false">
            <span class="document-title">{{ document.title }}</span>
            <small>{{ document.file_type === 'markdown' ? 'MD' : 'PDF' }} · {{ document.processing_status_label }} · {{ document.chunk_count }} 块</small>
          </button>
          <details v-close-popover class="document-menu"><summary :aria-label="`管理 ${document.title}`">···</summary><div class="menu-actions">
            <button @click="startEdit(document)">编辑信息</button>
            <button @click="startAsk(document); toolsOpen = true">全文问答</button>
            <button class="danger" :disabled="deletingId === document.id || asking || creatingSession" @click="deleteDocument(document.id)">{{ deletingId === document.id ? '删除中…' : '删除文档' }}</button>
          </div></details>
        </div>
        <p v-if="!documents.length" class="muted empty-documents">{{ loading ? '正在加载…' : '上传第一份资料，开始对话。' }}</p>
      </nav>
      <KnowledgeBases :documents="documents" :active-base="activeBase" @select="activeBase = $event; sidebarOpen = false" @process-document="activeDocumentId = $event; toolsOpen = true; sidebarOpen = false" />
      <details ref="metadataForm" class="metadata" :open="editingId !== null">
        <summary>{{ editingId === null ? '登记文档信息' : '编辑文档信息' }}</summary>
        <form @submit.prevent="submitDocument">
          <label>标题<input v-model="title" maxlength="100" required :disabled="saving" /></label>
          <label>类型<select v-model="fileType" :disabled="saving"><option value="markdown">Markdown</option><option value="pdf">PDF</option></select></label>
          <button :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button><button v-if="editingId !== null" type="button" @click="cancelEdit">取消</button>
          <p v-if="formError" class="error" role="alert">{{ formError }}</p>
        </form>
      </details>
      <footer class="sidebar-footer">回答附原文引用，请结合资料核对。</footer>
    </aside>
    <div class="sidebar-resizer" role="separator" tabindex="0" aria-label="调整侧边栏宽度" aria-orientation="vertical" :aria-valuenow="sidebarWidth" :aria-valuemin="220" :aria-valuemax="sidebarMaxWidth" title="拖动调整宽度，双击恢复默认" @pointerdown="startSidebarResize" @pointermove="resizeSidebar" @pointerup="stopSidebarResize" @pointercancel="stopSidebarResize" @lostpointercapture="stopSidebarResize" @keydown="resizeSidebarWithKeyboard" @dblclick="setSidebarWidth(280); saveSidebarWidth()"></div>
    <main class="main-area">
      <header class="workspace-header"><button class="mobile-toggle" @click="sidebarOpen = !sidebarOpen" :aria-expanded="sidebarOpen" aria-label="展开文档侧边栏">☰</button><span>资料对话</span><button :aria-expanded="toolsOpen" @click="toolsOpen = !toolsOpen">{{ toolsOpen ? '返回对话' : '文档处理与检索' }}</button></header>
      <div v-show="toolsOpen" class="tools-area">
        <KnowledgeBaseSearch v-if="activeBase" v-model:embedding-provider="embeddingProvider" :base="activeBase" />
        <DocumentSearch v-model:embedding-provider="embeddingProvider" :documents="documents" :active-document-id="activeDocumentId" @processing-updated="loadDocuments" />
        <details class="legacy" :open="Boolean(askingDocument)"><summary>全文问答与历史</summary>
    <section v-if="askingDocument" class="panel library" aria-labelledby="ask-heading" :aria-busy="asking">
      <h2 id="ask-heading" class="ask-title">文档问答：{{ askingDocument.title }}</h2>
      <p class="muted section-description">根据这篇文档的内容回答你的问题，当前支持正文不超过 20000 字符的 Markdown 或 PDF。</p>
      <div class="session-panel">
        <div class="session-picker">
          <label class="field" for="session-select">
            <span>选择会话</span>
            <select id="session-select" v-model="selectedSessionId" :disabled="asking || creatingSession || sessionsLoading || !sessions.length" @change="changeSession">
              <option :value="null" disabled>{{ sessionsLoading ? '加载中…' : '请先新建会话' }}</option>
              <option v-for="session in sessions" :key="session.id" :value="session.id">{{ session.title }}（#{{ session.id }}）</option>
            </select>
          </label>
          <button type="button" :disabled="asking || creatingSession || sessionsLoading" @click="loadSessions">刷新会话</button>
        </div>
        <form class="session-picker" @submit.prevent="createSession">
          <label class="field" for="session-title">
            <span>新建会话</span>
            <input id="session-title" v-model="sessionTitle" maxlength="100" required placeholder="例如：基础概念复习" :disabled="asking || creatingSession || sessionsLoading" />
          </label>
          <button type="submit" :disabled="asking || creatingSession || sessionsLoading">{{ creatingSession ? '创建中…' : '创建会话' }}</button>
        </form>
        <p v-if="sessionError" class="message error" role="alert">{{ sessionError }}</p>
        <p v-else-if="!sessionsLoading && !sessions.length" class="muted">还没有会话，输入名称创建一个后即可提问。</p>
      </div>
      <form @submit.prevent="askDocument">
        <label class="field" for="question">
          <span>你的问题</span>
          <textarea
            id="question"
            v-model="question"
            rows="3"
            maxlength="1000"
            placeholder="例如：这篇文档主要讲了什么？"
            required
            :disabled="asking || creatingSession || sessionsLoading || !selectedSessionId"
          ></textarea>
        </label>
        <div class="actions">
          <button class="primary" type="submit" :disabled="asking || creatingSession || sessionsLoading || !selectedSessionId">
            {{ asking ? '正在回答…' : '发送问题' }}
          </button>
          <button v-if="asking" type="button" class="danger" @click="stopGeneration">
            停止生成
          </button>
        </div>
      </form>
      <p v-if="askError" class="message error" role="alert">{{ askError }}</p>
      <p v-if="streamStatus" class="muted" role="status">{{ streamStatus }}</p>
      <div aria-live="polite">
        <p v-if="asking" class="muted">{{ answer ? '正在生成回答…' : '模型正在阅读文档，请稍候…' }}</p>
        <div v-if="answer" class="answer-box">
          <h3>模型回答</h3>
          <p class="answer-text">{{ answer }}</p>
        </div>
      </div>
      <section class="history-section" aria-labelledby="history-heading" :aria-busy="historyLoading">
        <div class="list-header">
          <h3 id="history-heading">当前会话历史 <span class="count">{{ history.length }}</span></h3>
          <button type="button" :disabled="historyLoading || asking || creatingSession || sessionsLoading || !selectedSessionId" @click="loadHistory">
            {{ historyLoading ? '加载中…' : '刷新历史' }}
          </button>
        </div>
        <p class="muted">按保存顺序展示，时间为本地时间。提问时会结合当前会话最近 5 轮问答。</p>
        <p v-if="historyError" class="message error" role="alert">{{ historyError }}</p>
        <p v-else-if="historyLoading" class="muted" role="status">正在读取历史记录…</p>
        <p v-else-if="!selectedSessionId" class="empty-state">请选择会话查看历史。</p>
        <p v-else-if="history.length === 0" class="empty-state">这个会话还没有问答记录，发送第一个问题吧。</p>
        <ol v-else class="history-list">
          <li v-for="item in history" :key="item.id" class="history-item">
            <p class="muted">#{{ item.id }} · {{ formatHistoryTime(item.created_at) }}</p>
            <h4>问题</h4>
            <p class="answer-text">{{ item.question }}</p>
            <h4>回答</h4>
            <p class="answer-text">{{ item.answer }}</p>
          </li>
        </ol>
      </section>
    </section>

        </details>
      </div>
      <RagChat v-show="!toolsOpen" v-model:embedding-provider="embeddingProvider" :documents="documents" :active-document-id="activeDocumentId" :knowledge-base="activeBase" @open-tools="toolsOpen = true" />
    </main>
  </div>
</template>
<style scoped>
.app-shell{display:grid;grid-template-columns:var(--sidebar-width,280px) 6px minmax(0,1fr);height:100dvh;background:#fff;color:#25352f}
.sidebar-resizer{cursor:col-resize;touch-action:none;background:#f4f6f3;position:relative;outline:none}.sidebar-resizer::after{content:'';position:absolute;inset:0 2px;background:#d9e1d9;transition:background .15s}.sidebar-resizer:hover::after,.sidebar-resizer:focus-visible::after,.resizing-sidebar .sidebar-resizer::after{background:#477e63}.resizing-sidebar,.resizing-sidebar *{cursor:col-resize!important;user-select:none!important}
.sidebar{display:flex;flex-direction:column;gap:18px;padding:24px 16px;background:#f4f6f3;border-right:1px solid #e3e8e1;overflow:auto}
.brand{display:flex;align-items:center;gap:12px;padding:0 8px 12px}.brand-icon{display:grid;place-items:center;width:36px;height:36px;background:#315e4b;color:white;border-radius:10px}.brand strong{font-size:18px}.brand small{display:block;color:#78847b;font-size:12px}
button,input,select,textarea{font:inherit}button{border:1px solid #d9e1d9;border-radius:8px;padding:8px 12px;background:white;cursor:pointer}button:hover:not(:disabled){background:#e8eee7}button:disabled{opacity:.5;cursor:not-allowed}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:2px solid #477e63;outline-offset:3px}
.primary,.upload-label{background:#315e4b;color:white}.wide{width:100%}.upload-label{display:block;text-align:center;padding:10px;border-radius:8px;cursor:pointer}.upload-area input[type=file]{width:100%;font-size:12px;margin-top:10px}.muted{color:#768279;font-size:12px}.upload-area p{margin:6px 0}.list-heading{display:flex;align-items:center;justify-content:space-between;padding:0 8px}.list-heading h2{font-size:13px;margin:0}.list-heading small{color:#819085;margin-left:6px}.list-heading button{border:0;background:transparent}
.document-list{flex:1;min-height:100px}.document-row{position:relative;display:flex;border-radius:9px;margin:3px 0}.document-row.active{background:#e3ebe1}.document-select{text-align:left;flex:1;min-width:0;border:0;background:transparent;padding:12px}.document-title{display:block;font-size:14px;overflow-wrap:anywhere}.document-select small{display:block;margin-top:4px;font-size:11px;color:#728173}.document-menu{padding:10px 8px}.document-menu summary{cursor:pointer;list-style:none}.menu-actions{position:absolute;z-index:5;right:5px;background:white;padding:6px;box-shadow:0 4px 18px #0002;border-radius:8px;display:grid;min-width:120px}.menu-actions button{border:0;text-align:left}.danger{color:#af3939}.empty-documents{padding:12px}.metadata{font-size:12px;border-top:1px solid #dde4db;padding-top:14px}.metadata summary{cursor:pointer}.metadata label{display:grid;gap:5px;margin:10px 0}.metadata input,.metadata select{width:100%;padding:8px;border:1px solid #d9e1d9;border-radius:6px}.sidebar-footer{font-size:11px;color:#859083;padding:8px}
.main-area{min-width:0;display:flex;flex-direction:column;overflow:hidden}.workspace-header{height:65px;flex-shrink:0;display:flex;align-items:center;justify-content:space-between;padding:0 32px;border-bottom:1px solid #edf0eb;font-size:14px}.workspace-header>span{flex:1}.workspace-header button{font-size:12px}.tools-area{flex:1;overflow:auto;padding:0 28px 32px}.tools-area :deep(.search-panel){border:0;border-radius:0;margin-top:0;box-shadow:none}.legacy{margin-top:24px}.legacy summary{cursor:pointer}.panel{padding:20px 0}.session-picker,.actions,.list-header{display:flex;flex-wrap:wrap;gap:10px;align-items:end;margin:12px 0}.field{display:grid;gap:6px;flex:1}.panel input,.panel select,.panel textarea{padding:10px;border:1px solid #d9e1d9;border-radius:8px;width:100%}.answer-text{white-space:pre-wrap;overflow-wrap:anywhere}.history-list{list-style:none;padding:0}.history-item{border-top:1px solid #e3e8e1;padding:16px 0}.error{color:#b23d3d;overflow-wrap:anywhere;font-size:13px}.success{color:#315e4b;font-size:12px}.mobile-toggle,.mobile-close{display:none}
.sidebar > *{flex-shrink:0}.document-list{flex:0 0 auto;min-height:0;max-height:280px;overflow:auto}.document-row{flex-wrap:wrap;align-items:start}.document-select{flex:1 1 0}.document-menu{flex:0 0 32px}.document-menu[open]{flex-basis:100%}.document-menu[open] summary{text-align:right}.menu-actions{position:static;min-width:0;box-shadow:none;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;margin-top:6px}.menu-actions button{font-size:11px;padding:6px 2px;text-align:center}.sidebar{scrollbar-gutter:stable}
@media(max-width:760px){.app-shell{grid-template-columns:1fr}.sidebar-resizer{display:none}.sidebar{display:none}.sidebar.expanded{display:flex;position:fixed;inset:0 auto 0 0;width:min(var(--sidebar-width,280px),90vw);z-index:20;box-shadow:10px 0 30px #0002}.mobile-toggle,.mobile-close{display:block}.mobile-close{margin-left:auto}.workspace-header{padding:0 16px;gap:12px}.tools-area{padding:0 12px 20px}}
</style>
