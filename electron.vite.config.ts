import { cpSync, existsSync, rmSync } from 'fs'
import { resolve } from 'path'
import type { Plugin } from 'vite'
import { defineConfig } from 'electron-vite'
import vue from '@vitejs/plugin-vue'
import sirv from 'sirv'

const cesiumDir = resolve(__dirname, 'node_modules/cesium/Build/Cesium')

/** dev 下用中间件把 /cesium 指向 node_modules；build 时复制到 outDir/cesium */
function cesiumAssets(): Plugin {
  let outDir = resolve(__dirname, 'out/renderer')
  return {
    name: 'geomind-cesium-assets',
    apply: () => true,
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir)
    },
    configureServer(server) {
      server.middlewares.use('/cesium', sirv(cesiumDir, { dev: true, single: false }))
    },
    // dev：Vite 预打包 cesium.js（esbuild CJS 垫片 + Cesium Expression 编译）需要 eval；
    // Web 构建（VITE_API_BASE 已定义，供 FastAPI/Docker 同源托管）：Cesium 生产 bundle 运行时
    // 仍有 eval 路径（worker 内联/Expression），需放开；
    // Electron 生产构建：保持严格 CSP，meta 不变。
    transformIndexHtml(html, ctx) {
      const isDev = !!ctx.server
      const isWebBuild = !isDev && process.env.VITE_API_BASE !== undefined
      if (!isDev && !isWebBuild) return html
      return html.replace(
        "script-src 'self' 'wasm-unsafe-eval'",
        "script-src 'self' 'wasm-unsafe-eval' 'unsafe-eval'"
      )
    },
    closeBundle() {
      const target = resolve(outDir, 'cesium')
      if (existsSync(target)) rmSync(target, { recursive: true, force: true })
      cpSync(cesiumDir, target, { recursive: true })
    }
  }
}

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    base: './',
    resolve: {
      alias: {
        '@renderer': resolve('src/renderer/src')
      }
    },
    plugins: [vue(), cesiumAssets()],
    build: {
      chunkSizeWarningLimit: 2500,
      rollupOptions: {
        output: {
          manualChunks(id: string): string | undefined {
            if (id.includes('/node_modules/cesium/') || id.includes('\\node_modules\\cesium\\')) {
              return 'cesium'
            }
            if (id.includes('/node_modules/@vue/') || /\/node_modules\/vue\//.test(id)) {
              return 'vue'
            }
            return undefined
          }
        }
      }
    }
  }
})
