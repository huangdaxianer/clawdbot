# WhatsApp 模块 - 产品需求文档 (PRD)

## 1. 模块概述与用途

`whatsapp` 模块负责处理 WhatsApp 平台相关的消息目标地址规范化和出站目标解析。其核心功能包括：

- WhatsApp 目标地址（电话号码、群组 JID、用户 JID、LID）的识别与规范化
- 出站消息目标的权限校验（基于允许列表和通配符）
- 支持多种 WhatsApp 标识符格式的解析（E.164 电话号码、`@s.whatsapp.net` 用户 JID、`@lid` LID、`@g.us` 群组 JID）

## 2. 目录结构

```
src/whatsapp/
├── normalize.ts                   # WhatsApp 目标地址规范化
├── normalize.test.ts              # 规范化测试（不在本文档范围内）
└── resolve-outbound-target.ts     # 出站目标解析与权限校验
```

## 3. 文件详细说明

---

### 3.1 `normalize.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `../utils.js` | `normalizeE164` |

#### 模块级常量（私有）

| 常量名 | 值 | 用途 |
|--------|----|------|
| `WHATSAPP_USER_JID_RE` | `/^(\d+)(?::\d+)?@s\.whatsapp\.net$/i` | 匹配 WhatsApp 用户 JID，例如 `"41796666864:0@s.whatsapp.net"` |
| `WHATSAPP_LID_RE` | `/^(\d+)@lid$/i` | 匹配 WhatsApp LID（Linked ID），例如 `"123@lid"` |

#### 私有函数

##### `stripWhatsAppTargetPrefixes(value: string): string`

**用途**：递归去除字符串前的 `"whatsapp:"` 前缀。

**逻辑**：
1. `value.trim()` 去除首尾空白
2. 无限循环：
   - 保存当前值 `before`
   - 执行 `candidate.replace(/^whatsapp:/i, "").trim()`
   - 若替换前后相同则退出循环返回结果
   - 否则继续循环（处理多层前缀如 `"whatsapp:whatsapp:+1234"`）

#### 导出函数

##### `isWhatsAppGroupJid(value: string): boolean`

**用途**：判断给定值是否为 WhatsApp 群组 JID。

**逻辑**：
1. 调用 `stripWhatsAppTargetPrefixes(value)` 去除前缀
2. 检查小写化后是否以 `"@g.us"` 结尾
3. 提取 `@g.us` 前的 `localPart`（长度 = `candidate.length - "@g.us".length`）
4. 验证 `localPart` 非空且不包含 `"@"`
5. 验证 `localPart` 匹配正则 `/^[0-9]+(-[0-9]+)*$/`（纯数字加连字符格式，如 `"120363-xxx"`)

##### `isWhatsAppUserTarget(value: string): boolean`

**用途**：判断给定值是否为 WhatsApp 用户目标（用户 JID 或 LID）。

**逻辑**：
1. 调用 `stripWhatsAppTargetPrefixes(value)` 去除前缀
2. 返回 `WHATSAPP_USER_JID_RE.test(candidate) || WHATSAPP_LID_RE.test(candidate)`

##### `normalizeWhatsAppTarget(value: string): string | null`

**用途**：将各种格式的 WhatsApp 目标地址规范化为标准格式。

**参数**：`value: string` - 原始目标地址字符串

**返回值**：规范化后的地址字符串，或 `null`（无效输入）

**逻辑**：
1. 调用 `stripWhatsAppTargetPrefixes(value)` 去除前缀
2. 若结果为空 → 返回 `null`
3. **群组 JID 处理**：若 `isWhatsAppGroupJid(candidate)` 为 true：
   - 提取 `localPart`（`@g.us` 前的部分）
   - 返回 `"${localPart}@g.us"`（统一小写格式）
4. **用户 JID 处理**：若 `isWhatsAppUserTarget(candidate)` 为 true：
   - 调用 `extractUserJidPhone(candidate)` 提取电话号码
   - 若提取失败返回 `null`
   - 调用 `normalizeE164(phone)` 规范化为 E.164 格式
   - 若结果长度 > 1 则返回，否则返回 `null`
5. **未知 JID 格式**：若 `candidate` 包含 `"@"` → 返回 `null`（防止将 JID 误解为电话号码）
6. **普通电话号码**：调用 `normalizeE164(candidate)`，长度 > 1 则返回，否则返回 `null`

#### 私有函数

##### `extractUserJidPhone(jid: string): string | null`

**用途**：从 WhatsApp 用户 JID 中提取电话号码部分。

**示例**：
- `"41796666864:0@s.whatsapp.net"` → `"41796666864"`
- `"123456@lid"` → `"123456"`

**逻辑**：
1. 尝试匹配 `WHATSAPP_USER_JID_RE`，成功则返回捕获组 `[1]`
2. 尝试匹配 `WHATSAPP_LID_RE`，成功则返回捕获组 `[1]`
3. 均不匹配返回 `null`

---

### 3.2 `resolve-outbound-target.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `../infra/outbound/target-errors.js` | `missingTargetError` |
| `./normalize.js` | `isWhatsAppGroupJid`, `normalizeWhatsAppTarget` |

#### 导出类型

##### `WhatsAppOutboundTargetResolution`

```typescript
export type WhatsAppOutboundTargetResolution =
  | { ok: true; to: string }
  | { ok: false; error: Error };
```

#### 导出函数

##### `resolveWhatsAppOutboundTarget(params: { to: string | null | undefined; allowFrom: Array<string | number> | null | undefined; mode: string | null | undefined; }): WhatsAppOutboundTargetResolution`

**用途**：解析并验证 WhatsApp 出站消息的目标地址，根据允许列表和发送模式进行权限控制。

**参数**：
- `to: string | null | undefined` - 目标地址
- `allowFrom: Array<string | number> | null | undefined` - 允许的发送源列表
- `mode: string | null | undefined` - 发送模式（如 `"implicit"`、`"heartbeat"`）

**逻辑**：

1. **预处理**：
   - `trimmed = params.to?.trim() ?? ""`
   - `allowListRaw`：将 `params.allowFrom`（默认 `[]`）的每项转为字符串并 `trim()`、过滤空值
   - `hasWildcard`：检查是否包含 `"*"`
   - `allowList`：过滤掉 `"*"`，对每项调用 `normalizeWhatsAppTarget()`，过滤 `null` 结果

2. **目标地址存在时** (`trimmed` 非空)：
   - 调用 `normalizeWhatsAppTarget(trimmed)` 规范化
   - 若规范化结果为 `null` → 返回错误 `missingTargetError("WhatsApp", "<E.164|group JID>")`
   - 若为群组 JID（`isWhatsAppGroupJid`）→ 直接返回 `{ ok: true, to: normalizedTo }`（群组不受允许列表限制）
   - 若 `mode` 为 `"implicit"` 或 `"heartbeat"`：
     - 通配符 `hasWildcard` 为 true 或 `allowList` 为空 → 允许
     - `allowList.includes(normalizedTo)` → 允许
     - 否则 → 返回错误
   - 其他模式 → 直接返回 `{ ok: true, to: normalizedTo }`

3. **目标地址不存在时** → 返回错误 `missingTargetError("WhatsApp", "<E.164|group JID>")`

**错误格式**：所有错误均通过 `missingTargetError("WhatsApp", "<E.164|group JID>")` 生成

## 4. 文件间依赖关系

```
normalize.ts
  └─→ ../utils.js (normalizeE164)

resolve-outbound-target.ts
  ├─→ ../infra/outbound/target-errors.js (missingTargetError)
  └─→ ./normalize.js (isWhatsAppGroupJid, normalizeWhatsAppTarget)
```

## 5. 整体架构

WhatsApp 模块采用两层结构：

1. **基础层：地址规范化** (`normalize.ts`)
   - 处理多种 WhatsApp 标识符格式
   - 支持的格式：
     - E.164 电话号码（如 `"+41796666864"`）
     - 带前缀的电话号码（如 `"whatsapp:+41796666864"`）
     - 用户 JID（如 `"41796666864:0@s.whatsapp.net"`）
     - LID（如 `"123456@lid"`）
     - 群组 JID（如 `"120363-xxx@g.us"`）
   - 所有电话号码最终通过 `normalizeE164` 规范化

2. **业务层：出站目标解析** (`resolve-outbound-target.ts`)
   - 在地址规范化基础上增加权限控制
   - 群组 JID 始终放行
   - `implicit`/`heartbeat` 模式下对个人号码进行白名单校验
   - 支持通配符 `"*"` 放行所有目标
   - 空允许列表等效于通配符
