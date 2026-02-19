"""
Ddoli configuration file
- Settings injected via environment variables (for Docker deployment)
- Uses default values when running locally
"""
import os
import subprocess

# ========================
# Working directories (local paths inside the container)
# ========================
CHAT_DIR = os.environ.get("DDOLI_CHAT_DIR", os.path.expanduser("~/chat"))
WORKSPACE_DIR = os.environ.get("DDOLI_WORKSPACE_DIR", os.path.expanduser("~/workspace"))
PAPERS_DIR = os.environ.get("DDOLI_PAPERS_DIR", os.path.expanduser("~/papers"))
TEMPLATES_DIR = os.environ.get("DDOLI_TEMPLATES_DIR", os.path.expanduser("~/paper-templates"))
ATTACHMENTS_DIR = os.environ.get("DDOLI_ATTACHMENTS_DIR", "/tmp/ddoli-attachments")

# ========================
# PostgreSQL settings
# ========================
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "ddoli")
DB_USER = os.environ.get("DB_USER", "ddoli")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "ddoli2026")

# ========================
# MCP servers (defaults — used when no values are stored in DB)
# ========================
DEFAULT_MCP_SERVERS = {
    "legal_mcp": {
        "type": "sse",
        "url": "https://mcp.crow-tit.com/sse",
        "modes": ["chat", "paper"],
    },
    "chrome_devtools": {
        "type": "stdio",
        "command": "npx",
        "args": ["chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:9222"],
        "modes": ["code", "paper"],
    }
}

# ========================
# Chat mode settings
# ========================
CHAT_SYSTEM_PROMPT = f"You are a helpful, friendly AI assistant. When the user attaches files, they are saved to {ATTACHMENTS_DIR}/. Use the Read tool to read those files when referenced. Only use the Read tool for files in that directory."

# ========================
# Utility functions
# ========================
def run_local_command(command: str, timeout: int = 30) -> tuple[bool, str]:
    """Helper to execute a local shell command."""
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)
