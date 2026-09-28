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
onMounted(loadDocuments)
</script>

<template>
  <main>
    <h1>文档管理</h1>
    <form @submit.prevent="submitDocument">
  <label>
    文档标题：
    <input
      v-model="title"
      placeholder="请输入文档标题"
      maxlength="100"
      required
      :disabled="saving"
    />
  </label>

  <label>
    文件类型：
    <select v-model="fileType" :disabled="saving">
      <option value="markdown">Markdown</option>
      <option value="pdf">PDF</option>
    </select>
  </label>

  <button type="submit" :disabled="saving">
    {{ saving ? '提交中…' : editingId === null ? '新增文档' : '保存修改' }}
  </button>
  <button
    v-if="editingId !== null"
    type="button"
    :disabled="saving"
    @click="cancelEdit">
      取消编辑
  </button>

  <p v-if="formError">{{ formError }}</p>
</form>

    <button :disabled="loading" @click="loadDocuments">
      {{ loading ? '加载中…' : '刷新列表' }}
    </button>

    <p v-if="errorMessage">{{ errorMessage }}</p>

    <template v-else>
      <p>共 {{ documents.length }} 条文档</p>

      <ul v-if="documents.length > 0">

        <li v-for="document in documents" :key="document.id">
          {{ document.id }} — {{ document.title }}（{{ document.file_type }}）
          <button type="button" @click="startEdit(document)">
              编辑
          </button>
          <button
            type="button"
            :disabled="deletingId === document.id"
            @click="deleteDocument(document.id)"
          >
            {{ deletingId === document.id ? '删除中…' : '删除' }}
          </button>
        </li>
      </ul>

      <p v-else-if="!loading">暂无文档</p>
    </template>
  </main>
</template>

<style scoped>
main {
  max-width: 720px;
  margin: 40px auto;
  padding: 20px;
}

button {
  padding: 8px 16px;
  cursor: pointer;
}

li {
  margin: 12px 0;
}
</style>


