import { computed, watch } from 'vue'
import type { PulseState } from '@/api/events'
import { useRunStore } from '@/stores/run'
import { useSessionStore } from '@/stores/session'
import { pendingItem, pendingOrder } from '@/utils/rail-presentation'
import { desktopEffects } from './useElectron'

const attention = ['waiting', 'acting', 'thinking', 'offline', 'error'] as const

type PulseTarget = {
  sessionId: string
  state: (typeof attention)[number]
  awaitId: string | undefined
}
function isAttention(value: PulseState): value is (typeof attention)[number] {
  return attention.some((item) => item === value)
}

export function usePulse() {
  const run = useRunStore(),
    session = useSessionStore()
  const known = computed(() => {
    const ids = Object.keys(run.turns)
    const active = session.activeSessionId
    if (active && !ids.includes(active)) ids.push(active)
    return ids
  })
  const states = computed(() =>
    known.value.map((id) => {
      const value = run.pulse(id)
      return {
        id,
        value,
        counted: value !== 'error' || !session.sessions.find((item) => item.id === id)?.archived,
      }
    }),
  )
  const offline = computed(() => known.value.some((id) => run.connected[id] === false))
  const recency = computed(() => {
    const rank = new Map<string, number>()
    session.sessions
      .slice()
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
      .forEach((item, index) => rank.set(item.id, index))
    return rank
  })
  const state = computed<PulseState>(() => {
    const values = states.value.filter((item) => item.counted).map((item) => item.value)
    const active = (['waiting', 'acting', 'thinking'] as const).find((value) =>
      values.includes(value),
    )
    if (active) return active
    if (offline.value) return 'offline'
    if (values.includes('error')) return 'error'
    const current = states.value.find((item) => item.id === session.activeSessionId)
    return current?.counted ? current.value : 'idle'
  })
  const target = computed<PulseTarget | null>((previous) => {
    const value = state.value
    if (!isAttention(value)) return null
    const first = states.value
      .filter((item) => item.counted && item.value === value)
      .sort(
        (a, b) =>
          (recency.value.get(a.id) ?? recency.value.size) -
          (recency.value.get(b.id) ?? recency.value.size),
      )[0]
    if (!first) return null
    const awaitId = run.awaits
      .filter((item) => item.sessionId === first.id && pendingItem(item))
      .sort(pendingOrder)[0]?.id
    if (
      previous &&
      previous.sessionId === first.id &&
      previous.state === value &&
      previous.awaitId === awaitId
    )
      return previous
    return { sessionId: first.id, state: value, awaitId }
  })
  watch(
    state,
    (value) => {
      void desktopEffects?.setPulse(value).catch(() => undefined)
    },
    { immediate: true },
  )
  return { state, offline, target }
}
