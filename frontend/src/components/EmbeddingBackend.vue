<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { apiError } from '../utils/apiError'

const props = defineProps({
  modelValue: { type: String, default: 'api' },
  documentId: { type: [String, Number], default: null },
  knowledgeBaseId: { type: [String, Number], default: null },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'preparing'])
const preparing = ref(false)
const message = ref('')
const error = ref('')
let controller
let version = 0

function reset() {
  version++
  controller?.abort()
  preparing.value = false
  message.value = ''
  error.value = ''
  emit('preparing', false)
}
watch(() => [props.documentId, props.knowledgeBaseId, props.modelValue], reset)
onBeforeUnmount(reset)

async function prepare() {
  if (preparing.value || props.disabled) return
  const scope = props.knowledgeBaseId
    ? { knowledge_base_id: Number(props.knowledgeBaseId) }
    : { document_id: Number(props.documentId) }
  if (!(scope.knowledge_base_id || scope.document_id)) return
  const current = ++version
  controller = new AbortController()
  preparing.value = true
  emit('preparing', true)
  message.value = '正在启动本地模型并准备索引，首次使用可能需要一些时间…'
  error.value = ''
  try {
    const response = await fetch('http://127.0.0.1:8000/embedding/local/prepare', {
      method: 'POST', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scope),
    })
    const data = await response.json()
    if (!response.ok) throw new Error(apiError(data, response.status))
    if (current === version) message.value = data.message
  } catch (err) {
    if (current === version && err.name !== 'AbortError') {
      error.value = err.message || '本地检索准备失败，请重试。'
      message.value = ''
    }
  } finally {
    if (current === version) {
      preparing.value = false
      emit('preparing', false)
    }
  }
}
</script>

<template>
  <div class="embedding-backend">
    <label>向量来源
      <select :value="modelValue" :disabled="disabled || preparing" @change="emit('update:modelValue', $event.target.value)">
        <option value="api">百炼 API（默认）</option>
        <option value="local">本地 BGE-M3</option>
      </select>
    </label>
    <template v-if="modelValue === 'local'">
      <button type="button" :disabled="disabled || preparing || !(documentId || knowledgeBaseId)" @click="prepare">{{ preparing ? '准备中…' : '准备本地检索' }}</button>
      <p class="hint">向量生成和检索在本机运行；对话回答仍需联网。首次使用或文档更新后，请先准备本地检索。</p>
      <p v-if="message" class="hint" role="status">{{ message }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </template>
  </div>
</template>

<style scoped>
.embedding-backend{display:grid;gap:8px;min-width:0}.embedding-backend label{display:grid;gap:5px}.hint{font-size:12px;color:#65766a;line-height:1.6;margin:0}.error{font-size:12px;color:#b33e3e;margin:0}select,button{font:inherit;padding:7px;border:1px solid #dbe3d8;border-radius:6px;background:white;color:inherit;max-width:100%}button{cursor:pointer}button:disabled,select:disabled{opacity:.5;cursor:not-allowed}
</style>
