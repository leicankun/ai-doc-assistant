<script setup>
import { onMounted, ref } from 'vue'

const documents = ref([])
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
      throw new Error(`删除失败，状态码：${response.status}`)
    }

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
}

async function uploadDocument() {
  uploadError.value = ''
  uploadSuccess.value = ''

  if (!selectedFile.value) {
    uploadError.value = '请先选择 Markdown 文件'
    return
  }

  uploading.value = true

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    const response = await fetch(
      'http://127.0.0.1:8000/documents/upload',
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
    uploadSuccess.value = `上传成功：${data.title}`

    await loadDocuments()
  } catch (error) {
    uploadError.value = error.message
  } finally {
    uploading.value = false
  }
}
onMounted(loadDocuments)
</script>

<template>
  <main class="page">
    <header class="page-header">
      <h1>文档管理</h1>
      <p class="muted">上传学习资料，管理你的文档。</p>
    </header>

    <div class="workspace">
      <section class="panel" aria-labelledby="upload-heading">
        <h2 id="upload-heading">上传文档</h2>
        <p class="muted section-description">选择 UTF-8 编码的 Markdown 文件，上传后自动加入列表。</p>
        <label class="field" for="document-file">
          <span>Markdown 文件</span>
          <input
            id="document-file"
            type="file"
            accept=".md"
            :disabled="uploading"
            @change="selectFile"
          />
        </label>
        <div class="actions">
          <button
            class="primary"
            type="button"
            :disabled="uploading || !selectedFile"
            @click="uploadDocument"
          >
            {{ uploading ? '上传中…' : '上传文件' }}
          </button>
        </div>
        <p v-if="uploadError" class="message error" role="alert">{{ uploadError }}</p>
        <p v-if="uploadSuccess" class="message success" role="status">{{ uploadSuccess }}</p>
      </section>

      <section class="panel" aria-labelledby="form-heading">
        <h2 id="form-heading">{{ editingId === null ? '新增文档信息' : '编辑文档信息' }}</h2>
        <p class="muted section-description">{{ editingId === null ? '手动登记标题和类型；上传文件请使用上传区。' : '修改标题和类型后，点击保存修改。' }}</p>
        <form @submit.prevent="submitDocument">
          <div class="form-fields">
            <label class="field" for="document-title">
              <span>文档标题</span>
              <input
                id="document-title"
                v-model="title"
                placeholder="例如：Python 学习笔记"
                maxlength="100"
                required
                :disabled="saving"
              />
            </label>
            <label class="field" for="document-type">
              <span>文件类型</span>
              <select id="document-type" v-model="fileType" :disabled="saving">
                <option value="markdown">Markdown</option>
                <option value="pdf">PDF</option>
              </select>
            </label>
          </div>
          <div class="actions">
            <button class="primary" type="submit" :disabled="saving">
              {{ saving ? '提交中…' : editingId === null ? '新增文档' : '保存修改' }}
            </button>
            <button v-if="editingId !== null" type="button" :disabled="saving" @click="cancelEdit">
              取消编辑
            </button>
          </div>
          <p v-if="formError" class="message error" role="alert">{{ formError }}</p>
        </form>
      </section>
    </div>

    <section class="panel library" aria-labelledby="list-heading" :aria-busy="loading">
      <div class="list-header">
        <div>
          <h2 id="list-heading">文档列表 <span class="count">{{ documents.length }}</span></h2>
          <p class="muted">已登记和上传的文档都显示在这里。</p>
        </div>
        <button type="button" :disabled="loading" @click="loadDocuments">
          {{ loading ? '加载中…' : '刷新列表' }}
        </button>
      </div>

      <p v-if="errorMessage" class="message error" role="alert">{{ errorMessage }}</p>
      <template v-else>
        <ul v-if="documents.length > 0" class="document-list">
          <li v-for="document in documents" :key="document.id" class="document-row">
            <div class="document-info">
              <span class="document-id">#{{ document.id }}</span>
              <span class="document-title">{{ document.title }}</span>
              <span class="badge">{{ document.file_type === 'markdown' ? 'Markdown' : 'PDF' }}</span>
            </div>
            <div class="row-actions">
              <button type="button" @click="startEdit(document)">编辑</button>
              <button
                class="danger"
                type="button"
                :disabled="deletingId === document.id"
                @click="deleteDocument(document.id)"
              >
                {{ deletingId === document.id ? '删除中…' : '删除' }}
              </button>
            </div>
          </li>
        </ul>
        <p v-else class="empty-state">{{ loading ? '正在读取文档…' : '暂无文档，上传一份 Markdown 开始吧。' }}</p>
      </template>
    </section>
  </main>
</template>

<style scoped>
.page { max-width: 1100px; margin: 0 auto; padding: 40px 24px 64px; }
.page-header { margin-bottom: 28px; }
h1 { margin: 0 0 6px; font-size: 28px; }
h2 { margin: 0; font-size: 18px; }
.muted { margin: 6px 0 0; color: #64748b; font-size: 14px; }
.workspace { display: grid; grid-template-columns: 1fr 1.2fr; gap: 20px; }
.panel { min-width: 0; padding: 24px; border: 1px solid #dce3eb; border-radius: 12px; background: #fff; }
.section-description { margin-bottom: 22px; }
.form-fields { display: grid; grid-template-columns: minmax(0, 1fr) 130px; gap: 16px; }
.field { display: flex; flex-direction: column; gap: 8px; min-width: 0; font-size: 14px; font-weight: 600; }
input, select { width: 100%; min-width: 0; min-height: 42px; padding: 9px 12px; border: 1px solid #cbd5e1; border-radius: 7px; background: #fff; }
input[type='file'] { padding: 6px; font-size: 13px; }
input::file-selector-button { margin-right: 10px; padding: 5px 10px; border: 0; border-radius: 4px; background: #eef2ff; color: #3730a3; cursor: pointer; }
.actions, .row-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.actions { margin-top: 20px; }
button { padding: 9px 16px; border: 1px solid #cbd5e1; border-radius: 7px; background: #fff; font-weight: 500; cursor: pointer; }
button:hover:not(:disabled) { background: #f1f5f9; }
.primary { border-color: #4338ca; background: #4338ca; color: #fff; }
.primary:hover:not(:disabled) { background: #3730a3; }
.danger { color: #b91c1c; border-color: #fecaca; }
.danger:hover:not(:disabled) { background: #fff1f2; }
button:disabled, input:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid #a5b4fc; outline-offset: 3px; }
.library { margin-top: 24px; }
.list-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.list-header > button { flex-shrink: 0; }
.count { display: inline-block; margin-left: 6px; padding: 1px 9px; border-radius: 20px; background: #eef2ff; color: #4338ca; font-size: 13px; vertical-align: middle; }
.document-list { margin: 0; padding: 0; list-style: none; }
.document-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 0; border-top: 1px solid #e8edf3; }
.document-info { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; min-width: 0; }
.document-id { color: #64748b; font-size: 13px; }
.document-title { overflow-wrap: anywhere; font-weight: 500; }
.badge { padding: 2px 8px; border-radius: 5px; background: #f1f5f9; color: #475569; font-size: 12px; }
.row-actions { flex-shrink: 0; }
.row-actions button { padding: 6px 12px; font-size: 13px; }
.message { margin: 16px 0 0; padding: 10px 12px; border-radius: 6px; overflow-wrap: anywhere; font-size: 14px; }
.error { background: #fff1f2; color: #b91c1c; }
.success { background: #ecfdf5; color: #047857; }
.empty-state { padding: 36px 12px; text-align: center; color: #64748b; }
@media (max-width: 760px) {
  .workspace { grid-template-columns: 1fr; }
  .page { padding: 24px 16px 40px; }
  .panel { padding: 20px; }
}
@media (max-width: 480px) {
  .form-fields { grid-template-columns: 1fr; }
  .document-row { align-items: flex-start; flex-direction: column; gap: 12px; }
  .list-header { align-items: flex-start; flex-wrap: wrap; }
}
</style>
