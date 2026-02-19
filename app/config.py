"""
Ddoli 설정 파일
- 환경변수로 설정 주입 (Docker 배포용)
- 로컬 실행 시 기본값 사용
"""
import os
import subprocess

# ========================
# 작업 디렉토리 (컨테이너 내 로컬 경로)
# ========================
CHAT_DIR = os.environ.get("DDOLI_CHAT_DIR", os.path.expanduser("~/chat"))
WORKSPACE_DIR = os.environ.get("DDOLI_WORKSPACE_DIR", os.path.expanduser("~/workspace"))
PAPERS_DIR = os.environ.get("DDOLI_PAPERS_DIR", os.path.expanduser("~/papers"))
TEMPLATES_DIR = os.environ.get("DDOLI_TEMPLATES_DIR", os.path.expanduser("~/paper-templates"))
ATTACHMENTS_DIR = os.environ.get("DDOLI_ATTACHMENTS_DIR", "/tmp/ddoli-attachments")

# ========================
# PostgreSQL 설정
# ========================
DB_HOST = os.environ.get("DB_HOST", "db")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "ddoli")
DB_USER = os.environ.get("DB_USER", "ddoli")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "ddoli2026")

# ========================
# MCP 서버 (기본값 — DB에 저장된 값이 없을 때 사용)
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
# 채팅 모드 설정
# ========================
CHAT_SYSTEM_PROMPT = f"You are a helpful, friendly AI assistant. When the user attaches files, they are saved to {ATTACHMENTS_DIR}/. Use the Read tool to read those files when referenced. Only use the Read tool for files in that directory."

# ========================
# 유틸리티 함수
# ========================
def run_local_command(command: str, timeout: int = 30) -> tuple[bool, str]:
    """로컬 셸 명령 실행 헬퍼"""
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "시간 초과"
    except Exception as e:
        return False, str(e)
