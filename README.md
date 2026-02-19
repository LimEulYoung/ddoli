# Ddoli

AI assistant web app based on Claude Code. Supports chat, code editing, and paper writing modes.

## Requirements

- Docker & Docker Compose
- Claude account (Max subscription recommended)

## Installation & Setup

### 1. Clone

```bash
git clone https://github.com/LimEulYoung/ddoli.git
cd ddoli
```

### 2. Build & Run

```bash
docker compose up --build -d
```

The first build may take some time as it installs LaTeX, Chrome, Node.js, etc.

### 3. Claude CLI Login

```bash
docker exec -it ddoli-app-1 bash
claude login
```

When a browser authentication URL is displayed, copy it and open it in your local browser to authenticate.

### 4. Access

Open `http://<server-IP>:8000` in your browser.

## Included Components

| Component | Purpose |
|-----------|---------|
| Python 3.12 + FastAPI | Web server |
| PostgreSQL 16 | Session/message storage |
| Claude CLI | AI response generation |
| LaTeX (texlive) | Paper PDF builds |
| Chrome Headless | DevTools MCP connection |
| Node.js 22 | Claude CLI & MCP stdio server |

## Common Commands

```bash
# Start
docker compose up -d

# Stop
docker compose down

# View logs
docker compose logs -f app

# Rebuild (after code changes)
docker compose up --build -d

# Full reset (delete DB and volumes)
docker compose down -v
```

## Volumes

| Volume | Path | Purpose |
|--------|------|---------|
| ddoli-claude-auth | /home/ddoli/.claude | Claude CLI auth credentials |
| ddoli-chat | /home/ddoli/chat | Chat session data |
| ddoli-workspace | /home/ddoli/workspace | Code projects |
| ddoli-papers | /home/ddoli/papers | Paper projects |
| ddoli-templates | /home/ddoli/paper-templates | Paper templates |
| ddoli-pgdata | PostgreSQL data | Persistent DB storage |

## Ports

- `8000` — Web UI (required)
- `9222` — Chrome DevTools (container-internal only)
