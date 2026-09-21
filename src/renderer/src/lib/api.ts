import type { SceneSpec } from '@renderer/types/scene'

export interface ToolTrace {
  callId?: string
  name: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  args: Record<string, any>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  result?: any
  pending?: boolean
}

export interface ChatResult {
  reply: string
  scene?: SceneSpec
  traces: ToolTrace[]
}

// Electron：直连主进程拉起的 sidecar；Web（Docker）：构建期 VITE_API_BASE='' 走同源相对路径
const RAW = import.meta.env.VITE_API_BASE
const BASE = typeof RAW === 'string' ? RAW : 'http://127.0.0.1:8765'

export type StreamEvent =
  | { type: 'status'; phase: 'planning' | 'executing'; step: number }
  | { type: 'tool_call'; name: string; args: Record<string, unknown>; callId: string }
  | {
      type: 'tool_result'
      callId: string
      name: string
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      result: any
      scene?: SceneSpec
    }
  | { type: 'text_delta'; delta: string }
  | {
      type: 'approval_required'
      runId: string
      kind: string
      summary: string
      currentLayerCount: number
    }
  | { type: 'done'; reply: string; traces: ToolTrace[]; scene: SceneSpec | null }
  | { type: 'error'; message: string }

export async function resetSession(): Promise<void> {
  await fetch(`${BASE}/api/session/reset`, { method: 'POST' })
}

export function getExportUrl(): string {
  return `${BASE}/api/scene/export`
}

export async function submitApproval(
  runId: string,
  approved: boolean,
  reason?: string
): Promise<boolean> {
  const r = await fetch(`${BASE}/api/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runId, approved, reason })
  })
  return r.ok && (await r.json()).ok === true
}

export async function checkHealth(): Promise<boolean> {
  try {
    const r = await fetch(`${BASE}/health`, { method: 'GET' })
    return r.ok
  } catch {
    return false
  }
}

export async function sendChat(message: string): Promise<ChatResult> {
  const r = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  })
  if (!r.ok) {
    throw new Error(`sidecar 请求失败：HTTP ${r.status}`)
  }
  return (await r.json()) as ChatResult
}

/** SSE：fetch POST + ReadableStream 手动解析（EventSource 不支持 POST） */
export async function* streamChat(message: string): AsyncGenerator<StreamEvent> {
  const r = await fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  })
  if (!r.ok || !r.body) {
    throw new Error(`sidecar 请求失败：HTTP ${r.status}`)
  }

  const reader = r.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const parseFrame = (frame: string): StreamEvent | null => {
    let dataLine = ''
    for (const line of frame.split('\n')) {
      if (line.startsWith('data:')) dataLine += line.slice(5).trim()
    }
    if (!dataLine) return null
    return JSON.parse(dataLine) as StreamEvent
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const event = parseFrame(frame)
      if (event) yield event
    }
  }
}
