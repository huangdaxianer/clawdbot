# Markdown 模块 PRD

## 1. 模块概述与用途

`markdown` 模块负责将标准 Markdown 文本解析为中间表示（IR），并提供基于标记（marker）的渲染机制，用于将 Markdown 转换为不同平台所需的格式（如 WhatsApp 格式）。该模块还包括围栏代码块解析、行内代码跨度追踪、表格转换、以及 YAML frontmatter 解析等功能。

## 2. 目录结构

```
src/markdown/
  ir.ts                         # Markdown 中间表示（IR）核心解析逻辑
  render.ts                     # 基于标记的 IR 渲染引擎
  tables.ts                     # Markdown 表格转换入口
  fences.ts                     # 围栏代码块解析
  code-spans.ts                 # 行内代码 / 围栏代码跨度索引
  whatsapp.ts                   # Markdown 转 WhatsApp 格式
  frontmatter.ts                # YAML frontmatter 解析
```

## 3. 各文件详细说明

---

### 3.1 `ir.ts` — Markdown 中间表示核心

#### 导入

```typescript
import MarkdownIt from "markdown-it";
import type { MarkdownTableMode } from "../config/types.base.js";
import { chunkText } from "../auto-reply/chunk.js";
```

#### 导出类型

##### `MarkdownStyle`
```typescript
export type MarkdownStyle =
  | "bold"
  | "italic"
  | "strikethrough"
  | "code"
  | "code_block"
  | "spoiler"
  | "blockquote";
```

##### `MarkdownStyleSpan`
```typescript
export type MarkdownStyleSpan = {
  start: number;   // 文本中的起始偏移量
  end: number;     // 文本中的结束偏移量
  style: MarkdownStyle;
};
```

##### `MarkdownLinkSpan`
```typescript
export type MarkdownLinkSpan = {
  start: number;
  end: number;
  href: string;
};
```

##### `MarkdownIR`
```typescript
export type MarkdownIR = {
  text: string;                    // 纯文本内容（无标记符号）
  styles: MarkdownStyleSpan[];     // 样式跨度列表
  links: MarkdownLinkSpan[];       // 链接跨度列表
};
```

##### `MarkdownParseOptions`
```typescript
export type MarkdownParseOptions = {
  linkify?: boolean;               // 默认 true
  enableSpoilers?: boolean;        // 默认 false
  headingStyle?: "none" | "bold";  // 默认 "none"
  blockquotePrefix?: string;       // 默认 ""
  autolink?: boolean;              // 默认 undefined (不禁用)
  tableMode?: MarkdownTableMode;   // "off" | "bullets" | "code"，默认 "off"
};
```

#### 内部类型

- `ListState`: `{ type: "bullet" | "ordered"; index: number }`
- `LinkState`: `{ href: string; labelStart: number }`
- `RenderEnv`: `{ listStack: ListState[] }`
- `MarkdownToken`: `{ type: string; content?: string; children?: MarkdownToken[]; attrs?: [string, string][]; attrGet?: (name: string) => string | null }`
- `OpenStyle`: `{ style: MarkdownStyle; start: number }`
- `RenderTarget`: `{ text: string; styles: MarkdownStyleSpan[]; openStyles: OpenStyle[]; links: MarkdownLinkSpan[]; linkStack: LinkState[] }`
- `TableCell`: `{ text: string; styles: MarkdownStyleSpan[]; links: MarkdownLinkSpan[] }`
- `TableState`: `{ headers: TableCell[]; rows: TableCell[][]; currentRow: TableCell[]; currentCell: RenderTarget | null; inHeader: boolean }`
- `RenderState`: 继承 `RenderTarget`，额外字段 `env: RenderEnv; headingStyle: "none" | "bold"; blockquotePrefix: string; enableSpoilers: boolean; tableMode: MarkdownTableMode; table: TableState | null; hasTables: boolean`

#### 内部函数

##### `createMarkdownIt(options: MarkdownParseOptions): MarkdownIt`
- 创建 MarkdownIt 实例：`html: false`, `linkify: options.linkify ?? true`, `breaks: false`, `typographer: false`
- 始终启用 `"strikethrough"`
- 当 `tableMode` 存在且不为 `"off"` 时启用 `"table"`，否则禁用
- 当 `autolink === false` 时禁用 `"autolink"`

##### `getAttr(token: MarkdownToken, name: string): string | null`
- 优先使用 `token.attrGet(name)`，否则遍历 `token.attrs` 查找

##### `createTextToken(base: MarkdownToken, content: string): MarkdownToken`
- 返回 `{ ...base, type: "text", content, children: undefined }`

##### `applySpoilerTokens(tokens: MarkdownToken[]): void`
- 遍历 tokens，如果有 children，则调用 `injectSpoilersIntoInline`

##### `injectSpoilersIntoInline(tokens: MarkdownToken[]): MarkdownToken[]`
- 在文本 token 中查找 `"||"` 分隔符
- 交替插入 `spoiler_open` 和 `spoiler_close` 类型的 token
- 使用状态 `{ spoilerOpen: false }` 追踪开关

##### `initRenderTarget(): RenderTarget`
- 返回 `{ text: "", styles: [], openStyles: [], links: [], linkStack: [] }`

##### `resolveRenderTarget(state: RenderState): RenderTarget`
- 如果在表格单元格内 (`state.table?.currentCell`)，返回 currentCell，否则返回 state 本身

##### `appendText(state: RenderState, value: string)`
- 追加文本到当前渲染目标

##### `openStyle(state: RenderState, style: MarkdownStyle)`
- 在当前目标的 `openStyles` 中记录 `{ style, start: target.text.length }`

##### `closeStyle(state: RenderState, style: MarkdownStyle)`
- 从 `openStyles` 尾部向前查找匹配 style，移除并创建 `MarkdownStyleSpan`
- 仅当 `end > start` 时推入 styles

##### `appendParagraphSeparator(state: RenderState)`
- 在列表中（`listStack.length > 0`）或表格中不添加分隔符
- 否则追加 `"\n\n"`

##### `appendListPrefix(state: RenderState)`
- 获取列表栈顶元素，递增 index
- 缩进 = `"  ".repeat(Math.max(0, stack.length - 1))`
- 有序列表前缀 = `${top.index}. `，无序列表前缀 = `"• "`

##### `renderInlineCode(state: RenderState, content: string)`
- 追加内容文本并创建 `code` 样式跨度

##### `renderCodeBlock(state: RenderState, content: string)`
- 若内容不以 `"\n"` 结尾则追加 `"\n"`
- 创建 `code_block` 样式跨度
- 若不在列表中（`listStack.length === 0`），额外追加 `"\n"`

##### `handleLinkClose(state: RenderState)`
- 从链接栈弹出，若 href 非空且 end > start，创建 `MarkdownLinkSpan`

##### `initTableState(): TableState`
- 返回 `{ headers: [], rows: [], currentRow: [], currentCell: null, inHeader: false }`

##### `finishTableCell(cell: RenderTarget): TableCell`
- 调用 `closeRemainingStyles(cell)` 后返回 `{ text, styles, links }`

##### `trimCell(cell: TableCell): TableCell`
- 去除首尾空白字符（使用 `/\s/` 测试）
- 调整所有 styles 和 links 的偏移量

##### `appendCell(state: RenderState, cell: TableCell)`
- 将单元格的文本、样式和链接追加到主 state（偏移量调整）

##### `appendCellTextOnly(state: RenderState, cell: TableCell)`
- 仅追加文本，不追加样式（用于 code 模式表格，避免样式重叠）

##### `renderTableAsBullets(state: RenderState)`
- 将表格渲染为项目符号列表
- 判断 `useFirstColAsLabel`: 当 `headers.length > 1 && rows.length > 0` 时为 true
- 带标签模式：第一列作为加粗标题，其余列格式为 `"• header: value\n"`
- 无标签模式：每个值格式为 `"• header: value\n"`
- 无头部的列使用 `"Column ${i}: "` 前缀

##### `renderTableAsCode(state: RenderState)`
- 将表格渲染为等宽代码块
- 计算列宽：每列取所有单元格文本长度的最大值
- 分隔行：使用 `"-".repeat(Math.max(3, widths[i]))` 生成
- 行格式：`"| text   |"` 右侧用空格填充
- 整个表格包裹在 `code_block` 样式跨度中

##### `renderTokens(tokens: MarkdownToken[], state: RenderState): void`
- 处理所有 token 类型的大型 switch 语句
- 支持的 token 类型：
  - `inline`: 递归子 token
  - `text`: 追加内容
  - `em_open/em_close`: italic 样式开关
  - `strong_open/strong_close`: bold 样式开关
  - `s_open/s_close`: strikethrough 样式开关
  - `code_inline`: 调用 `renderInlineCode`
  - `spoiler_open/spoiler_close`: 需要 `enableSpoilers` 开启
  - `link_open/link_close`: 链接处理，`link_open` 获取 href 并推入链接栈
  - `image`: 追加 content 文本
  - `softbreak/hardbreak`: 追加 `"\n"`
  - `paragraph_close`: 段落分隔
  - `heading_open/heading_close`: 当 `headingStyle === "bold"` 时开关 bold 样式
  - `blockquote_open`: 追加 `blockquotePrefix`，开启 blockquote 样式
  - `blockquote_close`: 关闭 blockquote 样式
  - `bullet_list_open`: 嵌套时追加 `"\n"`，推入 `{ type: "bullet", index: 0 }`
  - `bullet_list_close`: 弹出栈，顶层时追加 `"\n"`
  - `ordered_list_open`: 嵌套时追加 `"\n"`，获取 start 属性（默认 `"1"`），推入 `{ type: "ordered", index: start - 1 }`
  - `ordered_list_close`: 同 bullet
  - `list_item_open`: 追加列表前缀
  - `list_item_close`: 若不以 `"\n"` 结尾则追加
  - `code_block/fence`: 调用 `renderCodeBlock`
  - `html_block/html_inline`: 追加 content
  - `table_open/close`: 表格生命周期管理
  - `thead_open/close`: 切换 `inHeader`
  - `tr_open/close`: 行管理，tr_close 时分配到 headers 或 rows
  - `th_open/td_open`: 初始化 currentCell
  - `th_close/td_close`: 完成 cell 并推入 currentRow
  - `hr`: 渲染为 `"───\n\n"`
  - `default`: 递归子 token

##### `closeRemainingStyles(target: RenderTarget)`
- 反向遍历 `openStyles`，关闭所有未关闭样式

##### `clampStyleSpans(spans: MarkdownStyleSpan[], maxLength: number): MarkdownStyleSpan[]`
- 将所有 span 的 start/end 钳制到 `[0, maxLength]` 范围

##### `clampLinkSpans(spans: MarkdownLinkSpan[], maxLength: number): MarkdownLinkSpan[]`
- 同上，适用于链接跨度

##### `mergeStyleSpans(spans: MarkdownStyleSpan[]): MarkdownStyleSpan[]`
- 按 start、end、style 排序
- 合并相邻/重叠的同样式跨度
- **blockquote 样式例外**：相邻（`span.start === prev.end`）的 blockquote 不合并，防止"样式渗透"

##### `sliceStyleSpans(spans: MarkdownStyleSpan[], start: number, end: number): MarkdownStyleSpan[]`
- 切片并偏移样式跨度，之后调用 `mergeStyleSpans`

##### `sliceLinkSpans(spans: MarkdownLinkSpan[], start: number, end: number): MarkdownLinkSpan[]`
- 切片并偏移链接跨度

#### 导出函数

##### `markdownToIR(markdown: string, options?: MarkdownParseOptions): MarkdownIR`
- 调用 `markdownToIRWithMeta` 并返回 `.ir`

##### `markdownToIRWithMeta(markdown: string, options?: MarkdownParseOptions): { ir: MarkdownIR; hasTables: boolean }`
- 创建 `RenderEnv { listStack: [] }`
- 使用 `createMarkdownIt` 解析
- 若 `enableSpoilers` 开启，调用 `applySpoilerTokens`
- 初始化 `RenderState`，默认值：`headingStyle: "none"`, `blockquotePrefix: ""`, `enableSpoilers: false`, `tableMode: "off"`
- 调用 `renderTokens` 处理所有 token
- 调用 `closeRemainingStyles` 关闭残余样式
- trimEnd 处理：最终长度取 `trimmedLength` 和 `codeBlockEnd`（所有 code_block 跨度的最大 end 值）的较大者
- 返回时调用 `mergeStyleSpans(clampStyleSpans(...))` 和 `clampLinkSpans`

##### `chunkMarkdownIR(ir: MarkdownIR, limit: number): MarkdownIR[]`
- 若文本为空返回 `[]`
- 若 `limit <= 0` 或文本长度不超过 limit，返回 `[ir]`
- 使用 `chunkText` 分割文本
- 每个 chunk 计算 start/end 偏移量，跳过 chunk 间的空白字符（`/\s/` 测试）
- 为每个 chunk 切片 styles 和 links

---

### 3.2 `render.ts` — IR 渲染引擎

#### 导入

```typescript
import type { MarkdownIR, MarkdownLinkSpan, MarkdownStyle, MarkdownStyleSpan } from "./ir.js";
```

#### 导出类型

##### `RenderStyleMarker`
```typescript
export type RenderStyleMarker = {
  open: string;
  close: string;
};
```

##### `RenderStyleMap`
```typescript
export type RenderStyleMap = Partial<Record<MarkdownStyle, RenderStyleMarker>>;
```

##### `RenderLink`
```typescript
export type RenderLink = {
  start: number;
  end: number;
  open: string;
  close: string;
};
```

##### `RenderOptions`
```typescript
export type RenderOptions = {
  styleMarkers: RenderStyleMap;
  escapeText: (text: string) => string;
  buildLink?: (link: MarkdownLinkSpan, text: string) => RenderLink | null;
};
```

#### 内部常量

##### `STYLE_ORDER`
```typescript
const STYLE_ORDER: MarkdownStyle[] = [
  "blockquote", "code_block", "code", "bold", "italic", "strikethrough", "spoiler",
];
```

##### `STYLE_RANK`
- `new Map<MarkdownStyle, number>` — 由 `STYLE_ORDER.map((style, index) => [style, index])` 生成

#### 内部函数

##### `sortStyleSpans(spans: MarkdownStyleSpan[]): MarkdownStyleSpan[]`
- 使用 `.toSorted()` 排序：
  1. 按 start 升序
  2. 按 end 降序（更大范围优先）
  3. 按 `STYLE_RANK` 排序（默认值 0）

#### 导出函数

##### `renderMarkdownWithMarkers(ir: MarkdownIR, options: RenderOptions): string`
- 核心渲染算法：
  1. 获取文本，若空返回 `""`
  2. 过滤有对应 marker 的样式跨度，排序
  3. 收集所有边界点（boundaries），包含 0 和 text.length
  4. 构建 `startsAt: Map<number, MarkdownStyleSpan[]>` — 相同起点的跨度按 end 降序、rank 排序
  5. 处理链接：调用 `buildLink`，构建 `linkStarts: Map<number, RenderLink[]>`
  6. 排序所有边界点
  7. 维护统一的 LIFO 栈 `stack: { close: string; end: number }[]`
  8. 遍历边界点：
     - 关闭所有 `end === pos` 的栈顶元素
     - 收集当前位置的所有 opening items（链接和样式）
     - 排序 opening items：end 降序 > 链接优先于样式 > 样式按 rank > 链接按 index
     - 依次输出 open 标记并推入栈
     - 输出当前位置到下一位置的文本（通过 `escapeText` 转义）

---

### 3.3 `tables.ts` — 表格转换

#### 导入

```typescript
import type { MarkdownTableMode } from "../config/types.base.js";
import { markdownToIRWithMeta } from "./ir.js";
import { renderMarkdownWithMarkers } from "./render.js";
```

#### 内部常量

##### `MARKDOWN_STYLE_MARKERS`
```typescript
const MARKDOWN_STYLE_MARKERS = {
  bold: { open: "**", close: "**" },
  italic: { open: "_", close: "_" },
  strikethrough: { open: "~~", close: "~~" },
  code: { open: "`", close: "`" },
  code_block: { open: "```\n", close: "```" },
} as const;
```

#### 导出函数

##### `convertMarkdownTables(markdown: string, mode: MarkdownTableMode): string`
- 若 `!markdown` 或 `mode === "off"` 直接返回原文
- 使用 `markdownToIRWithMeta` 解析，选项：`linkify: false, autolink: false, headingStyle: "none", blockquotePrefix: "", tableMode: mode`
- 若无表格 (`!hasTables`) 直接返回原文
- 使用 `renderMarkdownWithMarkers` 渲染：
  - `escapeText`: 原样返回 (`(text) => text`)
  - `buildLink`: 提取 href 和 label，返回 `{ start, end, open: "[", close: "](href)" }`，href 或 label 为空时返回 null

---

### 3.4 `fences.ts` — 围栏代码块解析

#### 导出类型

##### `FenceSpan`
```typescript
export type FenceSpan = {
  start: number;     // 开始标记行的字符偏移
  end: number;       // 结束标记行末的字符偏移
  openLine: string;  // 开始标记行的完整内容
  marker: string;    // 标记字符串（如 "```" 或 "~~~"）
  indent: string;    // 缩进字符串
};
```

#### 导出函数

##### `parseFenceSpans(buffer: string): FenceSpan[]`
- 逐行扫描，匹配正则 `/^( {0,3})(`{3,}|~{3,})(.*)$/`
- 围栏匹配规则：
  - 缩进 0-3 个空格
  - 至少 3 个连续反引号或波浪号
  - 关闭标记必须使用相同字符且长度 >= 开始标记长度
- 未关闭的围栏延伸到 `buffer.length`

##### `findFenceSpanAt(spans: FenceSpan[], index: number): FenceSpan | undefined`
- 查找 `index > span.start && index < span.end` 的围栏（严格在内部）

##### `isSafeFenceBreak(spans: FenceSpan[], index: number): boolean`
- 返回 `!findFenceSpanAt(spans, index)`

---

### 3.5 `code-spans.ts` — 代码跨度索引

#### 导入

```typescript
import { parseFenceSpans, type FenceSpan } from "./fences.js";
```

#### 导出类型

##### `InlineCodeState`
```typescript
export type InlineCodeState = {
  open: boolean;
  ticks: number;
};
```

##### `CodeSpanIndex`
```typescript
export type CodeSpanIndex = {
  inlineState: InlineCodeState;
  isInside: (index: number) => boolean;
};
```

#### 内部类型

- `InlineCodeSpansResult`: `{ spans: Array<[number, number]>; state: InlineCodeState }`

#### 导出函数

##### `createInlineCodeState(): InlineCodeState`
- 返回 `{ open: false, ticks: 0 }`

##### `buildCodeSpanIndex(text: string, inlineState?: InlineCodeState): CodeSpanIndex`
- 解析围栏跨度 (`parseFenceSpans`)
- 解析行内代码跨度 (`parseInlineCodeSpans`)
- 返回 `{ inlineState, isInside }` — isInside 检查围栏和行内代码两种跨度

#### 内部函数

##### `parseInlineCodeSpans(text: string, fenceSpans: FenceSpan[], initialState: InlineCodeState): InlineCodeSpansResult`
- 跳过围栏跨度范围内的字符（使用 `findFenceSpanAtInclusive`，范围 `index >= start && index < end`）
- 计算反引号连续长度（runLength）
- 若未打开：记录 runLength 和 openStart
- 若已打开且 runLength === ticks：闭合跨度
- 未闭合的跨度延伸到 `text.length`

##### `findFenceSpanAtInclusive(spans: FenceSpan[], index: number): FenceSpan | undefined`
- 查找 `index >= span.start && index < span.end`（包含起始位置）

##### `isInsideFenceSpan(index: number, spans: FenceSpan[]): boolean`
- 同 `findFenceSpanAtInclusive` 的判断逻辑

##### `isInsideInlineSpan(index: number, spans: Array<[number, number]>): boolean`
- 检查 `index >= start && index < end`

---

### 3.6 `whatsapp.ts` — WhatsApp 格式转换

#### 导入

```typescript
import { escapeRegExp } from "../utils.js";
```

#### 内部常量

```typescript
const FENCE_PLACEHOLDER = "\x00FENCE";       // 围栏代码块占位符
const INLINE_CODE_PLACEHOLDER = "\x00CODE";   // 行内代码占位符
```

#### 导出函数

##### `markdownToWhatsApp(text: string): string`
- 空文本直接返回
- 处理步骤：
  1. 保护围栏代码块：正则 `/```[\s\S]*?```/g`，替换为 `${FENCE_PLACEHOLDER}${index}`
  2. 保护行内代码：正则 `/`[^`\n]+`/g`，替换为 `${INLINE_CODE_PLACEHOLDER}${index}`
  3. 转换粗体：`/\*\*(.+?)\*\*/g` -> `"*$1*"`；`/__(.+?)__/g` -> `"*$1*"`
  4. 转换删除线：`/~~(.+?)~~/g` -> `"~$1~"`
  5. 恢复行内代码：使用 `escapeRegExp` 转义占位符前缀，正则 `${escapedPrefix}(\d+)` 还原
  6. 恢复围栏代码块：同上

---

### 3.7 `frontmatter.ts` — YAML Frontmatter 解析

#### 导入

```typescript
import YAML from "yaml";
```

#### 导出类型

##### `ParsedFrontmatter`
```typescript
export type ParsedFrontmatter = Record<string, string>;
```

#### 内部函数

##### `stripQuotes(value: string): string`
- 去除首尾匹配的单引号或双引号

##### `coerceFrontmatterValue(value: unknown): string | undefined`
- `null/undefined` -> `undefined`
- `string` -> `value.trim()`
- `number/boolean` -> `String(value)`
- `object` -> `JSON.stringify(value)`，失败返回 `undefined`
- 其他 -> `undefined`

##### `parseYamlFrontmatter(block: string): ParsedFrontmatter | null`
- 使用 `YAML.parse(block)` 解析
- 验证结果为非空、非数组对象
- 遍历键值对，使用 `coerceFrontmatterValue` 转换值
- 解析失败返回 `null`

##### `extractMultiLineValue(lines: string[], startIndex: number): { value: string; linesConsumed: number }`
- 匹配行格式正则 `/^([\w-]+):\s*(.*)$/`
- 若行内有值直接返回
- 否则收集后续以空格或 tab 开头的行，合并 trim 后返回

##### `parseLineFrontmatter(block: string): ParsedFrontmatter`
- 逐行解析 `key: value` 格式
- 使用正则 `/^([\w-]+):\s*(.*)$/`
- 支持多行值（下一行以空格/tab 开头时调用 `extractMultiLineValue`）
- 行内值通过 `stripQuotes` 处理

#### 导出函数

##### `parseFrontmatterBlock(content: string): ParsedFrontmatter`
- 规范化换行符：`\r\n` -> `\n`，`\r` -> `\n`
- 检查以 `"---"` 开头
- 查找结束标记 `"\n---"`（从位置 3 开始搜索）
- 提取 block 为 `normalized.slice(4, endIndex)`
- 同时使用 `parseLineFrontmatter` 和 `parseYamlFrontmatter` 解析
- 若 YAML 解析失败，使用行解析结果
- 否则合并：以 YAML 结果为基础，用行解析结果覆盖以 `"{"` 或 `"["` 开头的值（保留 JSON 格式值）

---

## 4. 文件间依赖关系

```
ir.ts
  ├── 依赖 MarkdownIt (外部)
  ├── 依赖 ../config/types.base.js (MarkdownTableMode)
  └── 依赖 ../auto-reply/chunk.js (chunkText)

render.ts
  └── 依赖 ./ir.js (类型导入: MarkdownIR, MarkdownLinkSpan, MarkdownStyle, MarkdownStyleSpan)

tables.ts
  ├── 依赖 ../config/types.base.js (MarkdownTableMode)
  ├── 依赖 ./ir.js (markdownToIRWithMeta)
  └── 依赖 ./render.js (renderMarkdownWithMarkers)

fences.ts
  └── 无内部依赖

code-spans.ts
  └── 依赖 ./fences.js (parseFenceSpans, FenceSpan)

whatsapp.ts
  └── 依赖 ../utils.js (escapeRegExp)

frontmatter.ts
  └── 依赖 yaml (外部)
```

## 5. 整体架构

本模块采用**中间表示（IR）架构**：

1. **解析阶段** (`ir.ts`): 使用 MarkdownIt 将 Markdown 解析为 token 流，再通过自定义渲染状态机将 token 流转换为 `MarkdownIR`（纯文本 + 样式/链接跨度列表）
2. **渲染阶段** (`render.ts`): 通用渲染引擎，接受 IR 和渲染选项（标记映射、文本转义、链接构建器），输出最终格式化文本
3. **转换适配** (`tables.ts`, `whatsapp.ts`): 基于上述管线的平台适配层
4. **辅助解析** (`fences.ts`, `code-spans.ts`): 为其他模块提供代码区域检测能力
5. **元数据解析** (`frontmatter.ts`): 独立的 YAML frontmatter 解析器，支持行解析和 YAML 双模式回退

关键设计决策：
- IR 使用偏移量而非嵌套结构表示样式，支持样式重叠和灵活分割
- 表格支持 `bullets` 和 `code` 两种替代渲染模式
- blockquote 样式合并有特殊例外，防止跨段落渗透
- 分块时先去除 chunk 间空白字符，确保偏移量正确
