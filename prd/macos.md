# macOS 模块 PRD

## 1. 模块概述与用途

`macos` 模块包含 OpenClaw 在 macOS 平台上的两个入口点程序：**relay**（中继/CLI 入口）和 **gateway-daemon**（网关守护进程）。这两个文件都是可执行脚本（带 shebang），设计为被 macOS 原生 Swift 应用打包和调用。此外还包含一个烟雾测试（smoke test）工具。

## 2. 目录结构

```
src/macos/
  relay.ts              # 中继入口点（CLI 程序入口）
  gateway-daemon.ts     # 网关守护进程入口
  relay-smoke.ts        # 中继烟雾测试
```

## 3. 各文件详细说明

---

### 3.1 `relay.ts` — 中继入口点

#### 文件头

```typescript
#!/usr/bin/env node
import process from "node:process";
```

#### 全局声明

```typescript
declare const __OPENCLAW_VERSION__: string | undefined;
```

#### 内部常量

```typescript
const BUNDLED_VERSION =
  (typeof __OPENCLAW_VERSION__ === "string" && __OPENCLAW_VERSION__) ||
  process.env.OPENCLAW_BUNDLED_VERSION ||
  "0.0.0";
```
- 版本解析优先级：编译时常量 > 环境变量 > 默认 `"0.0.0"`

#### 内部函数

##### `hasFlag(args: string[], flag: string): boolean`
- `args.includes(flag)`

##### `patchBunLongForProtobuf(): Promise<void>`
- 检测 Bun 运行时（`process.versions.bun`）
- Bun 内置的全局 `Long` 类缺少 protobufjs/long.js 所需的 API（如 `fromBits`）
- 动态导入 `"long"` 模块并替换 `globalThis.Long`
- 处理 ESM/CJS 兼容：`(mod.default ?? mod)`

#### 主函数 `main()`

执行流程：

1. **版本输出**：`--version`、`-V` 或 `-v` 标志触发，输出 `BUNDLED_VERSION` 后 exit(0)

2. **烟雾测试**：
   - 动态导入 `./relay-smoke.js`
   - 调用 `parseRelaySmokeTest(args, process.env)`
   - 若返回非 null，运行 `runRelaySmokeTest(smokeTest)`
   - 成功 exit(0)，失败输出错误后 exit(1)

3. **Bun Long 补丁**：调用 `patchBunLongForProtobuf()`

4. **环境初始化**（全部动态导入）：
   - `loadDotEnv({ quiet: true })` — 加载 .env 文件
   - `ensureOpenClawCliOnPath()` — 确保 CLI 在 PATH 中
   - `enableConsoleCapture()` — 启用控制台捕获
   - `assertSupportedRuntime()` — 运行时兼容性检查

5. **错误处理**：
   - `formatUncaughtError` — 格式化未捕获异常
   - `installUnhandledRejectionHandler()` — 未处理 Promise rejection
   - `process.on("uncaughtException", ...)` — 输出 `"[openclaw] Uncaught exception:"` 后 exit(1)

6. **CLI 程序启动**：
   - `buildProgram()` — 构建命令行程序
   - `program.parseAsync(process.argv)` — 解析并执行

7. **顶层错误捕获**：
   - 输出 `"[openclaw] Relay failed:"`
   - 尝试输出 `err.stack ?? err.message`
   - exit(1)

---

### 3.2 `gateway-daemon.ts` — 网关守护进程

#### 文件头

```typescript
#!/usr/bin/env node
import process from "node:process";
import type { GatewayLockHandle } from "../infra/gateway-lock.js";
import { restartGatewayProcessWithFreshPid } from "../infra/process-respawn.js";
```

#### 全局声明和常量

```typescript
declare const __OPENCLAW_VERSION__: string | undefined;

const BUNDLED_VERSION = ...;  // 同 relay.ts

const args = process.argv.slice(2);
```

#### 内部类型

```typescript
type GatewayWsLogStyle = "auto" | "full" | "compact";
```

#### 内部函数

##### `argValue(args: string[], flag: string): string | undefined`
- 查找 flag 的索引，返回下一个非 `-` 开头的参数值

##### `hasFlag(args: string[], flag: string): boolean`
- `args.includes(flag)`

#### 主函数 `main()`

执行流程：

1. **版本输出**：`--version` 或 `-v` 触发

2. **Bun Long 补丁**：同 relay.ts

3. **批量动态导入**（10 个模块并行）：
   ```typescript
   const [
     { loadConfig },
     { startGatewayServer },
     { setGatewayWsLogStyle },
     { setVerbose },
     { acquireGatewayLock, GatewayLockError },
     {
       consumeGatewaySigusr1RestartAuthorization,
       isGatewaySigusr1RestartExternallyAllowed,
       markGatewaySigusr1RestartHandled,
     },
     { defaultRuntime },
     { enableConsoleCapture, setConsoleTimestampPrefix },
     commandQueueMod,
     { createRestartIterationHook },
   ] = await Promise.all([...]);
   ```

4. **初始化**：
   - `enableConsoleCapture()`
   - `setConsoleTimestampPrefix(true)`
   - `setVerbose(hasFlag(args, "--verbose"))`

5. **WebSocket 日志样式**：
   - `--compact` 标志 -> `"compact"`
   - `--ws-log` 参数 -> `"compact" | "full" | "auto"`
   - 默认 `"auto"`

6. **端口解析**：
   - 优先级：`--port` > `OPENCLAW_GATEWAY_PORT` > `CLAWDBOT_GATEWAY_PORT` > `cfg.gateway?.port` > `"18789"`
   - 无效端口 exit(1)

7. **绑定地址解析**：
   - 优先级：`--bind` > `OPENCLAW_GATEWAY_BIND` > `CLAWDBOT_GATEWAY_BIND` > `cfg.gateway?.bind` > `"loopback"`
   - 允许值：`"loopback"`, `"lan"`, `"auto"`, `"custom"`, `"tailnet"`
   - 无效值 exit(1)

8. **Token 处理**：`--token` 参数设置到 `process.env.OPENCLAW_GATEWAY_TOKEN`

9. **信号处理**：

   ##### `request(action: "stop" | "restart", signal: string)`
   - 设置 `shuttingDown = true` 防止重复处理
   - **强制退出定时器**：
     - restart: `DRAIN_TIMEOUT_MS (30_000) + SHUTDOWN_TIMEOUT_MS (5_000) = 35` 秒
     - stop: `SHUTDOWN_TIMEOUT_MS (5_000)` 秒
   - **restart 流程**：
     1. 检查活跃任务数 (`commandQueueMod.getActiveTaskCount()`)
     2. 有活跃任务时等待排空 (`waitForActiveTasks(30_000)`)
     3. 关闭服务器（reason: `"gateway restarting"`, restartExpectedMs: 1500）
     4. 尝试全进程重启 (`restartGatewayProcessWithFreshPid()`)
        - `"spawned"`: 记录日志后 exit(0)
        - `"supervised"`: 记录日志后 exit(0)
        - `"failed"`: 回退到进程内重启，记录失败原因
        - 其他: 进程内重启（`OPENCLAW_NO_RESPAWN` 模式）
     5. 进程内重启：`shuttingDown = false`, 调用 `restartResolver()`
   - **stop 流程**：关闭服务器后 exit(0)

   ##### 信号映射
   - `SIGTERM` -> `request("stop", "SIGTERM")`
   - `SIGINT` -> `request("stop", "SIGINT")`
   - `SIGUSR1` -> 验证授权后 `request("restart", "SIGUSR1")`
     - 需要 `consumeGatewaySigusr1RestartAuthorization()` 返回 true
     - 或 `isGatewaySigusr1RestartExternallyAllowed()` 返回 true
     - 未授权时记录日志并忽略

10. **网关锁获取**：
    - `acquireGatewayLock()` — 防止多实例
    - `GatewayLockError` 时 exit(1)

11. **主循环**：
    ```typescript
    while (true) {
      onIteration();  // 重启回调：重置 commandQueue 所有 lane
      server = await startGatewayServer(port, { bind });
      await new Promise<void>(resolve => { restartResolver = resolve; });
    }
    ```
    - 进程内重启通过 resolve restartResolver 实现
    - `onIteration` 钩子在每次迭代时重置命令队列 lane 状态

12. **清理**：`finally` 中释放网关锁

13. **顶层错误捕获**：输出 `"[openclaw] Gateway daemon failed:"` 后 exit(1)

---

### 3.3 `relay-smoke.ts` — 烟雾测试

#### 导出类型

```typescript
export type RelaySmokeTest = "qr";
```

#### 导出函数

##### `parseRelaySmokeTest(args: string[], env: NodeJS.ProcessEnv): RelaySmokeTest | null`
- **CLI 参数检测**：
  - `--smoke qr` -> 返回 `"qr"`
  - `--smoke` 后无值或 `-` 开头 -> 抛出 `"Missing value for --smoke (expected: qr)"`
  - `--smoke` 后非 `"qr"` 值 -> 抛出 `"Unknown smoke test: ${value}"`
  - `--smoke-qr` -> 返回 `"qr"`
- **环境变量检测**（仅当无 CLI 参数时）：
  - `OPENCLAW_SMOKE_QR === "1"` 或 `OPENCLAW_SMOKE === "qr"` -> 返回 `"qr"`
  - 条件：`args.length === 0`（防止全局环境变量意外触发）
- 无匹配返回 `null`

##### `runRelaySmokeTest(test: RelaySmokeTest): Promise<void>`
- switch on test:
  - `"qr"`: 动态导入 `../web/qr-image.js`，调用 `renderQrPngBase64("smoke-test")`

---

## 4. 文件间依赖关系

```
relay.ts
  ├── 动态导入 ./relay-smoke.js
  ├── 动态导入 ../infra/dotenv.js
  ├── 动态导入 ../infra/path-env.js
  ├── 动态导入 ../logging.js
  ├── 动态导入 ../infra/runtime-guard.js
  ├── 动态导入 ../infra/errors.js
  ├── 动态导入 ../infra/unhandled-rejections.js
  └── 动态导入 ../cli/program.js

gateway-daemon.ts
  ├── 静态导入 ../infra/gateway-lock.js (类型)
  ├── 静态导入 ../infra/process-respawn.js
  ├── 动态导入 ../config/config.js
  ├── 动态导入 ../gateway/server.js
  ├── 动态导入 ../gateway/ws-logging.js
  ├── 动态导入 ../globals.js
  ├── 动态导入 ../infra/gateway-lock.js
  ├── 动态导入 ../infra/restart.js
  ├── 动态导入 ../runtime.js
  ├── 动态导入 ../logging.js
  ├── 动态导入 ../process/command-queue.js
  └── 动态导入 ../process/restart-recovery.js

relay-smoke.ts
  └── 动态导入 ../web/qr-image.js
```

**模块内依赖**: relay.ts 动态导入 relay-smoke.ts

## 5. 整体架构

本模块是 OpenClaw macOS 应用的**Node.js 运行时入口层**，包含两个独立的可执行入口：

### 中继入口 (`relay.ts`)
- 完整的 CLI 应用入口点，对应 `openclaw` 命令
- 采用延迟加载策略：所有模块动态导入，减少启动时间
- 支持快速路径：`--version` 和烟雾测试在最小依赖下完成
- Bun 运行时兼容：修补 Long 类以支持 protobufjs

### 网关守护进程 (`gateway-daemon.ts`)
- 长驻 WebSocket 服务器进程
- **优雅重启机制**：
  - SIGUSR1 触发重启（需授权）
  - 先排空活跃任务（30 秒超时）
  - 优先全进程重启（spawn 新进程），回退到进程内重启
  - 进程内重启通过主循环的 Promise 解决实现
- **安全退出**：SIGTERM/SIGINT 触发，5 秒强制退出超时
- **单实例保证**：通过网关锁防止多进程竞争
- **进程内重启恢复**：重置命令队列 lane 状态，防止残留活跃计数阻塞新任务

### 烟雾测试 (`relay-smoke.ts`)
- 最小化验证：仅测试 QR 码渲染能力
- 多种触发方式：CLI 参数、简写标志、环境变量
- 环境变量模式仅在无 CLI 参数时生效（防止意外触发）

关键设计决策：
- 两个入口都使用 `__OPENCLAW_VERSION__` 编译时常量注入版本号
- 动态导入策略减少冷启动开销
- 网关守护进程的重启策略优先保证进程新鲜度（新 PID），退化为进程内热重启
- SIGUSR1 重启需要显式授权，防止意外重启
- 强制退出定时器确保进程不会永久挂起
