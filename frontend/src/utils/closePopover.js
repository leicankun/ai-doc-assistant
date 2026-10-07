// 用于 details 弹出表单：点击外部或按 Escape 收起，并在卸载时清理监听。
export const vClosePopover = {
  mounted(element) {
    const outside = event => {
      if (element.open && !element.contains(event.target)) element.open = false
    }
    const escape = event => {
      if (event.key === 'Escape' && element.open) {
        const focusedInside = element.contains(document.activeElement)
        element.open = false
        if (focusedInside) element.querySelector('summary')?.focus()
      }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    element._closePopover = () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  },
  beforeUnmount(element) { element._closePopover?.() },
}
