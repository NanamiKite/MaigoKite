import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EventSocket } from '@/api/socket'
import { api, type AgentEvent } from '@/api/client'

const event = (sequence: number, type = 'task.succeeded'): AgentEvent => ({
  schema_version: 1, event_id: `e-${sequence}`, sequence, type,
  occurred_at: '2026-10-13T00:00:00Z',
  conversation_id: 'c', task_id: 't', payload: {},
})
const ready = { ...event(0, 'stream.ready'), event_id: 'ready' }
const active: EventSocket[] = []
function harness() {
  let receive!: (event: AgentEvent) => void
  const socket = { readyState: 1, close: vi.fn(), onclose: null, onopen: null, onerror: null } as unknown as WebSocket
  const consume = vi.fn(), connection = vi.fn()
  const history = vi.fn().mockResolvedValue([])
  const stream = new EventSocket({
    connect: callback => { receive = callback; return socket }, history,
    receive: consume, connection,
  })
  active.push(stream)
  stream.start()
  return { stream, socket, consume, connection, history, receive: (value: AgentEvent) => receive(value) }
}
beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { for (const stream of active.splice(0)) stream.stop() })

describe('event acknowledgement and replay', () => {
  it('waits for registration, replays history, then deduplicates buffered live events', async () => {
    const h = harness()
    h.socket.onopen?.(new Event('open'))
    expect(h.history).not.toHaveBeenCalled()
    let resolveHistory!: (events: AgentEvent[]) => void
    h.history.mockImplementationOnce(() => new Promise(resolve => { resolveHistory = resolve }))
    h.receive(ready)
    expect(h.history).toHaveBeenCalledTimes(1)
    h.receive(event(2)); h.receive(event(1))
    resolveHistory([event(1)])
    await flushPromises()
    expect(h.consume.mock.calls.map(call => call[0].sequence)).toEqual([1, 2])
    expect(h.consume.mock.calls.map(call => call[1])).toEqual([true, false])
    // A live receipt is projected immediately but does not advance the HTTP
    // replay cursor until a later history response confirms that sequence.
    expect(h.stream.lastSeq).toBe(1)
    expect(h.connection).toHaveBeenLastCalledWith(true)
  })

  it('does not acknowledge a failed projection or skip its durable replay', async () => {
    const h = harness()
    h.history.mockResolvedValue([event(1), event(2)])
    h.consume.mockImplementationOnce(() => {}).mockImplementationOnce(() => { throw new Error('projection failed') })
    h.receive(ready)
    await flushPromises()
    expect(h.stream.lastSeq).toBe(1)
    expect(h.socket.close).toHaveBeenCalledTimes(1)
    expect(h.connection).toHaveBeenLastCalledWith(false)
    h.history.mockResolvedValue([event(2)])
    h.receive(ready)
    await flushPromises()
    expect(h.history).toHaveBeenLastCalledWith(1, expect.any(AbortSignal))
    expect(h.consume.mock.calls.map(call => call[0].sequence)).toEqual([1, 2, 2])
    expect(h.stream.lastSeq).toBe(2)
    expect(h.connection).toHaveBeenLastCalledWith(true)
  })

  it('disconnects after a live projection error and retries the same event through history', async () => {
    const h = harness()
    h.receive(ready); await flushPromises()
    h.consume.mockImplementationOnce(() => { throw new Error('live projection failed') })
    expect(() => h.receive(event(1))).not.toThrow()
    expect(h.socket.close).toHaveBeenCalledTimes(1)
    expect(h.connection).toHaveBeenLastCalledWith(false)
    h.history.mockResolvedValue([event(1)])
    h.receive(ready); await flushPromises()
    expect(h.consume).toHaveBeenCalledTimes(2)
    expect(h.stream.lastSeq).toBe(1)
  })

  it('keeps a failing buffered live event recoverable without an unhandled promise', async () => {
    const h = harness()
    let resolveHistory!: (events: AgentEvent[]) => void
    h.history.mockImplementationOnce(() => new Promise(resolve => { resolveHistory = resolve }))
    h.receive(ready)
    expect(h.history).toHaveBeenCalledTimes(1)
    h.receive(event(1))
    h.consume.mockImplementationOnce(() => { throw new Error('buffered projection failed') })
    resolveHistory([])
    await flushPromises()
    expect(h.connection).toHaveBeenLastCalledWith(false)
    expect(h.socket.close).toHaveBeenCalledTimes(1)
    expect(h.stream.lastSeq).toBe(0)
    h.history.mockResolvedValue([event(1)])
    h.receive(ready); await flushPromises()
    expect(h.consume).toHaveBeenCalledTimes(2)
    expect(h.stream.lastSeq).toBe(1)
  })

  it('aborts pending replay and releases timers when stopped', async () => {
    const h = harness()
    let finish!: (value: AgentEvent[]) => void
    h.history.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    h.socket.onopen?.(new Event('open'))
    h.receive(ready)
    const signal = h.history.mock.calls[0][1] as AbortSignal
    h.stream.stop()
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    finish([event(1)])
    h.receive(event(2))
    await flushPromises()
    expect(h.consume).not.toHaveBeenCalled()
  })
})

describe('WebSocket frame boundary', () => {
  it.each([
    ['a missing timestamp', { ...event(1), occurred_at: undefined }],
    ['a missing text payload', event(1, 'message.delta')],
  ])('rejects a durable event with %s before calling the consumer', (_description, frame) => {
    vi.stubGlobal('WebSocket', class { close = vi.fn() })
    const consume = vi.fn()
    const socket = api.connectEvents('c', consume)

    expect(() => socket.onmessage!(new MessageEvent('message', { data: JSON.stringify(frame) }))).not.toThrow()
    expect(consume).not.toHaveBeenCalled()
    expect(socket.close).toHaveBeenCalledTimes(1)
  })

  it('ignores invalid JSON but does not swallow consumer exceptions as malformed frames', () => {
    vi.stubGlobal('WebSocket', class { close = vi.fn() })
    const consume = vi.fn().mockImplementation(() => { throw new Error('consumer defect') })
    const socket = api.connectEvents('c', consume)
    expect(() => socket.onmessage!(new MessageEvent('message', { data: '{broken' }))).not.toThrow()
    expect(consume).not.toHaveBeenCalled()
    expect(socket.close).toHaveBeenCalledTimes(1)
    expect(() => socket.onmessage!(new MessageEvent('message', { data: JSON.stringify(event(1)) }))).toThrow('consumer defect')
    expect(consume).toHaveBeenCalledTimes(1)
    expect(consume).toHaveBeenCalledWith(event(1))
    expect(socket.close).toHaveBeenCalledTimes(1)
  })
})
