/**
 * 必须在 import 任何 cesium API 之前执行：
 * dev 下 static-copy 中间件把资源挂在 /cesium；
 * prod(file://) 下解析为 index.html 同级的 cesium/ 目录。
 */
;(window as unknown as { CESIUM_BASE_URL: string }).CESIUM_BASE_URL = new URL(
  'cesium/',
  window.location.href
).href
