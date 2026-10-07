export function apiError(data, status) {
  if (typeof data.detail === 'string') return data.detail
  if (data.detail?.message) {
    const documents = data.detail.documents || []
    return `${data.detail.message}${documents.length ? '：' + documents.map(doc => `${doc.title}（${doc.processing_status_label}）`).join('、') : ''}`
  }
  if (Array.isArray(data.detail)) return data.detail.map(item => item.msg).join('；')
  return `请求失败（${status}）`
}
