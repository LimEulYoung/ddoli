"""
Terminal mode - WebSocket-based local PTY terminal
Connects to a local shell via pty + subprocess, with bidirectional streaming over WebSocket
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

# Active terminal session tracking
terminal_sessions: dict[str, dict] = {}
# Maximum concurrent terminal sessions
MAX_TERMINALS = 3
# Idle timeout (seconds)
IDLE_TIMEOUT = 600  # 10min


def _cleanup_idle_sessions():
    """Clean up idle terminal sessions"""
    now = time.time()
    to_remove = []
    for sid, session in terminal_sessions.items():
        if now - session.get("last_activity", 0) > IDLE_TIMEOUT:
            to_remove.append(sid)
    for sid in to_remove:
        _close_session(sid)


def _close_session(session_id: str):
    """Close terminal session"""
    session = terminal_sessions.pop(session_id, None)
    if not session:
        return
    # Terminate child process
    pid = session.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            os.waitpid(pid, os.WNOHANG)
        except (ProcessLookupError, ChildProcessError):
            pass
    # Close fd
    fd = session.get("fd")
    if fd:
        try:
            os.close(fd)
        except OSError:
            pass


def _set_pty_size(fd, rows, cols):
    """Resize PTY"""
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


@router.get("/terminal/status")
async def terminal_status():
    """Get terminal status"""
    _cleanup_idle_sessions()
    return JSONResponse({
        "active": len(terminal_sessions),
        "max": MAX_TERMINALS,
        "sessions": list(terminal_sessions.keys())
    })


@router.websocket("/ws/terminal")
async def websocket_terminal(ws: WebSocket):
    """WebSocket terminal endpoint"""
    _cleanup_idle_sessions()

    if len(terminal_sessions) >= MAX_TERMINALS:
        await ws.close(code=1008, reason="Maximum terminal limit exceeded")
        return

    await ws.accept()

    session_id = str(uuid.uuid4())[:8]
    master_fd = None

    try:
        # PTY + fork (pty.fork handles master/slave setup correctly)
        pid, master_fd = pty.fork()
        if pid == 0:
            # Child process — execute shell
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            shell = os.environ.get("SHELL", "/bin/bash")
            os.execve(shell, [shell, "--login"], env)
        else:
            # Parent process
            # Register session
            terminal_sessions[session_id] = {
                "pid": pid,
                "fd": master_fd,
                "last_activity": time.time(),
                "ws": ws
            }

            # Send session ID
            await ws.send_json({"type": "session_id", "id": session_id})

            # Set initial PTY size
            _set_pty_size(master_fd, 24, 80)

            # Bidirectional streaming
            async def read_from_pty():
                """PTY output → WebSocket"""
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

            # Start PTY read task
            read_task = asyncio.create_task(read_from_pty())

            # WebSocket input → PTY
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
                        # Regular text input
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
            await ws.send_json({"type": "error", "message": f"Error: {str(e)}"})
        except Exception:
            pass
    finally:
        _close_session(session_id)
        try:
            await ws.close()
        except Exception:
            pass
