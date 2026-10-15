import { nextTick, onBeforeUnmount, ref, watch, type Ref } from 'vue'

export function useAutoFollow(element: Ref<HTMLElement | null>, revision: () => string) {
  const following = ref(true)
  let frame = 0
  let disposed = false
  async function follow() {
    await nextTick()
    if (disposed || !following.value) return
    if (frame) cancelAnimationFrame(frame)
    frame = requestAnimationFrame(() => {
      frame = 0
      if (following.value && element.value) element.value.scrollTop = element.value.scrollHeight
    })
  }
  function onScroll() {
    const target = element.value
    if (target) following.value = target.scrollHeight - target.scrollTop - target.clientHeight < 48
  }
  function resume() {
    following.value = true
    void follow()
  }
  function pause() {
    following.value = false
    if (frame) cancelAnimationFrame(frame)
    frame = 0
  }
  watch(
    revision,
    () => {
      if (following.value) void follow()
    },
    { flush: 'post' },
  )
  onBeforeUnmount(() => {
    disposed = true
    if (frame) cancelAnimationFrame(frame)
  })
  return { following, onScroll, resume, pause }
}
