# Canvas Host 模块 - 产品需求文档 (PRD)

## 1. 模块概述与用途

`canvas-host` 模块实现了一个本地 HTTP 静态文件服务器，用于托管 OpenClaw Canvas 的交互式页面内容。其核心功能包括：

- 在本地启动 HTTP 服务器，提供静态文件服务
- 支持 A2UI（App-to-UI）界面资源的加载与服务
- 通过 WebSocket 实现文件变更时的实时热重载（Live Reload）
- 使用 `chokidar` 监视文件系统变更
- 为 HTML 文件注入跨平台（iOS/Android）消息桥接脚本
- 提供安全的文件路径解析，防止目录遍历攻击

## 2. 目录结构

```
src/canvas-host/
├── a2ui.ts                        # A2UI 资源处理、HTML 注入、HTTP 请求处理
├── file-resolver.ts               # 安全文件路径解析
├── server.ts                      # Canvas 主服务器、Handler、WebSocket 热重载
├── server.test.ts                 # 服务器测试（不在本文档范围内）
└── server.state-dir.test.ts       # 状态目录测试（不在本文档范围内）
```

## 3. 文件详细说明

---

### 3.1 `file-resolver.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `node:fs/promises` | `fs`（默认导入） |
| `node:path` | `path`（默认导入） |
| `../infra/fs-safe.js` | `SafeOpenError`, `openFileWithinRoot`, `type SafeOpenResult` |

#### 导出函数

##### `normalizeUrlPath(rawPath: string): string`

**用途**：将原始 URL 路径规范化为以 `/` 开头的 POSIX 标准路径。

**逻辑**：
1. 使用 `decodeURIComponent` 解码 `rawPath`，若为空则默认 `"/"`
2. 使用 `path.posix.normalize` 标准化路径
3. 若标准化后不以 `/` 开头，则添加前缀 `/`

```typescript
export function normalizeUrlPath(rawPath: string): string {
  const decoded = decodeURIComponent(rawPath || "/");
  const normalized = path.posix.normalize(decoded);
  return normalized.startsWith("/") ? normalized : `/${normalized}`;
}
```

##### `resolveFileWithinRoot(rootReal: string, urlPath: string): Promise<SafeOpenResult | null>`

**用途**：在给定根目录内安全解析并打开文件，防止路径遍历。

**参数**：
- `rootReal: string` - 根目录的真实路径（已 `realpath` 解析）
- `urlPath: string` - 请求的 URL 路径

**返回值**：`Promise<SafeOpenResult | null>` - 成功时返回 `SafeOpenResult`，失败或不安全时返回 `null`

**内部逻辑**：
1. 调用 `normalizeUrlPath(urlPath)` 规范化路径
2. 去除前导 `/`，得到相对路径 `rel`
3. 检查路径各段是否包含 `".."`，若有则返回 `null`（防止遍历攻击）
4. 定义内部函数 `tryOpen(relative: string)`：
   - 调用 `openFileWithinRoot({ rootDir: rootReal, relativePath: relative })`
   - 若抛出 `SafeOpenError` 则返回 `null`
   - 其他错误则向上抛出
5. 若标准化路径以 `/` 结尾，尝试打开 `path.posix.join(rel, "index.html")`
6. 否则，尝试 `fs.lstat` 检查候选路径：
   - 若为符号链接 → 返回 `null`
   - 若为目录 → 尝试打开 `index.html`
   - `lstat` 出错则忽略
7. 最后尝试直接打开 `rel`

---

### 3.2 `a2ui.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `node:http` | `type IncomingMessage`, `type ServerResponse` |
| `node:fs/promises` | `fs`（默认导入） |
| `node:path` | `path`（默认导入） |
| `node:url` | `fileURLToPath` |
| `../media/mime.js` | `detectMime` |
| `./file-resolver.js` | `resolveFileWithinRoot` |

#### 导出常量

| 常量名 | 值 | 类型 |
|--------|----|------|
| `A2UI_PATH` | `"/__openclaw__/a2ui"` | `string` |
| `CANVAS_HOST_PATH` | `"/__openclaw__/canvas"` | `string` |
| `CANVAS_WS_PATH` | `"/__openclaw__/ws"` | `string` |

#### 模块级私有变量

| 变量名 | 类型 | 初始值 | 用途 |
|--------|------|--------|------|
| `cachedA2uiRootReal` | `string \| null \| undefined` | `undefined` | 缓存 A2UI 根目录的真实路径 |
| `resolvingA2uiRoot` | `Promise<string \| null> \| null` | `null` | 防止并发解析 |

#### 私有函数

##### `resolveA2uiRoot(): Promise<string | null>`

**用途**：在多个候选路径中查找 A2UI 资源目录。

**候选路径列表**（按顺序尝试）：
1. `path.resolve(path.dirname(process.execPath), "a2ui")` — 仅在 `process.execPath` 存在时，通过 `unshift` 添加到最前
2. `path.resolve(here, "a2ui")` — 从源码或 dist 运行
3. `path.resolve(here, "../../src/canvas-host/a2ui")` — 从 dist 但无复制的资源时回退到源码
4. `path.resolve(process.cwd(), "src/canvas-host/a2ui")` — 从仓库根目录运行
5. `path.resolve(process.cwd(), "dist/canvas-host/a2ui")` — 从仓库根目录运行（dist）

其中 `here` = `path.dirname(fileURLToPath(import.meta.url))`

**验证逻辑**：对每个候选目录，检查以下两个文件是否存在：
- `path.join(dir, "index.html")`
- `path.join(dir, "a2ui.bundle.js")`

找到第一个同时包含这两个文件的目录即返回，所有候选均不存在则返回 `null`。

##### `resolveA2uiRootReal(): Promise<string | null>`

**用途**：带缓存和防并发的 A2UI 根目录解析。

**逻辑**：
1. 若 `cachedA2uiRootReal !== undefined`，直接返回缓存值
2. 若 `resolvingA2uiRoot` 为 `null`，启动一个异步 IIFE：
   - 调用 `resolveA2uiRoot()` 获取根目录
   - 若结果非 `null`，通过 `fs.realpath` 获取真实路径
   - 缓存到 `cachedA2uiRootReal`
3. 返回 `resolvingA2uiRoot` promise

#### 导出函数

##### `injectCanvasLiveReload(html: string): string`

**用途**：向 HTML 页面注入跨平台消息桥接和 WebSocket 热重载脚本。

**注入的 `<script>` 内容功能**：

1. **跨平台 action bridge 辅助函数**：
   - `handlerNames` 数组: `["openclawCanvasA2UIAction"]`
   - `postToNode(payload)` 函数：
     - 将 payload 序列化为 JSON 字符串
     - 尝试 iOS 路径: `globalThis.webkit?.messageHandlers?.[name]?.postMessage(raw)`
     - 尝试 Android 路径: `globalThis[name]?.postMessage(raw)`
     - 成功返回 `true`，失败返回 `false`
   - `sendUserAction(userAction)` 函数：
     - 生成 id：优先使用 `userAction.id`（需为非空字符串），否则 `crypto.randomUUID()` 或 `String(Date.now())`
     - 调用 `postToNode({ userAction: action })`
   - 全局挂载：
     - `globalThis.OpenClaw.postMessage = postToNode`
     - `globalThis.OpenClaw.sendUserAction = sendUserAction`
     - `globalThis.openclawPostMessage = postToNode`
     - `globalThis.openclawSendUserAction = sendUserAction`

2. **WebSocket 连接**：
   - 协议：`location.protocol === "https:" ? "wss" : "ws"`
   - URL：`proto + "://" + location.host + CANVAS_WS_PATH`（其中 `CANVAS_WS_PATH` 通过 `JSON.stringify` 注入）
   - `ws.onmessage` 事件：若 `ev.data` 为字符串 `"reload"` 则调用 `location.reload()`

**HTML 注入位置**：
- 查找 `html.toLowerCase().lastIndexOf("</body>")`
- 若找到：在 `</body>` 前插入脚本
- 若未找到：追加到 HTML 末尾

##### `handleA2uiHttpRequest(req: IncomingMessage, res: ServerResponse): Promise<boolean>`

**用途**：处理 A2UI 资源的 HTTP 请求。

**返回值**：`true` 表示请求已被处理，`false` 表示该请求不属于 A2UI。

**逻辑**：
1. 检查 `req.url` 是否存在，不存在返回 `false`
2. 解析 URL，判断 `pathname` 是否等于 `A2UI_PATH` 或以 `A2UI_PATH + "/"` 开头
3. 若不匹配返回 `false`
4. 检查 HTTP 方法，仅允许 `GET` 和 `HEAD`，否则返回 `405 Method Not Allowed`
5. 解析 A2UI 根目录 `resolveA2uiRootReal()`，若为 `null` 返回 `503 "A2UI assets not found"`
6. 截取基础路径后的相对路径 `rel`，调用 `resolveFileWithinRoot(a2uiRootReal, rel || "/")`
7. 若结果为 `null` 返回 `404 "not found"`
8. 确定 MIME 类型：
   - 文件名以 `.html` 或 `.htm` 结尾 → `"text/html"`
   - 否则调用 `detectMime({ filePath: result.realPath })` 或默认 `"application/octet-stream"`
9. 设置 `Cache-Control: no-store`
10. 若 `HEAD` 请求：设置 `Content-Type`（HTML 使用 `text/html; charset=utf-8`）并结束
11. 若为 HTML：读取文件内容（UTF-8），调用 `injectCanvasLiveReload(buf)` 注入脚本，返回
12. 否则直接返回文件二进制内容
13. `finally` 块中关闭文件句柄（忽略关闭错误）

---

### 3.3 `server.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `node:net` | `type Socket` |
| `node:stream` | `type Duplex` |
| `chokidar` | `chokidar`（默认导入） |
| `node:fs` | `* as fsSync` |
| `node:fs/promises` | `fs`（默认导入） |
| `node:http` | `http`（默认导入）, `type IncomingMessage`, `type Server`, `type ServerResponse` |
| `node:path` | `path`（默认导入） |
| `ws` | `type WebSocket`, `WebSocketServer` |
| `../runtime.js` | `type RuntimeEnv` |
| `../config/paths.js` | `resolveStateDir` |
| `../infra/env.js` | `isTruthyEnvValue` |
| `../media/mime.js` | `detectMime` |
| `../utils.js` | `ensureDir`, `resolveUserPath` |
| `./a2ui.js` | `CANVAS_HOST_PATH`, `CANVAS_WS_PATH`, `handleA2uiHttpRequest`, `injectCanvasLiveReload` |
| `./file-resolver.js` | `normalizeUrlPath`, `resolveFileWithinRoot` |

#### 导出类型

##### `CanvasHostOpts`

```typescript
export type CanvasHostOpts = {
  runtime: RuntimeEnv;
  rootDir?: string;
  port?: number;
  listenHost?: string;
  allowInTests?: boolean;
  liveReload?: boolean;
};
```

##### `CanvasHostServerOpts`

```typescript
export type CanvasHostServerOpts = CanvasHostOpts & {
  handler?: CanvasHostHandler;
  ownsHandler?: boolean;
};
```

##### `CanvasHostServer`

```typescript
export type CanvasHostServer = {
  port: number;
  rootDir: string;
  close: () => Promise<void>;
};
```

##### `CanvasHostHandlerOpts`

```typescript
export type CanvasHostHandlerOpts = {
  runtime: RuntimeEnv;
  rootDir?: string;
  basePath?: string;
  allowInTests?: boolean;
  liveReload?: boolean;
};
```

##### `CanvasHostHandler`

```typescript
export type CanvasHostHandler = {
  rootDir: string;
  basePath: string;
  handleHttpRequest: (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
  handleUpgrade: (req: IncomingMessage, socket: Duplex, head: Buffer) => boolean;
  close: () => Promise<void>;
};
```

#### 私有函数

##### `defaultIndexHTML(): string`

**用途**：返回默认的 Canvas 主页 HTML 内容，包含完整的交互式测试页面。

**HTML 结构**：
- `<!doctype html>` 声明
- `<meta charset="utf-8" />`
- `<meta name="viewport" content="width=device-width, initial-scale=1" />`
- `<title>OpenClaw Canvas</title>`
- 内联 CSS 样式（黑色背景 `#000`、白色文字 `#fff`、卡片布局 `720px` 最大宽度、圆角 `16px` 边框等）
- 四个按钮：`Hello`、`Time`、`Photo`、`Dalek`
- 状态显示区域和日志区域
- 内联 JavaScript：
  - 检测 iOS/Android/辅助函数桥接状态
  - 监听 `openclaw:a2ui-action-status` 自定义事件
  - `send(name, sourceComponentId)` 函数发送用户操作
  - 按钮绑定：
    - `btn-hello` → `send("hello", "demo.hello")`
    - `btn-time` → `send("time", "demo.time")`
    - `btn-photo` → `send("photo", "demo.photo")`
    - `btn-dalek` → `send("dalek", "demo.dalek")`

**CSS 关键样式**：
- `.ok { color: #24e08a; }` — 绿色状态
- `.bad { color: #ff5c5c; }` — 红色状态
- `.log` — 等宽字体 `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace`

##### `isDisabledByEnv(): boolean`

**用途**：检查环境变量是否禁用了 Canvas Host。

**检查顺序**：
1. `process.env.OPENCLAW_SKIP_CANVAS_HOST` 为 truthy → 返回 `true`
2. 同上（重复检查，代码中存在重复）
3. `process.env.NODE_ENV === "test"` → 返回 `true`
4. `process.env.VITEST` 存在 → 返回 `true`
5. 其他情况返回 `false`

##### `normalizeBasePath(rawPath: string | undefined): string`

**用途**：规范化基础路径。

**逻辑**：
1. 默认值为 `CANVAS_HOST_PATH`（即 `"/__openclaw__/canvas"`）
2. 调用 `normalizeUrlPath` 规范化
3. 若结果为 `"/"` 则直接返回
4. 否则去除尾部斜杠

##### `prepareCanvasRoot(rootDir: string): Promise<string>`

**用途**：准备 Canvas 根目录。

**逻辑**：
1. 调用 `ensureDir(rootDir)` 确保目录存在
2. 获取 `rootReal` = `await fs.realpath(rootDir)`
3. 检查 `index.html` 是否存在（`fs.stat`）
4. 若不存在，写入 `defaultIndexHTML()` 内容
5. 返回 `rootReal`

##### `resolveDefaultCanvasRoot(): string`

**用途**：解析默认的 Canvas 根目录路径。

**逻辑**：
- 候选路径：`[path.join(resolveStateDir(), "canvas")]`
- 用 `fsSync.statSync` 同步检查是否存在且为目录
- 返回第一个存在的目录，或候选列表的第一项

#### 导出函数

##### `createCanvasHostHandler(opts: CanvasHostHandlerOpts): Promise<CanvasHostHandler>`

**用途**：创建 Canvas 请求处理器，包含 HTTP 请求处理和 WebSocket 升级处理。

**逻辑**：

1. **禁用检查**：若 `isDisabledByEnv()` 且 `opts.allowInTests !== true`，返回空操作处理器（所有方法均为 no-op）

2. **目录准备**：
   - `rootDir` = `resolveUserPath(opts.rootDir ?? resolveDefaultCanvasRoot())`
   - `rootReal` = `await prepareCanvasRoot(rootDir)`

3. **配置参数**：
   - `liveReload` = `opts.liveReload !== false`（默认启用）
   - `testMode` = `opts.allowInTests === true`
   - `reloadDebounceMs` = `testMode ? 12 : 75`
   - `writeStabilityThresholdMs` = `testMode ? 12 : 75`
   - `writePollIntervalMs` = `testMode ? 5 : 10`

4. **WebSocket 服务器**：
   - 若 `liveReload` 为 `true`，创建 `WebSocketServer({ noServer: true })`
   - 维护 `Set<WebSocket>` 集合 `sockets`
   - 连接时添加到集合，关闭时移除

5. **热重载广播**：
   - `broadcastReload()`：遍历所有连接的 WebSocket，发送字符串 `"reload"`
   - `scheduleReload()`：防抖处理，延迟 `reloadDebounceMs` 后调用 `broadcastReload()`，定时器设置 `.unref()`

6. **文件监视（chokidar）**：
   - 监视 `rootReal` 目录
   - 配置：
     - `ignoreInitial: true`
     - `awaitWriteFinish.stabilityThreshold`: `writeStabilityThresholdMs`
     - `awaitWriteFinish.pollInterval`: `writePollIntervalMs`
     - `usePolling`: `testMode`
     - `ignored`: `[/(^|[\\/])\../, /(^|[\\/])node_modules([\\/]|$)/]`（忽略 dotfiles 和 node_modules）
   - `"all"` 事件触发 `scheduleReload()`
   - `"error"` 事件：若 `watcherClosed` 为 `false`，则设置 `watcherClosed = true`，输出错误日志，关闭 watcher

7. **`handleUpgrade` 函数**：
   - 参数：`(req: IncomingMessage, socket: Duplex, head: Buffer) => boolean`
   - 若 `wss` 不存在返回 `false`
   - 检查 `url.pathname !== CANVAS_WS_PATH` → 返回 `false`
   - 调用 `wss.handleUpgrade` 并 emit `"connection"` 事件
   - 返回 `true`

8. **`handleHttpRequest` 函数**：
   - 参数：`(req: IncomingMessage, res: ServerResponse) => Promise<boolean>`
   - 若 `url.pathname === CANVAS_WS_PATH`：
     - `liveReload` 为 true → `426 "upgrade required"`
     - 否则 → `404 "not found"`
   - 基路径匹配逻辑：若 `basePath !== "/"`，检查请求路径是否匹配 `basePath`
   - 仅允许 `GET`/`HEAD` 方法，否则 `405`
   - 调用 `resolveFileWithinRoot(rootReal, urlPath)` 解析文件
   - 若未找到文件：
     - 根路径或以 `/` 结尾 → `404` HTML：`<!doctype html><meta charset="utf-8" /><title>OpenClaw Canvas</title><pre>Missing file.\nCreate ${rootDir}/index.html</pre>`
     - 其他 → `404 "not found"`
   - 找到文件后：
     - 读取文件内容
     - MIME 检测：`.html`/`.htm` → `"text/html"`，否则 `detectMime` 或 `"application/octet-stream"`
     - 设置 `Cache-Control: no-store`
     - HTML 文件：若 `liveReload` 为 true 则调用 `injectCanvasLiveReload`
     - 非 HTML：直接返回二进制内容
   - 出错时返回 `500 "error"`

9. **返回值**：`{ rootDir, basePath, handleHttpRequest, handleUpgrade, close }`
   - `close` 方法：清除防抖定时器 → 关闭 watcher → 关闭 WebSocketServer

##### `startCanvasHost(opts: CanvasHostServerOpts): Promise<CanvasHostServer>`

**用途**：启动完整的 Canvas Host HTTP 服务器。

**逻辑**：

1. **禁用检查**：若 `isDisabledByEnv()` 且 `opts.allowInTests !== true`，返回 `{ port: 0, rootDir: "", close: async () => {} }`

2. **创建 Handler**：使用 `opts.handler` 或调用 `createCanvasHostHandler`

3. **确定 Handler 所有权**：`ownsHandler = opts.ownsHandler ?? (opts.handler === undefined)`

4. **绑定主机**：`bindHost = opts.listenHost?.trim() || "127.0.0.1"`

5. **创建 HTTP 服务器**：
   - 请求处理顺序：
     1. 跳过 WebSocket 升级请求（`req.headers.upgrade === "websocket"`）
     2. `handleA2uiHttpRequest(req, res)` — A2UI 资源
     3. `handler.handleHttpRequest(req, res)` — Canvas 文件
     4. 兜底 `404 "Not Found"`
   - 错误处理：`500 "error"`

6. **WebSocket 升级处理**：
   - 调用 `handler.handleUpgrade(req, socket, head)`
   - 若未处理则 `socket.destroy()`

7. **监听端口**：
   - `listenPort`：若 `opts.port` 为有效正数则使用，否则 `0`（系统分配）
   - 使用 Promise 包装 `server.listen(listenPort, bindHost)`

8. **日志输出**：`canvas host listening on http://${bindHost}:${boundPort} (root ${handler.rootDir})`

9. **返回值**：`{ port: boundPort, rootDir: handler.rootDir, close }`
   - `close` 方法：若 `ownsHandler` 则关闭 handler → 关闭 HTTP 服务器

## 4. 文件间依赖关系

```
file-resolver.ts
  └─→ ../infra/fs-safe.js (SafeOpenError, openFileWithinRoot, SafeOpenResult)

a2ui.ts
  ├─→ ../media/mime.js (detectMime)
  └─→ ./file-resolver.js (resolveFileWithinRoot)

server.ts
  ├─→ ../runtime.js (RuntimeEnv)
  ├─→ ../config/paths.js (resolveStateDir)
  ├─→ ../infra/env.js (isTruthyEnvValue)
  ├─→ ../media/mime.js (detectMime)
  ├─→ ../utils.js (ensureDir, resolveUserPath)
  ├─→ ./a2ui.js (CANVAS_HOST_PATH, CANVAS_WS_PATH, handleA2uiHttpRequest, injectCanvasLiveReload)
  └─→ ./file-resolver.js (normalizeUrlPath, resolveFileWithinRoot)
```

## 5. 整体架构

Canvas Host 模块采用分层架构：

1. **底层：文件解析层** (`file-resolver.ts`)
   - 提供安全的 URL 路径到文件系统路径的映射
   - 防止路径遍历攻击（`..` 检测、符号链接检测）
   - 自动处理目录访问时的 `index.html` 回退

2. **中间层：A2UI 资源服务** (`a2ui.ts`)
   - 管理内置 A2UI 界面资源的查找和缓存
   - 为 HTML 注入跨平台消息桥接脚本（支持 iOS WebKit 和 Android WebView）
   - 注入 WebSocket 热重载客户端脚本

3. **上层：HTTP 服务器** (`server.ts`)
   - 组合 A2UI 请求处理和用户 Canvas 文件服务
   - 管理 WebSocket 连接和热重载广播
   - 使用 chokidar 监视文件系统变更
   - 支持测试模式（更短的防抖延迟、轮询模式）
   - 支持通过环境变量禁用（`OPENCLAW_SKIP_CANVAS_HOST`、`NODE_ENV=test`、`VITEST`）

**请求处理流程**：
```
HTTP 请求 → WebSocket 升级检测
         → A2UI 路径匹配 (/__openclaw__/a2ui/*)
         → Canvas 基路径匹配 (/__openclaw__/canvas/*)
         → 404 兜底
```

**热重载流程**：
```
文件变更 (chokidar) → scheduleReload (防抖) → broadcastReload → WebSocket "reload" → 客户端 location.reload()
```
