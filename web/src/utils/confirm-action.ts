import { readonly, shallowRef } from 'vue'

interface Confirmation { message: string; danger: boolean; title?: string; confirmLabel?: string }
interface ConfirmationOptions { title?: string; confirmLabel?: string; signal?: AbortSignal }
const pending = shallowRef<Confirmation | null>(null)
export const confirmation = readonly(pending)
let resolve: ((accepted: boolean) => void) | undefined

/** Presentation-only replacement for window.confirm; no business action runs here. */
export function confirmAction(message: string, danger = true, options: ConfirmationOptions = {}): Promise<boolean> {
  resolve?.(false)
  return new Promise<boolean>(settle => {
    function finish(accepted: boolean) {
      options.signal?.removeEventListener('abort', cancel)
      // An expired dialog must not dismiss a newer confirmation.
      if (resolve === finish) { resolve = undefined; pending.value = null }
      settle(accepted)
    }
    function cancel() { finish(false) }
    resolve = finish
    pending.value = { message, danger, title: options.title, confirmLabel: options.confirmLabel }
    if (options.signal?.aborted) finish(false)
    else options.signal?.addEventListener('abort', cancel, { once: true })
  })
}
export function settleConfirmation(accepted: boolean) {
  resolve?.(accepted)
}
