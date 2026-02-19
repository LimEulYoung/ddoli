"""
Ddoli - Claude Clone main server
- FastAPI app entry point
- Chat mode API (using Claude Code CLI)
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import asyncio
import threading
import re
import uuid
import html as html_lib
import json
import os

from app import db
from app.config import CHAT_DIR, CHAT_SYSTEM_PROMPT
from app.shared import render_user_message_html, render_tool_events_html, MCP_SERVERS, init_mcp_servers, UPLOAD_DIR, add_mcp_server, update_mcp_server, remove_mcp_server, chat_handler, make_chat_extra_events_fn, mode_stream_sse, mode_status_response, mode_active_response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_chat_directory():
    """Ensure chat directory exists"""
    os.makedirs(CHAT_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app):
    ensure_chat_directory()
    threading.Thread(target=init_mcp_servers, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

# Per-session lock (prevent concurrent messages)
_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()

def get_session_lock(session_key: str) -> threading.Lock:
    """Return per-session lock (create if not exists)"""
    with _session_locks_guard:
        if session_key not in _session_locks:
            _session_locks[session_key] = threading.Lock()
        return _session_locks[session_key]

# Active response status tracking (managed by chat_handler)
active_responses = {}

# Chat streaming tracking
chat_streams = {}  # {response_id: {"cancelled": bool, "process": ...}}

# File upload endpoint
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload file (save temporarily and return file ID)"""
    UPLOAD_DIR.mkdir(exist_ok=True)
    file_id = str(uuid.uuid4())[:8]
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', file.filename)
    save_name = f"{file_id}_{safe_name}"
    save_path = UPLOAD_DIR / save_name
    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)
    return JSONResponse({"fileId": file_id, "fileName": safe_name, "saveName": save_name})


# Register code mode router
from app.code_routes import router as code_router, active_streams as code_active_streams, code_responses
from app import code_routes
code_routes.get_session_lock = get_session_lock
app.include_router(code_router)

# Register paper mode router
from app.paper_routes import router as paper_router, active_paper_streams, paper_responses
from app import paper_routes
paper_routes.get_session_lock = get_session_lock
app.include_router(paper_router)

# Register terminal router
from app.terminal_routes import router as terminal_router
app.include_router(terminal_router)


@app.get("/settings/model")
async def get_selected_model():
    """Return saved model setting"""
    model = db.get_setting("selected_model") or "sonnet"
    return JSONResponse({"model": model})


@app.put("/settings/model")
async def save_selected_model(request: Request):
    """Save model setting"""
    data = await request.json()
    model = data.get("model", "sonnet")
    if model not in ("haiku", "sonnet", "opus"):
        model = "sonnet"
    db.set_setting("selected_model", model)
    return JSONResponse({"success": True})


@app.get("/mcp/tools")
async def get_mcp_tools(mode: str = ""):
    """Return cached MCP tool list (filtered by mode parameter)"""
    result = []
    for server_key, server in MCP_SERVERS.items():
        if mode and mode not in server.get("modes", ["chat", "code", "paper"]):
            continue
        for tool in server["tools"]:
            result.append({
                "serverName": server_key,
                "serverLabel": server_key,
                "name": tool["name"],
                "description": tool.get("description", "")
            })
    return JSONResponse(result)


@app.get("/mcp/settings")
async def get_mcp_settings():
    """Return saved MCP tool enabled list"""
    raw = db.get_setting("enabled_mcp_tools")
    if raw:
        try:
            return JSONResponse(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            pass
    return JSONResponse([])


@app.put("/mcp/settings")
async def save_mcp_settings(request: Request):
    """Save MCP tool enabled list"""
    tools = await request.json()
    db.set_setting("enabled_mcp_tools", json.dumps(tools))
    return JSONResponse({"success": True})


@app.get("/mcp/servers")
async def get_mcp_servers():
    """Return registered MCP server list"""
    result = []
    for key, server in MCP_SERVERS.items():
        info = {"key": key, "name": server.get("name", key), "type": server.get("type", "sse"), "modes": server.get("modes", []), "toolCount": len(server.get("tools", []))}
        if server.get("type") == "sse":
            info["url"] = server.get("url", "")
        elif server.get("type") == "stdio":
            info["command"] = server.get("command", "")
            info["args"] = server.get("args", [])
        result.append(info)
    return JSONResponse(result)


def _build_mcp_server_config(data: dict) -> tuple:
    """Build MCP server config. Returns (config, error_msg). config=None on error."""
    server_type = data.get("type", "sse")
    if server_type not in ("sse", "stdio"):
        return None, "Type must be sse or stdio."
    server_config = {
        "type": server_type,
        "modes": data.get("modes", ["chat", "code", "paper"]),
    }
    if server_type == "sse":
        url = data.get("url", "").strip()
        if not url:
            return None, "SSE server URL is required."
        server_config["url"] = url
    else:
        command = data.get("command", "").strip()
        if not command:
            return None, "Command is required."
        server_config["command"] = command
        server_config["args"] = data.get("args", [])
    return server_config, None


@app.post("/mcp/servers")
async def create_mcp_server(request: Request):
    """Add MCP server"""
    data = await request.json()
    name = data.get("name", "").strip()
    if not name or not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return JSONResponse({"success": False, "error": "Name can only contain letters, numbers, _, and -."}, status_code=400)
    server_config, error = _build_mcp_server_config(data)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)
    success, message = await asyncio.to_thread(add_mcp_server, name, server_config)
    if not success:
        return JSONResponse({"success": False, "error": message}, status_code=400)
    return JSONResponse({"success": True, "message": message})


@app.put("/mcp/servers/{key}")
async def edit_mcp_server(key: str, request: Request):
    """Update MCP server"""
    data = await request.json()
    server_config, error = _build_mcp_server_config(data)
    if error:
        return JSONResponse({"success": False, "error": error}, status_code=400)
    success, message = await asyncio.to_thread(update_mcp_server, key, server_config)
    if not success:
        return JSONResponse({"success": False, "error": message}, status_code=404)
    return JSONResponse({"success": True, "message": message})


@app.delete("/mcp/servers/{key}")
async def delete_mcp_server(key: str):
    """Delete MCP server"""
    success, message = remove_mcp_server(key)
    if not success:
        return JSONResponse({"success": False, "error": message}, status_code=404)
    return JSONResponse({"success": True, "message": message})


# ========== Chat HTML template ==========
def create_chat_response_html(response_id: str, session_id: str, message: str, include_user_message: bool = True) -> str:
    """Generate chat response HTML (code mode style, includes SSE connection)"""
    user_msg_html = render_user_message_html(message) if include_user_message else ""

    return f"""{user_msg_html}
    <div id="chat-response-{response_id}" class="mb-6">
        <div id="status-{response_id}" class="flex items-center gap-2 text-claude-text-secondary text-sm mb-3">
            <div class="w-5 h-5 rounded-full bg-claude-accent/20 flex items-center justify-center">
                <svg class="w-3 h-3 animate-spin text-claude-accent" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
            </div>
            <span id="status-text-{response_id}">Claude is thinking</span>
            <span id="timer-{response_id}" class="text-claude-text-secondary/60 text-xs">(0s)</span>
        </div>
        <div id="events-{response_id}" class="space-y-2 mb-3"></div>
    </div>
    <script>setupSSEHandlers({{responseId: "{response_id}", mode: "chat", sessionId: "{session_id}"}});</script>
    """


# ========== Chat API ==========

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Main page"""
    return templates.TemplateResponse("index.html", {"request": request})



@app.post("/chat", response_class=HTMLResponse)
async def chat(message: str = Form(""), session_id: str = Form(""), mcp_tools: str = Form(""), file_map: str = Form(""), model: str = Form("sonnet")):
    """Send chat message"""
    response_id, session_id, message = chat_handler(
        session_id, message, mcp_tools, file_map,
        CHAT_DIR, CHAT_SYSTEM_PROMPT,
        active_responses, chat_streams, get_session_lock, model
    )
    return create_chat_response_html(response_id, session_id, message, include_user_message=True)


@app.get("/chat/status/{response_id}")
async def get_chat_status(response_id: str):
    """Query active chat response status"""
    return JSONResponse(mode_status_response(
        response_id, active_responses, "session_id",
        extra_fields_fn=lambda resp: {
            "content": resp.get("content", ""),
            "title": resp.get("title"),
            "message_id": resp.get("message_id"),
        }
    ))


@app.get("/chat/active")
async def get_active_chats(session_id: str = ""):
    """Query active response ID list for session"""
    return JSONResponse(mode_active_response(active_responses, "session_id", session_id))


@app.get("/stream")
async def stream(id: str = "", start_from: int = 0):
    """Stream AI response via SSE"""
    return await mode_stream_sse(
        id, active_responses, chat_streams, start_from,
        extra_events_fn=make_chat_extra_events_fn(),
        done_data_fn=lambda resp: str(resp.get("message_id", "")) if resp.get("message_id") else ""
    )


def _cancel_streams(streams: dict, responses: dict, active_statuses: tuple) -> int:
    """Cancel streams and responses, return count of cancelled"""
    cancelled = 0
    for info in list(streams.values()):
        info["cancelled"] = True
        if info.get("process"):
            try:
                info["process"].terminate()
            except Exception:
                pass
        cancelled += 1
    for info in list(responses.values()):
        if info.get("status") in active_statuses:
            info["cancelled"] = True
            cancelled += 1
    return cancelled


@app.post("/stop")
async def stop_stream(mode: str = Form("chat")):
    """Stop streaming"""
    mode_map = {
        "chat": (chat_streams, active_responses, ("pending", "running")),
        "code": (code_active_streams, code_responses, ("pending", "running")),
        "paper": (active_paper_streams, paper_responses, ("pending", "running")),
    }
    if mode not in mode_map:
        return {"cancelled": 0}
    streams, responses, statuses = mode_map[mode]
    return {"cancelled": _cancel_streams(streams, responses, statuses)}


@app.post("/clear")
async def clear_history(session_id: str = Form("")):
    """Clear conversation history"""
    if session_id:
        db.delete_session(session_id)
    return HTMLResponse('<script>document.getElementById("session-title").textContent = "New Chat";</script>')


@app.get("/sessions")
async def get_sessions(mode: str = "chat"):
    """Query session list"""
    return JSONResponse(db.get_sessions_by_mode(mode))


@app.delete("/sessions/{mode}")
async def delete_all_sessions(mode: str):
    """Delete all sessions by mode"""
    db.delete_all_sessions_by_mode(mode)
    return JSONResponse({"success": True})



@app.get("/session/{session_id}/messages", response_class=HTMLResponse)
async def get_session_messages(session_id: str):
    """Session message list HTML"""
    if not db.get_session(session_id):
        return HTMLResponse("")

    html = ""
    for msg in db.get_messages(session_id):
        if msg["role"] == "user":
            html += render_user_message_html(msg["content"], include_script=False)
        else:
            message_id = msg.get("id", 0)
            content = msg.get("content", "")

            # Parse and render tool events
            events = []
            if msg.get("reasoning"):
                try:
                    events = json.loads(msg["reasoning"])
                except (json.JSONDecodeError, ValueError):
                    pass
            events_html = render_tool_events_html(events)

            # If events contain text, skip rendering content separately (prevent duplication)
            has_text_events = any(evt.get("type") == "text" for evt in events)
            content_html = "" if has_text_events else f'<div class="markdown-body" data-raw="{html_lib.escape(content)}"></div>'

            html += f'''
            <div class="mb-6" data-message-id="{message_id}">
                <div class="text-claude-text">
                    {events_html}
                    {content_html}
                    <div class="flex items-center justify-start gap-1 mt-4">
                        <button class="copy-btn p-1.5 text-claude-text-secondary hover:text-claude-text hover:bg-claude-sidebar rounded-lg transition-all" title="Copy all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
                        </button>
                    </div>
                </div>
            </div>
            '''

    return HTMLResponse(html)


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete session"""
    db.delete_session(session_id)
    return JSONResponse({"success": True})


@app.post("/archive/{mode}/{name}")
async def toggle_archive(mode: str, name: str):
    """Toggle project/paper archive"""
    if mode not in ("code", "paper"):
        return JSONResponse({"error": "Invalid mode."}, status_code=400)
    archived = db.archive_mode_project(mode, name)
    return JSONResponse({"success": True, "archived": archived})


@app.get("/archived/{mode}")
async def get_archived(mode: str):
    """List archived projects/papers"""
    if mode not in ("code", "paper"):
        return JSONResponse({"error": "Invalid mode."}, status_code=400)
    return JSONResponse(db.get_archived_projects(mode))


# ========== Commands API ==========

@app.get("/commands")
async def get_commands():
    """Query command list"""
    return JSONResponse(db.get_commands())


@app.get("/command/{command_id}")
async def get_command(command_id: int):
    """Query command"""
    cmd = db.get_command(command_id)
    if not cmd:
        return JSONResponse({"error": "Command not found"}, status_code=404)
    return JSONResponse(cmd)


@app.get("/command/name/{name}")
async def get_command_by_name(name: str):
    """Query command by name"""
    cmd = db.get_command_by_name(name)
    if not cmd:
        return JSONResponse({"error": "Command not found"}, status_code=404)
    return JSONResponse(cmd)


@app.post("/command")
async def create_command(name: str = Form(...), content: str = Form(...)):
    """Create command"""
    # Name validation (letters, numbers, hyphens, underscores only)
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return JSONResponse({"error": "Command name can only contain letters, numbers, hyphens, and underscores"}, status_code=400)

    # Duplicate check
    if db.get_command_by_name(name):
        return JSONResponse({"error": "Command name already exists"}, status_code=400)

    try:
        cmd = db.create_command(name, content)
        return JSONResponse(cmd)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/command/{command_id}")
async def update_command(command_id: int, name: str = Form(None), content: str = Form(None)):
    """Update command"""
    if name and not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return JSONResponse({"error": "Command name can only contain letters, numbers, hyphens, and underscores"}, status_code=400)

    # Duplicate check on name change
    if name:
        existing = db.get_command_by_name(name)
        if existing and existing["id"] != command_id:
            return JSONResponse({"error": "Command name already exists"}, status_code=400)

    if db.update_command(command_id, name, content):
        return JSONResponse({"success": True})
    return JSONResponse({"error": "Command not found"}, status_code=404)


@app.delete("/command/{command_id}")
async def delete_command(command_id: int):
    """Delete command"""
    if db.delete_command(command_id):
        return JSONResponse({"success": True})
    return JSONResponse({"error": "Command not found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
