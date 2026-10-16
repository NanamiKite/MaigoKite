import { nextTick, onBeforeUnmount, watch, type Ref } from 'vue'
import { useChatViewStore } from '@/stores/chat-view'
import { useUIStore } from '@/stores/ui'

export function useChatPosition(
  id: Ref<string>,
  scroll: Ref<HTMLElement | null>,
  count: Ref<number>,
  follow: { following: Ref<boolean>; pause: () => void; resume: () => void },
) {
  const view = useChatViewStore(),
    ui = useUIStore()
  let generation = 0,
    restoring = false
  let displayedId = id.value
  function save(key: string) {
    if (!scroll.value || restoring) return
    view.positions.set(key, {
      top: scroll.value.scrollTop,
      following: follow.following.value,
      count: count.value,
    })
    view.panels.set(key, ui.drawerTab)
  }
  async function restore(key: string) {
    const request = ++generation,
      position = view.positions.get(key)
    restoring = true
    follow.pause()
    count.value = position?.count ?? 20
    const panel = view.panels.get(key)
    if (panel && ui.drawerTab !== 'pending') ui.drawerTab = panel
    await nextTick()
    if (request !== generation) return
    if (position && !position.following) {
      if (scroll.value) scroll.value.scrollTop = position.top
    } else follow.resume()
    restoring = false
  }
  watch(
    id,
    (next, previous) => {
      if (previous !== undefined) save(previous)
      displayedId = next
      void restore(next)
    },
    { immediate: true },
  )
  onBeforeUnmount(() => {
    // The router has already changed its params when this view unmounts.
    save(displayedId)
    generation++
  })
}
