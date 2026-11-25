import type { ObjectDirective } from 'vue'
import { layoutPixels } from './layout-media'

const hoverDelay = 350

const texts = new WeakMap<HTMLElement, { value?: string }>()
const listeners = new WeakMap<HTMLElement, () => void>()

let node: HTMLElement | null = null
let host: HTMLElement | null = null
let described: string | null | undefined
let shown = ''
let timer: ReturnType<typeof setTimeout> | undefined

function tooltip(): HTMLElement {
  if (!node) {
    node = document.createElement('div')
    node.id = 'inkline-tooltip'
    node.className = 'inkline-tooltip inkline-shell'
    node.setAttribute('role', 'tooltip')
    node.setAttribute('aria-hidden', 'true')
    document.body.append(node)
  }
  return node
}
function cancel() {
  clearTimeout(timer)
  timer = undefined
}
function onScreen(element: HTMLElement) {
  const anchor = element.getBoundingClientRect()
  const viewport = document.documentElement
  return (
    element.isConnected &&
    anchor.bottom > 0 &&
    anchor.top < viewport.clientHeight &&
    anchor.right > 0 &&
    anchor.left < viewport.clientWidth
  )
}
function place() {
  if (!node || !host) return
  const anchor = host.getBoundingClientRect()
  const margin = layoutPixels('--space-2')
  const left = Math.min(
    Math.max(margin, anchor.left + (anchor.width - node.offsetWidth) / 2),
    document.documentElement.clientWidth - margin - node.offsetWidth,
  )
  const above = anchor.top - margin - node.offsetHeight
  node.style.left = `${left}px`
  node.style.top = `${above >= margin ? above : Math.max(margin, anchor.bottom + margin)}px`
}
function follow() {
  if (!host) return
  if (onScreen(host)) place()
  else hide()
}
function undesign(element: HTMLElement) {
  if (described === undefined) return
  if (described === null) element.removeAttribute('aria-describedby')
  else element.setAttribute('aria-describedby', described)
  described = undefined
}
function describe(element: HTMLElement, value: string) {
  if (value === element.getAttribute('aria-label') || value === element.textContent?.trim()) return
  described = element.getAttribute('aria-describedby')
  element.setAttribute('aria-describedby', tooltip().id)
}
function hideWhenHidden() {
  if (document.hidden) hide()
}
function hide() {
  cancel()
  if (!node) return
  if (host) undesign(host)
  host = null
  node.classList.remove('is-visible')
  node.setAttribute('aria-hidden', 'true')
  document.removeEventListener('keydown', hide)
  document.removeEventListener('pointerdown', hide, true)
  document.removeEventListener('scroll', follow, true)
  window.removeEventListener('resize', follow)
  window.removeEventListener('blur', hide)
  document.removeEventListener('visibilitychange', hideWhenHidden)
}
function show(element: HTMLElement, value: string) {
  cancel()
  if (host) undesign(host)
  shown = value
  host = element
  const tooltipNode = tooltip()
  tooltipNode.textContent = value
  describe(element, value)
  place()
  tooltipNode.classList.add('is-visible')
  tooltipNode.setAttribute('aria-hidden', 'false')
  document.addEventListener('keydown', hide)
  document.addEventListener('pointerdown', hide, true)
  document.addEventListener('scroll', follow, true)
  window.addEventListener('resize', follow)
  window.addEventListener('blur', hide)
  document.addEventListener('visibilitychange', hideWhenHidden)
}

export const vTooltip: ObjectDirective<HTMLElement, string | undefined> = {
  mounted(element, binding) {
    const text = { value: binding.value }
    texts.set(element, text)
    const enter = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !text.value) return
      cancel()
      timer = setTimeout(() => {
        if (texts.has(element) && text.value && onScreen(element)) show(element, text.value)
      }, hoverDelay)
    }
    const focus = () => {
      if (text.value && element.matches(':focus-visible')) show(element, text.value)
    }
    const leave = () => {
      cancel()
      if (host === element && !element.matches(':focus-visible')) hide()
    }
    const unfocus = () => {
      if (host === element) hide()
    }
    element.addEventListener('pointerenter', enter)
    element.addEventListener('pointerleave', leave)
    element.addEventListener('focusin', focus)
    element.addEventListener('focusout', unfocus)
    listeners.set(element, () => {
      element.removeEventListener('pointerenter', enter)
      element.removeEventListener('pointerleave', leave)
      element.removeEventListener('focusin', focus)
      element.removeEventListener('focusout', unfocus)
    })
  },
  updated(element, binding) {
    const text = texts.get(element)
    if (!text) return
    text.value = binding.value
    if (host !== element) return
    if (!binding.value) return hide()
    if (binding.value === shown) return
    shown = binding.value
    if (node) node.textContent = binding.value
    undesign(element)
    describe(element, binding.value)
    place()
  },
  beforeUnmount(element) {
    listeners.get(element)?.()
    listeners.delete(element)
    texts.delete(element)
    if (host === element) hide()
  },
}
