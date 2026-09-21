import { app, shell, BrowserWindow, ipcMain } from 'electron'
import { join } from 'path'
import { existsSync } from 'fs'
import { spawn, type ChildProcess } from 'child_process'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'

let sidecar: ChildProcess | null = null

/** 启动本地 Python sidecar（FastAPI on 127.0.0.1:8765）。GEOMIND_NO_SIDECAR=1 可关闭 */
function startSidecar(): void {
  if (process.env.GEOMIND_NO_SIDECAR === '1') return
  const serverDir = join(__dirname, '../../server')
  const pyPath = join(
    serverDir,
    process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python'
  )
  if (!existsSync(pyPath)) {
    console.warn(`[GeoMind] 未找到 sidecar Python：${pyPath}，请先在 server/ 创建 venv`)
    return
  }
  const child: ChildProcess = spawn(
    pyPath,
    ['-m', 'uvicorn', 'app.main:app', '--port', '8765'],
    { cwd: serverDir, stdio: ['ignore', 'pipe', 'pipe'] }
  )
  sidecar = child
  child.stdout?.on('data', d => console.log(`[sidecar] ${String(d).trimEnd()}`))
  child.stderr?.on('data', d => console.warn(`[sidecar] ${String(d).trimEnd()}`))
  child.on('exit', (code, signal) => {
    console.warn(`[GeoMind] sidecar 退出 code=${code} signal=${signal}`)
    sidecar = null
  })
}

function stopSidecar(): void {
  if (sidecar) {
    sidecar.kill()
    sidecar = null
  }
}

function createWindow(): void {
  // Create the browser window.
  const mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1180,
    minHeight: 720,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#0b1020',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))

  startSidecar()
  createWindow()

  app.on('before-quit', stopSidecar)

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
