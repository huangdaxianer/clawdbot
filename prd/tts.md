# TTS 模块 - 产品需求文档 (PRD)

## 1. 模块概述与用途

`tts` 模块实现了文本转语音（Text-to-Speech）功能，支持多个 TTS 提供商（OpenAI、ElevenLabs、Microsoft Edge TTS），并提供完整的配置管理、用户偏好持久化、文本摘要、指令解析和自动应用等能力。

核心功能包括：
- 支持三个 TTS 提供商：OpenAI TTS API、ElevenLabs API、Microsoft Edge TTS
- 提供商自动回退机制（主提供商失败时尝试其他提供商）
- 文本指令解析（`[[tts:...]]` 语法覆盖语音、模型、提供商等参数）
- 文本长度管理（自动摘要或截断）
- 用户偏好持久化（JSON 文件）
- 电话场景专用 TTS（PCM 输出格式）
- 针对 Telegram 渠道的 Opus 语音消息优化

## 2. 目录结构

```
src/tts/
├── tts-core.ts                    # 核心 TTS 功能：API 调用、指令解析、辅助函数
├── tts.ts                         # 高层 TTS 逻辑：配置解析、用户偏好、自动应用
├── tts.test.ts                    # 测试（不在本文档范围内）
└── prepare-text.test.ts           # 文本预处理测试（不在本文档范围内）
```

## 3. 文件详细说明

---

### 3.1 `tts-core.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `@mariozechner/pi-ai` | `completeSimple`, `type TextContent` |
| `node-edge-tts` | `EdgeTTS` |
| `node:fs` | `rmSync` |
| `../config/config.js` | `type OpenClawConfig` |
| `./tts.js` | `type ResolvedTtsConfig`, `type ResolvedTtsModelOverrides`, `type TtsDirectiveOverrides`, `type TtsDirectiveParseResult` |
| `../agents/model-auth.js` | `getApiKeyForModel`, `requireApiKey` |
| `../agents/model-selection.js` | `buildModelAliasIndex`, `resolveDefaultModelForAgent`, `resolveModelRefFromString`, `type ModelRef` |
| `../agents/pi-embedded-runner/model.js` | `resolveModel` |

#### 模块级常量（私有）

| 常量名 | 值 | 用途 |
|--------|----|------|
| `DEFAULT_ELEVENLABS_BASE_URL` | `"https://api.elevenlabs.io"` | ElevenLabs API 默认基础 URL |
| `TEMP_FILE_CLEANUP_DELAY_MS` | `5 * 60 * 1000`（300000，即 5 分钟） | 临时文件清理延迟 |

#### 导出常量

| 常量名 | 值 | 类型 |
|--------|----|------|
| `OPENAI_TTS_MODELS` | `["gpt-4o-mini-tts", "tts-1", "tts-1-hd"] as const` | OpenAI TTS 支持的模型列表 |
| `OPENAI_TTS_VOICES` | `["alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "juniper", "marin", "onyx", "nova", "sage", "shimmer", "verse"] as const` | OpenAI TTS 支持的语音列表 |

#### 私有类型

```typescript
type OpenAiTtsVoice = (typeof OPENAI_TTS_VOICES)[number];

type SummarizeResult = {
  summary: string;
  latencyMs: number;
  inputLength: number;
  outputLength: number;
};

type SummaryModelSelection = {
  ref: ModelRef;
  source: "summaryModel" | "default";
};
```

#### 私有函数

##### `normalizeElevenLabsBaseUrl(baseUrl: string): string`

去除尾部斜杠，空字符串返回 `DEFAULT_ELEVENLABS_BASE_URL`。

##### `requireInRange(value: number, min: number, max: number, label: string): void`

验证数值在 `[min, max]` 范围内，否则抛出 `Error: "${label} must be between ${min} and ${max}"`。

##### `assertElevenLabsVoiceSettings(settings: ResolvedTtsConfig["elevenlabs"]["voiceSettings"]): void`

验证 ElevenLabs 语音设置：
- `stability`: [0, 1]
- `similarityBoost`: [0, 1]
- `style`: [0, 1]
- `speed`: [0.5, 2]

##### `normalizeLanguageCode(code?: string): string | undefined`

- 空值返回 `undefined`
- 转小写并验证匹配 `/^[a-z]{2}$/`（ISO 639-1 两字母代码）
- 不匹配抛出 `Error: "languageCode must be a 2-letter ISO 639-1 code (e.g. en, de, fr)"`

##### `normalizeApplyTextNormalization(mode?: string): "auto" | "on" | "off" | undefined`

- 空值返回 `undefined`
- 转小写，接受 `"auto"` / `"on"` / `"off"`
- 其他值抛出 `Error: "applyTextNormalization must be one of: auto, on, off"`

##### `normalizeSeed(seed?: number): number | undefined`

- `null`/`undefined` 返回 `undefined`
- `Math.floor(seed)`，范围 [0, 4294967295]
- 超范围抛出 `Error: "seed must be between 0 and 4294967295"`

##### `parseBooleanValue(value: string): boolean | undefined`

- `true` 值: `["true", "1", "yes", "on"]`
- `false` 值: `["false", "0", "no", "off"]`
- 无法识别返回 `undefined`

##### `parseNumberValue(value: string): number | undefined`

- 使用 `Number.parseFloat(value)`，若 `Number.isFinite` 则返回，否则 `undefined`

##### `getOpenAITtsBaseUrl(): string`

- 从 `process.env.OPENAI_TTS_BASE_URL` 读取（运行时读取，非模块加载时）
- 默认 `"https://api.openai.com/v1"`
- 去除尾部斜杠

##### `isCustomOpenAIEndpoint(): boolean`

- 返回 `getOpenAITtsBaseUrl() !== "https://api.openai.com/v1"`

##### `resolveSummaryModelRef(cfg: OpenClawConfig, config: ResolvedTtsConfig): SummaryModelSelection`

**逻辑**：
1. 获取默认模型引用 `resolveDefaultModelForAgent({ cfg })`
2. 检查 `config.summaryModel?.trim()` 是否有覆盖值
3. 若无覆盖返回 `{ ref: defaultRef, source: "default" }`
4. 若有覆盖，调用 `buildModelAliasIndex` 和 `resolveModelRefFromString` 解析
5. 解析成功返回 `{ ref: resolved.ref, source: "summaryModel" }`
6. 解析失败返回默认

##### `isTextContentBlock(block: { type: string }): block is TextContent`

- 类型守卫：`block.type === "text"`

#### 导出函数

##### `isValidVoiceId(voiceId: string): boolean`

验证 ElevenLabs voice ID 格式：`/^[a-zA-Z0-9]{10,40}$/`

##### `isValidOpenAIModel(model: string): boolean`

- 自定义端点时始终返回 `true`
- 否则检查是否在 `OPENAI_TTS_MODELS` 列表中

##### `isValidOpenAIVoice(voice: string): voice is OpenAiTtsVoice`

- 自定义端点时始终返回 `true`
- 否则检查是否在 `OPENAI_TTS_VOICES` 列表中

##### `parseTtsDirectives(text: string, policy: ResolvedTtsModelOverrides): TtsDirectiveParseResult`

**用途**：解析文本中的 TTS 指令标记。

**返回值**：`{ cleanedText, ttsText?, hasDirective, overrides, warnings }`

**逻辑**：

1. 若 `policy.enabled` 为 `false`，返回 `{ cleanedText: text, overrides: {}, warnings: [], hasDirective: false }`

2. **块级指令**解析（正则 `/\[\[tts:text\]\]([\s\S]*?)\[\[\/tts:text\]\]/gi`）：
   - 替换匹配内容为空字符串
   - 若 `policy.allowText` 为 `true` 且 `overrides.ttsText` 尚未设置，设置 `overrides.ttsText = inner.trim()`

3. **行内指令**解析（正则 `/\[\[tts:([^\]]+)\]\]/gi`）：
   - 替换匹配内容为空字符串
   - body 按空白分割为 token
   - 每个 token 按第一个 `=` 分割为 key/value
   - key 转小写后按 switch 处理：

| key 别名 | 权限字段 | 处理逻辑 |
|----------|---------|---------|
| `provider` | `allowProvider` | 接受 `"openai"` / `"elevenlabs"` / `"edge"`，否则警告 |
| `voice`, `openai_voice`, `openaivoice` | `allowVoice` | 调用 `isValidOpenAIVoice`，设置 `overrides.openai.voice` |
| `voiceid`, `voice_id`, `elevenlabs_voice`, `elevenlabsvoice` | `allowVoice` | 调用 `isValidVoiceId`，设置 `overrides.elevenlabs.voiceId` |
| `model`, `modelid`, `model_id`, `elevenlabs_model`, `elevenlabsmodel`, `openai_model`, `openaimodel` | `allowModelId` | 先尝试 `isValidOpenAIModel` → `overrides.openai.model`，否则 → `overrides.elevenlabs.modelId` |
| `stability` | `allowVoiceSettings` | 范围 [0, 1]，设置 `overrides.elevenlabs.voiceSettings.stability` |
| `similarity`, `similarityboost`, `similarity_boost` | `allowVoiceSettings` | 范围 [0, 1]，设置 `overrides.elevenlabs.voiceSettings.similarityBoost` |
| `style` | `allowVoiceSettings` | 范围 [0, 1]，设置 `overrides.elevenlabs.voiceSettings.style` |
| `speed` | `allowVoiceSettings` | 范围 [0.5, 2]，设置 `overrides.elevenlabs.voiceSettings.speed` |
| `speakerboost`, `speaker_boost`, `usespeakerboost`, `use_speaker_boost` | `allowVoiceSettings` | 布尔值解析，设置 `overrides.elevenlabs.voiceSettings.useSpeakerBoost` |
| `normalize`, `applytextnormalization`, `apply_text_normalization` | `allowNormalization` | 调用 `normalizeApplyTextNormalization`，设置 `overrides.elevenlabs.applyTextNormalization` |
| `language`, `languagecode`, `language_code` | `allowNormalization` | 调用 `normalizeLanguageCode`，设置 `overrides.elevenlabs.languageCode` |
| `seed` | `allowSeed` | `parseInt(rawValue, 10)` 后调用 `normalizeSeed`，设置 `overrides.elevenlabs.seed` |

每个指令处理中的异常被捕获并将 `err.message` 加入 `warnings` 数组。

##### `summarizeText(params: {...}): Promise<SummarizeResult>`

**参数**：
```typescript
{
  text: string;
  targetLength: number;       // 范围 [100, 10000]
  cfg: OpenClawConfig;
  config: ResolvedTtsConfig;
  timeoutMs: number;
}
```

**逻辑**：
1. 验证 `targetLength` 范围 [100, 10000]
2. 解析摘要模型引用
3. 使用 `resolveModel` 解析具体模型
4. 获取 API key（`requireApiKey`）
5. 创建 `AbortController` 及超时定时器
6. 调用 `completeSimple(resolved.model, messages, options)`：
   - 系统提示词：`"You are an assistant that summarizes texts concisely while keeping the most important information. Summarize the text to approximately ${targetLength} characters. Maintain the original tone and style. Reply only with the summary, without additional explanations."`
   - 用户消息格式：`"<text_to_summarize>\n${text}\n</text_to_summarize>"`
   - `maxTokens`: `Math.ceil(targetLength / 2)`
   - `temperature`: `0.3`
7. 从响应中提取文本内容（过滤 `type === "text"` 的块）
8. 返回 `{ summary, latencyMs, inputLength, outputLength }`
9. `AbortError` 包装为 `"Summarization timed out"`

##### `scheduleCleanup(tempDir: string, delayMs: number = TEMP_FILE_CLEANUP_DELAY_MS): void`

- 延迟 `delayMs`（默认 5 分钟）后递归删除 `tempDir`
- `setTimeout` 设置 `.unref()` 防止阻止进程退出
- 清理错误静默忽略

##### `elevenLabsTTS(params: {...}): Promise<Buffer>`

**参数**：
```typescript
{
  text: string;
  apiKey: string;
  baseUrl: string;
  voiceId: string;
  modelId: string;
  outputFormat: string;
  seed?: number;
  applyTextNormalization?: "auto" | "on" | "off";
  languageCode?: string;
  voiceSettings: ResolvedTtsConfig["elevenlabs"]["voiceSettings"];
  timeoutMs: number;
}
```

**逻辑**：
1. 验证 `voiceId` 格式（`isValidVoiceId`）
2. 验证 `voiceSettings`（`assertElevenLabsVoiceSettings`）
3. 规范化 `languageCode`、`applyTextNormalization`、`seed`
4. 构建 URL：`${normalizeElevenLabsBaseUrl(baseUrl)}/v1/text-to-speech/${voiceId}`
5. 若 `outputFormat` 非空，设置查询参数 `output_format`
6. HTTP POST 请求：
   - Headers: `{ "xi-api-key": apiKey, "Content-Type": "application/json", Accept: "audio/mpeg" }`
   - Body JSON:
     ```json
     {
       "text": "...",
       "model_id": "...",
       "seed": ...,
       "apply_text_normalization": "...",
       "language_code": "...",
       "voice_settings": {
         "stability": ...,
         "similarity_boost": ...,
         "style": ...,
         "use_speaker_boost": ...,
         "speed": ...
       }
     }
     ```
7. 错误处理：非 ok 响应抛出 `"ElevenLabs API error (${response.status})"`
8. 返回 `Buffer.from(await response.arrayBuffer())`

##### `openaiTTS(params: {...}): Promise<Buffer>`

**参数**：
```typescript
{
  text: string;
  apiKey: string;
  model: string;
  voice: string;
  responseFormat: "mp3" | "opus" | "pcm";
  timeoutMs: number;
}
```

**逻辑**：
1. 验证 model（`isValidOpenAIModel`）和 voice（`isValidOpenAIVoice`）
2. HTTP POST 到 `${getOpenAITtsBaseUrl()}/audio/speech`：
   - Headers: `{ Authorization: "Bearer ${apiKey}", "Content-Type": "application/json" }`
   - Body JSON: `{ model, input: text, voice, response_format: responseFormat }`
3. 错误处理：非 ok 响应抛出 `"OpenAI TTS API error (${response.status})"`
4. 返回 Buffer

##### `inferEdgeExtension(outputFormat: string): string`

根据输出格式字符串推断文件扩展名：

| 包含子串（小写化后） | 返回扩展名 |
|---------------------|-----------|
| `"webm"` | `".webm"` |
| `"ogg"` | `".ogg"` |
| `"opus"` | `".opus"` |
| `"wav"` / `"riff"` / `"pcm"` | `".wav"` |
| 默认 | `".mp3"` |

##### `edgeTTS(params: {...}): Promise<void>`

**参数**：
```typescript
{
  text: string;
  outputPath: string;
  config: ResolvedTtsConfig["edge"];
  timeoutMs: number;
}
```

**逻辑**：
- 创建 `EdgeTTS` 实例，传入 `voice`, `lang`, `outputFormat`, `saveSubtitles`, `proxy`, `rate`, `pitch`, `volume`, `timeout`（优先 `config.timeoutMs`，回退 `timeoutMs`）
- 调用 `tts.ttsPromise(text, outputPath)`

---

### 3.2 `tts.ts`

#### 导入

| 模块 | 导入项 |
|------|--------|
| `node:fs` | `existsSync`, `mkdirSync`, `readFileSync`, `writeFileSync`, `mkdtempSync`, `rmSync`, `renameSync`, `unlinkSync` |
| `node:os` | `tmpdir` |
| `node:path` | `path`（默认导入） |
| `../auto-reply/types.js` | `type ReplyPayload` |
| `../channels/plugins/types.js` | `type ChannelId` |
| `../config/config.js` | `type OpenClawConfig` |
| `../config/types.tts.js` | `type TtsConfig`, `type TtsAutoMode`, `type TtsMode`, `type TtsProvider`, `type TtsModelOverrideConfig` |
| `../channels/plugins/index.js` | `normalizeChannelId` |
| `../globals.js` | `logVerbose` |
| `../line/markdown-to-line.js` | `stripMarkdown` |
| `../media/audio.js` | `isVoiceCompatibleAudio` |
| `../utils.js` | `CONFIG_DIR`, `resolveUserPath` |
| `./tts-core.js` | `edgeTTS`, `elevenLabsTTS`, `inferEdgeExtension`, `isValidOpenAIModel`, `isValidOpenAIVoice`, `isValidVoiceId`, `OPENAI_TTS_MODELS`, `OPENAI_TTS_VOICES`, `openaiTTS`, `parseTtsDirectives`, `scheduleCleanup`, `summarizeText` |

#### 再导出

```typescript
export { OPENAI_TTS_MODELS, OPENAI_TTS_VOICES } from "./tts-core.js";
```

#### 模块级常量

| 常量名 | 值 | 用途 |
|--------|----|------|
| `DEFAULT_TIMEOUT_MS` | `30_000` | 默认超时毫秒 |
| `DEFAULT_TTS_MAX_LENGTH` | `1500` | 默认 TTS 最大文本长度 |
| `DEFAULT_TTS_SUMMARIZE` | `true` | 默认启用摘要 |
| `DEFAULT_MAX_TEXT_LENGTH` | `4096` | 默认硬性最大文本长度 |
| `DEFAULT_ELEVENLABS_BASE_URL` | `"https://api.elevenlabs.io"` | ElevenLabs 默认 URL |
| `DEFAULT_ELEVENLABS_VOICE_ID` | `"pMsXgVXv3BLzUgSXRplE"` | ElevenLabs 默认语音 ID |
| `DEFAULT_ELEVENLABS_MODEL_ID` | `"eleven_multilingual_v2"` | ElevenLabs 默认模型 ID |
| `DEFAULT_OPENAI_MODEL` | `"gpt-4o-mini-tts"` | OpenAI 默认模型 |
| `DEFAULT_OPENAI_VOICE` | `"alloy"` | OpenAI 默认语音 |
| `DEFAULT_EDGE_VOICE` | `"en-US-MichelleNeural"` | Edge TTS 默认语音 |
| `DEFAULT_EDGE_LANG` | `"en-US"` | Edge TTS 默认语言 |
| `DEFAULT_EDGE_OUTPUT_FORMAT` | `"audio-24khz-48kbitrate-mono-mp3"` | Edge TTS 默认输出格式 |

| 常量名 | 结构 | 用途 |
|--------|------|------|
| `DEFAULT_ELEVENLABS_VOICE_SETTINGS` | `{ stability: 0.5, similarityBoost: 0.75, style: 0.0, useSpeakerBoost: true, speed: 1.0 }` | ElevenLabs 默认语音设置 |
| `TELEGRAM_OUTPUT` | `{ openai: "opus", elevenlabs: "opus_48000_64", extension: ".opus", voiceCompatible: true }` | Telegram 渠道输出格式 |
| `DEFAULT_OUTPUT` | `{ openai: "mp3", elevenlabs: "mp3_44100_128", extension: ".mp3", voiceCompatible: false }` | 默认输出格式 |
| `TELEPHONY_OUTPUT` | `{ openai: { format: "pcm", sampleRate: 24000 }, elevenlabs: { format: "pcm_22050", sampleRate: 22050 } }` | 电话场景输出格式 |
| `TTS_AUTO_MODES` | `new Set<TtsAutoMode>(["off", "always", "inbound", "tagged"])` | 有效的自动模式集合 |

#### 导出类型

##### `ResolvedTtsConfig`

```typescript
export type ResolvedTtsConfig = {
  auto: TtsAutoMode;
  mode: TtsMode;
  provider: TtsProvider;
  providerSource: "config" | "default";
  summaryModel?: string;
  modelOverrides: ResolvedTtsModelOverrides;
  elevenlabs: {
    apiKey?: string;
    baseUrl: string;
    voiceId: string;
    modelId: string;
    seed?: number;
    applyTextNormalization?: "auto" | "on" | "off";
    languageCode?: string;
    voiceSettings: {
      stability: number;
      similarityBoost: number;
      style: number;
      useSpeakerBoost: boolean;
      speed: number;
    };
  };
  openai: {
    apiKey?: string;
    model: string;
    voice: string;
  };
  edge: {
    enabled: boolean;
    voice: string;
    lang: string;
    outputFormat: string;
    outputFormatConfigured: boolean;
    pitch?: string;
    rate?: string;
    volume?: string;
    saveSubtitles: boolean;
    proxy?: string;
    timeoutMs?: number;
  };
  prefsPath?: string;
  maxTextLength: number;
  timeoutMs: number;
};
```

##### `ResolvedTtsModelOverrides`

```typescript
export type ResolvedTtsModelOverrides = {
  enabled: boolean;
  allowText: boolean;
  allowProvider: boolean;
  allowVoice: boolean;
  allowModelId: boolean;
  allowVoiceSettings: boolean;
  allowNormalization: boolean;
  allowSeed: boolean;
};
```

##### `TtsDirectiveOverrides`

```typescript
export type TtsDirectiveOverrides = {
  ttsText?: string;
  provider?: TtsProvider;
  openai?: {
    voice?: string;
    model?: string;
  };
  elevenlabs?: {
    voiceId?: string;
    modelId?: string;
    seed?: number;
    applyTextNormalization?: "auto" | "on" | "off";
    languageCode?: string;
    voiceSettings?: Partial<ResolvedTtsConfig["elevenlabs"]["voiceSettings"]>;
  };
};
```

##### `TtsDirectiveParseResult`

```typescript
export type TtsDirectiveParseResult = {
  cleanedText: string;
  ttsText?: string;
  hasDirective: boolean;
  overrides: TtsDirectiveOverrides;
  warnings: string[];
};
```

##### `TtsResult`

```typescript
export type TtsResult = {
  success: boolean;
  audioPath?: string;
  error?: string;
  latencyMs?: number;
  provider?: string;
  outputFormat?: string;
  voiceCompatible?: boolean;
};
```

##### `TtsTelephonyResult`

```typescript
export type TtsTelephonyResult = {
  success: boolean;
  audioBuffer?: Buffer;
  error?: string;
  latencyMs?: number;
  provider?: string;
  outputFormat?: string;
  sampleRate?: number;
};
```

#### 私有类型

```typescript
type TtsUserPrefs = {
  tts?: {
    auto?: TtsAutoMode;
    enabled?: boolean;
    provider?: TtsProvider;
    maxLength?: number;
    summarize?: boolean;
  };
};

type TtsStatusEntry = {
  timestamp: number;
  success: boolean;
  textLength: number;
  summarized: boolean;
  provider?: string;
  latencyMs?: number;
  error?: string;
};
```

#### 模块级变量

| 变量名 | 类型 | 初始值 | 用途 |
|--------|------|--------|------|
| `lastTtsAttempt` | `TtsStatusEntry \| undefined` | `undefined` | 记录最后一次 TTS 尝试状态 |

#### 私有函数

##### `resolveModelOverridePolicy(overrides: TtsModelOverrideConfig | undefined): ResolvedTtsModelOverrides`

- 若 `overrides?.enabled` 为 `false` → 所有字段为 `false`
- 否则各字段默认为 `true`（`value ?? true`）

##### `resolveTtsAutoModeFromPrefs(prefs: TtsUserPrefs): TtsAutoMode | undefined`

1. 先尝试 `normalizeTtsAutoMode(prefs.tts?.auto)`
2. 若 `prefs.tts?.enabled` 为布尔值 → `true` 映射 `"always"`，`false` 映射 `"off"`
3. 否则返回 `undefined`

##### `readPrefs(prefsPath: string): TtsUserPrefs`

- 检查文件是否存在（`existsSync`）
- 读取并 JSON 解析
- 出错返回 `{}`

##### `atomicWriteFileSync(filePath: string, content: string): void`

- 生成临时文件名：`${filePath}.tmp.${Date.now()}.${Math.random().toString(36).slice(2)}`
- 先写入临时文件，再 `renameSync` 原子替换
- 若 rename 失败，尝试清理临时文件后重新抛出

##### `updatePrefs(prefsPath: string, update: (prefs: TtsUserPrefs) => void): void`

- 读取现有偏好 → 应用 `update` 回调 → 确保父目录存在 → 原子写入 JSON（缩进 2 空格）

##### `resolveOutputFormat(channelId?: string | null)`

- `channelId === "telegram"` → `TELEGRAM_OUTPUT`
- 否则 → `DEFAULT_OUTPUT`

##### `resolveChannelId(channel: string | undefined): ChannelId | null`

- `channel` 非空则调用 `normalizeChannelId(channel)`，否则 `null`

##### `resolveEdgeOutputFormat(config: ResolvedTtsConfig): string`

- 返回 `config.edge.outputFormat`

##### `formatTtsProviderError(provider: TtsProvider, err: unknown): string`

- `AbortError` → `"${provider}: request timed out"`
- 其他 → `"${provider}: ${error.message}"`

#### 导出函数

##### `normalizeTtsAutoMode(value: unknown): TtsAutoMode | undefined`

- 仅接受 `string` 类型
- 转小写后检查是否在 `TTS_AUTO_MODES` 集合中
- 有效值：`"off"`, `"always"`, `"inbound"`, `"tagged"`

##### `resolveTtsConfig(cfg: OpenClawConfig): ResolvedTtsConfig`

**用途**：从 `OpenClawConfig` 解析完整的 TTS 配置。

**逻辑**：
- 原始配置来源：`cfg.messages?.tts ?? {}`
- `providerSource`：若 `raw.provider` 存在则 `"config"`，否则 `"default"`
- `auto`：`normalizeTtsAutoMode(raw.auto) ?? (raw.enabled ? "always" : "off")`
- `mode`：`raw.mode ?? "final"`
- `provider`：`raw.provider ?? "edge"`
- ElevenLabs 配置：各字段从 raw 读取，使用默认值回退
- OpenAI 配置：各字段从 raw 读取，使用默认值回退
- Edge 配置：各字段从 raw 读取，使用默认值回退
- `maxTextLength`：`raw.maxTextLength ?? DEFAULT_MAX_TEXT_LENGTH`（4096）
- `timeoutMs`：`raw.timeoutMs ?? DEFAULT_TIMEOUT_MS`（30000）

##### `resolveTtsPrefsPath(config: ResolvedTtsConfig): string`

偏好文件路径解析优先级：
1. `config.prefsPath?.trim()` → `resolveUserPath(...)`
2. `process.env.OPENCLAW_TTS_PREFS?.trim()` → `resolveUserPath(...)`
3. `path.join(CONFIG_DIR, "settings", "tts.json")`

##### `resolveTtsAutoMode(params: { config, prefsPath, sessionAuto? }): TtsAutoMode`

优先级：
1. `sessionAuto`（会话级覆盖）
2. 用户偏好文件中的设置
3. 配置文件中的 `config.auto`

##### `buildTtsSystemPromptHint(cfg: OpenClawConfig): string | undefined`

**用途**：为 AI 生成 TTS 相关的系统提示词。

- 若 `autoMode === "off"` → 返回 `undefined`
- 否则构建提示词数组：
  1. `"Voice (TTS) is enabled."`
  2. 若 `inbound` 模式：`"Only use TTS when the user's last message includes audio/voice."`
  3. 若 `tagged` 模式：`"Only use TTS when you include [[tts]] or [[tts:text]] tags."`
  4. `` `Keep spoken text ≤${maxLength} chars to avoid auto-summary (summary ${summarize}).` ``
  5. `"Use [[tts:...]] and optional [[tts:text]]...[[/tts:text]] to control voice/expressiveness."`
- 用 `"\n"` 连接

##### `isTtsEnabled(config, prefsPath, sessionAuto?): boolean`

- `resolveTtsAutoMode(...) !== "off"`

##### `setTtsAutoMode(prefsPath: string, mode: TtsAutoMode): void`

- 更新偏好：删除 `enabled` 字段，设置 `auto` 为新值

##### `setTtsEnabled(prefsPath: string, enabled: boolean): void`

- 调用 `setTtsAutoMode(prefsPath, enabled ? "always" : "off")`

##### `getTtsProvider(config, prefsPath): TtsProvider`

优先级：
1. 用户偏好中的 `provider`
2. 配置文件指定的 `provider`（`providerSource === "config"`）
3. 自动检测：有 OpenAI key → `"openai"`，有 ElevenLabs key → `"elevenlabs"`，否则 → `"edge"`

##### `setTtsProvider(prefsPath: string, provider: TtsProvider): void`

- 更新偏好中的 `provider`

##### `getTtsMaxLength(prefsPath: string): number`

- 从偏好读取 `maxLength`，默认 `DEFAULT_TTS_MAX_LENGTH`（1500）

##### `setTtsMaxLength(prefsPath: string, maxLength: number): void`

- 更新偏好中的 `maxLength`

##### `isSummarizationEnabled(prefsPath: string): boolean`

- 从偏好读取 `summarize`，默认 `DEFAULT_TTS_SUMMARIZE`（`true`）

##### `setSummarizationEnabled(prefsPath: string, enabled: boolean): void`

- 更新偏好中的 `summarize`

##### `getLastTtsAttempt(): TtsStatusEntry | undefined`

- 返回模块级 `lastTtsAttempt`

##### `setLastTtsAttempt(entry: TtsStatusEntry | undefined): void`

- 设置模块级 `lastTtsAttempt`

##### `resolveTtsApiKey(config, provider): string | undefined`

- `"elevenlabs"` → `config.elevenlabs.apiKey || process.env.ELEVENLABS_API_KEY || process.env.XI_API_KEY`
- `"openai"` → `config.openai.apiKey || process.env.OPENAI_API_KEY`
- 其他 → `undefined`

##### `TTS_PROVIDERS`（导出常量）

`["openai", "elevenlabs", "edge"] as const`

##### `resolveTtsProviderOrder(primary: TtsProvider): TtsProvider[]`

- 返回以 `primary` 为首，其余按 `TTS_PROVIDERS` 顺序排列

##### `isTtsProviderConfigured(config, provider): boolean`

- `"edge"` → `config.edge.enabled`
- 其他 → `Boolean(resolveTtsApiKey(config, provider))`

##### `textToSpeech(params: {...}): Promise<TtsResult>`

**参数**：
```typescript
{
  text: string;
  cfg: OpenClawConfig;
  prefsPath?: string;
  channel?: string;
  overrides?: TtsDirectiveOverrides;
}
```

**核心逻辑**：

1. 检查文本长度是否超过 `config.maxTextLength`
2. 确定提供商优先级：`overrideProvider ?? userProvider` → `resolveTtsProviderOrder`
3. 遍历提供商列表尝试转换：

   **Edge TTS**：
   - 检查是否启用
   - 创建临时目录 `mkdtempSync(path.join(tmpdir(), "tts-"))`
   - 尝试配置的输出格式，失败后尝试默认格式 `DEFAULT_EDGE_OUTPUT_FORMAT`
   - 成功后调用 `scheduleCleanup(tempDir)` 和 `isVoiceCompatibleAudio`

   **OpenAI / ElevenLabs**：
   - 检查 API key
   - 应用 `overrides` 中的覆盖参数
   - 写入临时文件 → `scheduleCleanup`

4. 所有提供商失败返回 `{ success: false, error: "TTS conversion failed: ${lastError || 'no providers available'}" }`

##### `textToSpeechTelephony(params: {...}): Promise<TtsTelephonyResult>`

**参数**：
```typescript
{
  text: string;
  cfg: OpenClawConfig;
  prefsPath?: string;
}
```

**与 `textToSpeech` 的区别**：
- 不支持 Edge TTS（`"edge: unsupported for telephony"`）
- 返回 `audioBuffer` 而非 `audioPath`
- 使用 `TELEPHONY_OUTPUT` 格式（PCM）
- 包含 `sampleRate` 信息

##### `maybeApplyTtsToPayload(params: {...}): Promise<ReplyPayload>`

**参数**：
```typescript
{
  payload: ReplyPayload;
  cfg: OpenClawConfig;
  channel?: string;
  kind?: "tool" | "block" | "final";
  inboundAudio?: boolean;
  ttsAuto?: string;
}
```

**核心逻辑**：

1. **自动模式检查**：若 `autoMode === "off"` → 返回原始 payload
2. **指令解析**：调用 `parseTtsDirectives(text, config.modelOverrides)`
3. **文本清理**：从 payload 文本中移除 TTS 指令标记
4. **模式过滤**：
   - `"tagged"` 模式：若无指令则跳过
   - `"inbound"` 模式：若无入站音频则跳过
   - `"final"` 模式：若 `kind` 非 `"final"` 则跳过
5. **跳过条件**：
   - 文本为空
   - payload 包含 `mediaUrl` 或 `mediaUrls`
   - 文本包含 `"MEDIA:"` 字符串
   - 文本长度 < 10 字符
6. **文本长度处理**：
   - 若超过 `maxLength`（默认 1500）：
     - 摘要禁用时：截断并添加 `"..."`（`textForAudio.slice(0, maxLength - 3) + "..."`）
     - 摘要启用时：调用 `summarizeText`，若摘要仍超 `maxTextLength` 则截断
     - 摘要失败时：回退到截断
7. **Markdown 清理**：`stripMarkdown(textForAudio).trim()`
8. **TTS 转换**：调用 `textToSpeech`
9. **结果处理**：
   - 成功：设置 `payload.mediaUrl` 为音频路径
   - Telegram 渠道且 `voiceCompatible` → 设置 `audioAsVoice: true`
   - 记录 `lastTtsAttempt`

#### 导出的测试辅助对象

```typescript
export const _test = {
  isValidVoiceId,
  isValidOpenAIVoice,
  isValidOpenAIModel,
  OPENAI_TTS_MODELS,
  OPENAI_TTS_VOICES,
  parseTtsDirectives,
  resolveModelOverridePolicy,
  summarizeText,
  resolveOutputFormat,
  resolveEdgeOutputFormat,
};
```

## 4. 文件间依赖关系

```
tts-core.ts
  ├─→ @mariozechner/pi-ai (completeSimple, TextContent)
  ├─→ node-edge-tts (EdgeTTS)
  ├─→ ../config/config.js (OpenClawConfig)
  ├─→ ./tts.js (ResolvedTtsConfig, ResolvedTtsModelOverrides, TtsDirectiveOverrides, TtsDirectiveParseResult)
  ├─→ ../agents/model-auth.js (getApiKeyForModel, requireApiKey)
  ├─→ ../agents/model-selection.js (buildModelAliasIndex, resolveDefaultModelForAgent, resolveModelRefFromString, ModelRef)
  └─→ ../agents/pi-embedded-runner/model.js (resolveModel)

tts.ts
  ├─→ ../auto-reply/types.js (ReplyPayload)
  ├─→ ../channels/plugins/types.js (ChannelId)
  ├─→ ../config/config.js (OpenClawConfig)
  ├─→ ../config/types.tts.js (TtsConfig, TtsAutoMode, TtsMode, TtsProvider, TtsModelOverrideConfig)
  ├─→ ../channels/plugins/index.js (normalizeChannelId)
  ├─→ ../globals.js (logVerbose)
  ├─→ ../line/markdown-to-line.js (stripMarkdown)
  ├─→ ../media/audio.js (isVoiceCompatibleAudio)
  ├─→ ../utils.js (CONFIG_DIR, resolveUserPath)
  └─→ ./tts-core.js (edgeTTS, elevenLabsTTS, inferEdgeExtension, isValidOpenAIModel, isValidOpenAIVoice, isValidVoiceId, OPENAI_TTS_MODELS, OPENAI_TTS_VOICES, openaiTTS, parseTtsDirectives, scheduleCleanup, summarizeText)
```

**注意**：`tts-core.ts` 导入 `./tts.js` 的类型（仅类型导入 `type`），而 `tts.ts` 导入 `./tts-core.js` 的运行时值，形成**单向运行时依赖**（无循环依赖，类型导入在编译后消失）。

## 5. 整体架构

TTS 模块采用双层架构：

1. **核心层** (`tts-core.ts`)
   - 底层 API 调用封装（OpenAI、ElevenLabs、Edge TTS）
   - 输入验证和参数规范化
   - TTS 指令解析引擎
   - 文本摘要功能
   - 临时文件清理调度

2. **业务层** (`tts.ts`)
   - 配置解析和默认值管理
   - 用户偏好持久化（JSON 文件，原子写入）
   - 提供商自动选择和回退
   - 渠道特定输出格式优化（Telegram → Opus，电话 → PCM）
   - 消息 payload 自动处理管线

**提供商回退流程**：
```
主提供商 → 第二提供商 → 第三提供商 → 失败报告
```

**消息处理管线**：
```
ReplyPayload
  → 检查自动模式（off/always/inbound/tagged）
  → 解析 TTS 指令 ([[tts:...]])
  → 清理文本（移除指令标记）
  → 过滤条件检查（长度、媒体、模式）
  → 文本预处理（摘要/截断、stripMarkdown）
  → TTS 转换（带提供商回退）
  → 生成新 ReplyPayload（含 mediaUrl）
```
