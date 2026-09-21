<script setup lang="ts">
import './../lib/cesium-base'
import * as Cesium from 'cesium'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { SceneSpec } from '@renderer/types/scene'
import { SceneRenderer } from '@renderer/lib/sceneRenderer'

const props = defineProps<{ scene: SceneSpec | null }>()

const hostRef = ref<HTMLDivElement | null>(null)
let viewer: Cesium.Viewer | null = null
let renderer: SceneRenderer | null = null

onMounted(() => {
  if (!hostRef.value) return
  // 高德栅格底图（国内访问快，无需 token）；后续可切换为同事提供的 WMTS
  const gaode = new Cesium.UrlTemplateImageryProvider({
    url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    subdomains: ['1', '2', '3', '4'],
    maximumLevel: 18
  })

  viewer = new Cesium.Viewer(hostRef.value, {
    baseLayer: new Cesium.ImageryLayer(gaode),
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false
  })
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString('#111a33')
  viewer.camera.setView({
    destination: Cesium.Cartesian3.fromDegrees(116.4, 39.9, 1_800_000)
  })
  // 隐藏 logo 容器
  ;(viewer.cesiumWidget.creditContainer as HTMLElement).style.display = 'none'

  renderer = new SceneRenderer(viewer)
  if (props.scene) void renderer.render(props.scene)
})

watch(
  () => props.scene,
  s => {
    if (s && renderer) void renderer.render(s)
  }
)

onBeforeUnmount(() => {
  viewer?.destroy()
  viewer = null
})
</script>

<template>
  <div ref="hostRef" class="cesium-host"></div>
</template>

<style scoped>
.cesium-host {
  width: 100%;
  height: 100%;
}
</style>
