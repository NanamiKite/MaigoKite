import type { ObjectDirective } from 'vue'

// Arrival-only presentation state. No scroll/resize offset synchronization.
const cleanup = new WeakMap<HTMLElement, () => void>()
const arrivalCheck = new WeakMap<HTMLElement, () => void>()
export const vDeskPaper: ObjectDirective<HTMLElement> = {
  mounted(element) {
    let generation = 0,
      frame = 0
    const cover = document.createElement('span')
    cover.className = 'desk-paper-cover'
    cover.setAttribute('aria-hidden', 'true')
    element.append(cover)
    function settle() {
      const current = ++generation
      element.classList.remove('desk-ready')
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(async () => {
        const animations: Animation[] = []
        for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement)
          animations.push(
            ...(parent.getAnimations?.() ?? []).filter(
              (a) => a.effect?.getComputedTiming().iterations !== Infinity,
            ),
          )
        await Promise.allSettled(animations.map((a) => a.finished))
        if (current === generation && element.isConnected) element.classList.add('desk-ready')
      })
    }
    const arrival = (event: AnimationEvent) => {
      if (event.target === element && event.animationName !== 'desk-reveal') settle()
    }
    element.addEventListener('animationstart', arrival)
    arrivalCheck.set(element, settle)
    settle()
    cleanup.set(element, () => {
      generation++
      cancelAnimationFrame(frame)
      element.removeEventListener('animationstart', arrival)
      cover.remove()
    })
  },
  beforeUpdate(element, binding) {
    // v-show can restart an animation before animationstart is dispatched.
    // Re-cover before Vue makes the arriving panel visible, not one frame later.
    if (binding.value !== binding.oldValue) arrivalCheck.get(element)?.()
  },
  beforeUnmount(element) {
    cleanup.get(element)?.()
    cleanup.delete(element)
    arrivalCheck.delete(element)
  },
}
