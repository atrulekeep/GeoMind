<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import CesiumViewer from './components/CesiumViewer.vue'
import { checkHealth, getExportUrl } from './lib/api'
import type { SceneSpec } from './types/scene'

const scene = ref<SceneSpec | null>(null)
const sidecarOk = ref(false)
let timer: ReturnType<typeof setInterval> | null = null

function onPreview(): void {
  if (!scene.value) return
  window.open(getExportUrl(), '_blank')
}

function onDownload(): void {
  if (!scene.value) return
  window.open(`${getExportUrl()}?download=1`, '_blank')
}

onMounted(() => {
  const poll = async (): Promise<void> => {
    sidecarOk.value = await checkHealth()
    if (sidecarOk.value && timer) {
      clearInterval(timer)
      timer = null
    }
  }
  void poll()
  timer = setInterval(poll, 2000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">GeoMind <span class="sub">三维 GIS 智能体</span></div>
      <div class="status">
        <i :class="sidecarOk ? 'ok' : 'bad'"></i>
        {{ sidecarOk ? 'Sidecar 已连接' : 'Sidecar 未连接' }}
        <button class="export-btn" :disabled="!scene" @click="onPreview">场景预览</button>
        <button class="export-btn" :disabled="!scene" @click="onDownload">下载 HTML</button>
      </div>
    </header>
    <div class="workspace">
      <aside class="side-pane">
        <ChatPanel @scene="scene = $event" @reset="scene = null" />
      </aside>
      <main class="view-pane">
        <CesiumViewer :scene="scene" />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0b1020;
  color: #d6e1f5;
}
.topbar {
  height: 46px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: #0d1426;
  border-bottom: 1px solid #232f4a;
  -webkit-app-region: drag;
}
.brand {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.5px;
}
.brand .sub {
  font-size: 12px;
  font-weight: 400;
  color: #7f8db0;
  margin-left: 8px;
}
.status {
  font-size: 12px;
  color: #9aa8c7;
  -webkit-app-region: no-drag;
}
.status i {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-right: 6px;
}
.status i.ok {
  background: #4ade80;
  box-shadow: 0 0 6px #4ade8088;
}
.status i.bad {
  background: #f87171;
}
.export-btn {
  margin-left: 14px;
  padding: 3px 12px;
  border: 1px solid #2c3c60;
  border-radius: 6px;
  background: transparent;
  color: #9fc0ff;
  font-size: 12px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}
.export-btn:hover:not(:disabled) {
  border-color: #3b82f6;
}
.export-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.workspace {
  flex: 1;
  display: flex;
  min-height: 0;
}
.side-pane {
  width: 340px;
  flex-shrink: 0;
  border-right: 1px solid #232f4a;
  background: #0d1426;
}
.view-pane {
  flex: 1;
  min-width: 0;
}
</style>
