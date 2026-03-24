# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Server

```bash
# Install dependencies
pip install -r requirements.txt

# Start development server (port 8500)
python main.py

# Docker production (external port 6888 → internal 8000)
docker-compose up --build
```

The web UI is served at `http://localhost:8500/ui`. There is no frontend build step — it's vanilla JS/HTML/CSS served statically by FastAPI.

## Environment Configuration

Copy `.env.template` to `.env`. Key variables:

```bash
SKILLS_HOME=Agent_skills/skills          # Path to skills directory
OPENAI_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
OPENAI_MODEL=gpt-4o
GEMINI_MODEL=gemini-2.0-flash
CLAUDE_MODEL=claude-3-5-sonnet-20241022
OPENAI_MAX_OUTPUT_TOKENS=16384           # Counts against TPM — set to 2048 for LINE Bot
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
LINE_ROUTER_ENABLED=true
LINE_MODEL_ROUTER=gpt-4.1-nano           # Cheapest model for tier classification
LINE_MODEL_MINI=gpt-4.1-mini
LINE_MODEL_FULL=gpt-4.1
NOTION_TOKEN=...                         # For mcp-meeting-to-notion skill
NOTION_DATABASE_ID=...
```

## Architecture Overview

### Core Concept: UMA (Unified Model Adapter)

UMA is the central intelligence layer. It:
1. **Scans** `Agent_skills/skills/` and parses each `SKILL.md` on startup
2. **Converts** skill metadata to model-specific tool schemas (OpenAI functions, Gemini FunctionDeclaration, Claude tools)
3. **Executes** skills safely as subprocesses with path sanitization
4. **Routes** tool calls from LLMs → `ExecutionEngine` → skill scripts → back to LLM

### Request Flow

```
User (Web/LINE) → FastAPI Routes → chat_core.py → Model Adapter
                                                       ↓
                              UMA.get_tools_for_model() → inject tool schemas
                                                       ↓
                                             LLM decides to call tool
                                                       ↓
                              UMA.execute_tool_call() → ExecutionEngine (subprocess)
                                                       ↓
                                         Result → LLM final synthesis → stream to user
```

### Skill System

Skills live in `Agent_skills/skills/{skill-name}/`. Each skill requires a `SKILL.md` with YAML frontmatter:

```yaml
---
name: mcp-example
provider: mcp
version: 1.0.0
runtime_requirements: []        # pip packages required
risk_level: high                # optional — triggers Auth Modal gate
risk_description: "..."
execution_timeout: 120          # optional — override default 30s subprocess timeout
parameters:
  type: object
  properties:
    input:
      type: string
      description: "..."
  required: [input]
---
```

Three execution modes determined by directory contents:
- **Executable**: `scripts/main.py` present → subprocess execution (stdin receives JSON args, stdout must be JSON)
- **Code**: `scripts/*.py` present (no main.py) → LLM references code via python-executor
- **Semantic**: No scripts → LLM handles with language capabilities only

Skills with `risk_level: high` are intercepted in `UMA.execute_tool_call()` — the adapter yields `requires_approval` and pauses; the frontend shows an Auth Modal.

**Per-Skill Timeout**: `execution_timeout` in SKILL.md frontmatter overrides the default 30s. Read in `uma_core.py`, passed to `executor.run_script(timeout=...)`. Use for any skill making external API calls (e.g. `mcp-meeting-to-notion` uses 120s for 3 sequential LLM calls + Notion upload).

### Session & Memory

- Sessions persisted to `workspace/sessions/{session_id}.json`
- LINE Bot session IDs: `line_{user_id}` / `line_group_{group_id}`
- `POST /chat/flush/{session_id}` triggers LLM summarization → appended to `memory/MEMORY.md`
- `SessionManager` in `server/core/session.py` is a singleton; get it via `server/dependencies/session.py`
- `session.set_metadata(session_id, key, value)` / `session.get_metadata(session_id, key)` — arbitrary per-session KV store (in-memory only, not persisted to disk)
- **Message Cache Persistence** (Phase A1): `_msg_cache_loaded` lazy-loads per chat_id from `workspace/sessions/{chat_id}_msg_cache.json`. TTL 120 hours, max 500 entries per chat. Dual-write (memory + disk) on every `_add_to_cache()`.

### Memory Enhancement System (Phase A–D)

Four-phase system in `server/services/`:

**Phase A — Group Stability**
- **A1** (`session.py`): Message cache disk persistence with TTL 120h, lazy-load on first access
- **A2** (`line_connector.py`): Quote retry loop — 30s max, 3s intervals, status push at 0s/15s. On timeout, sends error and `continue` (no fallback to LLM with stale data)

**Phase B — Profile System**
- **B1** (`profile_updater.py` → `trigger_if_needed()`): Auto-creates profile after 4+ messages (2 rounds), updates with 2-hour cooldown. Runs in background thread after each LINE reply. Profiles stored at `workspace/profiles/{session_id}.profile.md`
- **B2** (`app.py`): APScheduler (BackgroundScheduler, tz=Asia/Taipei) runs 3 cron jobs:
  - Profile update: 09:00 / 12:00 / 17:00
  - Token summary rebuild: 17:05
  - Message cache cleanup: 00:00
- **B3** (`profile_updater.py` → `_PROFILE_PROMPT_TEMPLATE`): Deep reasoning prompt with 6 analysis dimensions. Profile injected into system prompt (~50-100 tokens)

**Phase C — Signal Collection**
- **C1** (`profile_updater.py`): Text correction signals (neg/pos pattern matching) + sticker emotion classification (text→keywords→vision). Signals stored as append-only JSONL at `workspace/profiles/{session_id}_signals.jsonl` with 7-day retention

**Phase D — Token Management**
- **D1** (`openai_adapter.py`): Captures `response.completed` event for real token usage. Records per-tool-call AND pure-chat to `workspace/analytics/token_usage.jsonl`
- **D2** (`token_tracker.py` → `rebuild_summary()`): Scheduled aggregation to `workspace/analytics/token_summary.json` with by_user/by_skill/by_group/daily structure. 90-day retention

### Workspace Data Paths

```
workspace/
├── sessions/          # Session history JSON + message cache JSONL
├── profiles/          # .profile.md + _signals.jsonl per user/group
├── analytics/         # token_usage.jsonl + token_summary.json
├── downloads/         # Generated files (PDF, DOCX, images) served at /downloads/
└── temp/              # Temporary processing files
Agent_workspace/
└── line_uploads/      # LINE Bot uploaded files: {messageId}_{filename}
Agent_skills/
└── temp/              # original_{session_id}.txt for transcript injection
```

### Model Adapters

All adapters in `server/adapters/` implement two methods:
- `simple_chat(session_history, ...)` — pure LLM, no tools (used by Agent Console chat panel)
- `chat(messages, ...)` — tool-calling agent mode, yields streaming chunks with `status` field:
  - `"streaming"` — text delta
  - `"tool_call"` — about to run a skill
  - `"requires_approval"` — high-risk skill intercepted, pause execution
  - `"success"` — final assembled content
  - `"error"` — error occurred

The OpenAI adapter uses the **Responses API** (`client.responses.create`), not Chat Completions, for tool calling. `simple_chat` uses `client.chat.completions.create`.

**Original File Injection** (`openai_adapter.py`): When `mcp-meeting-to-notion` is called, the adapter checks `self._original_file_path`. If set and the file exists, it reads the full original text and overrides the `transcript` parameter (replacing any LLM-generated summary with the actual source content).

### LINE Bot Integration

`server/integrations/line_connector.py` handles the `/api/line/webhook` endpoint. Key behaviors:
- Background tasks are used for LLM calls (avoids webhook timeout)
- Group chats: only responds when `@Agent K` / `@AgentK` is mentioned
- Large files are chunked (15,000 chars/chunk); each chunk is summarized sequentially, then ALL summaries are assembled into the final synthesis call via a local `all_summaries` list (not relying on session history, which is token-trimmed)
- `adapter.max_output_tokens = 2048` is forced for LINE (saves ~14,000 TPM per request vs default 16,384)
- Token-aware history trim: 12,000 char budget (`_MAX_HISTORY_CHARS`)
- After chunked processing, `remove_chunk_entries()` cleans intermediate `[文件分段 N/M：file]` headers + paired summaries from session to prevent history pollution

**Original Text Persistence** (3 scenarios all handled):
1. **Single-pass file**: saved immediately after `extracted_text` is populated
2. **Chunked file**: saved after all chunks assembled via `"".join(chunks)`
3. **Long direct text** (> 400 chars pasted by user): saved before session truncation

All 3 write to `Agent_skills/temp/original_{session_id}.txt` and set `session.set_metadata(session_id, "last_original_file", path)`. The adapter reads this path before each LLM call and sets `adapter._original_file_path`.

### LLM-as-a-Router (LINE Bot)

`server/services/model_router.py` — classifies each request before the main LLM call:

| Tier | Model | Tools |
|------|-------|-------|
| `nano` | gpt-4.1-nano | Off |
| `mini` | gpt-4.1-mini | max 1 |
| `full` | gpt-4.1 | max 3 |
| `file` | gpt-4.1-mini | max 3 |
| `chunk_final` | (from chunk processing) | Off |

`route_model()` returns `(model: str, tier: str)`. After chunk processing, tier is overridden to `chunk_final` (tools disabled — final synthesis is pure summarization, no tool injection needed).

**Nano→Mini auto-upgrade**: If the router classifies as `nano` but the request contains tool-dependent intent keywords (畫, 生成圖, 製作圖表, draw, generate image, etc.), the tier is upgraded to `mini` so tools are injected. See `_TOOL_INTENT_KEYWORDS` in `model_router.py`.

### FAISS Vector Retriever

- Index stored at `~/.mcp_faiss/` (avoids Chinese characters in Windows paths)
- Model: `paraphrase-multilingual-MiniLM-L12-v2`
- Skills are indexed on startup via `delta_index_skills()` (hash-based incremental)
- Workspace documents are indexed via `retriever.sync_workspace()`
- Supports `.pdf`, `.txt`, `.md`, `.csv`, `.docx`

### Key File Map

| File | Role |
|------|------|
| `main.py` | Entry point, loads `.env`, initializes UMA, starts Uvicorn |
| `server/app.py` | Mounts all routers, registers startup/shutdown lifecycle hooks |
| `server/core/uma_core.py` | `UMA` + `SkillRegistry` classes; reads `execution_timeout` from SKILL.md |
| `server/core/executor.py` | `ExecutionEngine` — safe subprocess runner; `run_script(timeout=)` |
| `server/core/session.py` | `SessionManager` — history + MEMORY.md + `set/get_metadata()` |
| `server/core/retriever.py` | FAISS-based document/skill retriever |
| `server/core/converter.py` | `SchemaConverter` — skill metadata → model tool schemas |
| `server/services/chat_core.py` | SSE streaming, tool call loop, `event_generator()` |
| `server/services/runtime.py` | `get_universal_system_prompt()`, `delta_index_skills()` |
| `server/services/model_router.py` | LLM-as-a-Router; `route_model()` → `(model, tier)` |
| `server/services/profile_updater.py` | Phase B/C: Profile CRUD, signal collection, scheduled deep reasoning |
| `server/services/token_tracker.py` | Phase D: Token usage JSONL recording + summary aggregation |
| `server/adapters/openai_adapter.py` | OpenAI GPT; original file injection; D1 token capture from `response.completed` |
| `server/routes/chat.py` | `/chat`, `/chat/approve/{id}`, `/chat/reject/{id}`, flush, session CRUD |
| `server/integrations/line_connector.py` | LINE webhook, chunked processing, original text persistence, B1 auto profile trigger, C1 signal collection |
| `Agent_skills/skills_manifest.json` | Auto-generated skill index (do not edit manually) |
| `frontend/config.js` | Google OAuth client ID, demo credentials |

## Git Branch Strategy

| Branch | Purpose |
|--------|---------|
| `AgentK_UAT` | Main development branch (local + remote) |
| `AgentK_FSC` | Staging branch for tested features |
| `main` | Production (rarely updated) |

`Agent_skills/` is a **git submodule** (separate repo `fsc0638/Agent_skills`). Always commit submodule changes first, then commit the parent repo's submodule reference update. Push both independently.

## Adding a New Skill

1. Create `Agent_skills/skills/mcp-{name}/SKILL.md` with YAML frontmatter
2. Optionally add `Scripts/main.py` (reads JSON from stdin, prints JSON to stdout)
3. For long-running skills, add `execution_timeout: N` to SKILL.md (default 30s)
4. Restart the server or call `POST /skills/reload` — UMA rescans on startup
5. `skills_manifest.json` is regenerated automatically
6. Commit submodule first, then update parent repo reference

## Active Skills Reference

| Skill | Mode | Timeout | Notes |
|-------|------|---------|-------|
| `mcp-pdf-llm-analyzer` | executable | 30s | PDF semantic analysis |
| `mcp-docx-llm-analyzer` | executable | 30s | DOCX analysis |
| `mcp-txt-llm-analyzer` | executable | 30s | TXT analysis |
| `mcp-spreadsheet-llm-analyzer` | executable | 30s | Spreadsheet analysis |
| `mcp-web-search` | executable | 30s | Web search |
| `mcp-python-executor` | executable | 30s | Python code execution |
| `mcp-meeting-to-notion` | executable | 120s | 4-phase pipeline → Notion DB upload |
| `mcp-groovenaust-meeting-analyst` | semantic | — | PMP/PgMP/PfMP meeting analysis |
| `mcp-image-generator` | executable | 60s | AI image generation via gpt-image-1; returns base64 PNG saved to downloads/ |
| `mcp-high-risk-demo` | executable | 30s | Auth Modal flow demo |

### LINE Bot Image Delivery

When `mcp-image-generator` returns a result with `file_path`, `line_connector.py` detects image files (`.png`, `.jpg`, etc.) and sends a LINE `ImageMessage` instead of text. The image is served via `/downloads/{filename}` (same ngrok URL). For matplotlib charts generated by `mcp-python-executor`, the same image detection logic applies.
