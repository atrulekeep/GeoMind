# ---------- 阶段 1：构建渲染端静态产物 ----------
FROM node:22-bookworm-slim AS web
RUN corepack enable
WORKDIR /build

COPY package.json pnpm-lock.yaml ./
# 容器内只构建 renderer，不需要 Electron 二进制与安装脚本
RUN pnpm install --frozen-lockfile --ignore-scripts

COPY tsconfig*.json electron.vite.config.ts ./
COPY src ./src
# 空字符串 = 同源相对路径，API 与静态页由同一个 FastAPI 提供
ENV VITE_API_BASE=
# 全量 build（main/preload 仅打包不运行，无需 Electron 二进制）；运行时只取 renderer 产物
RUN pnpm exec electron-vite build

# ---------- 阶段 2：Python 运行时 ----------
FROM python:3.11-slim AS runtime
WORKDIR /app

COPY server/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/app ./app
COPY --from=web /build/out/renderer ./web

ENV GEOMIND_WEB_DIR=/app/web \
    GEOMIND_PUBLIC_BASE= \
    PYTHONUNBUFFERED=1
EXPOSE 8765

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
