import type { Appearance } from '../stores/ui'
import type { DesktopNotification, CapsuleRequest } from './preload'
export const pulseStates = ['idle', 'thinking', 'acting', 'waiting', 'error', 'offline'] as const
const string = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length <= max
export function appearanceValue(value: unknown): value is Appearance {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return (
    ['system', 'light', 'dark'].includes(String(p.theme)) &&
    ['stone', 'blue', 'green', 'earth', 'lotus', 'ink'].includes(String(p.accent)) &&
    typeof p.texture === 'boolean' &&
    (p.language === undefined || ['zh-CN', 'en', 'system'].includes(String(p.language))) &&
    (p.showContextUsage === undefined || typeof p.showContextUsage === 'boolean') &&
    (p.hardwareAcceleration === undefined || typeof p.hardwareAcceleration === 'boolean') &&
    (p.enterBehavior === undefined ||
      p.enterBehavior === 'send' ||
      p.enterBehavior === 'newline') &&
    (p.confirmEnterSend === undefined || typeof p.confirmEnterSend === 'boolean') &&
    string(p.greetingName, 32) &&
    string(p.avatar, 4) &&
    Array.from(p.avatar).length === 1 &&
    string(p.capsuleShortcut, 80) &&
    /^(?:(?:Alt|CommandOrControl|Command|Control|Shift|Super)\+)+(?:Space|[A-Z0-9]|F\d{1,2})$/.test(
      p.capsuleShortcut,
    )
  )
}
export function capsuleValue(value: unknown): value is CapsuleRequest {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return string(p.content, 10000) && Boolean(p.content.trim()) && typeof p.newSession === 'boolean'
}
export function notificationValue(value: unknown): value is DesktopNotification {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return (
    string(p.title, 100) &&
    string(p.body, 2000) &&
    (p.sessionId === undefined || string(p.sessionId, 128)) &&
    (p.approvalId === undefined || string(p.approvalId, 128))
  )
}
