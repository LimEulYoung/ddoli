import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from datetime import datetime
from typing import Optional
import json

from app.config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

@contextmanager
def get_conn():
    """PostgreSQL 연결 context manager"""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    try:
        yield conn
    finally:
        conn.close()


def _fetchone(conn, query, params=None):
    """단일 행 조회 → dict 또는 None"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row else None


def _fetchall(conn, query, params=None):
    """다중 행 조회 → list[dict]"""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [dict(row) for row in rows]


def init_db():
    """데이터베이스 초기화"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT DEFAULT '새 대화',
                    mode TEXT DEFAULT 'chat',
                    context_percent REAL DEFAULT 0,
                    archived INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (NOW()::TEXT),
                    updated_at TEXT DEFAULT (NOW()::TEXT)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reasoning TEXT,
                    thinking_label TEXT,
                    created_at TEXT DEFAULT (NOW()::TEXT),
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS commands (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT (NOW()::TEXT)
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

        conn.commit()


# ========== 세션 관련 ==========

def create_session(session_id: str, mode: str = "chat", title: str = "새 대화") -> dict:
    """새 세션 생성"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (id, title, mode, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                (session_id, title, mode, now, now)
            )
        conn.commit()
    return {"id": session_id, "title": title, "mode": mode, "created_at": now}


def get_session(session_id: str) -> Optional[dict]:
    """세션 조회"""
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM sessions WHERE id = %s", (session_id,))


def update_session_title(session_id: str, title: str):
    """세션 제목 업데이트"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET title = %s, updated_at = %s WHERE id = %s",
                (title, now, session_id)
            )
        conn.commit()


def get_sessions_by_mode(mode: str, limit: int = 50) -> list:
    """모드별 세션 목록 조회 (최신순)"""
    with get_conn() as conn:
        return _fetchall(
            conn,
            "SELECT * FROM sessions WHERE mode = %s ORDER BY updated_at DESC LIMIT %s",
            (mode, limit)
        )


def delete_session(session_id: str):
    """세션 삭제 (관련 메시지도 함께 삭제)"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
        conn.commit()


def delete_all_sessions_by_mode(mode: str):
    """모드별 전체 세션 삭제"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE mode = %s)",
                (mode,)
            )
            cur.execute("DELETE FROM sessions WHERE mode = %s", (mode,))
        conn.commit()


# ========== 메시지 관련 ==========

def add_message(session_id: str, role: str, content: str, reasoning: str = None) -> int:
    """메시지 추가"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (session_id, role, content, reasoning, created_at) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (session_id, role, content, reasoning, now)
            )
            message_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE sessions SET updated_at = %s WHERE id = %s",
                (now, session_id)
            )
        conn.commit()
    return message_id


def count_user_messages(session_id: str) -> int:
    """세션의 사용자 메시지 수 조회"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = %s AND role = 'user'",
                (session_id,)
            )
            row = cur.fetchone()
    return row[0] if row else 0


def get_messages(session_id: str) -> list:
    """세션의 모든 메시지 조회"""
    with get_conn() as conn:
        return _fetchall(
            conn,
            "SELECT * FROM messages WHERE session_id = %s ORDER BY created_at ASC",
            (session_id,)
        )


# ========== 모드별 공통 함수 (code/paper) ==========

def _mode_sid(mode: str, name: str) -> str:
    """모드별 세션 ID 생성"""
    return f"{mode}_{name}"


def _get_or_create_mode_session(mode: str, name: str) -> dict:
    """모드별 세션 조회 또는 생성"""
    session_id = _mode_sid(mode, name)
    session = get_session(session_id)
    if session:
        return session
    return create_session(session_id, mode=mode, title=name)


def get_mode_messages(mode: str, name: str) -> list:
    """모드별 메시지 조회"""
    return get_messages(_mode_sid(mode, name))


def add_mode_message(mode: str, name: str, role: str, content: str, events: list = None) -> int:
    """모드별 메시지 추가 (이벤트 목록 포함 가능)"""
    _get_or_create_mode_session(mode, name)
    events_json = json.dumps(events) if events else None
    return add_message(_mode_sid(mode, name), role, content, reasoning=events_json)


def clear_mode_session(mode: str, name: str):
    """모드별 세션 메시지만 삭제 (세션은 유지)"""
    session_id = _mode_sid(mode, name)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
            cur.execute("UPDATE sessions SET context_percent = 0 WHERE id = %s", (session_id,))
        conn.commit()


def update_context_percent_by_session(session_id: str, percent: float):
    """세션의 컨텍스트 퍼센트 업데이트"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET context_percent = %s WHERE id = %s",
                (percent, session_id)
            )
        conn.commit()


def get_context_percent_by_session(session_id: str) -> float:
    """세션의 컨텍스트 퍼센트 조회"""
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT context_percent FROM sessions WHERE id = %s", (session_id,))
    return row["context_percent"] if row and row["context_percent"] else 0


def update_mode_context_percent(mode: str, name: str, percent: float):
    """모드별 컨텍스트 퍼센트 업데이트"""
    update_context_percent_by_session(_mode_sid(mode, name), percent)


def get_mode_context_percent(mode: str, name: str) -> float:
    """모드별 컨텍스트 퍼센트 조회"""
    return get_context_percent_by_session(_mode_sid(mode, name))


def archive_mode_project(mode: str, name: str):
    """모드별 프로젝트 아카이브 토글"""
    session_id = _mode_sid(mode, name)
    session = get_session(session_id)
    if not session:
        create_session(session_id, mode=mode, title=name)
        session = get_session(session_id)
    new_val = 0 if session.get("archived", 0) else 1
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sessions SET archived = %s WHERE id = %s", (new_val, session_id))
        conn.commit()
    return new_val


def get_archived_projects(mode: str) -> list:
    """아카이브된 프로젝트 이름 목록 조회"""
    with get_conn() as conn:
        rows = _fetchall(
            conn,
            "SELECT id FROM sessions WHERE mode = %s AND archived = 1 ORDER BY updated_at DESC",
            (mode,)
        )
    prefix = f"{mode}_"
    return [row["id"][len(prefix):] for row in rows if row["id"].startswith(prefix)]


def delete_mode_project(mode: str, name: str):
    """모드별 세션 및 메시지 완전 삭제"""
    delete_session(_mode_sid(mode, name))


# ========== 명령어 관련 ==========

def get_commands() -> list:
    """모든 명령어 조회"""
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM commands ORDER BY name ASC")


def get_command(command_id: int) -> Optional[dict]:
    """명령어 조회"""
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM commands WHERE id = %s", (command_id,))


def get_command_by_name(name: str) -> Optional[dict]:
    """이름으로 명령어 조회"""
    with get_conn() as conn:
        return _fetchone(conn, "SELECT * FROM commands WHERE name = %s", (name,))


def create_command(name: str, content: str) -> dict:
    """명령어 생성"""
    now = datetime.now().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO commands (name, content, created_at) VALUES (%s, %s, %s) RETURNING id",
                (name, content, now)
            )
            command_id = cur.fetchone()[0]
        conn.commit()
    return {"id": command_id, "name": name, "content": content, "created_at": now}


def update_command(command_id: int, name: str = None, content: str = None) -> bool:
    """명령어 수정"""
    updates = []
    params = []
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if content is not None:
        updates.append("content = %s")
        params.append(content)
    if not updates:
        return False

    params.append(command_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE commands SET {', '.join(updates)} WHERE id = %s", params
            )
            affected = cur.rowcount
        conn.commit()
    return affected > 0


def delete_command(command_id: int) -> bool:
    """명령어 삭제"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM commands WHERE id = %s", (command_id,))
            affected = cur.rowcount
        conn.commit()
    return affected > 0


# ========== 설정 관련 ==========

def get_setting(key: str) -> Optional[str]:
    """설정 값 조회"""
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT value FROM settings WHERE key = %s", (key,))
    return row["value"] if row else None


def set_setting(key: str, value: str):
    """설정 값 저장 (upsert)"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value)
            )
        conn.commit()


# 앱 시작 시 DB 초기화
init_db()
