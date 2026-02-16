# Providers 模块 PRD

## 1. 模块概述与用途

`providers` 模块包含与第三方 AI 服务提供商集成相关的认证、令牌管理和模型定义逻辑。目前涵盖三个提供商：GitHub Copilot（OAuth 设备流登录 + API 令牌交换 + 模型定义）、Qwen Portal（OAuth 令牌刷新）以及 Google（测试辅助工具）。

## 2. 目录结构

```
src/providers/
  github-copilot-auth.ts             # GitHub Copilot 设备流 OAuth 登录
  github-copilot-token.ts            # GitHub Copilot API 令牌交换与缓存
  github-copilot-models.ts           # GitHub Copilot 默认模型定义
  qwen-portal-oauth.ts               # Qwen Portal OAuth 令牌刷新
  google-shared.test-helpers.ts      # Google 提供商测试辅助函数
```

## 3. 各文件详细说明

---

### 3.1 `github-copilot-auth.ts` — GitHub Copilot OAuth 登录

#### 导入

```typescript
import { intro, note, outro, spinner } from "@clack/prompts";
import type { RuntimeEnv } from "../runtime.js";
import { ensureAuthProfileStore, upsertAuthProfile } from "../agents/auth-profiles.js";
import { updateConfig } from "../commands/models/shared.js";
import { applyAuthProfileConfig } from "../commands/onboard-auth.js";
import { logConfigUpdated } from "../config/logging.js";
import { stylePromptTitle } from "../terminal/prompt-style.js";
```

#### 内部常量

```typescript
const CLIENT_ID = "Iv1.b507a08c87ecfe98";
const DEVICE_CODE_URL = "https://github.com/login/device/code";
const ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token";
```

#### 内部类型

##### `DeviceCodeResponse`
```typescript
type DeviceCodeResponse = {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
};
```

##### `DeviceTokenResponse`
```typescript
type DeviceTokenResponse =
  | { access_token: string; token_type: string; scope?: string }
  | { error: string; error_description?: string; error_uri?: string };
```

#### 内部函数

##### `parseJsonResponse<T>(value: unknown): T`
- 验证 value 为非空对象，否则抛出 `"Unexpected response from GitHub"`

##### `requestDeviceCode(params: { scope: string }): Promise<DeviceCodeResponse>`
- POST 到 `DEVICE_CODE_URL`
- Content-Type: `"application/x-www-form-urlencoded"`
- Accept: `"application/json"`
- 请求体包含 `client_id` 和 `scope`
- 验证响应包含 `device_code`、`user_code`、`verification_uri`
- 失败时抛出 `"GitHub device code failed: HTTP ${status}"`

##### `pollForAccessToken(params: { deviceCode: string; intervalMs: number; expiresAt: number }): Promise<string>`
- 循环 POST 到 `ACCESS_TOKEN_URL`
- grant_type: `"urn:ietf:params:oauth:grant-type:device_code"`
- 错误处理策略：
  - `"authorization_pending"`: 等待 `intervalMs` 后重试
  - `"slow_down"`: 等待 `intervalMs + 2000` 后重试
  - `"expired_token"`: 抛出 `"GitHub device code expired; run login again"`
  - `"access_denied"`: 抛出 `"GitHub login cancelled"`
  - 其他: 抛出 `"GitHub device flow error: ${err}"`
- 超时（`Date.now() >= expiresAt`）抛出 `"GitHub device code expired; run login again"`

#### 导出函数

##### `githubCopilotLoginCommand(opts: { profileId?: string; yes?: boolean }, runtime: RuntimeEnv)`
- 要求交互式 TTY（`process.stdin.isTTY`），否则抛出 `"github-copilot login requires an interactive TTY."`
- 流程：
  1. 显示 intro（标题 `"GitHub Copilot login"`）
  2. 确定 profileId：默认 `"github-copilot:github"`
  3. 创建 auth profile store（`allowKeychainPrompt: false`）
  4. 若 profile 已存在且非 `yes` 模式，显示覆盖提示
  5. 请求设备码（scope: `"read:user"`）
  6. 显示验证 URI 和用户码
  7. 计算过期时间和轮询间隔（最小 1000ms）：`Math.max(1000, device.interval * 1000)`
  8. 轮询直到获取 access token
  9. 存储凭证：type `"token"`、provider `"github-copilot"`
  10. 更新配置：调用 `applyAuthProfileConfig`，provider `"github-copilot"`，mode `"token"`
  11. 日志输出和 outro

---

### 3.2 `github-copilot-token.ts` — Copilot API 令牌交换

#### 导入

```typescript
import path from "node:path";
import { resolveStateDir } from "../config/paths.js";
import { loadJsonFile, saveJsonFile } from "../infra/json-file.js";
```

#### 内部常量

```typescript
const COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token";
```

#### 导出常量

```typescript
export const DEFAULT_COPILOT_API_BASE_URL = "https://api.individual.githubcopilot.com";
```

#### 导出类型

##### `CachedCopilotToken`
```typescript
export type CachedCopilotToken = {
  token: string;
  expiresAt: number;    // 毫秒时间戳
  updatedAt: number;    // 毫秒时间戳
};
```

#### 内部函数

##### `resolveCopilotTokenCachePath(env = process.env)`
- 返回 `path.join(resolveStateDir(env), "credentials", "github-copilot.token.json")`

##### `isTokenUsable(cache: CachedCopilotToken, now = Date.now()): boolean`
- 安全余量检查：`cache.expiresAt - now > 5 * 60 * 1000`（5 分钟）

##### `parseCopilotTokenResponse(value: unknown): { token: string; expiresAt: number }`
- 验证 value 为非空对象
- 提取 `token` 和 `expires_at`
- token 必须为非空字符串
- expires_at 处理：
  - 数字：若 `> 10_000_000_000` 视为毫秒，否则乘以 1000 转毫秒
  - 字符串：`parseInt` 后同上
  - 缺失：抛出 `"Copilot token response missing expires_at"`

#### 导出函数

##### `deriveCopilotApiBaseUrlFromToken(token: string): string | null`
- token 为 GitHub Copilot 返回的分号分隔键值对
- 正则提取 `proxy-ep` 值：`/(?:^|;)\s*proxy-ep=([^;\s]+)/i`
- 替换 host 前缀：移除 `https?://`，然后 `proxy.` -> `api.`
- 返回 `https://${host}` 或 null

##### `resolveCopilotApiToken(params: { githubToken: string; env?; fetchImpl?; cachePath?; loadJsonFileImpl?; saveJsonFileImpl? }): Promise<{ token: string; expiresAt: number; source: string; baseUrl: string }>`
- **缓存检查**：
  - 加载缓存文件
  - 若 token 和 expiresAt 有效且 `isTokenUsable`，返回缓存结果
  - source: `"cache:${cachePath}"`
  - baseUrl: `deriveCopilotApiBaseUrlFromToken(cached.token) ?? DEFAULT_COPILOT_API_BASE_URL`
- **令牌交换**：
  - GET 请求到 `COPILOT_TOKEN_URL`
  - Authorization: `"Bearer ${params.githubToken}"`
  - Accept: `"application/json"`
  - 失败抛出 `"Copilot token exchange failed: HTTP ${status}"`
  - 解析响应，构建缓存 payload（`updatedAt: Date.now()`）
  - 保存缓存文件
  - source: `"fetched:${COPILOT_TOKEN_URL}"`

---

### 3.3 `github-copilot-models.ts` — Copilot 模型定义

#### 导入

```typescript
import type { ModelDefinitionConfig } from "../config/types.js";
```

#### 内部常量

```typescript
const DEFAULT_CONTEXT_WINDOW = 128_000;
const DEFAULT_MAX_TOKENS = 8192;

const DEFAULT_MODEL_IDS = [
  "gpt-4o",
  "gpt-4.1",
  "gpt-4.1-mini",
  "gpt-4.1-nano",
  "o1",
  "o1-mini",
  "o3-mini",
] as const;
```

#### 导出函数

##### `getDefaultCopilotModelIds(): string[]`
- 返回 `[...DEFAULT_MODEL_IDS]`（浅拷贝）

##### `buildCopilotModelDefinition(modelId: string): ModelDefinitionConfig`
- trim modelId，空则抛出 `"Model id required"`
- 返回：
  ```typescript
  {
    id,
    name: id,
    api: "openai-responses",
    reasoning: false,
    input: ["text", "image"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: DEFAULT_CONTEXT_WINDOW,    // 128_000
    maxTokens: DEFAULT_MAX_TOKENS,            // 8192
  }
  ```

---

### 3.4 `qwen-portal-oauth.ts` — Qwen Portal OAuth 刷新

#### 导入

```typescript
import type { OAuthCredentials } from "@mariozechner/pi-ai";
import { formatCliCommand } from "../cli/command-format.js";
```

#### 内部常量

```typescript
const QWEN_OAUTH_BASE_URL = "https://chat.qwen.ai";
const QWEN_OAUTH_TOKEN_ENDPOINT = `${QWEN_OAUTH_BASE_URL}/api/v1/oauth2/token`;
const QWEN_OAUTH_CLIENT_ID = "f0304373b74a44d2b584a3fb70ca9e56";
```

#### 导出函数

##### `refreshQwenPortalCredentials(credentials: OAuthCredentials): Promise<OAuthCredentials>`
- 验证 `credentials.refresh` 非空，否则抛出 `"Qwen OAuth refresh token missing; re-authenticate."`
- POST 到 token 端点：
  - Content-Type: `"application/x-www-form-urlencoded"`
  - Accept: `"application/json"`
  - 请求体：`grant_type: "refresh_token"`, `refresh_token`, `client_id`
- HTTP 400 时抛出特殊消息，提示重新认证命令：`openclaw models auth login --provider qwen-portal`
- 其他错误：`"Qwen OAuth refresh failed: ${text || statusText}"`
- 验证响应包含 `access_token` 和 `expires_in`
- 返回更新后的凭证：
  - `access`: 新的 access_token
  - `refresh`: 新的 refresh_token 或保持原值
  - `expires`: `Date.now() + expires_in * 1000`

---

### 3.5 `google-shared.test-helpers.ts` — Google 测试辅助

#### 导入

```typescript
import type { Model } from "@mariozechner/pi-ai/dist/types.js";
import { expect } from "vitest";
```

#### 导出函数

##### `asRecord(value: unknown): Record<string, unknown>`
- 使用 vitest 断言 value 为 truthy、object、非数组
- 强制转换返回

##### `getFirstToolParameters(converted: ConvertedTools): Record<string, unknown>`
- 从第一个 tool 的第一个 functionDeclaration 中提取 parameters
- 优先使用 `parametersJsonSchema`，否则使用 `parameters`

##### `makeModel(id: string): Model<"google-generative-ai">`
- 返回固定的模型定义对象：
  - api: `"google-generative-ai"`, provider: `"google"`
  - baseUrl: `"https://example.invalid"`
  - reasoning: false, input: `["text"]`
  - cost 全部为 0, contextWindow: 1, maxTokens: 1

##### `makeGeminiCliModel(id: string): Model<"google-gemini-cli">`
- 类似 `makeModel`，api 为 `"google-gemini-cli"`，provider 为 `"google-gemini-cli"`

##### `makeZeroUsage()` (内部)
- 返回全零 usage 对象：`{ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } }`

##### `makeGoogleAssistantMessage(model: string, content: unknown)`
- 返回 assistant message 对象：
  - role: `"assistant"`, api: `"google-generative-ai"`, provider: `"google"`
  - usage: `makeZeroUsage()`, stopReason: `"stop"`, timestamp: 0

##### `makeGeminiCliAssistantMessage(model: string, content: unknown)`
- 同上，api: `"google-gemini-cli"`, provider: `"google-gemini-cli"`

---

## 4. 文件间依赖关系

```
github-copilot-auth.ts
  ├── 依赖 @clack/prompts (UI 组件)
  ├── 依赖 ../runtime.js
  ├── 依赖 ../agents/auth-profiles.js
  ├── 依赖 ../commands/models/shared.js
  ├── 依赖 ../commands/onboard-auth.js
  ├── 依赖 ../config/logging.js
  └── 依赖 ../terminal/prompt-style.js

github-copilot-token.ts
  ├── 依赖 node:path
  ├── 依赖 ../config/paths.js
  └── 依赖 ../infra/json-file.js

github-copilot-models.ts
  └── 依赖 ../config/types.js

qwen-portal-oauth.ts
  ├── 依赖 @mariozechner/pi-ai (类型)
  └── 依赖 ../cli/command-format.js

google-shared.test-helpers.ts
  ├── 依赖 @mariozechner/pi-ai/dist/types.js (类型)
  └── 依赖 vitest (测试框架)
```

**模块内无文件间依赖** — 每个文件独立负责一个提供商的特定功能。

## 5. 整体架构

本模块采用**按提供商分离**的平面架构，无模块内依赖。各文件职责明确：

### GitHub Copilot 集成（三文件）
1. **认证** (`github-copilot-auth.ts`): 实现 GitHub Device Flow OAuth，交互式终端流程
2. **令牌交换** (`github-copilot-token.ts`): GitHub PAT -> Copilot API 令牌的交换和磁盘缓存，智能解析 Copilot 令牌中的 proxy-ep 字段来推导 API base URL
3. **模型定义** (`github-copilot-models.ts`): 预定义的 7 个 GPT/O 系列模型，统一使用 `openai-responses` API 格式

### Qwen Portal 集成
- **OAuth 刷新** (`qwen-portal-oauth.ts`): 标准 OAuth2 refresh_token 流程，固定 client_id

### Google 测试工具
- **测试辅助** (`google-shared.test-helpers.ts`): 为 Google 相关测试提供工厂函数，支持 generative-ai 和 gemini-cli 两种 API 模式

关键设计决策：
- Copilot 令牌缓存使用 5 分钟安全余量，避免边界过期
- Copilot 令牌中的时间戳智能判断秒/毫秒（阈值 `10_000_000_000`）
- 所有 OAuth 凭证存储在 state 目录的 credentials 子目录中
- 模型成本全部设为 0（Copilot 通过订阅付费，非按用量计费）
