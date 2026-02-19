# Ddoli

AI assistant web app based on Claude Code. Supports chat, code editing, and paper writing modes.

## Screenshots

<p align="center">
  <img src="docs/screenshots/chat-mode.jpg" alt="Chat Mode" width="250">
  <img src="docs/screenshots/code-mode.jpg" alt="Code Mode" width="250">
  <img src="docs/screenshots/paper-mode.jpg" alt="Paper Mode" width="250">
</p>

## Requirements

- Docker & Docker Compose
- Claude account (Max subscription recommended)

## Installation & Setup

### 1. Clone

```bash
git clone https://github.com/LimEulYoung/ddoli.git
cd ddoli
```

### 2. Run

```bash
docker compose up -d
```

The pre-built image will be pulled from Docker Hub on the first run.

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
