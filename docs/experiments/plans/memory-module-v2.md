---
summary: "PRD: Memory Module v2 — structured recall, entity-aware retrieval, and opinion evolution on top of the existing Markdown-first memory system"
owner: "openclaw"
status: "draft"
last_updated: "2026-02-17"
title: "Memory Module v2 PRD"
---

# Memory Module v2 — Product Requirements Document

## 1. Overview

OpenClaw's memory system today stores facts as plain Markdown (daily logs + `MEMORY.md`) and retrieves them via hybrid search (BM25 + vector similarity). This works well for append-only journaling and simple semantic search, but falls short when the assistant needs **structured recall** ("what did we decide about X?"), **entity-centric answers** ("tell me about Alice"), **opinion tracking** ("what does Peter prefer?"), or **temporal queries** ("what happened last week?").

Memory v2 extends the existing Markdown-first architecture with a **derived structured index**, an **entity model**, and a **retain/recall/reflect loop** — all offline-first, explainable, and incrementally adoptable.

## 2. Problem Statement

### Current strengths (keep)

- **Markdown source of truth**: human-readable, git-friendly, easy to edit.
- **Append-only daily logs**: low friction capture.
- **Hybrid search**: BM25 + vector covers both exact-token and semantic queries.
- **Embedding provider flexibility**: OpenAI, Gemini, Voyage, local GGUF.
- **Pre-compaction memory flush**: auto-saves durable facts before context compaction.

### Current gaps (fix)

| Gap | Example |
|---|---|
| **Low recall for cross-session queries** | "What did we decide about the deploy strategy?" requires scanning many daily files. |
| **No entity model** | "Tell me about Alice" has no structured answer — must keyword-search across all memory. |
| **No opinion/preference tracking** | "What does Peter prefer for notification style?" has no canonical answer with confidence. |
| **No temporal filtering** | "What happened between Jan 5 and Jan 12?" requires brute-force. |
| **No fact deduplication or conflict resolution** | The same fact can be recorded many times with slight variations; contradictions are invisible. |
| **Memory flush is passive** | The model is reminded to save, but there is no structured extraction or classification. |

## 3. Goals

1. **Structured recall**: enable the assistant to answer "what did we decide / learn / observe about X?" with cited, ranked facts — without rereading all daily logs.
2. **Entity-aware retrieval**: maintain entity pages (people, projects, systems) that are automatically updated from daily facts.
3. **Opinion evolution**: track preferences and beliefs with confidence scores, evidence links, and temporal history.
4. **Temporal queries**: support "since", "between", and "around" date filters on memory recall.
5. **Offline-first**: no cloud dependency for core memory operations. Embeddings may use remote providers; recall and indexing must work locally.
6. **Incremental adoption**: v2 features layer on top of the existing system. Users who don't opt in see no behavior change.
7. **Explainability**: every recalled fact cites its source file and line range.

## 4. Non-Goals

- Replace the Markdown source of truth with a database.
- Build a full knowledge graph (Neo4j-style).
- Support multi-user / shared memory (OpenClaw is single-user).
- Change the existing `memory_search` / `memory_get` tool API (we extend, not break).
- Add a web UI for memory management (CLI + agent tools only for v2).

## 5. Architecture

### 5.1 Layers

```
┌──────────────────────────────────────────────────────┐
│                  Agent / LLM context                 │
│  (core memory block + tool results from recall)      │
├──────────────────────────────────────────────────────┤
│                   Memory Tools                       │
│  memory_search · memory_get · memory_recall (new)    │
│  memory_retain (new) · memory_entities (new)         │
├──────────────────────────────────────────────────────┤
│                 Memory Manager                       │
│  Orchestrates retain/recall/reflect                  │
├──────────────┬───────────────────────────────────────┤
│  Derived     │         Canonical Store               │
│  Index       │   ~/.openclaw/workspace/              │
│  (SQLite)    │     MEMORY.md                         │
│              │     memory/YYYY-MM-DD.md              │
│  facts       │     bank/                             │
│  entities    │       entities/*.md                   │
│  opinions    │       opinions.md                     │
│  timeline    │       world.md                        │
│              │       experience.md                   │
└──────────────┴───────────────────────────────────────┘
```

### 5.2 Canonical Store (Markdown, git-friendly)

The workspace layout extends the current structure:

```
~/.openclaw/workspace/
  MEMORY.md                        # core memory (always in context for DMs)
  memory/
    YYYY-MM-DD.md                  # daily log (unchanged)
  bank/                            # NEW: structured memory pages
    world.md                       # objective facts about the world
    experience.md                  # what the agent observed/did
    opinions.md                    # preferences + confidence + evidence
    entities/
      <slug>.md                    # per-entity summary pages
```

- **Daily logs stay daily logs.** No schema changes.
- **`bank/`** files are curated by the reflect job and editable by hand.
- **`MEMORY.md`** remains the "always-loaded core" block.

### 5.3 Derived Index (SQLite)

Stored at `~/.openclaw/workspace/.memory/index.sqlite` (or configured path). Always rebuildable from Markdown.

#### Schema (key tables)

```sql
-- Extracted facts with type classification
CREATE TABLE facts (
  id          INTEGER PRIMARY KEY,
  content     TEXT NOT NULL,           -- narrative, self-contained statement
  kind        TEXT NOT NULL,           -- 'world' | 'experience' | 'opinion' | 'observation'
  confidence  REAL DEFAULT 1.0,        -- 0.0–1.0 for opinions
  source_file TEXT NOT NULL,           -- memory/2026-01-15.md
  source_line INTEGER,                 -- line number in source file
  created_at  TEXT NOT NULL,           -- ISO 8601
  updated_at  TEXT NOT NULL,
  superseded_by INTEGER REFERENCES facts(id)  -- for deduplication/updates
);

-- Entity registry
CREATE TABLE entities (
  id    INTEGER PRIMARY KEY,
  slug  TEXT UNIQUE NOT NULL,          -- 'peter', 'the-castle', 'warelay'
  name  TEXT NOT NULL,                 -- display name
  type  TEXT DEFAULT 'unknown'         -- 'person' | 'project' | 'system' | 'place' | ...
);

-- Fact ↔ Entity links
CREATE TABLE fact_entities (
  fact_id   INTEGER REFERENCES facts(id),
  entity_id INTEGER REFERENCES entities(id),
  PRIMARY KEY (fact_id, entity_id)
);

-- Opinion evidence trail
CREATE TABLE opinion_evidence (
  opinion_fact_id    INTEGER REFERENCES facts(id),
  evidence_fact_id   INTEGER REFERENCES facts(id),
  direction          TEXT NOT NULL,    -- 'supporting' | 'contradicting'
  PRIMARY KEY (opinion_fact_id, evidence_fact_id)
);

-- FTS5 virtual table over facts
CREATE VIRTUAL TABLE facts_fts USING fts5(content, content=facts, content_rowid=id);

-- Embeddings (reuse existing sqlite-vec pattern)
CREATE VIRTUAL TABLE facts_vec USING vec0(embedding float[N], +fact_id INTEGER);
```

### 5.4 Retain / Recall / Reflect Loop

#### Retain (extract facts from daily logs)

**Trigger**: end of session, pre-compaction flush, explicit `/retain` command, or scheduled cron job.

**Process**:
1. Scan recent daily log entries that haven't been indexed.
2. Extract narrative, self-contained facts using the LLM (or parse explicit `## Retain` sections if present).
3. Classify each fact: `world` / `experience` / `opinion` / `observation`.
4. Extract entity mentions and link to entity registry.
5. For opinions, assign initial confidence and link evidence.
6. Insert into the derived index.
7. Deduplicate against existing facts (semantic similarity + entity overlap).

**Fact format** (in daily logs, optional but encouraged):

```markdown
## Retain
- W @Peter: Currently in Marrakech (Nov 27–Dec 1) for Andy's birthday.
- B @warelay: Fixed the Baileys WS crash by wrapping handlers in try/catch.
- O(c=0.95) @Peter: Prefers concise replies (<1500 chars) on WhatsApp.
```

Type prefixes: `W` (world), `B` (experience), `O` (opinion), `S` (observation/summary).

#### Recall (structured queries)

**New tool**: `memory_recall`

Parameters:
- `query` (string): natural language query
- `entities` (string[], optional): filter by entity slugs
- `kind` (string[], optional): filter by fact kind
- `since` / `until` (ISO date, optional): temporal bounds
- `minConfidence` (number, optional): minimum confidence for opinions
- `maxResults` (number, default 10)

Returns:
```json
[
  {
    "content": "Peter prefers concise replies (<1500 chars) on WhatsApp.",
    "kind": "opinion",
    "confidence": 0.95,
    "entities": ["peter"],
    "created_at": "2026-01-15",
    "source": "memory/2026-01-15.md#L12"
  }
]
```

The existing `memory_search` tool continues to work unchanged (returns raw Markdown snippets). `memory_recall` is the structured alternative that queries the derived fact index.

#### Reflect (update entity pages + opinions)

**Trigger**: daily cron job, heartbeat ultrathink, or explicit `/reflect` command.

**Process**:
1. Gather facts added since last reflection, grouped by entity.
2. For each entity with new facts:
   - Update `bank/entities/<slug>.md` with a summary.
   - Merge new facts into the entity narrative.
3. For opinions with new evidence:
   - Update confidence based on supporting/contradicting evidence.
   - Small deltas for reinforcement; require repeated strong evidence for big shifts.
4. Optionally propose updates to `MEMORY.md` for facts that have become "core".
5. Write updated `bank/` files to disk (Markdown remains source of truth).

## 6. New Tools

### 6.1 `memory_recall`

Structured fact retrieval with filtering. See Recall section above.

### 6.2 `memory_retain`

Explicitly extract and index facts from a given text or the current session.

Parameters:
- `text` (string, optional): raw text to extract facts from. If omitted, processes the current day's log.
- `facts` (array, optional): pre-structured facts to index directly (skip LLM extraction).

### 6.3 `memory_entities`

List or query the entity registry.

Parameters:
- `query` (string, optional): search entities by name/slug.
- `slug` (string, optional): get a specific entity's summary and linked facts.

### 6.4 Existing tools (unchanged)

- `memory_search`: raw Markdown snippet search (BM25 + vector hybrid). No changes.
- `memory_get`: read a specific memory file. Extended to support `bank/` paths.

## 7. CLI Commands

### 7.1 `openclaw memory recall`

```bash
openclaw memory recall "what does Peter prefer?" --entities peter --kind opinion --since 30d
```

Interactive recall against the derived index. Returns cited facts.

### 7.2 `openclaw memory retain`

```bash
openclaw memory retain                    # process today's unindexed log entries
openclaw memory retain --since 7d         # backfill last 7 days
openclaw memory retain --rebuild          # rebuild entire index from Markdown
```

### 7.3 `openclaw memory reflect`

```bash
openclaw memory reflect                   # run reflection on recent facts
openclaw memory reflect --since 7d        # reflect on last 7 days
openclaw memory reflect --entity peter    # reflect on a specific entity
```

### 7.4 `openclaw memory entities`

```bash
openclaw memory entities                  # list all entities
openclaw memory entities peter            # show entity detail + linked facts
```

### 7.5 `openclaw memory status` (extended)

Add v2 index stats to the existing status output:
- Facts indexed (total, by kind)
- Entities tracked
- Last retain/reflect timestamps
- Index freshness (dirty/clean)

## 8. Configuration

All v2 features are gated behind `memory.v2`:

```json5
{
  memory: {
    // Existing config unchanged
    backend: "builtin",
    citations: "auto",

    // NEW: v2 structured memory
    v2: {
      enabled: false,               // opt-in
      store: {
        path: "{workspace}/.memory/index.sqlite"
      },
      retain: {
        auto: true,                 // auto-extract on pre-compaction flush
        cronSchedule: "0 23 * * *", // daily 11pm
        extractionModel: null,      // null = use session model
        requireRetainSection: false  // true = only index ## Retain sections
      },
      reflect: {
        auto: true,
        cronSchedule: "0 6 * * *",  // daily 6am
        updateEntityPages: true,
        updateOpinions: true,
        proposeCoreFacts: false      // suggest MEMORY.md updates
      },
      recall: {
        maxResults: 10,
        minConfidence: 0.0,
        hybridSearch: true,          // combine FTS + vector on fact index
        vectorWeight: 0.7,
        textWeight: 0.3
      },
      entities: {
        autoExtract: true,           // extract entities from facts
        pageDir: "bank/entities"     // relative to workspace
      }
    }
  }
}
```

## 9. Migration & Compatibility

### 9.1 Backward compatibility

- **Opt-in**: `memory.v2.enabled` defaults to `false`. No behavior change for existing users.
- **Existing tools**: `memory_search` and `memory_get` are unchanged.
- **Existing files**: `MEMORY.md` and `memory/*.md` continue to work exactly as before.
- **Index is derived**: the `.memory/index.sqlite` is always rebuildable from Markdown. Users can delete it and rebuild with `openclaw memory retain --rebuild`.

### 9.2 Migration path

1. User enables `memory.v2.enabled = true`.
2. On first start, the system runs `retain --rebuild` to index all existing daily logs.
3. Entity pages are generated in `bank/entities/` (user can review and edit).
4. Scheduled cron jobs begin running retain + reflect cycles.
5. New tools (`memory_recall`, `memory_retain`, `memory_entities`) become available.

## 10. Implementation Phases

### Phase 1: Foundation (MVP)

**Goal**: Derived fact index with basic recall.

- [ ] SQLite schema for facts + entities + FTS5.
- [ ] `retain` command: LLM-based fact extraction from daily logs.
- [ ] `memory_recall` tool: query facts with kind/entity/temporal filters.
- [ ] `openclaw memory retain` CLI command.
- [ ] `openclaw memory recall` CLI command.
- [ ] Configuration under `memory.v2`.
- [ ] Extend `openclaw memory status` with v2 stats.

### Phase 2: Entity Model

**Goal**: Entity registry and auto-generated entity pages.

- [ ] Entity extraction from facts (LLM-based or rule-based `@slug` mentions).
- [ ] Entity registry table + `fact_entities` links.
- [ ] `memory_entities` tool.
- [ ] `openclaw memory entities` CLI command.
- [ ] `bank/entities/<slug>.md` page generation.
- [ ] Entity-filtered recall queries.

### Phase 3: Reflect Loop

**Goal**: Automated reflection that updates entity pages and opinions.

- [ ] Reflect job: update entity summaries from new facts.
- [ ] Opinion confidence evolution with evidence tracking.
- [ ] `bank/opinions.md` generation and updates.
- [ ] `openclaw memory reflect` CLI command.
- [ ] Cron integration for scheduled retain + reflect.

### Phase 4: Deep Integration

**Goal**: Seamless agent experience.

- [ ] Auto-retain during pre-compaction flush (structured extraction, not just a reminder).
- [ ] Core memory block injection (top-K entity facts injected into system prompt based on conversation context).
- [ ] Fact deduplication and conflict resolution.
- [ ] Vector embeddings on the fact index (reuse existing embedding infrastructure).
- [ ] `bank/world.md` and `bank/experience.md` generation.

## 11. Success Metrics

| Metric | Target |
|---|---|
| **Recall precision** | >80% of `memory_recall` results are relevant to the query (manual eval on sample queries). |
| **Entity coverage** | >90% of mentioned people/projects in daily logs have entity pages after 30 days of use. |
| **Index freshness** | Facts from today's log are queryable within 5 minutes of writing. |
| **No regressions** | Existing `memory_search` latency and quality unchanged. |
| **Rebuild time** | Full index rebuild from 1 year of daily logs < 5 minutes (excluding embedding generation). |

## 12. Open Questions

1. **Extraction model**: Should fact extraction use the session model (convenient, possibly expensive) or a dedicated smaller model (cheaper, may need fine-tuning)?
2. **Entity resolution**: How do we handle entity aliases ("Pete" vs "Peter" vs "@peter")? Start with case-insensitive slug matching + explicit aliases in entity pages?
3. **Confidence calibration**: What's the right initial confidence for opinions extracted without explicit confidence markers? Default 0.7?
4. **Privacy/scope**: Should entity pages and opinions be scoped per-channel (DM-only, like `MEMORY.md`) or global? Start DM-only, extend later?
5. **Reflect model cost**: Reflection jobs may consume significant tokens. Should they use a cheaper model by default? Allow configuring `reflect.model`?
6. **Git tracking**: Should `bank/` and `.memory/` be gitignored by default, or tracked? Recommendation: track `bank/` (human-readable), gitignore `.memory/` (derived index).

## 13. References

- [Current memory docs](/concepts/memory)
- [Memory v2 research notes](/experiments/research/memory)
- [Session management + compaction](/reference/session-management-compaction)
- Letta / MemGPT: core memory blocks + archival memory + tool-driven self-editing memory
- Hindsight Technical Report: retain/recall/reflect, narrative fact extraction, opinion confidence evolution
