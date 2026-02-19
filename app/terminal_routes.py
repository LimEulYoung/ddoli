"""
터미널 모드 - WebSocket 기반 로컬 PTY 터미널
pty + subprocess로 로컬 셸 연결, WebSocket으로 양방향 스트리밍
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import asyncio
import json
import os
import pty
import select
import signal
import struct
import fcntl
import termios
import time
import uuid

router = APIRouter()

# 활성 터미널 세션 추적
terminal_sessions: dict[str, dict] = {}
# 최대 동시 터미널 세션 수
MAX_TERMINALS = 3
# 비활성 타임아웃 (초)
IDLE_TIMEOUT = 600  # 10분


def _cleanup_idle_sessions():
    """비활성 터미널 세션 정리"""
    now = time.time()
    to_remove = []
    for sid, session in terminal_sessions.items():
        if now - session.get("last_activity", 0) > IDLE_TIMEOUT:
            to_remove.append(sid)
    for sid in to_remove:
        _close_session(sid)


def _close_session(session_id: str):
    """터미널 세션 종료"""
    session = terminal_sessions.pop(session_id, None)
    if not session:
        return
    # 자식 프로세스 종료
    pid = session.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, os.WNOHANG)
        except (ProcessLookupError, ChildProcessError):
            pass
    # fd 닫기
    fd = session.get("fd")
    if fd:
        try:
            os.close(fd)
        except OSError:
            pass


def _set_pty_size(fd, rows, cols):
    """PTY 크기 조정"""
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


@router.get("/terminal/status")
async def terminal_status():
    """터미널 상태 조회"""
    _cleanup_idle_sessions()
    return JSONResponse({
        "active": len(terminal_sessions),
        "max": MAX_TERMINALS,
        "sessions": list(terminal_sessions.keys())
    })


@router.websocket("/ws/terminal")
async def websocket_terminal(ws: WebSocket):
    """WebSocket 터미널 엔드포인트"""
    _cleanup_idle_sessions()

    if len(terminal_sessions) >= MAX_TERMINALS:
        await ws.close(code=1008, reason="최대 터미널 수 초과")
        return

    await ws.accept()

    session_id = str(uuid.uuid4())[:8]
    master_fd = None

    try:
        # PTY + fork (pty.fork가 master/slave 설정을 올바르게 처리)
        pid, master_fd = pty.fork()
        if pid == 0:
            # 자식 프로세스 — 셸 실행
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            shell = os.environ.get("SHELL", "/bin/bash")
            os.execve(shell, [shell, "--login"], env)
        else:
            # 부모 프로세스
            # 세션 등록
            terminal_sessions[session_id] = {
                "pid": pid,
                "fd": master_fd,
                "last_activity": time.time(),
                "ws": ws
            }

            # 세션 ID 전달
            await ws.send_json({"type": "session_id", "id": session_id})

            # PTY 초기 크기 설정
            _set_pty_size(master_fd, 24, 80)

            # 양방향 스트리밍
            async def read_from_pty():
                """PTY 출력 → WebSocket"""
                loop = asyncio.get_event_loop()
                try:
                    while True:
                        ready, _, _ = await loop.run_in_executor(
                            None, lambda: select.select([master_fd], [], [], 0.02)
                        )
                        if ready:
                            try:
                                data = os.read(master_fd, 4096)
                            except OSError:
                                break
                            if not data:
                                break
                            await ws.send_bytes(data)
                            if session_id in terminal_sessions:
                                terminal_sessions[session_id]["last_activity"] = time.time()
                        else:
                            await asyncio.sleep(0.02)
                except Exception:
                    pass

            # PTY 읽기 태스크 시작
            read_task = asyncio.create_task(read_from_pty())

            # WebSocket 입력 → PTY
            try:
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        break

                    if session_id in terminal_sessions:
                        terminal_sessions[session_id]["last_activity"] = time.time()

                    if "text" in message:
                        try:
                            data = json.loads(message["text"])
                            if data.get("type") == "resize" and master_fd:
                                _set_pty_size(
                                    master_fd,
                                    data.get("rows", 24),
                                    data.get("cols", 80)
                                )
                                continue
                        except (json.JSONDecodeError, ValueError):
                            pass
                        # 일반 텍스트 입력
                        if master_fd:
                            os.write(master_fd, message["text"].encode())

                    elif "bytes" in message:
                        if master_fd:
                            os.write(master_fd, message["bytes"])

            except WebSocketDisconnect:
                pass
            finally:
                read_task.cancel()
                try:
                    await read_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": f"오류: {str(e)}"})
        except Exception:
            pass
    finally:
        _close_session(session_id)
        try:
            await ws.close()
        except Exception:
            pass
