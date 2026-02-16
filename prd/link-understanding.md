# Link Understanding 模块 PRD

## 1. 模块概述与用途

`link-understanding` 模块负责从用户消息中检测 URL 链接，通过可配置的外部 CLI 工具获取链接内容摘要，并将结果合并回消息上下文中。该模块实现了完整的链接理解管线：检测 -> 执行 -> 格式化 -> 应用。

## 2. 目录结构

```
src/link-understanding/
  index.ts        # 模块公开 API 的统一导出入口
  detect.ts       # 从消息文本中提取 URL
  defaults.ts     # 默认常量定义
  runner.ts       # 链接理解核心执行引擎
  format.ts       # 输出格式化
  apply.ts        # 将链接理解结果应用到消息上下文
```

## 3. 各文件详细说明

---

### 3.1 `index.ts` — 统一导出入口

```typescript
export { applyLinkUnderstanding } from "./apply.js";
export { extractLinksFromMessage } from "./detect.js";
export { formatLinkUnderstandingBody } from "./format.js";
export { runLinkUnderstanding } from "./runner.js";
```

---

### 3.2 `defaults.ts` — 默认常量

```typescript
export const DEFAULT_LINK_TIMEOUT_SECONDS = 30;
export const DEFAULT_MAX_LINKS = 3;
```

---

### 3.3 `detect.ts` — URL 检测与提取

#### 导入

```typescript
import { isBlockedHostname, isPrivateIpAddress } from "../infra/net/ssrf.js";
import { DEFAULT_MAX_LINKS } from "./defaults.js";
```

#### 内部常量

```typescript
const MARKDOWN_LINK_RE = /\[[^\]]*]\((https?:\/\/\S+?)\)/gi;  // Markdown 链接语法
const BARE_LINK_RE = /https?:\/\/\S+/gi;                       // 裸 URL
```

#### 内部函数

##### `stripMarkdownLinks(message: string): string`
- 使用 `MARKDOWN_LINK_RE` 将 Markdown 链接替换为空格 `" "`
- 目的：仅保留裸 URL，排除 Markdown 链接语法中的 URL

##### `resolveMaxLinks(value?: number): number`
- 若 value 为有效正有限数字，返回 `Math.floor(value)`
- 否则返回 `DEFAULT_MAX_LINKS`（3）

##### `isAllowedUrl(raw: string): boolean`
- 使用 `new URL(raw)` 解析
- 仅允许 `http:` 和 `https:` 协议
- 调用 `isBlockedHost` 检查主机名
- 解析失败返回 `false`

##### `isBlockedHost(hostname: string): boolean`
- 规范化：`hostname.trim().toLowerCase()`
- 阻止条件（任一为真即阻止）：
  - `normalized === "localhost.localdomain"`
  - `isBlockedHostname(normalized)` — 来自 SSRF 防护库
  - `isPrivateIpAddress(normalized)` — 私有 IP 地址检测

#### 导出函数

##### `extractLinksFromMessage(message: string, opts?: { maxLinks?: number }): string[]`
- 对输入 message 执行 `trim()`，空则返回 `[]`
- 解析最大链接数 `resolveMaxLinks(opts?.maxLinks)`
- 先 `stripMarkdownLinks` 去除 Markdown 链接
- 使用 `BARE_LINK_RE` 的 `matchAll` 遍历所有匹配
- 对每个 URL：
  - trim 处理
  - 调用 `isAllowedUrl` 安全检查
  - 使用 `Set<string>` 去重
  - 达到 maxLinks 上限时停止
- 返回去重后的 URL 列表

---

### 3.4 `format.ts` — 输出格式化

#### 导出函数

##### `formatLinkUnderstandingBody(params: { body?: string; outputs: string[] }): string`
- 对 outputs 数组执行 trim 并过滤空字符串
- 若无有效 output，返回 `params.body ?? ""`
- 若 base（`params.body` 的 trim 结果）为空，返回 `outputs.join("\n")`
- 否则返回 `` `${base}\n\n${outputs.join("\n")}` ``

---

### 3.5 `runner.ts` — 链接理解执行引擎

#### 导入

```typescript
import type { MsgContext } from "../auto-reply/templating.js";
import type { OpenClawConfig } from "../config/config.js";
import type { LinkModelConfig, LinkToolsConfig } from "../config/types.tools.js";
import { applyTemplate } from "../auto-reply/templating.js";
import { logVerbose, shouldLogVerbose } from "../globals.js";
import { CLI_OUTPUT_MAX_BUFFER } from "../media-understanding/defaults.js";
import { resolveTimeoutMs } from "../media-understanding/resolve.js";
import {
  normalizeMediaUnderstandingChatType,
  resolveMediaUnderstandingScope,
} from "../media-understanding/scope.js";
import { runExec } from "../process/exec.js";
import { DEFAULT_LINK_TIMEOUT_SECONDS } from "./defaults.js";
import { extractLinksFromMessage } from "./detect.js";
```

#### 导出类型

##### `LinkUnderstandingResult`
```typescript
export type LinkUnderstandingResult = {
  urls: string[];      // 检测到的所有 URL
  outputs: string[];   // 成功获取的理解输出
};
```

#### 内部函数

##### `resolveScopeDecision(params: { config?: LinkToolsConfig; ctx: MsgContext }): "allow" | "deny"`
- 调用 `resolveMediaUnderstandingScope`：
  - `scope`: `params.config?.scope`
  - `sessionKey`: `params.ctx.SessionKey`
  - `channel`: `params.ctx.Surface ?? params.ctx.Provider`
  - `chatType`: `normalizeMediaUnderstandingChatType(params.ctx.ChatType)`

##### `resolveTimeoutMsFromConfig(params: { config?: LinkToolsConfig; entry: LinkModelConfig }): number`
- 优先使用 `entry.timeoutSeconds`，其次 `config?.timeoutSeconds`
- 通过 `resolveTimeoutMs` 转换为毫秒，默认值 `DEFAULT_LINK_TIMEOUT_SECONDS`（30）

##### `runCliEntry(params: { entry: LinkModelConfig; ctx: MsgContext; url: string; config?: LinkToolsConfig }): Promise<string | null>`
- 检查 `entry.type`，若不为 `"cli"`（默认值）则返回 null
- 获取 `entry.command.trim()`，为空返回 null
- 获取 `entry.args ?? []`
- 计算超时时间
- 构建模板上下文：`{ ...ctx, LinkUrl: url }`
- 构建 argv：第一个元素（command）不进行模板替换，后续 args 通过 `applyTemplate` 替换
- verbose 日志：`"Link understanding via CLI: ${argv.join(" ")}"`
- 调用 `runExec(argv[0], argv.slice(1), { timeoutMs, maxBuffer: CLI_OUTPUT_MAX_BUFFER })`
- 返回 `stdout.trim()` 或 null

##### `runLinkEntries(params: { entries: LinkModelConfig[]; ctx: MsgContext; url: string; config?: LinkToolsConfig }): Promise<string | null>`
- 顺序尝试每个 entry：
  - 调用 `runCliEntry`
  - 若返回非空 output，立即返回
  - 异常时记录 verbose 日志：`"Link understanding failed for ${url}: ${String(err)}"`
  - 设置 `lastError`
- 所有 entry 都失败后，verbose 日志：`"Link understanding exhausted for ${url}"`
- 返回 null

#### 导出函数

##### `runLinkUnderstanding(params: { cfg: OpenClawConfig; ctx: MsgContext; message?: string }): Promise<LinkUnderstandingResult>`
- 获取 `config = params.cfg.tools?.links`
- 若无 config 或 `config.enabled === false`，返回空结果
- 执行作用域检查 `resolveScopeDecision`，deny 时返回空结果并记录 verbose 日志 `"Link understanding disabled by scope policy."`
- 确定消息文本：`params.message ?? params.ctx.CommandBody ?? params.ctx.RawBody ?? params.ctx.Body`
- 调用 `extractLinksFromMessage` 提取链接，`maxLinks` 使用 `config?.maxLinks`
- 若无链接返回空结果
- 获取 `entries = config?.models ?? []`，若无 entries 返回 `{ urls: links, outputs: [] }`
- 对每个 URL 调用 `runLinkEntries`，收集非空 outputs
- 返回 `{ urls: links, outputs }`

---

### 3.6 `apply.ts` — 应用链接理解结果

#### 导入

```typescript
import type { MsgContext } from "../auto-reply/templating.js";
import type { OpenClawConfig } from "../config/config.js";
import { finalizeInboundContext } from "../auto-reply/reply/inbound-context.js";
import { formatLinkUnderstandingBody } from "./format.js";
import { runLinkUnderstanding } from "./runner.js";
```

#### 导出类型

##### `ApplyLinkUnderstandingResult`
```typescript
export type ApplyLinkUnderstandingResult = {
  outputs: string[];
  urls: string[];
};
```

#### 导出函数

##### `applyLinkUnderstanding(params: { ctx: MsgContext; cfg: OpenClawConfig }): Promise<ApplyLinkUnderstandingResult>`
- 调用 `runLinkUnderstanding({ cfg, ctx })`
- 若无 outputs，直接返回结果
- 将 outputs 追加到 `ctx.LinkUnderstanding` 数组
- 使用 `formatLinkUnderstandingBody` 更新 `ctx.Body`
- 调用 `finalizeInboundContext(ctx, { forceBodyForAgent: true, forceBodyForCommands: true })`
- 返回结果

---

## 4. 文件间依赖关系

```
index.ts
  ├── 重导出 apply.js
  ├── 重导出 detect.js
  ├── 重导出 format.js
  └── 重导出 runner.js

detect.ts
  ├── 依赖 ../infra/net/ssrf.js (isBlockedHostname, isPrivateIpAddress)
  └── 依赖 ./defaults.js (DEFAULT_MAX_LINKS)

defaults.ts
  └── 无依赖

runner.ts
  ├── 依赖 ../auto-reply/templating.js (MsgContext, applyTemplate)
  ├── 依赖 ../config/config.js (OpenClawConfig)
  ├── 依赖 ../config/types.tools.js (LinkModelConfig, LinkToolsConfig)
  ├── 依赖 ../globals.js (logVerbose, shouldLogVerbose)
  ├── 依赖 ../media-understanding/defaults.js (CLI_OUTPUT_MAX_BUFFER)
  ├── 依赖 ../media-understanding/resolve.js (resolveTimeoutMs)
  ├── 依赖 ../media-understanding/scope.js (normalizeMediaUnderstandingChatType, resolveMediaUnderstandingScope)
  ├── 依赖 ../process/exec.js (runExec)
  ├── 依赖 ./defaults.js (DEFAULT_LINK_TIMEOUT_SECONDS)
  └── 依赖 ./detect.js (extractLinksFromMessage)

format.ts
  └── 无依赖

apply.ts
  ├── 依赖 ../auto-reply/templating.js (MsgContext)
  ├── 依赖 ../config/config.js (OpenClawConfig)
  ├── 依赖 ../auto-reply/reply/inbound-context.js (finalizeInboundContext)
  ├── 依赖 ./format.js (formatLinkUnderstandingBody)
  └── 依赖 ./runner.js (runLinkUnderstanding)
```

## 5. 整体架构

本模块采用**管线式架构**，四个阶段清晰分离：

1. **检测阶段** (`detect.ts`): 从原始消息中提取有效 URL，内置 SSRF 防护（阻止 localhost、私有 IP、受限主机名），限制最大链接数（默认 3），先移除 Markdown 链接语法再提取裸 URL
2. **执行阶段** (`runner.ts`): 作用域策略检查后，对每个 URL 按配置的 CLI 模型条目顺序尝试执行，采用 fail-over 策略（单个条目失败时尝试下一个），支持模板变量 `LinkUrl`
3. **格式化阶段** (`format.ts`): 将理解输出与原始消息体合并，格式为 `body\n\noutput1\noutput2`
4. **应用阶段** (`apply.ts`): 将结果写入消息上下文的 `LinkUnderstanding` 和 `Body` 字段，并调用 `finalizeInboundContext` 确保后续处理可用

安全设计要点：
- URL 过滤在 detect 层完成，runner 不直接访问网络（通过 CLI 工具间接访问）
- SSRF 防护覆盖 localhost、localhost.localdomain、私有 IP、受限主机名
- 超时机制确保单个链接处理不会阻塞过长（默认 30 秒）
