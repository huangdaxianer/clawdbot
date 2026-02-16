# Node Host 模块 PRD

## 1. 模块概述与用途

`node-host` 模块实现了 OpenClaw 的节点宿主（Node Host）功能 — 一个通过 WebSocket 连接到网关（Gateway）的长驻进程，接收并执行远程命令调用（invoke）。核心功能包括：系统命令执行（`system.run`）、可执行文件查找（`system.which`）、执行审批管理（`system.execApprovals`）、以及浏览器代理（`browser.proxy`）。

## 2. 目录结构

```
src/node-host/
  config.ts           # 节点主机配置持久化
  with-timeout.ts     # 通用超时包装器
  invoke.ts           # 命令调用处理核心
  invoke-browser.ts   # 浏览器代理命令处理
  runner.ts           # 节点主机运行入口
```

## 3. 各文件详细说明

---

### 3.1 `config.ts` — 节点主机配置

#### 导入

```typescript
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { resolveStateDir } from "../config/paths.js";
```

#### 导出类型

##### `NodeHostGatewayConfig`
```typescript
export type NodeHostGatewayConfig = {
  host?: string;
  port?: number;
  tls?: boolean;
  tlsFingerprint?: string;
};
```

##### `NodeHostConfig`
```typescript
export type NodeHostConfig = {
  version: 1;
  nodeId: string;
  token?: string;
  displayName?: string;
  gateway?: NodeHostGatewayConfig;
};
```

#### 内部常量

```typescript
const NODE_HOST_FILE = "node.json";
```

#### 导出函数

##### `resolveNodeHostConfigPath(): string`
- 返回 `path.join(resolveStateDir(), NODE_HOST_FILE)`
- 即 `<stateDir>/node.json`

#### 内部函数

##### `normalizeConfig(config: Partial<NodeHostConfig> | null): NodeHostConfig`
- 创建基础对象：`{ version: 1, nodeId: "", token, displayName, gateway }`
- 若 `config.version === 1` 且 `nodeId` 为字符串，取 `config.nodeId.trim()`
- 若 `nodeId` 仍为空，生成 `crypto.randomUUID()`

#### 导出函数

##### `loadNodeHostConfig(): Promise<NodeHostConfig | null>`
- 读取配置文件，JSON 解析后调用 `normalizeConfig`
- 失败返回 `null`

##### `saveNodeHostConfig(config: NodeHostConfig): Promise<void>`
- 创建目录 `path.dirname(filePath)`（递归）
- 写入 JSON（2 空格缩进 + 换行），文件权限 `0o600`
- 尝试 `chmod 0o600`（最佳努力）

##### `ensureNodeHostConfig(): Promise<NodeHostConfig>`
- 加载现有配置 -> `normalizeConfig` -> 保存 -> 返回

---

### 3.2 `with-timeout.ts` — 通用超时包装

#### 导出函数

##### `withTimeout<T>(work: (signal: AbortSignal | undefined) => Promise<T>, timeoutMs?: number, label?: string): Promise<T>`
- 解析超时值：必须为有限数字，最小值 1 毫秒（`Math.max(1, Math.floor(timeoutMs))`）
- 无有效超时值时直接执行 `work(undefined)`
- 有超时时：
  - 创建 `AbortController`
  - 超时错误消息格式：`"${label ?? "request"} timed out"`
  - 设置 `setTimeout` 并调用 `timer.unref?.()`
  - 构建 abort Promise：
    - 若已中止则立即 reject
    - 否则监听 `"abort"` 事件（`{ once: true }`）
  - 使用 `Promise.race([work(signal), abortPromise])`
  - `finally` 中清理定时器和事件监听器

---

### 3.3 `invoke.ts` — 命令调用处理核心

#### 导入

```typescript
import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { resolveAgentConfig } from "../agents/agent-scope.js";
import { loadConfig } from "../config/config.js";
import { GatewayClient } from "../gateway/client.js";
// exec-approvals 系列导入（大量）
import { ... } from "../infra/exec-approvals.js";
import { requestExecHostViaSocket, ... } from "../infra/exec-host.js";
import { validateSystemRunCommandConsistency } from "../infra/system-run-command.js";
import { runBrowserProxyCommand } from "./invoke-browser.js";
```

#### 内部常量

```typescript
const OUTPUT_CAP = 200_000;              // 输出捕获上限（字符数）
const OUTPUT_EVENT_TAIL = 20_000;        // 事件日志中输出的尾部截取长度
const DEFAULT_NODE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";

// 环境变量
const execHostEnforced = process.env.OPENCLAW_NODE_EXEC_HOST?.trim().toLowerCase() === "app";
const execHostFallbackAllowed = process.env.OPENCLAW_NODE_EXEC_FALLBACK?.trim().toLowerCase() !== "0";

// 被阻止的环境变量键
const blockedEnvKeys = new Set([
  "NODE_OPTIONS", "PYTHONHOME", "PYTHONPATH", "PERL5LIB", "PERL5OPT", "RUBYOPT",
]);

// 被阻止的环境变量前缀
const blockedEnvPrefixes = ["DYLD_", "LD_"];
```

#### 内部类型

- `SystemRunParams`: `{ command: string[]; rawCommand?: string | null; cwd?: string | null; env?: Record<string, string>; timeoutMs?: number | null; needsScreenRecording?: boolean | null; agentId?: string | null; sessionKey?: string | null; approved?: boolean | null; approvalDecision?: string | null; runId?: string | null }`
- `SystemWhichParams`: `{ bins: string[] }`
- `SystemExecApprovalsSetParams`: `{ file: ExecApprovalsFile; baseHash?: string | null }`
- `ExecApprovalsSnapshot`: `{ path: string; exists: boolean; hash: string; file: ExecApprovalsFile }`
- `RunResult`: `{ exitCode?: number; timedOut: boolean; success: boolean; stdout: string; stderr: string; error?: string | null; truncated: boolean }`
- `ExecEventPayload`: `{ sessionKey: string; runId: string; host: string; command?: string; exitCode?: number; timedOut?: boolean; success?: boolean; output?: string; reason?: string }`

#### 导出类型

##### `NodeInvokeRequestPayload`
```typescript
export type NodeInvokeRequestPayload = {
  id: string;
  nodeId: string;
  command: string;
  paramsJSON?: string | null;
  timeoutMs?: number | null;
  idempotencyKey?: string | null;
};
```

##### `SkillBinsProvider`
```typescript
export type SkillBinsProvider = {
  current(force?: boolean): Promise<Set<string>>;
};
```

#### 内部函数

##### `resolveExecSecurity(value?: string): ExecSecurity`
- 返回 `"deny" | "allowlist" | "full"` 之一，默认 `"allowlist"`

##### `isCmdExeInvocation(argv: string[]): boolean`
- 获取第一个参数的 `path.win32.basename` 的小写形式
- 匹配 `"cmd.exe"` 或 `"cmd"`

##### `resolveExecAsk(value?: string): ExecAsk`
- 返回 `"off" | "on-miss" | "always"` 之一，默认 `"on-miss"`

##### `truncateOutput(raw: string, maxChars: number): { text: string; truncated: boolean }`
- 若长度不超过 maxChars，返回原文
- 否则返回 `"... (truncated) ${raw.slice(raw.length - maxChars)}"`

##### `redactExecApprovals(file: ExecApprovalsFile): ExecApprovalsFile`
- 仅保留 `socket.path`，移除其他 socket 字段

##### `requireExecApprovalsBaseHash(params, snapshot)`
- 若文件存在：
  - 若无 hash 抛出 `"INVALID_REQUEST: exec approvals base hash unavailable; reload and retry"`
  - 若请求无 baseHash 抛出 `"INVALID_REQUEST: exec approvals base hash required; reload and retry"`
  - 若 baseHash 不匹配抛出 `"INVALID_REQUEST: exec approvals changed; reload and retry"`

##### `runCommand(argv: string[], cwd, env, timeoutMs): Promise<RunResult>`
- 使用 `spawn(argv[0], argv.slice(1), { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true })`
- `onChunk` 处理：
  - 达到 `OUTPUT_CAP` 后标记 `truncated = true` 并丢弃
  - 剩余容量不足时截断当前 chunk
- 超时时发送 `SIGKILL`
- `success = exitCode === 0 && !timedOut && !error`

##### `resolveEnvPath(env?): string[]`
- 优先检查 `env.PATH`、`env.Path`、`process.env.PATH`、`process.env.Path`
- 默认值 `DEFAULT_NODE_PATH`
- 使用 `path.delimiter` 分割

##### `resolveExecutable(bin: string, env?)`
- 若包含 `/` 或 `\` 返回 null
- Windows 上使用 `PATHEXT`（默认 `".EXE;.CMD;.BAT;.COM"`）
- 遍历 PATH 目录和扩展名，`fs.existsSync` 检测

##### `handleSystemWhich(params: SystemWhichParams, env?)`
- 对每个 bin 调用 `resolveExecutable`
- 返回 `{ bins: found }` — 找到的路径映射

##### `buildExecEventPayload(payload: ExecEventPayload): ExecEventPayload`
- 截断 output：`truncateOutput(trimmed, OUTPUT_EVENT_TAIL)`

##### `sendExecFinishedEvent(params: { client, sessionKey, runId, cmdText, result })`
- 合并 stdout + stderr + error 为 combined
- 发送 `"exec.finished"` 事件

##### `runViaMacAppExecHost(params: { approvals, request: ExecHostRequest }): Promise<ExecHostResponse | null>`
- 通过 `requestExecHostViaSocket` 将命令委托给 macOS 应用

##### `decodeParams<T>(raw?: string | null): T`
- 空值时抛出 `"INVALID_REQUEST: paramsJSON required"`
- 使用 `JSON.parse` 解析

##### `sendInvokeResult(client, frame, result)`
- 调用 `client.request("node.invoke.result", buildNodeInvokeResultParams(frame, result))`
- 异常时静默忽略（best-effort）

##### `sendNodeEvent(client, event, payload)`
- 调用 `client.request("node.event", { event, payloadJSON: JSON.stringify(payload) })`
- 异常时静默忽略

#### 导出函数

##### `sanitizeEnv(overrides?: Record<string, string> | null): Record<string, string> | undefined`
- 无覆盖时返回 `undefined`
- 以 `process.env` 为基础合并覆盖
- **阻止规则**：
  - `PATH`/`Path` 键完全阻止（安全边界）
  - `blockedEnvKeys` 集合中的键（NODE_OPTIONS, PYTHONHOME 等）
  - 以 `blockedEnvPrefixes` 开头的键（DYLD_, LD_）
- 键名 trim 后进行大写比较

##### `handleInvoke(frame: NodeInvokeRequestPayload, client: GatewayClient, skillBins: SkillBinsProvider)`
- 命令分发：
  - `"system.execApprovals.get"`: 读取并返回执行审批快照（socket path 脱敏）
  - `"system.execApprovals.set"`: 验证 baseHash 后更新执行审批，调用 `normalizeExecApprovals` + `mergeExecApprovalsSocketDefaults`
  - `"system.which"`: 查找可执行文件路径
  - `"browser.proxy"`: 代理到 `runBrowserProxyCommand`
  - `"system.run"`: 完整的命令执行流程（见下方）
  - 其他命令: 返回 `UNAVAILABLE: command not supported`

- **system.run 完整流程**：
  1. 解析参数，验证 command 数组非空
  2. 构建 argv，获取 rawCommand
  3. 调用 `validateSystemRunCommandConsistency` 验证一致性
  4. 解析 agentId，加载配置
  5. 解析安全策略：`resolveExecSecurity`（agent 级 > 全局级），默认 `"allowlist"`
  6. 解析 ask 策略：`resolveExecAsk`（agent 级 > 全局级），默认 `"on-miss"`
  7. 解析执行审批配置
  8. **shell 命令 vs argv 命令**分支：
     - shell 命令：使用 `evaluateShellAllowlist`
     - argv 命令：使用 `analyzeArgvCommand` + `evaluateExecAllowlist`
  9. Windows + cmd.exe 调用在 allowlist 模式下强制 `analysisOk = false`
  10. **macOS 路径** (`process.platform === "darwin"`):
      - 委托给 macOS 应用执行宿主
      - 支持 `allow-once` 和 `allow-always` 审批决定
      - macOS 不可用时检查 `execHostEnforced` 和 `execHostFallbackAllowed`
      - 不可用时发送 `"exec.denied"` 事件，原因 `"companion-unavailable"`
  11. **security=deny**: 拒绝并发送 `"exec.denied"` 事件
  12. **审批检查**: `requiresExecApproval` 判断是否需要审批
  13. **allow-always + allowlist**: 将命令的 resolvedPath 添加到 allowlist
  14. **allowlist miss**: 拒绝并发送事件
  15. **记录 allowlist 使用**: `recordAllowlistUse`
  16. **screenRecording 权限检查**: 拒绝并发送事件，原因 `"permission:screenRecording"`
  17. **Windows shell 命令优化**: 在特定条件下使用分析后的 argv 替代原始 argv
  18. **执行命令**: `runCommand`
  19. **截断处理**: 在 stderr 或 stdout 末尾追加 `"... (truncated)"`
  20. **发送完成事件和结果**

##### `coerceNodeInvokePayload(payload: unknown): NodeInvokeRequestPayload | null`
- 验证 payload 为对象
- 提取 `id`、`nodeId`、`command`（必须为非空字符串）
- `paramsJSON`: 字符串直接使用，若有 `params` 字段则 `JSON.stringify`
- `timeoutMs`: 数字则保留
- `idempotencyKey`: 字符串则保留

##### `buildNodeInvokeResultParams(frame, result): { id, nodeId, ok, payload?, payloadJSON?, error? }`
- 构建调用结果参数对象

---

### 3.4 `invoke-browser.ts` — 浏览器代理

#### 导入

```typescript
import fsPromises from "node:fs/promises";
import { resolveBrowserConfig } from "../browser/config.js";
import { createBrowserControlContext, startBrowserControlServiceFromConfig } from "../browser/control-service.js";
import { createBrowserRouteDispatcher } from "../browser/routes/dispatcher.js";
import { loadConfig } from "../config/config.js";
import { detectMime } from "../media/mime.js";
import { withTimeout } from "./with-timeout.js";
```

#### 内部类型

- `BrowserProxyParams`: `{ method?: string; path?: string; query?: Record<string, string | number | boolean | null | undefined>; body?: unknown; timeoutMs?: number; profile?: string }`
- `BrowserProxyFile`: `{ path: string; base64: string; mimeType?: string }`
- `BrowserProxyResult`: `{ result: unknown; files?: BrowserProxyFile[] }`

#### 内部常量

```typescript
const BROWSER_PROXY_MAX_FILE_BYTES = 10 * 1024 * 1024;  // 10MB
```

#### 内部函数

##### `normalizeProfileAllowlist(raw?: string[]): string[]`
- 数组时 trim + filter，否则返回 `[]`

##### `resolveBrowserProxyConfig()`
- 返回 `{ enabled: boolean; allowProfiles: string[] }`
- `enabled`: `proxy?.enabled !== false`（默认启用）

##### `ensureBrowserControlService(): Promise<void>`
- 单例模式（`browserControlReady`）
- 加载配置，调用 `resolveBrowserConfig` 和 `startBrowserControlServiceFromConfig`
- 未启用时抛出 `"browser control disabled"`

##### `isProfileAllowed(params: { allowProfiles: string[]; profile?: string | null })`
- 空 allowProfiles 时允许所有
- 否则检查 `allowProfiles.includes(profile.trim())`

##### `collectBrowserProxyPaths(payload: unknown): string[]`
- 从响应对象中收集文件路径：`path`、`imagePath`、`download.path`
- 返回去重路径列表

##### `readBrowserProxyFile(filePath: string): Promise<BrowserProxyFile | null>`
- 检查文件存在且为文件
- 大小检查：超过 `BROWSER_PROXY_MAX_FILE_BYTES` 抛出错误（消息中计算 MB）
- 读取为 base64，检测 MIME 类型

##### `decodeParams<T>(raw?: string | null): T`
- 同 invoke.ts 中的实现

#### 导出函数

##### `runBrowserProxyCommand(paramsJSON?: string | null): Promise<string>`
- 解析参数，验证 path 非空
- 检查浏览器代理是否启用
- 确保浏览器控制服务就绪
- **profile 访问控制**：
  - 非 `/profiles` 路径：检查请求的或默认 profile 是否在允许列表
  - `/profiles` 路径：若指定了 profile 也检查
  - 不允许时抛出 `"INVALID_REQUEST: browser profile not allowed"`
- 规范化 HTTP 方法：默认 `"GET"`，支持 `"DELETE"` 和 `"POST"`
- 路径确保以 `/` 开头
- 构建查询参数
- 通过 `createBrowserRouteDispatcher` 分发请求，使用 `withTimeout` 包装
- 响应状态 >= 400 时提取错误消息
- **profile 过滤**：对 `/profiles` 响应，过滤掉不在允许列表的 profile
- **文件收集**：从响应中收集文件路径，读取为 base64
- 返回 `JSON.stringify({ result, files? })`

---

### 3.5 `runner.ts` — 节点主机运行入口

#### 导入

```typescript
import { resolveBrowserConfig } from "../browser/config.js";
import { loadConfig } from "../config/config.js";
import { GatewayClient } from "../gateway/client.js";
import { loadOrCreateDeviceIdentity } from "../infra/device-identity.js";
import { getMachineDisplayName } from "../infra/machine-name.js";
import { ensureOpenClawCliOnPath } from "../infra/path-env.js";
import { GATEWAY_CLIENT_MODES, GATEWAY_CLIENT_NAMES } from "../utils/message-channel.js";
import { VERSION } from "../version.js";
import { ensureNodeHostConfig, saveNodeHostConfig, type NodeHostGatewayConfig } from "./config.js";
import { coerceNodeInvokePayload, handleInvoke, type SkillBinsProvider, buildNodeInvokeResultParams } from "./invoke.js";
```

#### 导出

```typescript
export { buildNodeInvokeResultParams };  // 重导出
```

#### 内部常量

```typescript
const DEFAULT_NODE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
```

#### 内部类型

##### `NodeHostRunOptions`
```typescript
type NodeHostRunOptions = {
  gatewayHost: string;
  gatewayPort: number;
  gatewayTls?: boolean;
  gatewayTlsFingerprint?: string;
  nodeId?: string;
  displayName?: string;
};
```

#### 内部类

##### `SkillBinsCache implements SkillBinsProvider`
- 私有字段：`bins: Set<string>`、`lastRefresh: number`（初始 0）、`ttlMs: 90_000`（90 秒缓存）、`fetch: () => Promise<string[]>`
- `current(force = false)`: 若 force 或超过 TTL 则刷新
- `refresh()`: 调用 fetch 函数，失败时若无历史数据则设为空 Set

#### 内部函数

##### `ensureNodePathEnv(): string`
- 调用 `ensureOpenClawCliOnPath`
- 若 `process.env.PATH` 为空，设置为 `DEFAULT_NODE_PATH`
- 返回当前 PATH

#### 导出函数

##### `runNodeHost(opts: NodeHostRunOptions): Promise<void>`
- 加载/创建节点配置
- 解析 nodeId：优先 `opts.nodeId`，然后 `config.nodeId`
- 解析 displayName：优先 `opts.displayName`，然后 `config.displayName`，最后 `getMachineDisplayName()`
- 构建网关配置：
  - `tls`: `opts.gatewayTls ?? loadConfig().gateway?.tls?.enabled ?? false`
- 保存配置到磁盘
- 判断浏览器代理是否启用：`cfg.nodeHost?.browserProxy?.enabled !== false && resolvedBrowser.enabled`
- 获取认证信息：
  - 远程模式（`cfg.gateway?.mode === "remote"`）：使用 remote 配置
  - 否则：使用 auth 配置
  - 环境变量优先：`OPENCLAW_GATEWAY_TOKEN`、`OPENCLAW_GATEWAY_PASSWORD`
- 构建 WebSocket URL：`${scheme}://${host}:${port}`，默认端口 `18789`
- 创建 `GatewayClient` 实例：
  - `clientName`: `GATEWAY_CLIENT_NAMES.NODE_HOST`
  - `mode`: `GATEWAY_CLIENT_MODES.NODE`
  - `role`: `"node"`
  - `caps`: `["system", ...(browserProxyEnabled ? ["browser"] : [])]`
  - `commands`: `["system.run", "system.which", "system.execApprovals.get", "system.execApprovals.set", ...(browserProxyEnabled ? ["browser.proxy"] : [])]`
  - `onEvent`: 监听 `"node.invoke.request"` 事件，解析 payload 后调用 `handleInvoke`
  - `onConnectError`: 记录错误（持续重试由 GatewayClient 处理）
  - `onClose`: 记录关闭代码和原因
- 创建 `SkillBinsCache`，通过 `client.request("skills.bins", {})` 获取 bins
- 启动 client，`await new Promise(() => {})` 永不解决（保持进程运行）

---

## 4. 文件间依赖关系

```
config.ts
  ├── 依赖 node:crypto (randomUUID)
  ├── 依赖 node:fs/promises
  ├── 依赖 node:path
  └── 依赖 ../config/paths.js (resolveStateDir)

with-timeout.ts
  └── 无外部依赖

invoke.ts
  ├── 依赖 node:child_process (spawn)
  ├── 依赖 node:crypto, node:fs, node:path
  ├── 依赖 ../agents/agent-scope.js
  ├── 依赖 ../config/config.js
  ├── 依赖 ../gateway/client.js
  ├── 依赖 ../infra/exec-approvals.js (大量函数)
  ├── 依赖 ../infra/exec-host.js
  ├── 依赖 ../infra/system-run-command.js
  └── 依赖 ./invoke-browser.js

invoke-browser.ts
  ├── 依赖 node:fs/promises
  ├── 依赖 ../browser/config.js, ../browser/control-service.js, ../browser/routes/dispatcher.js
  ├── 依赖 ../config/config.js
  ├── 依赖 ../media/mime.js
  └── 依赖 ./with-timeout.js

runner.ts
  ├── 依赖 ../browser/config.js
  ├── 依赖 ../config/config.js
  ├── 依赖 ../gateway/client.js
  ├── 依赖 ../infra/device-identity.js, ../infra/machine-name.js, ../infra/path-env.js
  ├── 依赖 ../utils/message-channel.js
  ├── 依赖 ../version.js
  ├── 依赖 ./config.js
  └── 依赖 ./invoke.js
```

## 5. 整体架构

本模块是一个**远程命令执行代理**，架构分为四层：

1. **配置层** (`config.ts`): 持久化节点标识（nodeId = UUID）和网关连接参数
2. **传输层** (`runner.ts`): 建立 WebSocket 连接到网关，监听 invoke 请求事件，管理 skill bins 缓存
3. **调度层** (`invoke.ts`): 根据命令类型分发到不同处理器，实现完整的安全策略（deny/allowlist/full + ask 策略）
4. **执行层** (`invoke.ts` + `invoke-browser.ts`): 实际的命令执行和浏览器代理

安全架构要点：
- **环境变量沙箱化**: PATH 不可覆盖，危险环境变量（NODE_OPTIONS、LD_*、DYLD_*等）被过滤
- **三级安全策略**: deny（完全禁止）、allowlist（白名单）、full（全开放）
- **审批机制**: off/on-miss/always 三种 ask 策略，支持 allow-once 和 allow-always
- **macOS 应用委托**: 在 Darwin 平台优先通过 macOS 应用执行宿主执行，获得 UI 级别审批
- **输出截断**: 200,000 字符上限防止内存溢出
- **浏览器代理**: profile 白名单控制、文件大小限制（10MB）
