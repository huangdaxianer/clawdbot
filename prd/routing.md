# Routing 模块 PRD

## 1. 模块概述与用途

`routing` 模块负责 OpenClaw 的消息路由决策：根据消息的频道、账户、对等方（peer）、Discord guild/角色、Slack team 等属性，确定应使用哪个 AI 代理（agent）来处理，并生成对应的会话键（session key）。该模块是多代理架构的核心路由层。

## 2. 目录结构

```
src/routing/
  session-key.ts      # 会话键构建与规范化工具
  bindings.ts         # 绑定配置查询
  resolve-route.ts    # 路由决策引擎
```

## 3. 各文件详细说明

---

### 3.1 `session-key.ts` — 会话键工具

#### 导入

```typescript
import type { ChatType } from "../channels/chat-type.js";
import { parseAgentSessionKey, type ParsedAgentSessionKey } from "../sessions/session-key-utils.js";
```

#### 重导出

```typescript
export {
  getSubagentDepth,
  isCronSessionKey,
  isAcpSessionKey,
  isSubagentSessionKey,
  parseAgentSessionKey,
  type ParsedAgentSessionKey,
} from "../sessions/session-key-utils.js";
```

#### 导出常量

```typescript
export const DEFAULT_AGENT_ID = "main";
export const DEFAULT_MAIN_KEY = "main";
export const DEFAULT_ACCOUNT_ID = "default";
```

#### 导出类型

```typescript
export type SessionKeyShape = "missing" | "agent" | "legacy_or_alias" | "malformed_agent";
```

#### 内部常量

```typescript
const VALID_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/i;     // 合法 ID 正则
const INVALID_CHARS_RE = /[^a-z0-9_-]+/g;                // 非法字符
const LEADING_DASH_RE = /^-+/;                           // 前导破折号
const TRAILING_DASH_RE = /-+$/;                          // 尾部破折号
```

#### 内部函数

##### `normalizeToken(value: string | undefined | null): string`
- `(value ?? "").trim().toLowerCase()`

#### 导出函数

##### `normalizeMainKey(value: string | undefined | null): string`
- trim 后转小写，空则返回 `DEFAULT_MAIN_KEY`（`"main"`）

##### `toAgentRequestSessionKey(storeKey: string | undefined | null): string | undefined`
- trim 后解析 `parseAgentSessionKey`
- 返回 `parsed?.rest ?? raw`，空则返回 `undefined`

##### `toAgentStoreSessionKey(params: { agentId: string; requestKey?: string; mainKey?: string }): string`
- 空或等于 `DEFAULT_MAIN_KEY` -> `buildAgentMainSessionKey`
- 已以 `"agent:"` 开头 -> 原样返回（小写）
- 以 `"subagent:"` 开头 -> `agent:${normalizedAgentId}:${lowered}`
- 其他 -> `agent:${normalizedAgentId}:${lowered}`

##### `resolveAgentIdFromSessionKey(sessionKey: string | undefined | null): string`
- 解析 session key 中的 agentId，规范化后返回
- 默认 `DEFAULT_AGENT_ID`

##### `classifySessionKeyShape(sessionKey: string | undefined | null): SessionKeyShape`
- 空 -> `"missing"`
- 可解析 -> `"agent"`
- 以 `"agent:"` 开头但不可解析 -> `"malformed_agent"`
- 其他 -> `"legacy_or_alias"`

##### `normalizeAgentId(value: string | undefined | null): string`
- trim 后若匹配 `VALID_ID_RE`，转小写返回
- 否则：小写 -> 非法字符替换为 `"-"` -> 去除首尾破折号 -> 截断 64 字符
- 结果为空则返回 `DEFAULT_AGENT_ID`（`"main"`）

##### `sanitizeAgentId(value: string | undefined | null): string`
- 逻辑与 `normalizeAgentId` 完全相同

##### `normalizeAccountId(value: string | undefined | null): string`
- 逻辑同 `normalizeAgentId`，默认值为 `DEFAULT_ACCOUNT_ID`（`"default"`）

##### `buildAgentMainSessionKey(params: { agentId: string; mainKey?: string }): string`
- 返回 `agent:${normalizeAgentId(agentId)}:${normalizeMainKey(mainKey)}`
- 示例：`"agent:main:main"`

##### `buildAgentPeerSessionKey(params: { agentId; mainKey?; channel; accountId?; peerKind?; peerId?; identityLinks?; dmScope? }): string`
- `peerKind` 默认 `"direct"`
- **direct 对话 (`peerKind === "direct"`)** :
  - `dmScope` 默认 `"main"`
  - 尝试身份链接解析 `resolveLinkedPeerId`（dmScope 非 "main" 时）
  - `per-account-channel-peer`: `agent:${agentId}:${channel}:${accountId}:direct:${peerId}`
  - `per-channel-peer`: `agent:${agentId}:${channel}:direct:${peerId}`
  - `per-peer`: `agent:${agentId}:direct:${peerId}`
  - `main`: `buildAgentMainSessionKey`（所有 DM 共享同一会话）
- **group/channel 对话**:
  - `agent:${agentId}:${channel}:${peerKind}:${peerId}`
  - 默认 channel = `"unknown"`，peerId = `"unknown"`

##### `resolveLinkedPeerId(params: { identityLinks?; channel; peerId }): string | null`（内部）
- 在 `identityLinks` 映射中查找 peerId
- 候选值：原始 peerId 和 `${channel}:${peerId}` 的规范化形式
- 返回匹配的 canonical 名称或 null

##### `buildGroupHistoryKey(params: { channel; accountId?; peerKind: "group" | "channel"; peerId }): string`
- 返回 `${channel}:${accountId}:${peerKind}:${peerId}`
- 所有值规范化为小写

##### `resolveThreadSessionKeys(params: { baseSessionKey; threadId?; parentSessionKey?; useSuffix? }): { sessionKey; parentSessionKey? }`
- 无 threadId: 返回基础键
- 有 threadId 且 `useSuffix`（默认 true）: `${baseSessionKey}:thread:${normalizedThreadId}`
- `useSuffix` 为 false: 使用 baseSessionKey

---

### 3.2 `bindings.ts` — 绑定配置查询

#### 导入

```typescript
import type { OpenClawConfig } from "../config/config.js";
import type { AgentBinding } from "../config/types.agents.js";
import { resolveDefaultAgentId } from "../agents/agent-scope.js";
import { normalizeChatChannelId } from "../channels/registry.js";
import { normalizeAccountId, normalizeAgentId } from "./session-key.js";
```

#### 内部函数

##### `normalizeBindingChannelId(raw?: string | null): string | null`
- 优先使用 `normalizeChatChannelId`
- 回退到 trim + 小写
- 空则返回 null

##### `resolveNormalizedBindingMatch(binding: AgentBinding): { agentId; accountId; channelId } | null`
- 验证 binding 和 match 为有效对象
- 解析 channelId
- accountId 为空或 `"*"` 时返回 null
- 返回规范化后的匹配参数

#### 导出函数

##### `listBindings(cfg: OpenClawConfig): AgentBinding[]`
- 返回 `cfg.bindings`（数组则返回，否则返回 `[]`）

##### `listBoundAccountIds(cfg, channelId): string[]`
- 查找指定频道下所有有效绑定的 accountId
- 去重后按字母升序排序（`.toSorted`）

##### `resolveDefaultAgentBoundAccountId(cfg, channelId): string | null`
- 查找默认 agent 在指定频道的绑定 accountId
- 返回第一个匹配的 accountId 或 null

##### `buildChannelAccountBindings(cfg): Map<string, Map<string, string[]>>`
- 构建三层映射：channelId -> agentId -> accountId[]
- 去重 accountId

##### `resolvePreferredAccountId(params: { accountIds; defaultAccountId; boundAccounts }): string`
- 若有绑定账户，返回第一个
- 否则返回 defaultAccountId

---

### 3.3 `resolve-route.ts` — 路由决策引擎

#### 导入

```typescript
import type { ChatType } from "../channels/chat-type.js";
import type { OpenClawConfig } from "../config/config.js";
import { resolveDefaultAgentId } from "../agents/agent-scope.js";
import { normalizeChatType } from "../channels/chat-type.js";
import { shouldLogVerbose } from "../globals.js";
import { logDebug } from "../logger.js";
import { listBindings } from "./bindings.js";
import {
  buildAgentMainSessionKey, buildAgentPeerSessionKey,
  DEFAULT_ACCOUNT_ID, DEFAULT_MAIN_KEY,
  normalizeAgentId, sanitizeAgentId,
} from "./session-key.js";
```

#### 导出类型

##### `RoutePeerKind` (deprecated)
```typescript
/** @deprecated Use ChatType from channels/chat-type.js */
export type RoutePeerKind = ChatType;
```

##### `RoutePeer`
```typescript
export type RoutePeer = {
  kind: ChatType;
  id: string;
};
```

##### `ResolveAgentRouteInput`
```typescript
export type ResolveAgentRouteInput = {
  cfg: OpenClawConfig;
  channel: string;
  accountId?: string | null;
  peer?: RoutePeer | null;
  parentPeer?: RoutePeer | null;       // 线程父级对等方
  guildId?: string | null;             // Discord guild
  teamId?: string | null;              // Slack team
  memberRoleIds?: string[];            // Discord 角色 ID 列表
};
```

##### `ResolvedAgentRoute`
```typescript
export type ResolvedAgentRoute = {
  agentId: string;
  channel: string;
  accountId: string;
  sessionKey: string;
  mainSessionKey: string;
  matchedBy:
    | "binding.peer"
    | "binding.peer.parent"
    | "binding.guild+roles"
    | "binding.guild"
    | "binding.team"
    | "binding.account"
    | "binding.channel"
    | "default";
};
```

#### 重导出

```typescript
export { DEFAULT_ACCOUNT_ID, DEFAULT_AGENT_ID } from "./session-key.js";
```

#### 内部类型

##### `NormalizedPeerConstraint`
```typescript
type NormalizedPeerConstraint =
  | { state: "none" }
  | { state: "invalid" }
  | { state: "valid"; kind: ChatType; id: string };
```

##### `NormalizedBindingMatch`
```typescript
type NormalizedBindingMatch = {
  accountPattern: string;
  peer: NormalizedPeerConstraint;
  guildId: string | null;
  teamId: string | null;
  roles: string[] | null;
};
```

##### `EvaluatedBinding`
```typescript
type EvaluatedBinding = {
  binding: ReturnType<typeof listBindings>[number];
  match: NormalizedBindingMatch;
};
```

##### `BindingScope`
```typescript
type BindingScope = {
  peer: RoutePeer | null;
  guildId: string;
  teamId: string;
  memberRoleIds: Set<string>;
};
```

#### 内部常量/缓存

```typescript
const evaluatedBindingsCacheByCfg = new WeakMap<OpenClawConfig, EvaluatedBindingsCache>();
const MAX_EVALUATED_BINDINGS_CACHE_KEYS = 2000;
```

- `EvaluatedBindingsCache`: `{ bindingsRef; byChannelAccount: Map<string, EvaluatedBinding[]> }`
- 缓存键格式：`"${channel}\t${accountId}"`
- 缓存失效：当 `cfg.bindings` 引用变化时重建
- 缓存大小限制：超过 2000 键时全部清除后重建

#### 内部函数

##### `normalizeToken(value): string`
##### `normalizeId(value): string` — 支持 string/number/bigint

##### `normalizeAccountId(value): string`
- 默认 `DEFAULT_ACCOUNT_ID`

##### `matchesAccountId(match, actual): boolean`
- 空值仅匹配 `DEFAULT_ACCOUNT_ID`
- `"*"` 匹配所有
- 其他精确匹配

##### `listAgents(cfg): AgentConfig[]`

##### `pickFirstExistingAgentId(cfg, agentId): string`
- 在配置的 agents 列表中查找匹配的 agent
- 未找到则回退到默认 agent

##### `matchesChannel(match, channel): boolean`

##### `normalizePeerConstraint(peer): NormalizedPeerConstraint`
- 规范化 kind 和 id，缺失则标记为 invalid

##### `normalizeBindingMatch(match): NormalizedBindingMatch`
- 规范化所有绑定匹配字段

##### `hasGuildConstraint/hasTeamConstraint/hasRolesConstraint(match): boolean`

##### `matchesBindingScope(match, scope): boolean`
- peer invalid -> false
- peer valid -> 必须完全匹配 kind + id
- guildId 约束 -> 必须匹配
- teamId 约束 -> 必须匹配
- roles 约束 -> 成员角色中**任一**匹配即可

##### `getEvaluatedBindingsForChannelAccount(cfg, channel, accountId): EvaluatedBinding[]`
- 带缓存的绑定评估
- 过滤匹配 channel 和 accountId 的绑定

##### `buildAgentSessionKey(params): string`（导出）
- 包装 `buildAgentPeerSessionKey`

#### 导出函数

##### `resolveAgentRoute(input: ResolveAgentRouteInput): ResolvedAgentRoute`
- 规范化所有输入参数
- 获取匹配的绑定列表（带缓存）
- 获取 `dmScope` 和 `identityLinks` 从配置

- **7 级路由优先级**（从高到低）：

  | 层级 | matchedBy | 启用条件 | scope peer | 额外条件 |
  |------|-----------|----------|------------|----------|
  | 1 | `binding.peer` | peer 存在 | peer | peer 约束为 valid |
  | 2 | `binding.peer.parent` | parentPeer 存在且有 id | parentPeer | peer 约束为 valid |
  | 3 | `binding.guild+roles` | guildId + memberRoleIds | peer | guild + roles 约束 |
  | 4 | `binding.guild` | guildId 存在 | peer | guild 约束（无 roles） |
  | 5 | `binding.team` | teamId 存在 | peer | team 约束 |
  | 6 | `binding.account` | 始终 | peer | accountPattern 非 `"*"` |
  | 7 | `binding.channel` | 始终 | peer | accountPattern 为 `"*"` |

- 每个层级从绑定列表中查找第一个匹配的条目
- 找到匹配后通过 `choose` 函数构建结果：
  - 解析 agentId（在 agents 列表中查找存在的 agent）
  - 构建 sessionKey（小写化）
  - 构建 mainSessionKey
- 无匹配时使用默认 agent，matchedBy 为 `"default"`
- verbose 日志记录路由决策过程

---

## 4. 文件间依赖关系

```
session-key.ts
  ├── 依赖 ../channels/chat-type.js (ChatType)
  └── 重导出 ../sessions/session-key-utils.js (多个函数和类型)

bindings.ts
  ├── 依赖 ../config/config.js (OpenClawConfig)
  ├── 依赖 ../config/types.agents.js (AgentBinding)
  ├── 依赖 ../agents/agent-scope.js (resolveDefaultAgentId)
  ├── 依赖 ../channels/registry.js (normalizeChatChannelId)
  └── 依赖 ./session-key.js (normalizeAccountId, normalizeAgentId)

resolve-route.ts
  ├── 依赖 ../channels/chat-type.js (ChatType, normalizeChatType)
  ├── 依赖 ../config/config.js (OpenClawConfig)
  ├── 依赖 ../agents/agent-scope.js (resolveDefaultAgentId)
  ├── 依赖 ../globals.js (shouldLogVerbose)
  ├── 依赖 ../logger.js (logDebug)
  ├── 依赖 ./bindings.js (listBindings)
  └── 依赖 ./session-key.js (多个函数和常量)
```

## 5. 整体架构

本模块实现了一个**基于绑定（bindings）的分层路由系统**：

### 会话键系统 (`session-key.ts`)
- 统一的会话键命名空间：`agent:{agentId}:{rest}`
- 支持 4 种 DM 作用域：main（共享）、per-peer、per-channel-peer、per-account-channel-peer
- 身份链接（identityLinks）支持跨平台用户统一识别
- 线程会话键通过 `:thread:` 后缀扩展
- ID 规范化：最大 64 字符，仅允许 `[a-z0-9_-]`，非法字符折叠为 `-`

### 绑定查询 (`bindings.ts`)
- 从配置中提取有效的 agent-channel-account 绑定关系
- 提供频道级和 agent 级的查询接口

### 路由引擎 (`resolve-route.ts`)
- **7 层匹配优先级**确保最具体的绑定优先：peer > parentPeer > guild+roles > guild > team > account > channel
- **绑定评估缓存**使用 WeakMap（以 cfg 为键）+ 内部 Map（以 channel+account 为键），2000 键上限
- 支持 Discord 特有的 guild/roles 路由和 Slack 的 team 路由
- 线程继承：parentPeer 允许子线程继承父级的路由绑定
- 所有 session key 最终小写化
