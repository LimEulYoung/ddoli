# Ddoli

Mobile-friendly Claude Code web client: Browser → FastAPI → Claude CLI (local) → SSE streaming.

## Tech Stack

- **Backend**: Python 3.12, FastAPI, PostgreSQL (psycopg2), sse-starlette, openai (Upstage Solar)
- **Frontend**: Alpine.js 3, HTMX, TailwindCSS (CDN), marked.js, highlight.js, xterm.js, PDF.js
- **Infra**: Docker, docker-compose, S3 (PDF hosting), PWA

## Directory Structure

```
main.py                     # FastAPI entry, chat mode API, /upload, /stop, /mcp, /commands, session mgmt
app/                        # Backend package
  __init__.py               # Package init (empty)
  shared.py                 # Core: CLI stream parser, HTML renderers, MCP (SSE+stdio), mode_* shared handlers
  code_routes.py            # Code mode router (delegates to shared.py)
  paper_routes.py           # Paper mode router (LaTeX templates + delegates to shared.py)
  terminal_routes.py        # WebSocket + local PTY terminal (max 3 sessions, 10-min idle timeout)
  db.py                     # PostgreSQL ORM (sessions, messages, commands, settings), auto-init on import
  config.py                 # DB credentials (env vars), local dirs, MCP defaults, run_local_command()
templates/index.html        # SPA (Alpine.js x-data="appData()")
static/js/app.js            # Alpine state, SSE handlers, ToolCards, MODE_CONFIG, PDF.js rendering
static/js/voice.js          # Voice input
static/js/terminal.js       # Terminal UI (xterm.js)
static/css/main.css         # Markdown/layout/file-modal styles
static/sw.js                # Service worker (PWA, CACHE_NAME versioned)
static/manifest.json        # PWA manifest
uploads/                    # Temp file storage (copied to attachments dir, then deleted)
Dockerfile                  # Python 3.12 + Node.js + Claude CLI + LaTeX
docker-compose.yml          # app + PostgreSQL
.env.example                # Environment variable template
```

## Commands

```bash
# Docker 배포
cp .env.example .env        # 필요 시 설정 수정
docker compose up -d --build

# 로컬 개발
pip install -r requirements.txt
python main.py               # http://localhost:8000
```

## Three Modes

| Mode  | Session ID   | Work Dir           | CLI Flags                                |
|-------|--------------|--------------------|------------------------------------------|
| Chat  | UUID         | ~/chat             | `--model {model} --tools WebSearch,Read` |
| Code  | code_{name}  | ~/workspace/{name} | All tools                                |
| Paper | paper_{name} | ~/papers/{name}    | All tools + S3 PDF deploy                |

## Architecture

- **CLI pipe**: `echo '{msg}' | claude -p --output-format stream-json --verbose` executed locally via subprocess
- **Threading**: LLM responses run in `threading.Thread`; DB save completes even if client disconnects
- **SSE**: Events indexed with `_idx` + `start_from` for reconnection; `visibilitychange` triggers resume
- **Generation counter**: `window._chatGen` invalidates stale SSE on session switch
- **Session lock**: `get_session_lock()` (120s timeout) prevents concurrent requests per session; injected from `main.py` into routers at import time
- **MCP**: `build_mcp_flags()` generates `--mcp-config` (SSE/stdio); `AskUserQuestion` always blocked
- **File uploads**: `{{file:xxx}}` placeholders → copy to `/tmp/ddoli-attachments/` → path substitution
- **Title generation**: Claude CLI Haiku with 5-second timeout, fallback to first 12 chars
- **Context usage**: Calculated from last assistant message's `usage` field, not cumulative `modelUsage`
- **Terminal**: Local PTY via `pty.openpty()` + `os.fork()`, WebSocket bidirectional streaming

## DB Schema

- **sessions**: id(PK), title, mode, context_percent, archived, created_at, updated_at
- **messages**: id, session_id(FK), role, content, reasoning (JSON tool events), thinking_label
- **commands**: id, name(UNIQUE), content — substituted via `{{cmd:name}}`
- **settings**: key(PK), value — MCP config, CLI session UUIDs, PDF URLs, selected_model

## Workflow Rules

- All code/paper shared logic MUST live in `app/shared.py`. Routers only delegate.
- DB helpers: `db.get_mode_messages(mode, name)`, `db.add_mode_message()`. Session IDs: `{mode}_{name}`.
- Single-quote escaping for CLI: `'` → `'"'"'` for safe shell piping.
- Always HTML-escape user content: backend `html.escape()`, frontend `ToolCards.escapeHtml()`.
- Path validation: block `..` and leading `/`. Use `_shell_quote_path()` for Unicode/space-safe quoting.
- Frontend modes use `MODE_CONFIG` + `_modeConfig()` factory. Unified methods (`_selectItem`, `submitMessage`) accept mode.
- Completed responses cleaned up after 10 min (`cleanup_old_responses`).
- CLI session IDs persisted in `settings` table and reused via `--resume`.
- SSE event types: `init`, `tool_use`, `edit_result`, `read_result`, `bash_result`, `tool_output`, `text`, `result`, `done`, `error_msg`. Chat-only: `title`, `session_id`, `status`.

## Authentication

- Claude CLI는 구독 인증 사용 — 컨테이너 내에서 `claude login`으로 로그인 필요
- 로그인 세션은 `~/.claude/` 에 저장됨 — Docker 볼륨 마운트로 영속화 권장

## Environment Variables

- `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` — PostgreSQL (defaults: db/5432/ddoli/ddoli/ddoli2026)
- `DDOLI_CHAT_DIR`, `DDOLI_WORKSPACE_DIR`, `DDOLI_PAPERS_DIR`, `DDOLI_TEMPLATES_DIR`, `DDOLI_ATTACHMENTS_DIR` — Work directories

## Caveats

- `app/config.py` reads credentials from environment variables — use `.env` file, never hardcode.
- Terminal mode limited to 3 concurrent local PTY sessions with 10-min idle timeout.
- Paper mode auto-generates `CLAUDE.md` per project with LaTeX build/deploy instructions.
- Service worker (`static/sw.js`) changes require `CACHE_NAME` version bump.
- `db.init_db()` runs on import — creates tables with `CREATE TABLE IF NOT EXISTS` for idempotency.
- PostgreSQL runs as a docker-compose service; data persisted in `ddoli-pgdata` volume.

## Do Not Touch

- `uploads/` — transient directory managed by upload/copy flow
- `static/sw.js` — minimal service worker; changes require cache version bump
