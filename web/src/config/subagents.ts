export interface SubagentSettings {
  calls: number
  concurrent: number
  modelIds: string[]
}

export function emptySubagentSettings(): SubagentSettings {
  return { calls: 0, concurrent: 1, modelIds: [] }
}

export function copySubagentSettings(value: SubagentSettings): SubagentSettings {
  return { ...value, modelIds: [...value.modelIds] }
}

/** Mirrors the existing conversation limits; the Core still validates requests. */
export function validSubagentSettings(value: unknown): value is SubagentSettings {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  const { calls, concurrent, modelIds } = candidate
  return typeof calls === 'number' && Number.isInteger(calls) && calls >= 0 && calls <= 32 &&
    typeof concurrent === 'number' && Number.isInteger(concurrent) &&
    concurrent >= 1 && concurrent <= 8 && (calls === 0 || concurrent <= calls) &&
    Array.isArray(modelIds) && modelIds.length <= 32 &&
    modelIds.every((id: unknown) => typeof id === 'string' && id.trim() === id && id.length > 0 && id.length <= 100) &&
    new Set(modelIds).size === modelIds.length
}
