<script setup>
import { onMounted, ref, watch } from 'vue'
import { apiError } from '../utils/apiError'
const props = defineProps({ documents: { type: Array, required: true }, activeBase: { type: Object, default: null } })
const emit = defineEmits(['select', 'process-document'])
const bases = ref([])
const title = ref('')
const members = ref([])
const chosen = ref([])
const busy = ref(false)
const error = ref('')
const managementForm = ref(null)
const addForm = ref(null)
let generation = 0
async function request(path, method = 'GET', body) {
  const response = await fetch(`http://127.0.0.1:8000/knowledge-bases${path}`, { method, ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  const data = await response.json()
  if (!response.ok) throw new Error(apiError(data, response.status))
  return data
}
async function operate(work) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await work() } catch (err) { error.value = err.message } finally { busy.value = false }
}
async function load() { bases.value = (await request('')).items }
async function loadMembers() {
  const id = props.activeBase?.id
  const current = ++generation
  if (!id) { members.value = []; return }
  try {
    const data = await request(`/${id}`)
    if (current === generation) members.value = data.documents
  } catch (err) { if (current === generation) error.value = err.message }
}
async function create() {
  await operate(async () => {
    const base = await request('', 'POST', { title: title.value.trim() })
    title.value = ''; managementForm.value.open = false; await load(); emit('select', base)
  })
}
async function rename() {
  await operate(async () => {
    const base = await request(`/${props.activeBase.id}`, 'PUT', { title: title.value.trim() })
    title.value = ''; managementForm.value.open = false; await load(); emit('select', base)
  })
}
async function removeBase() {
  await operate(async () => {
    await request(`/${props.activeBase.id}`, 'DELETE'); emit('select', null); await load()
  })
}
async function add() {
  await operate(async () => {
    await request(`/${props.activeBase.id}/documents`, 'POST', { document_ids: chosen.value })
    chosen.value = []; addForm.value.open = false; await loadMembers(); await load()
  })
}
async function removeDocument(id) {
  await operate(async () => {
    await request(`/${props.activeBase.id}/documents/${id}`, 'DELETE'); await loadMembers(); await load()
  })
}
watch(() => props.activeBase?.id, () => { chosen.value = []; error.value = ''; loadMembers() })
watch(() => props.documents, loadMembers)
onMounted(() => operate(load))
</script>

<template>
  <section class="knowledge-bases" aria-label="知识库管理">
    <h2>知识库 <button :disabled="busy" @click="operate(load)" aria-label="刷新知识库">↻</button></h2>
    <button v-for="base in bases" :key="base.id" class="base" :class="{ active: activeBase?.id === base.id }" :aria-pressed="activeBase?.id === base.id" :disabled="busy" @click="emit('select', activeBase?.id === base.id ? null : base)">{{ base.title }} <small>{{ base.document_count }} 份文档</small></button>
    <details ref="managementForm"><summary>＋ 创建 / 管理知识库</summary>
      <form @submit.prevent="create"><label>知识库名称<input v-model="title" maxlength="100" required :disabled="busy" /></label><button :disabled="busy || !title.trim()">创建</button><button v-if="activeBase" type="button" :disabled="busy || !title.trim()" @click="rename">重命名当前库</button></form>
      <button v-if="activeBase" class="danger" :disabled="busy" @click="removeBase">删除当前知识库</button><p class="hint">删除知识库保留原文档，清除该库会话。</p>
    </details>
    <details v-if="activeBase" open><summary>{{ activeBase.title }} · 成员文档</summary>
      <p v-if="!members.length" class="hint">还没有文档，请在下方多选添加。</p>
      <div v-for="doc in members" :key="doc.id" class="member"><span>{{ doc.title }}<small>{{ doc.processing_status_label }}</small></span><button :disabled="busy" @click="emit('process-document', doc.id)">处理</button><button :disabled="busy" @click="removeDocument(doc.id)">移出</button></div>
      <details ref="addForm"><summary>添加已有文档</summary><form @submit.prevent="add"><label v-for="doc in documents.filter(d => !members.some(m => m.id === d.id))" :key="doc.id" class="choice"><input v-model="chosen" type="checkbox" :value="doc.id" :disabled="busy" />{{ doc.title }}</label><button :disabled="busy || !chosen.length">添加所选文档</button></form></details>
      <p class="hint">移出只解除关联，原文件和既有历史保留。</p>
    </details>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.knowledge-bases{border-top:1px solid #dde4db;padding-top:12px;font-size:12px}h2{font-size:13px;display:flex;justify-content:space-between;align-items:center}button{font:inherit;padding:6px 8px;border:1px solid #d9e1d9;border-radius:6px;background:white;cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.base{display:block;text-align:left;width:100%;margin:5px 0;border:0;padding:10px}.base.active{background:#e3ebe1}.base small{float:right;color:#728173}details{margin:12px 0}summary{cursor:pointer}label{display:grid;gap:5px;margin:10px 0}input:not([type=checkbox]){width:100%;padding:7px;border:1px solid #d9e1d9;border-radius:6px}.choice{display:flex;align-items:center;overflow-wrap:anywhere}.member{display:flex;align-items:center;gap:5px;margin:10px 0}.member span{flex:1;min-width:0;overflow-wrap:anywhere}.member small{display:block;color:#728173}.hint{color:#728173;font-size:11px}.danger,.error{color:#ad3939}
.base{display:flex;align-items:center;justify-content:space-between;gap:10px;overflow-wrap:anywhere}.base small{float:none;flex-shrink:0;white-space:nowrap}.member{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px;align-items:center;padding:8px 0;border-bottom:1px solid #e4e9e1}.member button{white-space:nowrap}.choice input{flex-shrink:0}.knowledge-bases{min-width:0}.hint,.error,summary{overflow-wrap:anywhere}
</style>
