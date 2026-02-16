# OpenClaw (clawdbot) 根级源文件产品需求文档 (PRD)

> 本文档详细描述了 `/home/user/clawdbot/src/` 根级所有 TypeScript 源文件的完整实现细节。
> 文档目标：提供足够的精度使开发者能够从本文档完全重建原始源代码。

---

## 目录

1. [模块总览与架构关系](#1-模块总览与架构关系)
2. [entry.ts — CLI 入口与进程重生](#2-entryts--cli-入口与进程重生)
3. [index.ts — 主模块入口与公共 API 导出](#3-indexts--主模块入口与公共-api-导出)
4. [globals.ts — 全局状态与主题化输出](#4-globalsts--全局状态与主题化输出)
5. [runtime.ts — 运行时环境抽象](#5-runtimets--运行时环境抽象)
6. [extensionAPI.ts — 扩展 API 桶导出](#6-extensionapits--扩展-api-桶导出)
7. [channel-web.ts — Web 频道桶导出](#7-channel-webts--web-频道桶导出)
8. [logger.ts — 高级日志辅助函数](#8-loggerts--高级日志辅助函数)
9. [logging.ts — 日志系统桶导出](#9-loggingts--日志系统桶导出)
10. [polls.ts — 投票输入标准化](#10-pollsts--投票输入标准化)
11. [utils.ts — 通用工具函数集](#11-utilsts--通用工具函数集)
12. [version.ts — 版本号解析](#12-versionts--版本号解析)
13. [文件间依赖关系图](#13-文件间依赖关系图)

---

## 1. 模块总览与架构关系

本项目（OpenClaw / clawdbot）是一个 WhatsApp 机器人 CLI 工具。根级源文件承担以下核心职责：

| 文件 | 职责 |
|------|------|
| `entry.ts` | Node.js CLI 真正入口点，处理进程重生（respawn）以抑制实验性警告 |
| `index.ts` | 主模块入口，聚合导出公共 API，作为 main 模块运行时启动 CLI |
| `globals.ts` | 管理全局 verbose/yes 标志，导出主题色输出函数 |
| `runtime.ts` | 定义 `RuntimeEnv` 类型，提供默认和非退出运行时环境 |
| `extensionAPI.ts` | 扩展 API 桶（barrel）文件，从 agents 和 config 子模块重新导出 |
| `channel-web.ts` | Web（WhatsApp Web）频道功能桶文件，从 web 子模块重新导出 |
| `logger.ts` | 面向业务代码的高级日志函数（info/warn/success/error/debug） |
| `logging.ts` | 日志子系统完整桶文件，聚合 console、levels、logger、subsystem 模块 |
| `polls.ts` | 投票（poll）输入数据的验证与标准化 |
| `utils.ts` | 通用工具函数：路径处理、电话号码标准化、JID 转换、UTF-16 安全截断等 |
| `version.ts` | 从 package.json / build-info.json / 编译时常量解析应用版本号 |

---

## 2. entry.ts — CLI 入口与进程重生

### 2.1 文件概述

此文件是整个 CLI 的真正入口点（shebang `#!/usr/bin/env node`）。它负责：
1. 设置进程标题
2. 安装进程警告过滤器
3. 标准化环境变量
4. 处理 `--no-color` 参数
5. 在需要时重新生成子进程以抑制 Node.js 实验性警告
6. 解析 CLI profile 参数
7. 动态导入并运行实际 CLI

### 2.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `spawn` | `node:child_process` |
| `process` (默认导入) | `node:process` |
| `applyCliProfileEnv`, `parseCliProfileArgs` | `./cli/profile.js` |
| `shouldSkipRespawnForArgv` | `./cli/respawn-policy.js` |
| `normalizeWindowsArgv` | `./cli/windows-argv.js` |
| `isTruthyEnvValue`, `normalizeEnv` | `./infra/env.js` |
| `installProcessWarningFilter` | `./infra/warning-filter.js` |
| `attachChildProcessBridge` | `./process/child-process-bridge.js` |

### 2.3 顶层副作用（执行顺序）

1. **`process.title = "openclaw"`** — 设置进程标题为字符串 `"openclaw"`。
2. **`installProcessWarningFilter()`** — 安装进程警告过滤器。
3. **`normalizeEnv()`** — 标准化环境变量。
4. **`--no-color` 检测**：如果 `process.argv` 包含 `"--no-color"`，则设置 `process.env.NO_COLOR = "1"` 和 `process.env.FORCE_COLOR = "0"`。
5. **`process.argv = normalizeWindowsArgv(process.argv)`** — 标准化 Windows 平台下的 argv。

### 2.4 常量

| 常量名 | 值 | 说明 |
|--------|----|------|
| `EXPERIMENTAL_WARNING_FLAG` | `"--disable-warning=ExperimentalWarning"` | Node.js CLI 参数，用于禁用实验性警告 |

### 2.5 内部函数

#### `hasExperimentalWarningSuppressed(): boolean`

**用途**：检测当前进程是否已经抑制了实验性警告。

**逻辑**：
1. 读取 `process.env.NODE_OPTIONS`（若为 `undefined` 则取 `""`）。
2. 如果 `NODE_OPTIONS` 包含 `EXPERIMENTAL_WARNING_FLAG` 或 `"--no-warnings"` 字符串，返回 `true`。
3. 遍历 `process.execArgv` 数组，如果任何元素严格等于 `EXPERIMENTAL_WARNING_FLAG` 或 `"--no-warnings"`，返回 `true`。
4. 否则返回 `false`。

#### `ensureExperimentalWarningSuppressed(): boolean`

**用途**：确保实验性警告被抑制。如果尚未抑制，则重新生成（respawn）子进程。

**返回值**：`true` 表示已重生子进程（父进程应停止继续执行 CLI），`false` 表示无需重生（当前进程可以继续）。

**逻辑**：
1. 如果 `shouldSkipRespawnForArgv(process.argv)` 返回 `true`，返回 `false`。
2. 如果 `isTruthyEnvValue(process.env.OPENCLAW_NO_RESPAWN)` 为 `true`，返回 `false`。
3. 如果 `isTruthyEnvValue(process.env.OPENCLAW_NODE_OPTIONS_READY)` 为 `true`，返回 `false`。
4. 如果 `hasExperimentalWarningSuppressed()` 返回 `true`，返回 `false`。
5. 设置 `process.env.OPENCLAW_NODE_OPTIONS_READY = "1"` 作为递归防护。
6. 调用 `spawn(process.execPath, [EXPERIMENTAL_WARNING_FLAG, ...process.execArgv, ...process.argv.slice(1)], { stdio: "inherit", env: process.env })` 创建子进程。
7. 调用 `attachChildProcessBridge(child)` 附加子进程桥接。
8. 注册 `child.once("exit", ...)` 回调：
   - 如果收到信号（`signal` 非空），设置 `process.exitCode = 1` 后返回。
   - 否则调用 `process.exit(code ?? 1)`。
9. 注册 `child.once("error", ...)` 回调：
   - 输出 `"[openclaw] Failed to respawn CLI:"` 加上错误信息（优先使用 `error.stack`，其次 `error.message`，最后直接转换 error）。
   - 调用 `process.exit(1)`。
10. 返回 `true`。

### 2.6 主逻辑流程

在 `normalizeWindowsArgv` 之后：

```
if (!ensureExperimentalWarningSuppressed()) {
  // 仅当没有重生子进程时才执行以下代码
  1. 调用 parseCliProfileArgs(process.argv) 获取 parsed
  2. 如果 parsed.ok 为 false:
     - 输出 "[openclaw] ${parsed.error}" 到 stderr
     - 以退出码 2 退出
  3. 如果 parsed.profile 存在:
     - 调用 applyCliProfileEnv({ profile: parsed.profile })
     - 将 process.argv 替换为 parsed.argv
  4. 动态 import("./cli/run-main.js")
     - 成功后调用 runCli(process.argv)
     - 失败时输出 "[openclaw] Failed to start CLI:" + 错误信息，设置 process.exitCode = 1
}
```

### 2.7 导出

此文件无任何导出（纯入口文件）。

---

## 3. index.ts — 主模块入口与公共 API 导出

### 3.1 文件概述

此文件是包的主模块入口点（同样有 shebang `#!/usr/bin/env node`）。它负责：
1. 加载 `.env` 文件
2. 标准化环境变量
3. 确保 CLI 在 PATH 上
4. 启用控制台日志捕获
5. 断言运行时版本兼容
6. 构建 CLI 程序
7. 作为 main 模块运行时启动 CLI 解析
8. 聚合导出公共 API

### 3.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `process` (默认导入) | `node:process` |
| `fileURLToPath` | `node:url` |
| `getReplyFromConfig` | `./auto-reply/reply.js` |
| `applyTemplate` | `./auto-reply/templating.js` |
| `monitorWebChannel` | `./channel-web.js` |
| `createDefaultDeps` | `./cli/deps.js` |
| `promptYesNo` | `./cli/prompt.js` |
| `waitForever` | `./cli/wait.js` |
| `loadConfig` | `./config/config.js` |
| `deriveSessionKey`, `loadSessionStore`, `resolveSessionKey`, `resolveStorePath`, `saveSessionStore` | `./config/sessions.js` |
| `ensureBinary` | `./infra/binaries.js` |
| `loadDotEnv` | `./infra/dotenv.js` |
| `normalizeEnv` | `./infra/env.js` |
| `formatUncaughtError` | `./infra/errors.js` |
| `isMainModule` | `./infra/is-main.js` |
| `ensureOpenClawCliOnPath` | `./infra/path-env.js` |
| `describePortOwner`, `ensurePortAvailable`, `handlePortError`, `PortInUseError` | `./infra/ports.js` |
| `assertSupportedRuntime` | `./infra/runtime-guard.js` |
| `installUnhandledRejectionHandler` | `./infra/unhandled-rejections.js` |
| `enableConsoleCapture` | `./logging.js` |
| `runCommandWithTimeout`, `runExec` | `./process/exec.js` |
| `assertWebChannel`, `normalizeE164`, `toWhatsappJid` | `./utils.js` |
| `buildProgram` | `./cli/program.js` |

### 3.3 顶层副作用（执行顺序）

1. **`loadDotEnv({ quiet: true })`** — 静默加载 `.env` 文件。
2. **`normalizeEnv()`** — 标准化环境变量。
3. **`ensureOpenClawCliOnPath()`** — 确保 openclaw CLI 在系统 PATH 中。
4. **`enableConsoleCapture()`** — 捕获所有 console 输出到结构化日志，同时保持 stdout/stderr 行为。
5. **`assertSupportedRuntime()`** — 在执行任何工作前强制检查最低支持的运行时版本。
6. **`const program = buildProgram()`** — 构建 CLI 程序实例。

### 3.4 命名导出

此文件导出以下公共 API（均为重新导出，来源已在上方导入表中标明）：

```typescript
export {
  assertWebChannel,      // 来自 ./utils.js
  applyTemplate,         // 来自 ./auto-reply/templating.js
  createDefaultDeps,     // 来自 ./cli/deps.js
  deriveSessionKey,      // 来自 ./config/sessions.js
  describePortOwner,     // 来自 ./infra/ports.js
  ensureBinary,          // 来自 ./infra/binaries.js
  ensurePortAvailable,   // 来自 ./infra/ports.js
  getReplyFromConfig,    // 来自 ./auto-reply/reply.js
  handlePortError,       // 来自 ./infra/ports.js
  loadConfig,            // 来自 ./config/config.js
  loadSessionStore,      // 来自 ./config/sessions.js
  monitorWebChannel,     // 来自 ./channel-web.js
  normalizeE164,         // 来自 ./utils.js
  PortInUseError,        // 来自 ./infra/ports.js
  promptYesNo,           // 来自 ./cli/prompt.js
  resolveSessionKey,     // 来自 ./config/sessions.js
  resolveStorePath,      // 来自 ./config/sessions.js
  runCommandWithTimeout, // 来自 ./process/exec.js
  runExec,               // 来自 ./process/exec.js
  saveSessionStore,      // 来自 ./config/sessions.js
  toWhatsappJid,         // 来自 ./utils.js
  waitForever,           // 来自 ./cli/wait.js
};
```

### 3.5 主模块运行逻辑

```typescript
const isMain = isMainModule({
  currentFile: fileURLToPath(import.meta.url),
});

if (isMain) {
  // 1. 安装未处理 rejection 处理器
  installUnhandledRejectionHandler();

  // 2. 监听未捕获异常
  process.on("uncaughtException", (error) => {
    console.error("[openclaw] Uncaught exception:", formatUncaughtError(error));
    process.exit(1);
  });

  // 3. 异步解析 CLI 命令
  void program.parseAsync(process.argv).catch((err) => {
    console.error("[openclaw] CLI failed:", formatUncaughtError(err));
    process.exit(1);
  });
}
```

**错误消息格式**：
- 未捕获异常：`"[openclaw] Uncaught exception:"` + `formatUncaughtError(error)` 的结果
- CLI 失败：`"[openclaw] CLI failed:"` + `formatUncaughtError(err)` 的结果

---

## 4. globals.ts — 全局状态与主题化输出

### 4.1 文件概述

管理两个全局布尔标志（`verbose` 和 `yes`），并导出主题化的控制台输出辅助函数。

### 4.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `getLogger`, `isFileLogLevelEnabled` | `./logging/logger.js` |
| `theme` | `./terminal/theme.js` |

### 4.3 内部状态

| 变量名 | 类型 | 初始值 |
|--------|------|--------|
| `globalVerbose` | `boolean` | `false` |
| `globalYes` | `boolean` | `false` |

### 4.4 导出函数

#### `setVerbose(v: boolean): void`
设置 `globalVerbose = v`。

#### `isVerbose(): boolean`
返回 `globalVerbose` 的当前值。

#### `shouldLogVerbose(): boolean`
返回 `globalVerbose || isFileLogLevelEnabled("debug")`。即如果全局 verbose 开启或文件日志级别允许 debug，都返回 `true`。

#### `logVerbose(message: string): void`
1. 如果 `shouldLogVerbose()` 返回 `false`，直接返回。
2. 在 `try...catch` 块中调用 `getLogger().debug({ message }, "verbose")`，catch 中忽略所有错误（防止日志器故障影响 verbose 输出）。
3. 如果 `globalVerbose` 为 `false`（即仅文件日志级别触发的情况），直接返回（不输出到控制台）。
4. 调用 `console.log(theme.muted(message))` 输出到控制台。

#### `logVerboseConsole(message: string): void`
1. 如果 `globalVerbose` 为 `false`，直接返回。
2. 调用 `console.log(theme.muted(message))` 输出到控制台。

#### `setYes(v: boolean): void`
设置 `globalYes = v`。

#### `isYes(): boolean`
返回 `globalYes` 的当前值。

### 4.5 导出常量

| 导出名 | 值 | 说明 |
|--------|----|------|
| `success` | `theme.success` | 成功主题色函数 |
| `warn` | `theme.warn` | 警告主题色函数 |
| `info` | `theme.info` | 信息主题色函数 |
| `danger` | `theme.error` | 危险/错误主题色函数（注意：来源是 `theme.error`，但导出名为 `danger`） |

---

## 5. runtime.ts — 运行时环境抽象

### 5.1 文件概述

定义 `RuntimeEnv` 类型，提供默认运行时实现和一个不退出进程的替代运行时。用于在生产代码和测试代码之间提供统一的运行时接口。

### 5.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `clearActiveProgressLine` | `./terminal/progress-line.js` |
| `restoreTerminalState` | `./terminal/restore.js` |

### 5.3 导出类型

#### `RuntimeEnv`

```typescript
export type RuntimeEnv = {
  log: typeof console.log;
  error: typeof console.error;
  exit: (code: number) => never;
};
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `log` | `typeof console.log` | 日志输出函数（与 `console.log` 签名相同） |
| `error` | `typeof console.error` | 错误输出函数（与 `console.error` 签名相同） |
| `exit` | `(code: number) => never` | 进程退出函数，接收退出码，永不返回 |

### 5.4 内部函数

#### `shouldEmitRuntimeLog(env: NodeJS.ProcessEnv = process.env): boolean`

**用途**：判断是否应该发出运行时日志。在测试环境中默认抑制日志，除非显式启用。

**逻辑**：
1. 如果 `env.VITEST` 不等于 `"true"`，返回 `true`（非测试环境始终输出）。
2. 如果 `env.OPENCLAW_TEST_RUNTIME_LOG` 等于 `"1"`，返回 `true`（测试中显式启用）。
3. 将 `console.log` 强制转型为 `{ mock?: unknown }`，检查 `typeof maybeMockedLog.mock === "object"`。如果是对象则返回 `true`（检测 vitest mock）。
4. 否则返回 `false`。

### 5.5 导出常量

#### `defaultRuntime: RuntimeEnv`

默认运行时环境实例：

- **`log(...args)`**：
  1. 如果 `shouldEmitRuntimeLog()` 返回 `false`，直接返回。
  2. 调用 `clearActiveProgressLine()` 清除活动进度行。
  3. 调用 `console.log(...args)`。

- **`error(...args)`**：
  1. 调用 `clearActiveProgressLine()` 清除活动进度行。
  2. 调用 `console.error(...args)`。

- **`exit(code)`**：
  1. 调用 `restoreTerminalState("runtime exit", { resumeStdinIfPaused: false })` 恢复终端状态。
  2. 调用 `process.exit(code)` 退出进程。
  3. `throw new Error("unreachable")` — 此行代码永远不会在生产中执行，仅为满足测试中 mock `process.exit` 时的类型检查（确保返回类型为 `never`）。

### 5.6 导出函数

#### `createNonExitingRuntime(): RuntimeEnv`

**用途**：创建一个不实际退出进程的运行时环境，exit 方法改为抛出异常。

**返回值**：一个新的 `RuntimeEnv` 对象：

- **`log(...args)`**：与 `defaultRuntime.log` 行为相同。
- **`error(...args)`**：与 `defaultRuntime.error` 行为相同。
- **`exit(code: number): never`**：抛出 `new Error(\`exit ${code}\`)`，其中 `code` 被插入到字符串中。

---

## 6. extensionAPI.ts — 扩展 API 桶导出

### 6.1 文件概述

此文件是一个纯桶（barrel）模块，从内部子模块重新导出扩展 API 所需的功能。注意此文件使用 `.ts` 扩展名导入（而非 `.js`），暗示可能用于不同的构建管线。

### 6.2 导出项

#### 来自 `./agents/agent-scope.ts`

| 导出名 | 类型 |
|--------|------|
| `resolveAgentDir` | 函数 |
| `resolveAgentWorkspaceDir` | 函数 |

#### 来自 `./agents/defaults.ts`

| 导出名 | 类型 |
|--------|------|
| `DEFAULT_MODEL` | 常量 |
| `DEFAULT_PROVIDER` | 常量 |

#### 来自 `./agents/identity.ts`

| 导出名 | 类型 |
|--------|------|
| `resolveAgentIdentity` | 函数 |

#### 来自 `./agents/model-selection.ts`

| 导出名 | 类型 |
|--------|------|
| `resolveThinkingDefault` | 函数 |

#### 来自 `./agents/pi-embedded.ts`

| 导出名 | 类型 |
|--------|------|
| `runEmbeddedPiAgent` | 函数 |

#### 来自 `./agents/timeout.ts`

| 导出名 | 类型 |
|--------|------|
| `resolveAgentTimeoutMs` | 函数 |

#### 来自 `./agents/workspace.ts`

| 导出名 | 类型 |
|--------|------|
| `ensureAgentWorkspace` | 函数 |

#### 来自 `./config/sessions.ts`

| 导出名 | 类型 |
|--------|------|
| `resolveStorePath` | 函数 |
| `loadSessionStore` | 函数 |
| `saveSessionStore` | 函数 |
| `resolveSessionFilePath` | 函数 |

---

## 7. channel-web.ts — Web 频道桶导出

### 7.1 文件概述

此文件是 WhatsApp Web 频道功能的桶（barrel）模块。文件顶部注释说明：将原始 900+ 行模块拆分为更小的、可测试的模块。

### 7.2 导出项

#### 来自 `./web/auto-reply.js`

| 导出名 | 类别 | 说明 |
|--------|------|------|
| `DEFAULT_WEB_MEDIA_BYTES` | 常量 | Web 媒体默认字节数 |
| `HEARTBEAT_PROMPT` | 常量 | 心跳提示符 |
| `HEARTBEAT_TOKEN` | 常量 | 心跳令牌 |
| `monitorWebChannel` | 函数 | 监控 Web 频道 |
| `resolveHeartbeatRecipients` | 函数 | 解析心跳接收者 |
| `runWebHeartbeatOnce` | 函数 | 执行一次 Web 心跳 |
| `WebChannelStatus` | 类型 | Web 频道状态类型 |
| `WebMonitorTuning` | 类型 | Web 监控调优参数类型 |

#### 来自 `./web/inbound.js`

| 导出名 | 类别 |
|--------|------|
| `extractMediaPlaceholder` | 函数 |
| `extractText` | 函数 |
| `monitorWebInbox` | 函数 |
| `WebInboundMessage` | 类型 |
| `WebListenerCloseReason` | 类型 |

#### 来自 `./web/login.js`

| 导出名 | 类别 |
|--------|------|
| `loginWeb` | 函数 |

#### 来自 `./web/media.js`

| 导出名 | 类别 |
|--------|------|
| `loadWebMedia` | 函数 |
| `optimizeImageToJpeg` | 函数 |

#### 来自 `./web/outbound.js`

| 导出名 | 类别 |
|--------|------|
| `sendMessageWhatsApp` | 函数 |

#### 来自 `./web/session.js`

| 导出名 | 类别 |
|--------|------|
| `createWaSocket` | 函数 |
| `formatError` | 函数 |
| `getStatusCode` | 函数 |
| `logoutWeb` | 函数 |
| `logWebSelfId` | 函数 |
| `pickWebChannel` | 函数 |
| `WA_WEB_AUTH_DIR` | 常量 |
| `waitForWaConnection` | 函数 |
| `webAuthExists` | 函数 |

---

## 8. logger.ts — 高级日志辅助函数

### 8.1 文件概述

提供面向业务代码使用的结构化日志函数。每个函数同时输出到控制台（带主题色）和文件日志器。支持自动检测子系统前缀并路由到子系统日志器。

### 8.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `danger`, `info`, `logVerboseConsole`, `success`, `warn` | `./globals.js` |
| `getLogger` | `./logging/logger.js` |
| `createSubsystemLogger` | `./logging/subsystem.js` |
| `defaultRuntime`, `type RuntimeEnv` | `./runtime.js` |

### 8.3 内部常量与函数

#### `subsystemPrefixRe`（正则表达式常量）

```typescript
const subsystemPrefixRe = /^([a-z][a-z0-9-]{1,20}):\s+(.*)$/i;
```

**模式说明**：
- `^` — 字符串开头
- `([a-z][a-z0-9-]{1,20})` — 捕获组1：子系统名，以字母开头，后跟1-20个字母/数字/连字符
- `:\s+` — 冒号后跟一个或多个空白字符
- `(.*)$` — 捕获组2：剩余消息内容
- `i` 标志 — 不区分大小写

#### `splitSubsystem(message: string): { subsystem: string; rest: string } | null`

**用途**：尝试从消息中提取子系统前缀。

**逻辑**：
1. 使用 `subsystemPrefixRe` 匹配 `message`。
2. 如果不匹配，返回 `null`。
3. 解构 `match` 为 `[, subsystem, rest]`。
4. 返回 `{ subsystem, rest }`。

### 8.4 导出函数

#### `logInfo(message: string, runtime: RuntimeEnv = defaultRuntime): void`

1. 如果 `runtime === defaultRuntime`（引用相等），则调用 `splitSubsystem(message)`；否则 `parsed` 为 `null`。
2. 如果 `parsed` 非空，调用 `createSubsystemLogger(parsed.subsystem).info(parsed.rest)` 后返回。
3. 调用 `runtime.log(info(message))` 输出带信息主题色的消息。
4. 调用 `getLogger().info(message)` 记录到文件日志。

#### `logWarn(message: string, runtime: RuntimeEnv = defaultRuntime): void`

1. 如果 `runtime === defaultRuntime`，则调用 `splitSubsystem(message)`；否则 `parsed` 为 `null`。
2. 如果 `parsed` 非空，调用 `createSubsystemLogger(parsed.subsystem).warn(parsed.rest)` 后返回。
3. 调用 `runtime.log(warn(message))` 输出带警告主题色的消息。
4. 调用 `getLogger().warn(message)` 记录到文件日志。

#### `logSuccess(message: string, runtime: RuntimeEnv = defaultRuntime): void`

1. 如果 `runtime === defaultRuntime`，则调用 `splitSubsystem(message)`；否则 `parsed` 为 `null`。
2. 如果 `parsed` 非空，调用 `createSubsystemLogger(parsed.subsystem).info(parsed.rest)` 后返回。（**注意**：成功消息路由到子系统日志器时使用 `info` 级别，而非独立的 success 级别。）
3. 调用 `runtime.log(success(message))` 输出带成功主题色的消息。
4. 调用 `getLogger().info(message)` 记录到文件日志（使用 `info` 级别）。

#### `logError(message: string, runtime: RuntimeEnv = defaultRuntime): void`

1. 如果 `runtime === defaultRuntime`，则调用 `splitSubsystem(message)`；否则 `parsed` 为 `null`。
2. 如果 `parsed` 非空，调用 `createSubsystemLogger(parsed.subsystem).error(parsed.rest)` 后返回。
3. 调用 `runtime.error(danger(message))` 输出带错误主题色的消息（注意使用 `runtime.error` 而非 `runtime.log`）。
4. 调用 `getLogger().error(message)` 记录到文件日志。

#### `logDebug(message: string): void`

1. 调用 `getLogger().debug(message)` — 始终发送到文件日志器（由日志级别过滤）。
2. 调用 `logVerboseConsole(message)` — 仅在 verbose 模式下输出到控制台。

**注意**：`logDebug` 不接受 `runtime` 参数，不支持子系统前缀检测。

---

## 9. logging.ts — 日志系统桶导出

### 9.1 文件概述

此文件是日志子系统的完整桶（barrel）模块，聚合来自四个子模块的所有导出。

### 9.2 导入与重新导出

#### 类型导入（`import type`）

| 类型名 | 来源模块 |
|--------|----------|
| `ConsoleLoggerSettings` | `./logging/console.js` |
| `ConsoleStyle` | `./logging/console.js` |
| `LogLevel` | `./logging/levels.js` |
| `LoggerResolvedSettings` | `./logging/logger.js` |
| `LoggerSettings` | `./logging/logger.js` |
| `PinoLikeLogger` | `./logging/logger.js` |
| `SubsystemLogger` | `./logging/subsystem.js` |

#### 来自 `./logging/console.js` 的值导出

| 导出名 | 类别 |
|--------|------|
| `enableConsoleCapture` | 函数 |
| `getConsoleSettings` | 函数 |
| `getResolvedConsoleSettings` | 函数 |
| `routeLogsToStderr` | 函数 |
| `setConsoleSubsystemFilter` | 函数 |
| `setConsoleConfigLoaderForTests` | 函数 |
| `setConsoleTimestampPrefix` | 函数 |
| `shouldLogSubsystemToConsole` | 函数 |

#### 来自 `./logging/levels.js` 的值导出

| 导出名 | 类别 |
|--------|------|
| `ALLOWED_LOG_LEVELS` | 常量 |
| `levelToMinLevel` | 函数 |
| `normalizeLogLevel` | 函数 |

#### 来自 `./logging/logger.js` 的值导出

| 导出名 | 类别 |
|--------|------|
| `DEFAULT_LOG_DIR` | 常量 |
| `DEFAULT_LOG_FILE` | 常量 |
| `getChildLogger` | 函数 |
| `getLogger` | 函数 |
| `getResolvedLoggerSettings` | 函数 |
| `isFileLogLevelEnabled` | 函数 |
| `resetLogger` | 函数 |
| `setLoggerOverride` | 函数 |
| `toPinoLikeLogger` | 函数 |

#### 来自 `./logging/subsystem.js` 的值导出

| 导出名 | 类别 |
|--------|------|
| `createSubsystemLogger` | 函数 |
| `createSubsystemRuntime` | 函数 |
| `runtimeForLogger` | 函数 |
| `stripRedundantSubsystemPrefixForConsole` | 函数 |

### 9.3 类型导出

```typescript
export type {
  ConsoleLoggerSettings,
  ConsoleStyle,
  LogLevel,
  LoggerResolvedSettings,
  LoggerSettings,
  PinoLikeLogger,
  SubsystemLogger,
};
```

---

## 10. polls.ts — 投票输入标准化

### 10.1 文件概述

定义投票（poll）相关的类型和验证/标准化函数。用于跨多个频道（Telegram、Discord 等）统一投票数据格式。

### 10.2 导入

此文件无任何导入。

### 10.3 导出类型

#### `PollInput`

```typescript
export type PollInput = {
  question: string;
  options: string[];
  maxSelections?: number;
  /** Poll duration in seconds. Channel-specific limits apply (e.g. Telegram open_period is 5-600s). */
  durationSeconds?: number;
  /** Poll duration in hours. Used by channels that model duration in hours (e.g. Discord). */
  durationHours?: number;
};
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | `string` | 是 | 投票问题 |
| `options` | `string[]` | 是 | 选项列表 |
| `maxSelections` | `number` | 否 | 最大可选数 |
| `durationSeconds` | `number` | 否 | 持续时间（秒） |
| `durationHours` | `number` | 否 | 持续时间（小时） |

#### `NormalizedPollInput`

```typescript
export type NormalizedPollInput = {
  question: string;
  options: string[];
  maxSelections: number;
  durationSeconds?: number;
  durationHours?: number;
};
```

与 `PollInput` 的区别：`maxSelections` 变为必填（总是有值）。

### 10.4 内部类型

#### `NormalizePollOptions`

```typescript
type NormalizePollOptions = {
  maxOptions?: number;
};
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `maxOptions` | `number` | 否 | 允许的最大选项数 |

### 10.5 导出函数

#### `normalizePollInput(input: PollInput, options: NormalizePollOptions = {}): NormalizedPollInput`

**用途**：验证并标准化投票输入。

**详细逻辑**：

1. **问题验证**：`const question = input.question.trim()`。如果 `question` 为空字符串，抛出 `new Error("Poll question is required")`。

2. **选项清理**：
   - `const pollOptions = (input.options ?? []).map((option) => option.trim())`
   - `const cleaned = pollOptions.filter(Boolean)` — 过滤掉空字符串。
   - 如果 `cleaned.length < 2`，抛出 `new Error("Poll requires at least 2 options")`。
   - 如果 `options.maxOptions !== undefined && cleaned.length > options.maxOptions`，抛出 `new Error(\`Poll supports at most ${options.maxOptions} options\`)`。

3. **maxSelections 标准化**：
   - `const maxSelectionsRaw = input.maxSelections`
   - 如果 `typeof maxSelectionsRaw === "number" && Number.isFinite(maxSelectionsRaw)`，则 `maxSelections = Math.floor(maxSelectionsRaw)`；否则默认值为 `1`。
   - 如果 `maxSelections < 1`，抛出 `new Error("maxSelections must be at least 1")`。
   - 如果 `maxSelections > cleaned.length`，抛出 `new Error("maxSelections cannot exceed option count")`。

4. **durationSeconds 标准化**：
   - `const durationSecondsRaw = input.durationSeconds`
   - 如果 `typeof durationSecondsRaw === "number" && Number.isFinite(durationSecondsRaw)`，则 `durationSeconds = Math.floor(durationSecondsRaw)`；否则为 `undefined`。
   - 如果 `durationSeconds !== undefined && durationSeconds < 1`，抛出 `new Error("durationSeconds must be at least 1")`。

5. **durationHours 标准化**：
   - `const durationRaw = input.durationHours`
   - 如果 `typeof durationRaw === "number" && Number.isFinite(durationRaw)`，则 `durationHours = Math.floor(durationRaw)`；否则为 `undefined`。
   - 如果 `durationHours !== undefined && durationHours < 1`，抛出 `new Error("durationHours must be at least 1")`。

6. **互斥检查**：如果 `durationSeconds !== undefined && durationHours !== undefined`，抛出 `new Error("durationSeconds and durationHours are mutually exclusive")`。

7. **返回**：`{ question, options: cleaned, maxSelections, durationSeconds, durationHours }`。

#### `normalizePollDurationHours(value: number | undefined, options: { defaultHours: number; maxHours: number }): number`

**用途**：标准化投票持续时间（小时），确保在有效范围内。

**逻辑**：
1. `const base = typeof value === "number" && Number.isFinite(value) ? Math.floor(value) : options.defaultHours`
2. `return Math.min(Math.max(base, 1), options.maxHours)` — 钳位到 `[1, maxHours]` 范围。

---

## 11. utils.ts — 通用工具函数集

### 11.1 文件概述

提供项目全局使用的通用工具函数，涵盖文件系统操作、数值钳位、正则转义、JSON 解析、类型守卫、WhatsApp 号码/JID 处理、UTF-16 安全字符串操作、路径解析与显示等。

### 11.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `fs` (默认导入) | `node:fs` |
| `os` (默认导入) | `node:os` |
| `path` (默认导入) | `node:path` |
| `resolveOAuthDir` | `./config/paths.js` |
| `logVerbose`, `shouldLogVerbose` | `./globals.js` |
| `expandHomePrefix`, `resolveEffectiveHomeDir`, `resolveRequiredHomeDir` | `./infra/home-dir.js` |

### 11.3 导出类型

#### `WebChannel`

```typescript
export type WebChannel = "web";
```

字面量联合类型，仅允许值 `"web"`。

#### `JidToE164Options`

```typescript
export type JidToE164Options = {
  authDir?: string;
  lidMappingDirs?: string[];
  logMissing?: boolean;
};
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `authDir` | `string` | 否 | 认证目录路径 |
| `lidMappingDirs` | `string[]` | 否 | LID 映射目录列表 |
| `logMissing` | `boolean` | 否 | 是否在 LID 映射缺失时记录日志 |

### 11.4 内部类型

#### `LidLookup`

```typescript
type LidLookup = {
  getPNForLID?: (jid: string) => Promise<string | null>;
};
```

### 11.5 导出函数

#### `ensureDir(dir: string): Promise<void>`

调用 `fs.promises.mkdir(dir, { recursive: true })`。递归创建目录。

#### `pathExists(targetPath: string): Promise<boolean>`

在 `try...catch` 块中调用 `fs.promises.access(targetPath)`。成功返回 `true`，异常返回 `false`。

#### `clampNumber(value: number, min: number, max: number): number`

返回 `Math.max(min, Math.min(max, value))`。将值钳位到 `[min, max]` 范围。

#### `clampInt(value: number, min: number, max: number): number`

返回 `clampNumber(Math.floor(value), min, max)`。先向下取整，再钳位。

#### `clamp`（常量别名）

```typescript
export const clamp = clampNumber;
```

`clampNumber` 的别名。

#### `escapeRegExp(value: string): string`

```typescript
return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
```

转义正则表达式特殊字符。正则模式匹配以下字符：`. * + ? ^ $ { } ( ) | [ ] \`。

#### `safeParseJson<T>(raw: string): T | null`

在 `try...catch` 块中调用 `JSON.parse(raw) as T`。成功返回解析结果，异常返回 `null`。泛型参数 `T` 用于类型断言。

#### `isPlainObject(value: unknown): value is Record<string, unknown>`

类型守卫函数，判断是否为纯对象。条件：
1. `typeof value === "object"`
2. `value !== null`
3. `!Array.isArray(value)`
4. `Object.prototype.toString.call(value) === "[object Object]"`

#### `isRecord(value: unknown): value is Record<string, unknown>`

类型守卫函数，判断是否为记录类型（比 `isPlainObject` 更宽松）。条件：
1. `typeof value === "object"`
2. `value !== null`
3. `!Array.isArray(value)`

#### `assertWebChannel(input: string): asserts input is WebChannel`

断言函数。如果 `input !== "web"`，抛出 `new Error("Web channel must be 'web'")`。

#### `normalizePath(p: string): string`

如果 `p` 不以 `"/"` 开头，返回 `\`/${p}\``。否则返回原值。确保路径以 `/` 开头。

#### `withWhatsAppPrefix(number: string): string`

如果 `number` 以 `"whatsapp:"` 开头，返回原值。否则返回 `\`whatsapp:${number}\``。

#### `normalizeE164(number: string): string`

**逻辑**：
1. `const withoutPrefix = number.replace(/^whatsapp:/, "").trim()` — 移除 `whatsapp:` 前缀并去除两端空白。
2. `const digits = withoutPrefix.replace(/[^\d+]/g, "")` — 仅保留数字和 `+` 字符。
3. 如果 `digits` 以 `"+"` 开头，返回 `\`+${digits.slice(1)}\`` — 保持 `+` 前缀，取后续部分。
4. 否则返回 `\`+${digits}\`` — 添加 `+` 前缀。

#### `isSelfChatMode(selfE164: string | null | undefined, allowFrom?: Array<string | number> | null): boolean`

**用途**：检测是否处于"自聊天模式"（机器人和人类使用同一 WhatsApp 身份）。

**逻辑**：
1. 如果 `selfE164` 为 falsy，返回 `false`。
2. 如果 `allowFrom` 不是数组或长度为 0，返回 `false`。
3. `const normalizedSelf = normalizeE164(selfE164)`。
4. 返回 `allowFrom.some(...)` 的结果：
   - 如果 `n === "*"`，返回 `false`（通配符不触发自聊天模式）。
   - 在 `try...catch` 中调用 `normalizeE164(String(n)) === normalizedSelf`。
   - catch 中返回 `false`。

#### `toWhatsappJid(number: string): string`

**逻辑**：
1. `const withoutPrefix = number.replace(/^whatsapp:/, "").trim()`。
2. 如果 `withoutPrefix` 包含 `"@"`，直接返回（已经是 JID 格式）。
3. `const e164 = normalizeE164(withoutPrefix)`。
4. `const digits = e164.replace(/\D/g, "")` — 移除所有非数字字符。
5. 返回 `` `${digits}@s.whatsapp.net` ``。

#### `jidToE164(jid: string, opts?: JidToE164Options): string | null`

**用途**：将 WhatsApp JID 转换回 E.164 格式电话号码。

**逻辑**：
1. 尝试匹配正则 `/^(\d+)(?::\d+)?@(s\.whatsapp\.net|hosted)$/`：
   - 匹配成功：返回 `\`+${match[1]}\``（即 `+` 加上捕获的数字部分）。
2. 尝试匹配正则 `/^(\d+)(?::\d+)?@(lid|hosted\.lid)$/`（LID 格式）：
   - 匹配成功：提取 `lid = lidMatch[1]`。
   - 调用 `readLidReverseMapping(lid, opts)`。
   - 如果返回非空，返回该值。
   - 否则：`const shouldLog = opts?.logMissing ?? shouldLogVerbose()`。如果 `shouldLog` 为 `true`，调用 `logVerbose(\`LID mapping not found for ${lid}; skipping inbound message\`)`。
3. 返回 `null`。

#### `resolveJidToE164(jid: string | null | undefined, opts?: JidToE164Options & { lidLookup?: LidLookup }): Promise<string | null>`

**用途**：异步解析 JID 到 E.164，支持 LID 查找回调。

**逻辑**：
1. 如果 `jid` 为 falsy，返回 `null`。
2. `const direct = jidToE164(jid, opts)`。如果 `direct` 非空，返回。
3. 如果 jid 不匹配 `/(@lid|@hosted\.lid)$/`，返回 `null`。
4. 如果 `opts?.lidLookup?.getPNForLID` 不存在，返回 `null`。
5. 在 `try...catch` 中：
   - 调用 `const pnJid = await opts.lidLookup.getPNForLID(jid)`。
   - 如果 `pnJid` 为 falsy，返回 `null`。
   - 返回 `jidToE164(pnJid, opts)`。
6. catch 中：如果 `shouldLogVerbose()` 为 `true`，调用 `logVerbose(\`LID mapping lookup failed for ${jid}: ${String(err)}\`)`。返回 `null`。

#### `sleep(ms: number): Promise<void>`

```typescript
return new Promise((resolve) => setTimeout(resolve, ms));
```

#### `sliceUtf16Safe(input: string, start: number, end?: number): string`

**用途**：UTF-16 安全的字符串切片，避免在代理对中间切割。

**逻辑**：
1. `const len = input.length`。
2. 计算 `from`：如果 `start < 0` 则 `Math.max(len + start, 0)`，否则 `Math.min(start, len)`。
3. 计算 `to`：如果 `end === undefined` 则 `len`；如果 `end < 0` 则 `Math.max(len + end, 0)`；否则 `Math.min(end, len)`。
4. 如果 `to < from`，交换两者（`const tmp = from; from = to; to = tmp`）。
5. **起始位置调整**：如果 `from > 0 && from < len`，检查 `input.charCodeAt(from)` 是否为低位代理（low surrogate）且 `input.charCodeAt(from - 1)` 是否为高位代理（high surrogate）。如果是，则 `from += 1`（跳过被切割的代理对）。
6. **结束位置调整**：如果 `to > 0 && to < len`，检查 `input.charCodeAt(to - 1)` 是否为高位代理且 `input.charCodeAt(to)` 是否为低位代理。如果是，则 `to -= 1`（收缩以避免切割代理对）。
7. 返回 `input.slice(from, to)`。

#### `truncateUtf16Safe(input: string, maxLen: number): string`

**逻辑**：
1. `const limit = Math.max(0, Math.floor(maxLen))`。
2. 如果 `input.length <= limit`，返回原值。
3. 返回 `sliceUtf16Safe(input, 0, limit)`。

#### `resolveUserPath(input: string): string`

**逻辑**：
1. `const trimmed = input.trim()`。如果 `trimmed` 为空，返回 `trimmed`。
2. 如果 `trimmed` 以 `"~"` 开头：
   - 调用 `expandHomePrefix(trimmed, { home: resolveRequiredHomeDir(process.env, os.homedir), env: process.env, homedir: os.homedir })`。
   - 返回 `path.resolve(expanded)`。
3. 否则返回 `path.resolve(trimmed)`。

#### `resolveConfigDir(env: NodeJS.ProcessEnv = process.env, homedir: () => string = os.homedir): string`

**逻辑**：
1. `const override = env.OPENCLAW_STATE_DIR?.trim() || env.CLAWDBOT_STATE_DIR?.trim()`。
2. 如果 `override` 为真值，返回 `resolveUserPath(override)`。
3. `const newDir = path.join(resolveRequiredHomeDir(env, homedir), ".openclaw")`。
4. 在 `try...catch` 中：调用 `fs.existsSync(newDir)`。如果存在则返回 `newDir`。
5. 无论是否存在，最终都返回 `newDir`（best-effort 检查）。

#### `resolveHomeDir(): string | undefined`

返回 `resolveEffectiveHomeDir(process.env, os.homedir)`。

#### `shortenHomePath(input: string): string`

**用途**：将绝对路径中的 home 目录部分替换为简短前缀（`~` 或 `$OPENCLAW_HOME`）。

**逻辑**：
1. 如果 `input` 为 falsy，返回原值。
2. `const display = resolveHomeDisplayPrefix()`。如果 `display` 为 `undefined`，返回原值。
3. 解构 `{ home, prefix }`。
4. 如果 `input === home`，返回 `prefix`。
5. 如果 `input` 以 `\`${home}/\`` 或 `\`${home}\\\`` 开头，返回 `\`${prefix}${input.slice(home.length)}\``。
6. 否则返回原值。

#### `shortenHomeInString(input: string): string`

**用途**：在任意字符串中将所有 home 目录出现替换为简短前缀。

**逻辑**：
1. 如果 `input` 为 falsy，返回原值。
2. `const display = resolveHomeDisplayPrefix()`。如果为 `undefined`，返回原值。
3. 返回 `input.split(display.home).join(display.prefix)`。

#### `displayPath(input: string): string`

返回 `shortenHomePath(input)`。简单别名。

#### `displayString(input: string): string`

返回 `shortenHomeInString(input)`。简单别名。

#### `formatTerminalLink(label: string, url: string, opts?: { fallback?: string; force?: boolean }): string`

**用途**：生成终端超链接（OSC 8 转义序列）或降级为纯文本。

**逻辑**：
1. `const esc = "\u001b"` — ESC 字符。
2. `const safeLabel = label.replaceAll(esc, "")` — 移除标签中的 ESC 字符。
3. `const safeUrl = url.replaceAll(esc, "")` — 移除 URL 中的 ESC 字符。
4. 确定 `allow`：
   - 如果 `opts?.force === true`，则 `allow = true`。
   - 如果 `opts?.force === false`，则 `allow = false`。
   - 否则 `allow = Boolean(process.stdout.isTTY)`。
5. 如果 `!allow`：返回 `opts?.fallback ?? \`${safeLabel} (${safeUrl})\``。
6. 返回 `\`\u001b]8;;${safeUrl}\u0007${safeLabel}\u001b]8;;\u0007\``（OSC 8 超链接格式）。

### 11.6 内部辅助函数

#### `isHighSurrogate(codeUnit: number): boolean`

返回 `codeUnit >= 0xD800 && codeUnit <= 0xDBFF`。

#### `isLowSurrogate(codeUnit: number): boolean`

返回 `codeUnit >= 0xDC00 && codeUnit <= 0xDFFF`。

#### `resolveHomeDisplayPrefix(): { home: string; prefix: string } | undefined`

**逻辑**：
1. `const home = resolveHomeDir()`。如果 `home` 为 `undefined`，返回 `undefined`。
2. `const explicitHome = process.env.OPENCLAW_HOME?.trim()`。
3. 如果 `explicitHome` 为真值，返回 `{ home, prefix: "$OPENCLAW_HOME" }`。
4. 否则返回 `{ home, prefix: "~" }`。

#### `resolveLidMappingDirs(opts?: JidToE164Options): string[]`

**用途**：收集所有可能包含 LID 映射文件的目录。

**逻辑**：
1. 创建 `const dirs = new Set<string>()`。
2. 定义内部函数 `addDir(dir?: string | null)`：如果 `dir` 为真值，调用 `dirs.add(resolveUserPath(dir))`。
3. 调用 `addDir(opts?.authDir)`。
4. 遍历 `opts?.lidMappingDirs ?? []`，对每个 `dir` 调用 `addDir(dir)`。
5. 调用 `addDir(resolveOAuthDir())`。
6. 调用 `addDir(path.join(CONFIG_DIR, "credentials"))`。
7. 返回 `[...dirs]`。

#### `readLidReverseMapping(lid: string, opts?: JidToE164Options): string | null`

**用途**：从磁盘读取 LID 反向映射。

**逻辑**：
1. `const mappingFilename = \`lid-mapping-${lid}_reverse.json\``。
2. `const mappingDirs = resolveLidMappingDirs(opts)`。
3. 遍历 `mappingDirs`：
   - `const mappingPath = path.join(dir, mappingFilename)`。
   - 在 `try...catch` 中：
     - `const data = fs.readFileSync(mappingPath, "utf8")`。
     - `const phone = JSON.parse(data) as string | number | null`。
     - 如果 `phone === null || phone === undefined`，`continue`。
     - 返回 `normalizeE164(String(phone))`。
   - catch 中不做任何事（continue 到下一个目录）。
4. 返回 `null`。

### 11.7 导出常量

#### `CONFIG_DIR`

```typescript
export const CONFIG_DIR = resolveConfigDir();
```

在模块加载时计算的配置根目录。可通过 `OPENCLAW_STATE_DIR` 环境变量覆盖。

---

## 12. version.ts — 版本号解析

### 12.1 文件概述

提供从多个来源解析当前应用版本号的功能。支持三种版本来源（按优先级排列）：
1. 编译时注入的 `__OPENCLAW_VERSION__` 常量
2. `OPENCLAW_BUNDLED_VERSION` 环境变量
3. `package.json` 或 `build-info.json` 文件

### 12.2 导入

| 导入项 | 来源模块 |
|--------|----------|
| `createRequire` | `node:module` |

### 12.3 全局声明

```typescript
declare const __OPENCLAW_VERSION__: string | undefined;
```

声明一个可能由构建工具（如 esbuild define）注入的全局常量。

### 12.4 内部常量

#### `CORE_PACKAGE_NAME`

```typescript
const CORE_PACKAGE_NAME = "openclaw";
```

#### `PACKAGE_JSON_CANDIDATES`

```typescript
const PACKAGE_JSON_CANDIDATES = [
  "../package.json",
  "../../package.json",
  "../../../package.json",
  "./package.json",
] as const;
```

按顺序尝试读取的 `package.json` 候选路径。

#### `BUILD_INFO_CANDIDATES`

```typescript
const BUILD_INFO_CANDIDATES = [
  "../build-info.json",
  "../../build-info.json",
  "./build-info.json",
] as const;
```

按顺序尝试读取的 `build-info.json` 候选路径。

### 12.5 内部函数

#### `readVersionFromJsonCandidates(moduleUrl: string, candidates: readonly string[], opts: { requirePackageName?: boolean } = {}): string | null`

**逻辑**：
1. 外层 `try...catch`（整体失败返回 `null`）。
2. `const require = createRequire(moduleUrl)` — 基于调用模块 URL 创建 require 函数。
3. 遍历 `candidates`：
   - 内层 `try...catch`（单个候选失败则 continue）。
   - `const parsed = require(candidate) as { name?: string; version?: string }`。
   - `const version = parsed.version?.trim()`。如果 `version` 为 falsy，`continue`。
   - 如果 `opts.requirePackageName` 为 `true` 且 `parsed.name !== CORE_PACKAGE_NAME`（即 `"openclaw"`），`continue`。
   - 返回 `version`。
4. 返回 `null`。

### 12.6 导出函数

#### `readVersionFromPackageJsonForModuleUrl(moduleUrl: string): string | null`

调用 `readVersionFromJsonCandidates(moduleUrl, PACKAGE_JSON_CANDIDATES, { requirePackageName: true })`。

要求 package.json 的 `name` 字段必须为 `"openclaw"`。

#### `readVersionFromBuildInfoForModuleUrl(moduleUrl: string): string | null`

调用 `readVersionFromJsonCandidates(moduleUrl, BUILD_INFO_CANDIDATES)`。

不要求特定的 package name。

#### `resolveVersionFromModuleUrl(moduleUrl: string): string | null`

```typescript
return (
  readVersionFromPackageJsonForModuleUrl(moduleUrl) ||
  readVersionFromBuildInfoForModuleUrl(moduleUrl)
);
```

优先从 package.json 读取，回退到 build-info.json。

### 12.7 导出常量

#### `VERSION`

```typescript
export const VERSION =
  (typeof __OPENCLAW_VERSION__ === "string" && __OPENCLAW_VERSION__) ||
  process.env.OPENCLAW_BUNDLED_VERSION ||
  resolveVersionFromModuleUrl(import.meta.url) ||
  "0.0.0";
```

**解析优先级**：
1. 编译时全局常量 `__OPENCLAW_VERSION__`（类型为 `string` 且非空时使用）
2. 环境变量 `process.env.OPENCLAW_BUNDLED_VERSION`
3. 运行时从 package.json / build-info.json 解析
4. 兜底值 `"0.0.0"`

---

## 13. 文件间依赖关系图

```
entry.ts
├── ./cli/profile.js
├── ./cli/respawn-policy.js
├── ./cli/windows-argv.js
├── ./infra/env.js
├── ./infra/warning-filter.js
├── ./process/child-process-bridge.js
└── (动态) ./cli/run-main.js

index.ts
├── ./auto-reply/reply.js
├── ./auto-reply/templating.js
├── ./channel-web.js ──────────────► channel-web.ts
├── ./cli/deps.js
├── ./cli/prompt.js
├── ./cli/wait.js
├── ./cli/program.js
├── ./config/config.js
├── ./config/sessions.js
├── ./infra/binaries.js
├── ./infra/dotenv.js
├── ./infra/env.js
├── ./infra/errors.js
├── ./infra/is-main.js
├── ./infra/path-env.js
├── ./infra/ports.js
├── ./infra/runtime-guard.js
├── ./infra/unhandled-rejections.js
├── ./logging.js ──────────────────► logging.ts
├── ./process/exec.js
└── ./utils.js ────────────────────► utils.ts

globals.ts
├── ./logging/logger.js
└── ./terminal/theme.js

runtime.ts
├── ./terminal/progress-line.js
└── ./terminal/restore.js

extensionAPI.ts
├── ./agents/agent-scope.ts
├── ./agents/defaults.ts
├── ./agents/identity.ts
├── ./agents/model-selection.ts
├── ./agents/pi-embedded.ts
├── ./agents/timeout.ts
├── ./agents/workspace.ts
└── ./config/sessions.ts

channel-web.ts
├── ./web/auto-reply.js
├── ./web/inbound.js
├── ./web/login.js
├── ./web/media.js
├── ./web/outbound.js
└── ./web/session.js

logger.ts
├── ./globals.js ──────────────────► globals.ts
├── ./logging/logger.js
├── ./logging/subsystem.js
└── ./runtime.js ──────────────────► runtime.ts

logging.ts
├── ./logging/console.js
├── ./logging/levels.js
├── ./logging/logger.js
└── ./logging/subsystem.js

polls.ts
└── (无导入)

utils.ts
├── ./config/paths.js
├── ./globals.js ──────────────────► globals.ts
└── ./infra/home-dir.js

version.ts
└── node:module
```

### 根级文件间直接依赖关系

| 源文件 | 依赖的根级文件 |
|--------|----------------|
| `entry.ts` | 无 |
| `index.ts` | `channel-web.ts`, `logging.ts`, `utils.ts` |
| `globals.ts` | 无（仅依赖子模块） |
| `runtime.ts` | 无（仅依赖子模块） |
| `extensionAPI.ts` | 无（仅依赖子模块） |
| `channel-web.ts` | 无（仅依赖子模块） |
| `logger.ts` | `globals.ts`, `runtime.ts` |
| `logging.ts` | 无（仅依赖子模块） |
| `polls.ts` | 无 |
| `utils.ts` | `globals.ts` |
| `version.ts` | 无 |

### 关键设计模式

1. **桶导出模式**：`channel-web.ts`、`logging.ts`、`extensionAPI.ts` 均为纯桶文件，将分散在子目录中的功能聚合为统一的导入入口。

2. **运行时抽象模式**：`runtime.ts` 定义 `RuntimeEnv` 类型，允许在测试中替换 `log`/`error`/`exit` 行为。`logger.ts` 中的所有高级日志函数都接受可选的 `runtime` 参数，默认使用 `defaultRuntime`。

3. **子系统日志路由**：`logger.ts` 通过正则匹配消息前缀（格式 `subsystem: message`），自动将带有子系统前缀的消息路由到对应的子系统日志器，仅在使用默认运行时时启用此行为。

4. **渐进式版本解析**：`version.ts` 采用多源回退策略，按优先级依次尝试编译时常量、环境变量、文件系统读取，最终回退到 `"0.0.0"`。

5. **进程重生模式**：`entry.ts` 在需要抑制 Node.js 实验性警告时，会重新生成子进程并附加相应的 Node.js CLI 参数，使用环境变量 `OPENCLAW_NODE_OPTIONS_READY` 防止无限递归。

6. **UTF-16 代理对安全**：`utils.ts` 中的 `sliceUtf16Safe` 和 `truncateUtf16Safe` 确保在包含 emoji 或其他补充平面字符的字符串中不会在代理对中间截断。

---

*本文档基于 `/home/user/clawdbot/src/` 根级源文件生成，涵盖了所有函数、类型、常量、接口及其完整实现逻辑。*
