# Embedding 模型与记忆机制研究报告

## 1. Embedding 模型的作用

Embedding 模型在这个产品中用于**记忆搜索（Memory Search）**功能，核心作用是把文本转化为向量，实现**语义级别的相似度检索**。

具体流程：
1. **索引阶段**：记忆文件（markdown 笔记、会话历史等）被分块（chunk），每个 chunk 通过 embedding 模型转成向量，存入 SQLite 数据库的 `chunks_vec` 向量表
2. **查询阶段**：用户查询文本同样通过 embedding 模型转成向量，然后做向量相似度搜索
3. **混合检索（Hybrid Search）**：默认同时使用向量搜索（权重 0.7）+ FTS 全文检索（权重 0.3），通过 `src/memory/hybrid.ts` 合并结果，还支持 MMR 去重和时间衰减

## 2. Embedding 模型是怎么调用的

### 调用链路

```
MemoryIndexManager.search()                    # src/memory/manager.ts:259
  → embedQueryWithTimeout(query)               # 把查询文本转向量
  → searchVector(queryVec, candidates)         # 向量搜索
  → searchKeyword(query, candidates)           # FTS 搜索
  → mergeHybridResults(...)                    # 混合排序
```

### Provider 工厂模式

`createEmbeddingProvider()`（`src/memory/embeddings.ts:168`）是核心工厂函数，支持 6 个 provider + `auto` 模式：

| Provider | 默认模型 | 实现文件 |
|----------|---------|---------|
| `openai` | `text-embedding-3-small` | `src/memory/embeddings-openai.ts` |
| `gemini` | `gemini-embedding-001` | `src/memory/embeddings-gemini.ts` |
| `voyage` | `voyage-4-large` | `src/memory/embeddings-voyage.ts` |
| `mistral` | `mistral-embed` | `src/memory/embeddings-mistral.ts` |
| `ollama` | `nomic-embed-text` | `src/memory/embeddings-ollama.ts` |
| `local` | `embeddinggemma-300m-qat` (GGUF) | `src/memory/embeddings.ts:105` |

### 远程 Provider 的统一调用方式

所有远程 provider（OpenAI/Gemini/Voyage/Mistral/Ollama）都通过 `createRemoteEmbeddingProvider()`（`src/memory/embeddings-remote-provider.ts`）统一封装，最终调 HTTP POST 到 `{baseUrl}/embeddings`：

```typescript
// src/memory/embeddings-remote-fetch.ts
POST {baseUrl}/embeddings
Body: { model: "模型名", input: ["文本1", "文本2", ...] }
Response: { data: [{ embedding: [0.1, 0.2, ...] }, ...] }
```

这个接口格式兼容 **OpenAI Embeddings API** 规范。

本地 Provider 则通过 `node-llama-cpp` 加载 GGUF 格式的模型文件直接在本地计算。

### EmbeddingProvider 接口

```typescript
// src/memory/embeddings.ts:29-36
type EmbeddingProvider = {
  id: string;
  model: string;
  maxInputTokens?: number;
  embedQuery: (text: string) => Promise<number[]>;
  embedBatch: (texts: string[]) => Promise<number[][]>;
  embedBatchInputs?: (inputs: EmbeddingInput[]) => Promise<number[][]>; // 多模态
};
```

## 3. `auto` 模式的选择策略

当 `provider = "auto"`（默认值）时（`src/memory/embeddings.ts:202-240`）：

1. 先检查是否有本地 GGUF 模型文件可用，有则用 local
2. 依次尝试 `openai` → `gemini` → `voyage` → `mistral`（Ollama 被排除在 auto 之外）
3. 每个 provider 尝试解析 API key，没 key 就跳过
4. 全部失败则降级为 **FTS-only 模式**（纯全文检索，不用向量）

## 4. API Key 的解析优先级

`src/memory/embeddings-remote-client.ts` 中定义了三级 fallback：

1. `agents.*.memorySearch.remote.apiKey` — 记忆搜索专用配置
2. `models.providers.<provider>.apiKey` — Provider 级别的通用配置
3. `resolveApiKeyForProvider()` — auth-profile 或环境变量（如 `OPENAI_API_KEY`）

## 5. 向量处理

### 归一化（`src/memory/embedding-vectors.ts`）

所有 embedding 向量都做 **L2 归一化**（单位向量），NaN/Inf 值替换为 0。

### 相似度计算（`src/memory/internal.ts`）

使用 **余弦相似度**：`dot(a,b) / (||a|| * ||b||)`，返回 [0, 1] 范围的分数。

## 6. 数据存储

- **数据库**：SQLite（路径 `~/.openclaw/memory/{agentId}.sqlite`）
- **向量表**：`chunks_vec`（通过 sqlite-vec 扩展，BLOB 存储 Float32Array）
- **FTS 表**：`chunks_fts`（SQLite FTS5，BM25 排序）
- **Embedding 缓存表**：`embedding_cache`（按 hash 缓存，避免重复计算）
- **分块策略**：默认 400 tokens/chunk，80 tokens overlap

## 7. 如何接入第三方 Embedding 模型

### 方式一：利用 OpenAI 兼容接口（最简单，零代码改动）

如果第三方模型提供 OpenAI 兼容的 `/embeddings` 接口（大多数都支持），直接修改配置：

```json
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "provider": "openai",
        "model": "你的模型名",
        "remote": {
          "baseUrl": "https://your-provider.com/v1",
          "apiKey": { "env": "YOUR_API_KEY_ENV" }
        }
      }
    }
  }
}
```

这是因为 `createRemoteEmbeddingProvider()` 本质上就是调 OpenAI 格式的 `/embeddings` 接口，只要响应格式是 `{ data: [{ embedding: [...] }] }` 即可。

### 方式二：用 Ollama 套壳

Ollama 本身支持各种开源 embedding 模型，配置：

```json
{
  "agents": {
    "defaults": {
      "memorySearch": {
        "provider": "ollama",
        "model": "你想用的模型名"
      }
    }
  }
}
```

### 方式三：新增一个 Provider（需改代码）

如果第三方 API 格式不兼容 OpenAI，需要修改以下文件：

1. **新建** `src/memory/embeddings-yourprovider.ts`，参照 `embeddings-openai.ts` 的模式
2. **更新类型** `EmbeddingProviderId`（`src/memory/embeddings.ts:38`）加入新 provider 名
3. **更新工厂** `createProvider()` 内部（`src/memory/embeddings.ts:174`）加入分支
4. **更新配置 schema** `src/config/types.tools.ts` 中的 `MemorySearchConfig.provider` 联合类型
5. **更新 Zod 校验** `src/config/zod-schema.agent-runtime.ts`
6. **更新默认模型** `src/agents/memory-search.ts` 里的 model default 映射

只需实现 `EmbeddingProvider` 接口的 `embedQuery` 和 `embedBatch` 两个方法即可。

## 8. 关键文件索引

| 文件 | 用途 |
|------|------|
| `src/memory/embeddings.ts` | Provider 工厂，local/remote 创建 |
| `src/memory/embeddings-openai.ts` | OpenAI API 客户端 |
| `src/memory/embeddings-gemini.ts` | Google Gemini（支持多模态） |
| `src/memory/embeddings-voyage.ts` | Voyage AI 客户端 |
| `src/memory/embeddings-mistral.ts` | Mistral API 客户端 |
| `src/memory/embeddings-ollama.ts` | Ollama 本地推理 |
| `src/memory/embeddings-remote-provider.ts` | 通用远程 provider 工厂 |
| `src/memory/embeddings-remote-fetch.ts` | HTTP POST /embeddings |
| `src/memory/embeddings-remote-client.ts` | API Key 解析 + Bearer 认证 |
| `src/memory/embedding-vectors.ts` | L2 归一化 |
| `src/memory/manager.ts` | MemoryIndexManager（搜索入口） |
| `src/memory/manager-embedding-ops.ts` | 批量/查询 embedding 操作、重试逻辑 |
| `src/memory/manager-search.ts` | searchVector() 和 searchKeyword() |
| `src/memory/manager-sync-ops.ts` | 文件分块、同步编排 |
| `src/memory/internal.ts` | cosineSimilarity()、parseEmbedding() |
| `src/memory/memory-schema.ts` | SQLite schema 创建 |
| `src/memory/hybrid.ts` | 混合搜索合并、MMR、时间衰减 |
| `src/memory/query-expansion.ts` | FTS fallback 关键词提取 |
| `src/memory/batch-openai.ts` | OpenAI Batch API（异步批量） |
| `src/memory/batch-gemini.ts` | Gemini Batch API |
| `src/memory/batch-voyage.ts` | Voyage Batch API |
| `src/agents/memory-search.ts` | 配置解析与默认值 |
| `src/agents/tools/memory-tool.ts` | Agent 的 memory_search 工具定义 |
