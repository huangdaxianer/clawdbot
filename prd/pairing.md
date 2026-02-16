# Pairing 模块 PRD

## 1. 模块概述与用途

`pairing` 模块实现了 OpenClaw 的设备/用户配对机制。当新用户通过消息平台（如 WhatsApp、Telegram 等）首次联系 bot 时，系统会生成一个配对码，bot 所有者通过 CLI 批准该配对码后，用户的 ID 被添加到允许列表。该模块还处理配对设置码的生成（用于连接到网关的 WebSocket URL 和认证信息编码）。

## 2. 目录结构

```
src/pairing/
  pairing-store.ts       # 配对请求和允许列表的持久化存储
  pairing-messages.ts    # 配对回复消息构建
  pairing-labels.ts      # 配对 ID 标签解析
  setup-code.ts          # 配对设置码（网关连接信息编码）
```

## 3. 各文件详细说明

---

### 3.1 `pairing-store.ts` — 配对存储核心

#### 导入

```typescript
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ChannelId, ChannelPairingAdapter } from "../channels/plugins/types.js";
import { getPairingAdapter } from "../channels/plugins/pairing.js";
import { resolveOAuthDir, resolveStateDir } from "../config/paths.js";
import { withFileLock as withPathLock } from "../infra/file-lock.js";
import { resolveRequiredHomeDir } from "../infra/home-dir.js";
import { safeParseJson } from "../utils.js";
```

#### 内部常量

```typescript
const PAIRING_CODE_LENGTH = 8;
const PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";  // 无 0O1I，避免歧义
const PAIRING_PENDING_TTL_MS = 60 * 60 * 1000;                    // 1 小时过期
const PAIRING_PENDING_MAX = 3;                                     // 最多 3 个待审批请求

const PAIRING_STORE_LOCK_OPTIONS = {
  retries: {
    retries: 10,
    factor: 2,
    minTimeout: 100,
    maxTimeout: 10_000,
    randomize: true,
  },
  stale: 30_000,
} as const;
```

#### 导出类型

##### `PairingChannel`
```typescript
export type PairingChannel = ChannelId;  // 频道标识符的别名
```

##### `PairingRequest`
```typescript
export type PairingRequest = {
  id: string;                          // 用户 ID
  code: string;                        // 配对码
  createdAt: string;                   // ISO 时间戳
  lastSeenAt: string;                  // 最后活跃时间
  meta?: Record<string, string>;       // 可选元数据
};
```

#### 内部类型

##### `PairingStore`
```typescript
type PairingStore = {
  version: 1;
  requests: PairingRequest[];
};
```

##### `AllowFromStore`
```typescript
type AllowFromStore = {
  version: 1;
  allowFrom: string[];
};
```

#### 内部函数

##### `resolveCredentialsDir(env = process.env): string`
- 通过 `resolveStateDir` + `resolveOAuthDir` 解析凭证目录

##### `safeChannelKey(channel: PairingChannel): string`
- 转小写、替换路径不安全字符 `/[\\/:*?"<>|]/g` 为 `"_"`、替换 `".."` 为 `"_"`
- 结果为空或仅 `"_"` 时抛出 `"invalid pairing channel"`

##### `resolvePairingPath(channel, env): string`
- 返回 `<credentialsDir>/${safeChannelKey(channel)}-pairing.json`

##### `safeAccountKey(accountId: string): string`
- 同 `safeChannelKey` 逻辑，错误消息为 `"invalid pairing account id"`

##### `resolveAllowFromPath(channel, env, accountId?): string`
- 无 accountId: `<credentialsDir>/${base}-allowFrom.json`
- 有 accountId: `<credentialsDir>/${base}-${safeAccountKey(accountId)}-allowFrom.json`

##### `readJsonFile<T>(filePath, fallback): Promise<{ value: T; exists: boolean }>`
- 读取文件，使用 `safeParseJson` 解析
- ENOENT 和其他错误均返回 `{ value: fallback, exists: false }`

##### `writeJsonFile(filePath, value): Promise<void>`
- 创建目录（`mode: 0o700`）
- 写入临时文件（名称含 `crypto.randomUUID()`），`chmod 0o600`
- 原子重命名（`rename`）

##### `ensureJsonFile(filePath, fallback)`
- 若文件不存在则写入默认值

##### `withFileLock<T>(filePath, fallback, fn): Promise<T>`
- 先 `ensureJsonFile`，然后使用 `withPathLock` 加文件锁执行操作

##### `parseTimestamp(value: string | undefined): number | null`
- 使用 `Date.parse`，无效则返回 null

##### `isExpired(entry: PairingRequest, nowMs: number): boolean`
- 解析 createdAt，无效视为过期
- 判断 `nowMs - createdAt > PAIRING_PENDING_TTL_MS`

##### `pruneExpiredRequests(reqs, nowMs): { requests, removed }`
- 移除过期请求，返回保留列表和是否有移除

##### `resolveLastSeenAt(entry): number`
- 优先 `lastSeenAt`，其次 `createdAt`，默认 0

##### `pruneExcessRequests(reqs, maxPending): { requests, removed }`
- 若超过上限，按 `resolveLastSeenAt` 升序排序，保留最近的 maxPending 个

##### `randomCode(): string`
- 使用 `crypto.randomInt(0, PAIRING_CODE_ALPHABET.length)` 生成 8 位码
- 字母表：`ABCDEFGHJKLMNPQRSTUVWXYZ23456789`

##### `generateUniqueCode(existing: Set<string>): string`
- 最多尝试 500 次生成不重复的码
- 失败抛出 `"failed to generate unique pairing code"`

##### `normalizeId(value: string | number): string`
- `String(value).trim()`

##### `normalizeAllowEntry(channel, entry): string`
- trim 后若为 `"*"` 返回空字符串
- 调用频道适配器的 `normalizeAllowEntry`（若存在）

##### `normalizeAllowFromList(channel, store): string[]`
- 对 allowFrom 列表的每个元素调用 `normalizeAllowEntry`，过滤空值

##### `normalizeAllowFromInput(channel, entry): string`
- 先 `normalizeId` 再 `normalizeAllowEntry`

##### `dedupePreserveOrder(entries: string[]): string[]`
- 使用 Set 去重，保留首次出现的顺序

##### `readAllowFromStateForPath(channel, filePath): Promise<string[]>`
- 读取文件并规范化

##### `readAllowFromState(params): Promise<{ current, normalized }>`
- 读取当前列表 + 规范化输入条目

##### `writeAllowFromState(filePath, allowFrom): Promise<void>`
- 写入 `{ version: 1, allowFrom }`

##### `updateAllowFromStoreEntry(params: { channel, entry, accountId?, env?, apply }): Promise<{ changed, allowFrom }>`
- 获取文件路径，加文件锁
- 读取当前状态
- 调用 `apply(current, normalized)` — 返回 null 表示无变更
- 有变更则写入并返回 `{ changed: true }`

#### 导出函数

##### `readChannelAllowFromStore(channel, env?, accountId?): Promise<string[]>`
- 无 accountId: 读取频道级允许列表
- 有 accountId: 合并账户级和频道级（旧版兼容）允许列表
  - 旧版兼容说明：升级前的无作用域 allowFrom 文件继续生效，避免用户重新配对

##### `addChannelAllowFromStoreEntry(params: { channel, entry, accountId?, env? }): Promise<{ changed, allowFrom }>`
- apply 逻辑：若已存在返回 null，否则追加

##### `removeChannelAllowFromStoreEntry(params: { channel, entry, accountId?, env? }): Promise<{ changed, allowFrom }>`
- apply 逻辑：过滤匹配项，若数量不变返回 null

##### `listChannelPairingRequests(channel, env?, accountId?): Promise<PairingRequest[]>`
- 加文件锁读取配对存储
- 清理过期请求（`pruneExpiredRequests`）
- 限制数量（`pruneExcessRequests`，上限 `PAIRING_PENDING_MAX`）
- 若有清理则写回文件
- 按 accountId 过滤（若提供）
- 验证每个 entry 的 id/code/createdAt 字段为字符串
- 按 createdAt 升序排序

##### `upsertChannelPairingRequest(params: { channel, id, accountId?, meta?, env?, pairingAdapter? }): Promise<{ code: string; created: boolean }>`
- 加文件锁
- 规范化 id，处理 meta（过滤空值），若有 accountId 加入 meta
- 清理过期请求
- **已存在的请求**：
  - 保留原有 code（若有效），否则生成新 code
  - 更新 lastSeenAt 和 meta
  - `created: false`
- **达到上限**：返回 `{ code: "", created: false }`
- **新请求**：
  - 生成唯一 code
  - `created: true`

##### `approveChannelPairingCode(params: { channel, code, accountId?, env? }): Promise<{ id: string; entry?: PairingRequest } | null>`
- code 转大写 trim
- 加文件锁
- 清理过期请求
- 查找匹配的请求（code 和可选的 accountId）
- 找到后：
  - 从请求列表中移除
  - 调用 `addChannelAllowFromStoreEntry` 将 id 加入允许列表
  - accountId 使用请求参数或 entry 中的 meta.accountId
  - 返回 `{ id, entry }`
- 未找到返回 null

---

### 3.2 `pairing-messages.ts` — 配对回复消息

#### 导入

```typescript
import type { PairingChannel } from "./pairing-store.js";
import { formatCliCommand } from "../cli/command-format.js";
```

#### 导出函数

##### `buildPairingReply(params: { channel: PairingChannel; idLine: string; code: string }): string`
- 返回多行字符串：
  ```
  OpenClaw: access not configured.

  ${idLine}

  Pairing code: ${code}

  Ask the bot owner to approve with:
  ${formatCliCommand(`openclaw pairing approve ${channel} ${code}`)}
  ```

---

### 3.3 `pairing-labels.ts` — 配对 ID 标签

#### 导入

```typescript
import type { PairingChannel } from "./pairing-store.js";
import { getPairingAdapter } from "../channels/plugins/pairing.js";
```

#### 导出函数

##### `resolvePairingIdLabel(channel: PairingChannel): string`
- 调用 `getPairingAdapter(channel)` 获取适配器
- 返回 `adapter?.idLabel ?? "userId"`

---

### 3.4 `setup-code.ts` — 配对设置码

#### 导入

```typescript
import os from "node:os";
import type { OpenClawConfig } from "../config/types.js";
```

#### 内部常量

```typescript
const DEFAULT_GATEWAY_PORT = 18789;
```

#### 导出类型

##### `PairingSetupPayload`
```typescript
export type PairingSetupPayload = {
  url: string;
  token?: string;
  password?: string;
};
```

##### `PairingSetupCommandResult`
```typescript
export type PairingSetupCommandResult = {
  code: number | null;
  stdout: string;
  stderr?: string;
};
```

##### `PairingSetupCommandRunner`
```typescript
export type PairingSetupCommandRunner = (
  argv: string[],
  opts: { timeoutMs: number },
) => Promise<PairingSetupCommandResult>;
```

##### `ResolvePairingSetupOptions`
```typescript
export type ResolvePairingSetupOptions = {
  env?: NodeJS.ProcessEnv;
  publicUrl?: string;
  preferRemoteUrl?: boolean;
  forceSecure?: boolean;
  runCommandWithTimeout?: PairingSetupCommandRunner;
  networkInterfaces?: () => ReturnType<typeof os.networkInterfaces>;
};
```

##### `PairingSetupResolution`
```typescript
export type PairingSetupResolution =
  | { ok: true; payload: PairingSetupPayload; authLabel: "token" | "password"; urlSource: string }
  | { ok: false; error: string };
```

#### 内部类型

- `ResolveUrlResult`: `{ url?; source?; error? }`
- `ResolveAuthResult`: `{ token?; password?; label?; error? }`

#### 内部函数

##### `normalizeUrl(raw: string, schemeFallback: "ws" | "wss"): string | null`
- 尝试 `new URL(raw)` 解析
- 协议转换：`http` -> `ws`，`https` -> `wss`
- 仅允许 `ws` 和 `wss` 协议
- 返回 `${scheme}://${host}${port}`
- URL 解析失败时：取 `/` 前部分作为 host:port，加上 schemeFallback

##### `resolveGatewayPort(cfg, env): number`
- 优先级：`OPENCLAW_GATEWAY_PORT` > `CLAWDBOT_GATEWAY_PORT` > `cfg.gateway?.port` > `18789`

##### `resolveScheme(cfg, opts?): "ws" | "wss"`
- `forceSecure` 时返回 `"wss"`
- 否则根据 `cfg.gateway?.tls?.enabled` 判断

##### `parseIPv4Octets(address: string): [number, number, number, number] | null`
- 分割 `.`，验证 4 段，每段 0-255

##### `isPrivateIPv4(address: string): boolean`
- 10.x.x.x (Class A)
- 172.16-31.x.x (Class B)
- 192.168.x.x (Class C)

##### `isTailnetIPv4(address: string): boolean`
- 100.64-127.x.x (CGNAT 范围，Tailscale 使用)

##### `pickLanIPv4(networkInterfaces): string | null`
- 遍历网络接口，查找非内部、IPv4、私有 IP

##### `pickTailnetIPv4(networkInterfaces): string | null`
- 同上，查找 Tailnet IP

##### `parsePossiblyNoisyJsonObject(raw: string): Record<string, unknown>`
- 查找第一个 `{` 和最后一个 `}`，提取 JSON
- 解析失败返回空对象

##### `resolveTailnetHost(runCommandWithTimeout?): Promise<string | null>`
- 候选命令：`["tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]`
- 执行 `${candidate} status --json`（超时 5000ms）
- 解析 JSON，提取 `Self.DNSName`（去除尾部 `.`）或 `Self.TailscaleIPs[0]`

##### `resolveAuth(cfg, env): ResolveAuthResult`
- 认证模式 (`cfg.gateway?.auth?.mode`):
  - `"password"`: 需要 password
  - `"token"`: 需要 token
  - 未指定: 优先 token，其次 password
- 环境变量优先级：`OPENCLAW_GATEWAY_TOKEN` > `CLAWDBOT_GATEWAY_TOKEN` > 配置文件
- 无认证时返回 `"Gateway auth is not configured (no token or password)."`

##### `resolveGatewayUrl(cfg, opts): Promise<ResolveUrlResult>`
- URL 解析优先级（高到低）：
  1. `publicUrl`（用户配置的公开 URL）
  2. `preferRemoteUrl && remoteUrl`
  3. Tailscale serve/funnel 模式 -> `wss://${tailnetHost}`
  4. `remoteUrl`
  5. `bind=custom` -> `cfg.gateway?.customBindHost`
  6. `bind=tailnet` -> `pickTailnetIPv4`
  7. `bind=lan` -> `pickLanIPv4`
  8. `bind=loopback` -> 返回错误（loopback 无法远程访问）

#### 导出函数

##### `encodePairingSetupCode(payload: PairingSetupPayload): string`
- `JSON.stringify` -> base64 编码 -> URL 安全替换：`+` -> `-`，`/` -> `_`，去除 `=` 填充

##### `resolvePairingSetupFromConfig(cfg, options?): Promise<PairingSetupResolution>`
- 解析认证信息 (`resolveAuth`)
- 解析网关 URL (`resolveGatewayUrl`)
- 任一失败返回 `{ ok: false, error }`
- 成功返回包含 `payload`、`authLabel` 和 `urlSource` 的结果

---

## 4. 文件间依赖关系

```
pairing-store.ts
  ├── 依赖 node:crypto, node:fs, node:os, node:path
  ├── 依赖 ../channels/plugins/types.js (ChannelId, ChannelPairingAdapter)
  ├── 依赖 ../channels/plugins/pairing.js (getPairingAdapter)
  ├── 依赖 ../config/paths.js (resolveOAuthDir, resolveStateDir)
  ├── 依赖 ../infra/file-lock.js (withFileLock)
  ├── 依赖 ../infra/home-dir.js (resolveRequiredHomeDir)
  └── 依赖 ../utils.js (safeParseJson)

pairing-messages.ts
  ├── 依赖 ./pairing-store.js (PairingChannel 类型)
  └── 依赖 ../cli/command-format.js (formatCliCommand)

pairing-labels.ts
  ├── 依赖 ./pairing-store.js (PairingChannel 类型)
  └── 依赖 ../channels/plugins/pairing.js (getPairingAdapter)

setup-code.ts
  ├── 依赖 node:os
  └── 依赖 ../config/types.js (OpenClawConfig)
```

## 5. 整体架构

本模块实现了一个**基于配对码的访问控制系统**，架构分三个层次：

### 存储层 (`pairing-store.ts`)
- 使用文件系统 JSON 文件持久化，支持原子写入（临时文件 + rename）
- 文件锁机制防止并发修改（指数退避重试，10 次，100ms-10s）
- 两种存储：配对请求存储（`-pairing.json`）和允许列表存储（`-allowFrom.json`）
- 支持账户作用域隔离（不同 bot 账户独立的允许列表）
- 向后兼容：合并旧版无作用域文件和新版有作用域文件

### 交互层 (`pairing-messages.ts`, `pairing-labels.ts`)
- 构建标准化的配对提示消息
- 通过频道适配器获取平台特定的 ID 标签

### 设置层 (`setup-code.ts`)
- 解析网关连接参数（URL + 认证），支持多种网络拓扑：
  - 直连 LAN IP
  - Tailscale VPN（serve/funnel 模式或直接 IP）
  - 远程中继 URL
  - 自定义绑定地址
- 将连接信息编码为 URL 安全的 base64 设置码

关键设计决策：
- 配对码 8 位，使用人类友好字母表（无 0/O/1/I 歧义字符）
- 待审批请求上限 3 个，1 小时自动过期
- 最近使用的请求优先保留（按 lastSeenAt 排序后保留尾部）
- 文件权限严格控制（目录 0o700，文件 0o600）
- 频道 ID 路径安全化，防止目录遍历攻击
