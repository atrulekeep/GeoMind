<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { resetSession, streamChat, submitApproval, type ToolTrace } from '@renderer/lib/api'
import type { SceneSpec } from '@renderer/types/scene'

interface PendingApproval {
  runId: string
  summary: string
  layerCount: number
  resolved?: boolean
}

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  traces?: ToolTrace[]
  error?: boolean
  streaming?: boolean
  phase?: string
  approval?: PendingApproval
}

const emit = defineEmits<{ scene: [spec: SceneSpec]; reset: [] }>()

const WELCOME =
  '你好，我是 GeoMind。试试对我说：加载北京各区边界，统计每个区的医院数量，用三维柱状图展示。'

const messages = ref<ChatMessage[]>([{ role: 'assistant', text: WELCOME }])
const input = ref('')
const loading = ref(false)
const listRef = ref<HTMLDivElement | null>(null)

async function onNewConversation(): Promise<void> {
  if (loading.value) return
  await resetSession().catch(() => undefined)
  messages.value = [{ role: 'assistant', text: WELCOME }]
  emit('reset')
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
}

async function onSend(): Promise<void> {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''
  messages.value.push({ role: 'user', text })
  messages.value.push({
    role: 'assistant',
    text: '',
    traces: [],
    streaming: true,
    phase: '正在连接本地 Agent…'
  })
  const current = messages.value[messages.value.length - 1]
  loading.value = true
  await scrollToBottom()
  let sceneEmitted = false

  try {
    for await (const ev of streamChat(text)) {
      switch (ev.type) {
        case 'status':
          current.phase = ev.phase === 'planning' ? '模型思考中…' : '执行 GIS 工具…'
          break
        case 'tool_call':
          current.traces?.push({
            callId: ev.callId,
            name: ev.name,
            args: ev.args as ToolTrace['args'],
            pending: true
          })
          current.phase = ''
          void scrollToBottom()
          break
        case 'tool_result': {
          const trace = current.traces?.find(t => t.callId === ev.callId)
          if (trace) {
            trace.result = ev.result
            trace.pending = false
          }
          if (ev.scene) {
            emit('scene', ev.scene)
            sceneEmitted = true
          }
          break
        }
        case 'approval_required':
          current.approval = {
            runId: ev.runId,
            summary: ev.summary,
            layerCount: ev.currentLayerCount
          }
          current.phase = ''
          void scrollToBottom()
          break
        case 'text_delta':
          current.text += ev.delta
          current.phase = ''
          void scrollToBottom()
          break
        case 'done':
          if (ev.reply) current.text = ev.reply
          if (ev.scene && !sceneEmitted) emit('scene', ev.scene)
          current.streaming = false
          current.phase = ''
          break
        case 'error':
          current.error = true
          current.text = ev.message
          current.streaming = false
          current.phase = ''
          break
      }
    }
  } catch (e) {
    current.error = true
    current.streaming = false
    current.phase = ''
    current.text = `${(e as Error).message}。请确认本地 sidecar 已启动（server 目录）。`
  } finally {
    current.streaming = false
    loading.value = false
    await scrollToBottom()
  }
}

function traceState(t: ToolTrace): 'running' | 'error' | 'ok' {
  if (t.pending) return 'running'
  if (t.result && typeof t.result === 'object' && 'error' in t.result) return 'error'
  return 'ok'
}

async function onApproval(m: ChatMessage, approved: boolean): Promise<void> {
  if (!m.approval || m.approval.resolved) return
  m.approval.resolved = true
  await submitApproval(
    m.approval.runId,
    approved,
    approved ? undefined : '用户拒绝了场景重建'
  ).catch(() => undefined)
  void scrollToBottom()
}
</script>

<template>
  <div class="chat-panel">
    <div class="chat-header">
      <span>对话</span>
      <button class="new-chat" :disabled="loading" @click="onNewConversation">＋ 新对话</button>
    </div>
    <div ref="listRef" class="msg-list">
      <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
        <div class="bubble" :class="{ error: m.error }">
          <template v-if="m.text">{{ m.text }}</template>
          <span v-else-if="m.phase" class="phase">{{ m.phase }}</span>
          <span v-if="m.streaming" class="caret"></span>
        </div>
        <div v-if="m.traces?.length" class="traces">
          <details v-for="(t, j) in m.traces" :key="j" class="trace-item">
            <summary>
              <span class="tool-dot" :class="traceState(t)"></span>{{ t.name }}
            </summary>
            <pre class="trace-json">{{ JSON.stringify(t.args, null, 2) }}</pre>
            <pre
              v-if="t.result"
              class="trace-json result"
              :class="{ error: traceState(t) === 'error' }"
            >→ {{ JSON.stringify(t.result, null, 2) }}</pre>
          </details>
        </div>
        <div v-if="m.approval" class="approval-card">
          <div class="approval-title">⚠ 破坏性操作，需要你确认</div>
          <div class="approval-summary">
            {{ m.approval.summary }}（当前场景含 {{ m.approval.layerCount }} 个图层）
          </div>
          <div v-if="!m.approval.resolved" class="approval-actions">
            <button class="btn-approve" @click="onApproval(m, true)">批准重建</button>
            <button class="btn-reject" @click="onApproval(m, false)">拒绝（改用增量）</button>
          </div>
          <div v-else class="approval-done">已提交决定，Agent 继续处理中…</div>
        </div>
      </div>
    </div>
    <div class="composer">
      <textarea
        v-model="input"
        rows="2"
        placeholder="输入指令，Enter 发送 / Shift+Enter 换行"
        @keydown.enter.exact.prevent="onSend"
      ></textarea>
      <button :disabled="loading || !input.trim()" @click="onSend">发送</button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.chat-header {
  height: 38px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  border-bottom: 1px solid #232f4a;
  font-size: 12px;
  color: #7f8db0;
}
.new-chat {
  background: transparent;
  border: 1px solid #2c3c60;
  color: #9fc0ff;
  border-radius: 6px;
  font-size: 12px;
  padding: 3px 10px;
  cursor: pointer;
}
.new-chat:hover:not(:disabled) {
  border-color: #3b82f6;
}
.new-chat:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.msg-list {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bubble {
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-row.user {
  align-self: flex-end;
  max-width: 92%;
}
.msg-row.user .bubble {
  background: #2563eb;
  color: #fff;
  border-bottom-right-radius: 2px;
}
.msg-row.assistant {
  align-self: flex-start;
  max-width: 96%;
}
.msg-row.assistant .bubble {
  background: #18223a;
  color: #d6e1f5;
  border: 1px solid #263450;
  border-bottom-left-radius: 2px;
}
.bubble.error {
  border-color: #b3424a !important;
  color: #f3a7ab !important;
}
.phase {
  color: #7f8db0;
  font-style: italic;
}
.caret {
  display: inline-block;
  width: 7px;
  height: 13px;
  margin-left: 3px;
  vertical-align: -2px;
  background: #8fb4ff;
  animation: blink 1s steps(2, start) infinite;
}
@keyframes blink {
  to {
    visibility: hidden;
  }
}
.traces {
  margin-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.trace-item {
  background: #10182b;
  border: 1px solid #232f4a;
  border-radius: 6px;
  padding: 4px 8px;
}
.trace-item summary {
  font-size: 12px;
  color: #8fb4ff;
  cursor: pointer;
  list-style: none;
}
.trace-item summary::marker {
  display: none;
}
.tool-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 6px;
  background: #4ade80;
}
.tool-dot.running {
  background: #fbbf24;
  animation: pulse 1s ease-in-out infinite;
}
.tool-dot.error {
  background: #f87171;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
.trace-json {
  font-size: 11px;
  color: #9aa8c7;
  margin: 6px 0 2px;
  white-space: pre-wrap;
  word-break: break-word;
}
.trace-json.result {
  border-top: 1px dashed #232f4a;
  padding-top: 5px;
  color: #7fb98e;
}
.trace-json.result.error {
  color: #f3a7ab;
}
.approval-card {
  margin-top: 6px;
  border: 1px solid #7a5a1f;
  background: #241c0e;
  border-radius: 8px;
  padding: 9px 12px;
  max-width: 420px;
}
.approval-title {
  font-size: 12px;
  color: #fbbf24;
  font-weight: 600;
}
.approval-summary {
  font-size: 12px;
  color: #d8c9a3;
  margin: 5px 0 8px;
  line-height: 1.5;
}
.approval-actions {
  display: flex;
  gap: 8px;
}
.approval-actions button {
  border-radius: 6px;
  font-size: 12px;
  padding: 4px 14px;
  cursor: pointer;
}
.btn-approve {
  background: #b45309;
  border: 1px solid #d97706;
  color: #fff;
}
.btn-approve:hover {
  background: #d97706;
}
.btn-reject {
  background: transparent;
  border: 1px solid #4b5a80;
  color: #bcc8e4;
}
.btn-reject:hover {
  border-color: #93a4cc;
}
.approval-done {
  font-size: 11px;
  color: #8fa0c4;
  font-style: italic;
}
.composer {
  border-top: 1px solid #232f4a;
  padding: 10px;
  display: flex;
  gap: 8px;
}
.composer textarea {
  flex: 1;
  resize: none;
  background: #10182b;
  border: 1px solid #263450;
  border-radius: 8px;
  color: #d6e1f5;
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
}
.composer textarea:focus {
  border-color: #3b82f6;
}
.composer button {
  align-self: flex-end;
  background: #2563eb;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 0 18px;
  height: 38px;
  cursor: pointer;
  font-size: 13px;
}
.composer button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
