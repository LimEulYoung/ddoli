"""
Common utilities
- HTML rendering (user messages, tool events)
- CLI tool result processing
- Command/file placeholder substitution
- Code/Paper mode common functions (generation, stream, status, etc.)
"""
import re
import uuid
import html as html_lib
import json
import subprocess
import threading
import time
import asyncio
import base64
import requests
from pathlib import Path
from urllib.parse import quote

from app import db
from app import config
from app.config import run_local_command

# File upload
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
ATTACHMENTS_DIR = config.ATTACHMENTS_DIR


# MCP server config (populated at runtime by load_mcp_servers())
MCP_SERVERS = {}

# Event types sent via SSE
SSE_EVENT_TYPES = {"init", "tool_use", "edit_result", "read_result", "bash_result", "tool_output", "text", "result"}


def _read_sse_response(sse_iter, target_id, timeout_lines=200):
    """Read and return a response for a specific JSON-RPC id from an SSE stream"""
    for i, line in enumerate(sse_iter):
        if i > timeout_lines:
            break
        if not line:
            continue
        if line.startswith("event:") and "message" in line:
            continue
        if line.startswith("data:"):
            data_str = line[5:].strip()
            try:
                data = json.loads(data_str)
                if data.get("id") == target_id:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def _extract_tools(tools_result) -> list:
    """Extract tool list from MCP tools/list response"""
    if not tools_result:
        return []
    return [{"name": t.get("name", ""), "description": t.get("description", "")}
            for t in tools_result.get("result", {}).get("tools", [])]


def discover_mcp_tools_sse(url: str) -> list:
    """Discover tool list via MCP SSE protocol.
    SSE transport: open stream with GET, send request with POST, receive response from SSE stream."""
    from urllib.parse import urlparse

    try:
        # Open and maintain SSE stream
        sse_resp = requests.get(url, stream=True, timeout=(10, 15))
        sse_resp.raise_for_status()
        sse_iter = sse_resp.iter_lines(decode_unicode=True)

        # Obtain message endpoint from first event
        message_endpoint = None
        for line in sse_iter:
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
                if data.startswith("/") or data.startswith("http"):
                    message_endpoint = data
                    break

        if not message_endpoint:
            sse_resp.close()
            return []

        # Construct absolute URL
        if message_endpoint.startswith("/"):
            parsed = urlparse(url)
            message_endpoint = f"{parsed.scheme}://{parsed.netloc}{message_endpoint}"

        def post_async(endpoint, payload):
            """Send POST in a separate thread (parallel with SSE reading)"""
            t = threading.Thread(target=lambda: requests.post(endpoint, json=payload, timeout=10))
            t.daemon = True
            t.start()
            return t

        # initialize: send POST request and wait for response from SSE
        post_async(message_endpoint, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ddoli", "version": "1.0.0"}
            }
        })

        init_result = _read_sse_response(sse_iter, 1)
        if not init_result:
            sse_resp.close()
            return []

        # initialized notification
        requests.post(message_endpoint, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        }, timeout=10)

        # tools/list: send POST request and wait for response from SSE
        post_async(message_endpoint, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })

        tools_result = _read_sse_response(sse_iter, 2)
        sse_resp.close()
        return _extract_tools(tools_result)

    except Exception:
        return []


def discover_mcp_tools_stdio(command: str, args: list) -> list:
    """Discover tool list from stdio MCP server.
    Run process locally and communicate via stdin/stdout JSON-RPC."""
    try:
        process = subprocess.Popen(
            [command] + args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        def send_jsonrpc(msg):
            data = json.dumps(msg)
            process.stdin.write(data + "\n")
            process.stdin.flush()

        def read_jsonrpc(target_id, timeout=15):
            import select
            deadline = time.time() + timeout
            buf = ""
            while time.time() < deadline:
                remaining = deadline - time.time()
                ready, _, _ = select.select([process.stdout], [], [], min(remaining, 0.5))
                if not ready:
                    continue
                chunk = process.stdout.readline()
                if not chunk:
                    break
                buf += chunk
                # JSON-RPC responses are line-delimited
                for line in buf.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("id") == target_id:
                            return data
                    except (json.JSONDecodeError, ValueError):
                        continue
                buf = ""
            return None

        # initialize
        send_jsonrpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ddoli", "version": "1.0.0"}
            }
        })

        init_result = read_jsonrpc(1)
        if not init_result:
            process.terminate()
            return []

        # initialized notification
        send_jsonrpc({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })

        # tools/list
        send_jsonrpc({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })

        tools_result = read_jsonrpc(2)
        process.terminate()
        return _extract_tools(tools_result)

    except Exception:
        return []


def load_mcp_servers():
    """Load MCP server list from DB. Falls back to config.DEFAULT_MCP_SERVERS if not found."""
    global MCP_SERVERS
    raw = db.get_setting("mcp_servers")
    if raw:
        try:
            saved = json.loads(raw)
            MCP_SERVERS.clear()
            for key, srv in saved.items():
                MCP_SERVERS[key] = {**srv, "tools": []}
            return
        except (json.JSONDecodeError, ValueError):
            pass
    # If not in DB, use defaults and save to DB
    MCP_SERVERS.clear()
    for key, srv in config.DEFAULT_MCP_SERVERS.items():
        MCP_SERVERS[key] = {**srv, "tools": []}
    save_mcp_servers()


def save_mcp_servers():
    """Save current MCP_SERVERS to DB (excluding tools)."""
    to_save = {}
    for name, srv in MCP_SERVERS.items():
        to_save[name] = {k: v for k, v in srv.items() if k != "tools"}
    db.set_setting("mcp_servers", json.dumps(to_save, ensure_ascii=False))


def _discover_server_tools(name, server):
    """Discover tools for a single MCP server"""
    server_type = server.get("type", "sse")
    if server_type == "sse":
        return discover_mcp_tools_sse(server["url"])
    elif server_type == "stdio":
        cmd = server.get("command", "")
        args = server.get("args", [])
        return discover_mcp_tools_stdio(cmd, args)
    else:
        return []


def _upsert_mcp_server(name: str, server_config: dict) -> tuple[bool, str]:
    """Common add/update for MCP server: save to DB -> discover tools. Returns (success, message)."""
    server = {**server_config, "tools": []}
    MCP_SERVERS[name] = server
    save_mcp_servers()
    tools = _discover_server_tools(name, server)
    server["tools"] = tools
    return True, f"{len(tools)} tools discovered"


def add_mcp_server(name: str, server_config: dict) -> tuple[bool, str]:
    """Add MCP server -> save to DB -> discover tools. Returns (success, message)."""
    if name in MCP_SERVERS:
        return False, f"Server '{name}' already exists."
    return _upsert_mcp_server(name, server_config)


def update_mcp_server(name: str, server_config: dict) -> tuple[bool, str]:
    """Update MCP server -> save to DB -> rediscover tools. Returns (success, message)."""
    if name not in MCP_SERVERS:
        return False, f"Server '{name}' not found."
    return _upsert_mcp_server(name, server_config)


def remove_mcp_server(name: str) -> tuple[bool, str]:
    """Remove MCP server -> save to DB. Returns (success, message)."""
    if name not in MCP_SERVERS:
        return False, f"Server '{name}' not found."
    del MCP_SERVERS[name]
    save_mcp_servers()
    return True, "Deleted"


def init_mcp_servers():
    """Load servers from DB and auto-discover tools from all MCP servers"""
    load_mcp_servers()
    for name, server in MCP_SERVERS.items():
        tools = _discover_server_tools(name, server)
        server["tools"] = tools


def build_mcp_flags(enabled_tools: list, mode: str = "") -> str:
    """Build MCP flag string from list of enabled tools.
    Explicitly specify MCP servers via --mcp-config (supports both SSE/stdio),
    disallow all MCP tools if enabled_tools is empty.
    If mode is specified, only include servers that support that mode."""

    flags = []
    all_mcp_tools = []

    # Single pass: generate --mcp-config flags + collect tool list
    for server_name, server in MCP_SERVERS.items():
        if mode and mode not in server.get("modes", ["chat", "code", "paper"]):
            continue
        server_type = server.get("type", "sse")
        if server_type == "sse" and server.get("url"):
            config_obj = {"mcpServers": {server_name: {"type": "sse", "url": server["url"]}}}
            config_json = json.dumps(config_obj, ensure_ascii=False)
            flags.append(f"--mcp-config '{config_json}'")
        elif server_type == "stdio" and server.get("command"):
            config_obj = {"mcpServers": {server_name: {"type": "stdio", "command": server["command"], "args": server.get("args", [])}}}
            config_json = json.dumps(config_obj, ensure_ascii=False)
            flags.append(f"--mcp-config '{config_json}'")

        for tool in server["tools"]:
            all_mcp_tools.append(f"mcp__{server_name}__{tool['name']}")

    if not all_mcp_tools:
        disabled = []
    elif not enabled_tools:
        # If no tools enabled, disable all MCP tools
        disabled = all_mcp_tools
    else:
        disabled = [t for t in all_mcp_tools
                     if t.split("__", 2)[-1] not in enabled_tools]

    # AskUserQuestion cannot be answered in mobile environments, so always block it
    disabled.append("AskUserQuestion")

    if disabled:
        disabled_str = " ".join(disabled)
        flags.append(f"--disallowedTools {disabled_str}")

    return " ".join(flags)


def calc_context_percent(data: dict) -> float:
    """Calculate context usage percentage from the last assistant message input tokens.
    modelUsage is inaccurate for multi-turn (tool use) as it sums all API calls.
    The last assistant's usage reflects actual session context usage."""
    # Get contextWindow
    context_window = 200000
    for model_data in data.get("modelUsage", {}).values():
        context_window = model_data.get("contextWindow", 200000)
        break

    # Use last assistant message input tokens (actual context usage)
    last_usage = data.get("_last_assistant_usage")
    if last_usage:
        total_input = (last_usage.get("input_tokens", 0) +
                       last_usage.get("cache_read_input_tokens", 0) +
                       last_usage.get("cache_creation_input_tokens", 0))
        return round((total_input / context_window) * 100, 1)

    # Fallback: use modelUsage (for single turn)
    for model_data in data.get("modelUsage", {}).values():
        total = model_data.get("inputTokens", 0) + model_data.get("outputTokens", 0) + \
                model_data.get("cacheReadInputTokens", 0) + model_data.get("cacheCreationInputTokens", 0)
        return round((total / context_window) * 100, 1)
    return 0


def build_local_command(cli_cmd: str) -> list:
    """Build local shell command"""
    return ["bash", "-c", cli_cmd]


def parse_cli_stream(process, is_cancelled_fn, events_list, on_text=None, on_result=None) -> str:
    """Parse CLI stdout JSON stream. Append events to events_list. Return final response text.

    on_text(text, full_response): called on text events
    on_result(data, full_response): called on result events (not directly added to events_list)
    """
    pending_tools = []
    full_response = ""
    last_assistant_usage = {}

    while True:
        if is_cancelled_fn():
            process.terminate()
            process.wait(timeout=5)
            break

        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue

        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
            event_type = data.get("type", "")

            if event_type == "system":
                events_list.append({
                    "type": "init",
                    "data": {"session_id": data.get("session_id", "")}
                })

            elif event_type == "assistant":
                msg_usage = data.get("message", {}).get("usage")
                if msg_usage:
                    last_assistant_usage = msg_usage
                for item in data.get("message", {}).get("content", []):
                    if item.get("type") == "text" and item.get("text"):
                        full_response = item["text"]
                        events_list.append({"type": "text", "data": {"text": item["text"]}})
                        if on_text:
                            on_text(item["text"], full_response)
                    elif item.get("type") == "tool_use":
                        tool_data = {
                            "id": item.get("id", ""),
                            "name": item.get("name", ""),
                            "input": item.get("input", {})
                        }
                        pending_tools.append(tool_data)
                        events_list.append({"type": "tool_use", "data": tool_data})

            elif event_type == "user":
                tool_result = data.get("tool_use_result")
                if tool_result:
                    tool_info = pending_tools.pop(0) if pending_tools else {}
                    evt = process_tool_result(tool_result, tool_info)
                    if evt:
                        events_list.append(evt)

            elif event_type == "result":
                result_text = data.get("result", "")
                if result_text:
                    full_response = result_text
                if last_assistant_usage:
                    data["_last_assistant_usage"] = last_assistant_usage
                if on_result:
                    on_result(data, full_response)

        except json.JSONDecodeError:
            pass

    process.wait()
    return full_response


def replace_command_placeholders(text: str) -> str:
    """Replace {{cmd:xxx}} patterns with actual command content"""
    pattern = r'\{\{cmd:([a-zA-Z0-9_-]+)\}\}'
    def replacer(match):
        cmd = db.get_command_by_name(match.group(1))
        return cmd["content"] if cmd else match.group(0)
    return re.sub(pattern, replacer, text)


def copy_upload_file(local_path: str, dest_path: str) -> tuple[bool, str]:
    """Copy file to local attachments directory"""
    import shutil
    try:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest_path)
        return True, ""
    except Exception as e:
        return False, str(e)


def replace_file_placeholders(text: str, file_map: dict = None) -> str:
    """Detect {{file:xxx}} patterns, copy to attachments directory, and replace with info message.
    file_map: {shortName: saveName} mapping (e.g., {"image1": "abc12345_photo.jpg"})"""
    if not file_map:
        file_map = {}
    pattern = r'\{\{file:([a-zA-Z0-9._-]+)\}\}'
    # Create directory only when file placeholders exist (once)
    if re.search(pattern, text):
        Path(ATTACHMENTS_DIR).mkdir(parents=True, exist_ok=True)
    def replacer(match):
        short_name = match.group(1)
        save_name = file_map.get(short_name, short_name)
        local_path = UPLOAD_DIR / save_name
        if not local_path.exists():
            return f"[Attachment not found: {short_name}]"
        dest_path = f"{ATTACHMENTS_DIR}/{save_name}"
        success, err = copy_upload_file(str(local_path), dest_path)
        if success:
            local_path.unlink(missing_ok=True)
            return f"\n[Attachment: {dest_path}]\n"
        return f"[File transfer failed: {err}]"
    return re.sub(pattern, replacer, text)


def process_tool_result(tool_result, tool_info):
    """Parse CLI tool_use_result and return event dict. Returns None if unparseable."""
    tool_id = tool_info.get("id", "")
    if isinstance(tool_result, str):
        return {"type": "tool_output", "data": {"toolId": tool_id, "output": tool_result}}
    if not isinstance(tool_result, dict):
        return None
    # structuredPatch -> edit_result or Write completion
    if "structuredPatch" in tool_result:
        if tool_info.get("name") == "Write":
            return {"type": "tool_output", "data": {"toolId": tool_id, "output": "File created"}}
        return {"type": "edit_result", "data": {"toolId": tool_id, "filePath": tool_result.get("filePath", ""), "patch": tool_result.get("structuredPatch", [])}}
    # file -> read_result
    if "file" in tool_result:
        f = tool_result["file"]
        return {"type": "read_result", "data": {"toolId": tool_id, "filePath": f.get("filePath", ""), "content": f.get("content", "")[:500]}}
    # stdout/stderr -> bash_result
    if "stdout" in tool_result or "stderr" in tool_result:
        cmd = tool_info.get("input", {}).get("command", "") if tool_info.get("name") == "Bash" else ""
        return {"type": "bash_result", "data": {"toolId": tool_id, "command": cmd, "stdout": tool_result.get("stdout", ""), "stderr": tool_result.get("stderr", ""), "exitCode": tool_result.get("exitCode", 0)}}
    # MCP/other tools
    text = tool_result.get("content") or tool_result.get("result") or ""
    if isinstance(text, str):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict): text = parsed.get("result") or parsed.get("content") or text
        except (json.JSONDecodeError, TypeError): pass
    return {"type": "tool_output", "data": {"toolId": tool_id, "output": text if isinstance(text, str) else str(text)}}


def render_user_message_html(content: str, include_script: bool = True) -> str:
    """User message HTML (with collapse feature)"""
    msg_id = str(uuid.uuid4())[:8]
    # Display {{file:xxx}} patterns as file badges (for history)
    display_content = content
    display_content = re.sub(r'\{\{file:([a-zA-Z0-9._-]+)\}\}', r'📎 \1', display_content)
    escaped = html_lib.escape(display_content).replace('\n', '<br>')
    html = f'''<div class="flex justify-end mb-4">
        <div class="relative max-w-[85%]">
            <div id="user-msg-{msg_id}" class="bg-claude-user-msg text-claude-text rounded-2xl px-4 py-2 user-msg-content collapsed">{escaped}</div>
            <button id="user-msg-toggle-{msg_id}" class="hidden absolute top-1 right-1 p-1 text-claude-text-secondary hover:text-claude-text hover:bg-claude-user-msg/50 rounded transition-colors" onclick="const el=document.getElementById('user-msg-{msg_id}');el.classList.toggle('collapsed');this.innerHTML=el.classList.contains('collapsed')?'<svg class=\\'w-4 h-4\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M19 9l-7 7-7-7\\'/></svg>':'<svg class=\\'w-4 h-4\\' fill=\\'none\\' stroke=\\'currentColor\\' viewBox=\\'0 0 24 24\\'><path stroke-linecap=\\'round\\' stroke-linejoin=\\'round\\' stroke-width=\\'2\\' d=\\'M5 15l7-7 7 7\\'/></svg>'">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            </button>
        </div>
    </div>'''
    if include_script:
        html += f'''<script>setTimeout(function(){{const el=document.getElementById('user-msg-{msg_id}');const btn=document.getElementById('user-msg-toggle-{msg_id}');if(el&&btn&&el.scrollHeight>el.clientHeight+10){{btn.classList.remove('hidden');el.classList.remove('px-4');el.classList.add('pl-4','pr-10');}}}},50);</script>'''
    return html


def _svg_icon(color, path_d):
    return f'<svg class="w-4 h-4 {color}" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{path_d}"/></svg>'

# (color, SVG path, title, key to extract from input)
_TOOL_REGISTRY = {
    "Read":      ("text-blue-600",   "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z", "Read file", "file_path"),
    "Write":     ("text-green-600",  "M12 4v16m8-8H4", "Create file", "file_path"),
    "Glob":      ("text-purple-600", "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", "Search", "pattern"),
    "Grep":      ("text-purple-600", "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z", "Search", "pattern"),
    "WebSearch": ("text-green-600",  "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9", "Web search", "query"),
    "WebFetch":  ("text-blue-500",   "M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1", "Web page", "url"),
}

def _get_tool_icon_title(tool_name: str, tool_input: dict) -> tuple[str, str, str]:
    """Return icon, title, and detail based on tool name/input"""
    if tool_name in _TOOL_REGISTRY:
        color, path_d, title, key = _TOOL_REGISTRY[tool_name]
        val = (tool_input.get(key, "") or "")
        detail = html_lib.escape(val.split("/")[-1] if key == "file_path" else val)
        return _svg_icon(color, path_d), title, detail
    # Default/MCP tools
    icon = _svg_icon("text-claude-text-secondary", "M13 10V3L4 14h7v7l9-11h-7z")
    if tool_name.startswith("mcp__") or tool_name.startswith("mcp_"):
        parts = tool_name.split("__")
        title = ("__".join(parts[2:]) if len(parts) >= 3 else parts[-1]).replace("_", " ")
    else:
        title = tool_name or "Tool"
    detail = "\n".join(f"{html_lib.escape(k)}: {html_lib.escape(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))}" for k, v in tool_input.items()) if tool_input else ""
    return icon, title, detail


def render_tool_events_html(events: list) -> str:
    """Render tool events list as HTML"""
    if not events:
        return ""

    parts = []
    tool_info_map, result_tool_ids = {}, set()
    for evt in events:
        t, d = evt.get("type", ""), evt.get("data", {})
        if t == "tool_use": tool_info_map[d.get("id", "")] = d
        elif t in ("edit_result", "bash_result", "tool_output"): result_tool_ids.add(d.get("toolId", ""))

    def _detail_pre(text): return f'<div class="px-3 py-2 bg-slate-100 border-t border-claude-border"><pre class="text-xs text-slate-600 font-mono whitespace-pre-wrap break-all">{text}</pre></div>' if text else ""
    def _output_pre(text): return f'<div class="px-3 py-2 max-h-60 overflow-y-auto border-t border-claude-border"><pre class="text-xs text-claude-text whitespace-pre-wrap break-all">{html_lib.escape(text)}</pre></div>' if text else ""
    def _tool_card(icon, title, status_cls, status_text, body=""): return f'<div class="bg-white rounded-lg overflow-hidden border border-claude-border mb-2"><div class="px-3 py-2 flex items-center gap-2 bg-claude-sidebar">{icon}<span class="text-sm text-claude-text">{title}</span><span class="text-xs {status_cls} ml-auto">{status_text}</span></div>{body}</div>'

    for evt in events:
        t, d = evt.get("type", ""), evt.get("data", {})
        if t == "tool_use":
            name, inp, tid = d.get("name", ""), d.get("input", {}), d.get("id", "")
            if name in ("Edit", "Bash") or tid in result_tool_ids: continue
            icon, title, detail = _get_tool_icon_title(name, inp)
            parts.append(_tool_card(icon, title, "text-claude-accent", "Done", _detail_pre(detail)))
        elif t == "edit_result":
            fn = html_lib.escape(d.get("filePath", "").split("/")[-1]) if d.get("filePath") else ""
            diff = "".join(f'<div class="bg-{"red" if l.startswith("-") else "green"}-100 text-{"red" if l.startswith("-") else "green"}-700 px-2 font-mono text-xs">{html_lib.escape(l)}</div>' for p in d.get("patch", []) for l in p.get("lines", []) if l.startswith(("-", "+")))
            parts.append(f'<div class="bg-white rounded-lg overflow-hidden border border-claude-accent mb-2"><div class="px-3 py-2 bg-claude-accent/5 flex items-center gap-2">{_svg_icon("text-claude-accent", "M5 13l4 4L19 7")}<span class="text-sm text-claude-text">File modified</span><span class="text-xs text-claude-text-secondary">{fn}</span></div><div class="max-h-32 overflow-y-auto">{diff}</div></div>')
        elif t == "bash_result":
            cmd, output = d.get("command", ""), d.get("stdout", "") or d.get("stderr", "")
            err = d.get("exitCode", 0) != 0 or d.get("stderr", "")
            sc, st = ("text-red-500", "Failed") if err else ("text-claude-accent", "Done")
            cmd_html = f'<div class="px-3 py-2 bg-slate-100 border-t border-claude-border"><pre class="text-xs text-slate-600 font-mono">$ {html_lib.escape(cmd)}</pre></div>' if cmd else ""
            parts.append(_tool_card(_svg_icon(sc, "M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"), "Run command", sc, st, cmd_html + _output_pre(output)))
        elif t == "tool_output":
            info = tool_info_map.get(d.get("toolId", ""), {})
            if info.get("name") in ("Edit", "Bash"): continue
            icon, title, detail = _get_tool_icon_title(info.get("name", ""), info.get("input", {}))
            parts.append(_tool_card(icon, title, "text-claude-accent", "Done", _detail_pre(detail) + _output_pre(d.get("output", ""))))
        elif t == "text" and d.get("text"):
            parts.append(f'<div class="bg-white rounded-lg border border-claude-border p-4 mb-2"><div class="markdown-body text-claude-text" data-raw="{html_lib.escape(d["text"])}">{html_lib.escape(d["text"])}</div></div>')

    return "".join(parts)


# ========== Code/Paper mode common functions ==========

def parse_file_map(file_map: str) -> dict:
    """Parse file mapping string to dict (shortName:saveName,...)"""
    fmap = {}
    if file_map:
        for pair in file_map.split(","):
            if ":" in pair:
                k, v = pair.split(":", 1)
                fmap[k.strip()] = v.strip()
    return fmap


def cleanup_old_responses(responses: dict):
    """Clean up old completed responses (older than 10 minutes)"""
    now = time.time()
    expired = [rid for rid, r in responses.items()
               if r.get("status") in ("completed", "error") and now - r.get("created_at", 0) > 600]
    for rid in expired:
        del responses[rid]


def get_or_create_mode_session(mode: str, name: str, get_messages_fn) -> tuple[str, bool, str]:
    """Return mode-specific session ID. Returns (session_id, is_first_message, cli_session_id)"""
    session_id = f"{mode}_{name}"
    messages = get_messages_fn(name)
    is_first = len(messages) <= 1
    cli_session_id = db.get_setting(f"cli_session_{session_id}")
    if not cli_session_id:
        cli_session_id = str(uuid.uuid4())
        db.set_setting(f"cli_session_{session_id}", cli_session_id)
    return session_id, is_first, cli_session_id


def run_mode_generation(response_id: str, mode: str, name: str, message: str,
                        is_first_message: bool, cli_session_id: str, mcp_tools: str,
                        work_dir: str, responses: dict, streams: dict,
                        get_session_lock, update_context_fn, add_message_fn,
                        model: str = "sonnet", mode_opts: dict = None):
    """Run Claude CLI in background (common for chat/code/paper)

    mode_opts (optional):
        lock_key: lock key (default: f"{mode}_{name}")
        extra_cli_flags: additional CLI flags (e.g., --system-prompt, --tools)
        status_running: running status name (default: "running")
        on_first_message: first message hook callable(response_id, message)
        work_dir_suffix: working directory suffix (None->/{name}, ""->none)
        on_text: text event hook callable(text, full_response)
        event_filter: event filter set for DB save (None->save all)
    """
    opts = mode_opts or {}
    lock_key = opts.get("lock_key", f"{mode}_{name}")
    lock = get_session_lock(lock_key)
    if not lock.acquire(timeout=120):
        responses[response_id]["status"] = "error"
        responses[response_id]["error"] = "Previous response is still processing."
        streams.pop(response_id, None)
        return

    escaped_message = message.replace("'", "'\"'\"'")
    session_flag = f"--session-id '{cli_session_id}'" if is_first_message else f"--resume '{cli_session_id}'"
    enabled = [t.strip() for t in mcp_tools.split(",") if t.strip()] if mcp_tools else []
    mcp_flags = build_mcp_flags(enabled, mode=mode)
    extra_flags = opts.get("extra_cli_flags", "")

    # Determine working directory
    suffix = opts.get("work_dir_suffix")
    if suffix is not None:
        dir_part = f"{work_dir}/{suffix}" if suffix else work_dir
    else:
        dir_part = f"{work_dir}/{name}"

    cli_cmd = f"cd {dir_part} && echo '{escaped_message}' | claude -p --output-format stream-json --verbose --dangerously-skip-permissions --model {model} {session_flag} {extra_flags} {mcp_flags}"

    try:
        # First message hook (e.g., chat title generation)
        on_first = opts.get("on_first_message")
        if on_first and is_first_message:
            on_first(response_id, message)

        if responses.get(response_id, {}).get("cancelled"):
            responses[response_id]["status"] = "completed"
            return

        status_running = opts.get("status_running", "running")
        responses[response_id]["status"] = status_running

        process = subprocess.Popen(
            build_local_command(cli_cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        streams[response_id]["process"] = process
        resp = responses[response_id]

        def on_result(data, full):
            context_percent = calc_context_percent(data)
            resp["final_result"] = full
            resp["content"] = full
            resp["context_percent"] = context_percent
            resp["events"].append({
                "type": "result",
                "data": {"text": data.get("result", ""), "contextPercent": context_percent}
            })
            if context_percent > 0:
                update_context_fn(name, context_percent)

        on_text = opts.get("on_text")
        final_result = parse_cli_stream(
            process,
            lambda: resp.get("cancelled", False),
            resp["events"],
            on_text=on_text,
            on_result=on_result
        )

        # Save to DB
        event_filter = opts.get("event_filter")
        if event_filter:
            collected_events = [evt for evt in resp["events"] if evt.get("type") in event_filter]
        else:
            collected_events = resp["events"]
        if not resp.get("cancelled") and (final_result or collected_events):
            add_message_fn(name, "assistant", final_result, collected_events if collected_events else None)

        resp["status"] = "completed"

    except Exception as e:
        responses[response_id]["status"] = "error"
        responses[response_id]["error"] = str(e)
    finally:
        streams.pop(response_id, None)
        lock.release()


async def mode_stream_sse(id: str, responses: dict, streams: dict, start_from: int = 0,
                          extra_events_fn=None, done_data_fn=None):
    """SSE streaming generator (common for chat/code/paper)

    extra_events_fn: callable(resp) -> list[dict] - additional SSE events (chat: title, session_id, status)
    done_data_fn: callable(resp) -> str - done event data (chat: message_id)
    """
    from sse_starlette.sse import EventSourceResponse

    async def generate():
        if id not in responses:
            yield {"event": "error_msg", "data": "Session not found."}
            yield {"event": "done", "data": ""}
            return

        resp = responses[id]
        last_event_idx = max(0, start_from)

        while True:
            if streams.get(id, {}).get("cancelled"):
                responses[id]["cancelled"] = True
                yield {"event": "done", "data": ""}
                return

            status = resp.get("status", "pending")

            # Mode-specific additional events (chat: title, session_id, status)
            if extra_events_fn:
                for extra_evt in extra_events_fn(resp):
                    yield extra_evt

            # Send tool events
            events = resp.get("events", [])
            while last_event_idx < len(events):
                evt = events[last_event_idx]
                evt_type = evt.get("type", "")
                evt_data = evt.get("data", {})
                if evt_type in SSE_EVENT_TYPES:
                    evt_data_with_idx = {**evt_data, "_idx": last_event_idx}
                    yield {"event": evt_type, "data": json.dumps(evt_data_with_idx)}
                last_event_idx += 1

            if status == "completed":
                done_data = done_data_fn(resp) if done_data_fn else ""
                yield {"event": "done", "data": done_data}
                return
            elif status == "error":
                yield {"event": "error_msg", "data": resp.get("error", "Unknown error")}
                yield {"event": "done", "data": ""}
                return

            await asyncio.sleep(0.05)

    return EventSourceResponse(generate())


def mode_status_response(response_id: str, responses: dict, item_key: str,
                         extra_fields_fn=None):
    """Query in-progress response status (common for chat/code/paper)
    extra_fields_fn: callable(resp) -> dict - additional fields (chat: content, title, message_id)"""
    if response_id not in responses:
        return {"status": "not_found"}
    resp = responses[response_id]
    result = {
        "status": resp.get("status", "unknown"),
        "events": resp.get("events", []),
        "final_result": resp.get("final_result", ""),
        "context_percent": resp.get("context_percent", 0),
        "error": resp.get("error"),
        item_key: resp.get(item_key, "")
    }
    if extra_fields_fn:
        result.update(extra_fields_fn(resp))
    return result


def mode_active_response(responses: dict, item_key: str, item_value: str,
                         active_statuses=("pending", "running")):
    """Query list of in-progress response IDs (common for chat/code/paper)"""
    active = []
    for rid, data in responses.items():
        if data.get("status") in active_statuses:
            if not item_value or data.get(item_key) == item_value:
                active.append(rid)
    return {"active": active}


MEDIA_EXTENSIONS = {
    'image': {'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'heic', 'heif'},
    'video': {'mp4', 'webm', 'mov', 'ogg'},
}

MEDIA_MIME = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'webp': 'image/webp', 'svg': 'image/svg+xml',
    'bmp': 'image/bmp', 'ico': 'image/x-icon',
    'heic': 'image/heic', 'heif': 'image/heif',
    'mp4': 'video/mp4', 'webm': 'video/webm', 'mov': 'video/quicktime',
    'ogg': 'video/ogg',
    'pdf': 'application/pdf',
}


def _get_media_type(path: str):
    """Return media type by file extension. Returns None if not media"""
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    for mtype, exts in MEDIA_EXTENSIONS.items():
        if ext in exts:
            return mtype, MEDIA_MIME.get(ext, 'application/octet-stream')
    return None, None


def _shell_quote_path(base_dir: str, name: str, path: str) -> str:
    """Safely wrap shell path in single quotes (handles unicode/spaces, preserves ~ expansion)"""
    full = f"{base_dir}/{name}/{path}"
    # ~ must be outside quotes for shell to expand to home directory
    if full.startswith("~/"):
        rest = full[2:].replace("'", "'\"'\"'")
        return "~/" + "'" + rest + "'"
    return "'" + full.replace("'", "'\"'\"'") + "'"


def mode_file_content(base_dir: str, name: str, path: str):
    """Get file content (common for Code/Paper)"""
    if not name or not path:
        return {"error": "Project and path are required."}
    if ".." in path:
        return {"error": "Invalid path."}
    safe = _shell_quote_path(base_dir, name, path)
    success, output = run_local_command(f"cat {safe} 2>&1 | head -1000")
    if not success:
        return {"error": output or "Cannot read file."}
    return {"content": output, "path": path}


def mode_file_write(base_dir: str, name: str, path: str, content: str):
    """Save file content (common for Code/Paper)"""
    if not name or not path:
        return {"error": "Project and path are required."}
    if ".." in path or path.startswith("/"):
        return {"error": "Invalid path."}
    safe = _shell_quote_path(base_dir, name, path)
    # Encode content as base64 (prevent shell injection)
    import base64 as b64mod
    encoded = b64mod.b64encode(content.encode('utf-8')).decode('ascii')
    success, output = run_local_command(f"echo '{encoded}' | base64 -d > {safe}")
    if not success:
        return {"error": output or "Cannot save file."}
    return {"success": True, "path": path}


def _read_file_base64(base_dir: str, name: str, path: str, require_media: bool = False):
    """Read remote file as base64 (common internal helper).
    If require_media=True, only allow media files.
    Returns: (b64_data, mime, error)"""
    if not name or not path:
        return None, None, "Project and path are required."
    if ".." in path or path.startswith("/"):
        return None, None, "Invalid path."
    if require_media:
        media_type, mime = _get_media_type(path)
        if not media_type:
            return None, None, "Not a media file."
    else:
        ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
        mime = MEDIA_MIME.get(ext, 'application/octet-stream')
    safe = _shell_quote_path(base_dir, name, path)
    success, size_out = run_local_command(f"stat -c%s {safe} 2>/dev/null")
    if not success:
        return None, None, "File not found."
    try:
        if int(size_out.strip()) > 200 * 1024 * 1024:
            return None, None, "File too large (200MB limit)."
    except ValueError:
        return None, None, "Cannot determine file size."
    success, b64_out = run_local_command(f"base64 -w0 {safe}", timeout=120)
    if not success:
        return None, None, "Cannot read file."
    return b64_out.strip(), mime, None


def mode_file_raw(base_dir: str, name: str, path: str):
    """Get media file as base64 (common for Code/Paper)"""
    return _read_file_base64(base_dir, name, path, require_media=True)


def mode_file_download(base_dir: str, name: str, path: str):
    """Get file as base64 for download (all file types, common for Code/Paper)"""
    return _read_file_base64(base_dir, name, path, require_media=False)


def make_file_raw_response(base_dir: str, name: str, path: str):
    """Generate HTTP response for media file (common for Code/Paper)"""
    from fastapi.responses import JSONResponse, Response
    b64_data, mime, error = mode_file_raw(base_dir, name, path)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    return Response(content=base64.b64decode(b64_data), media_type=mime)


def make_file_download_response(base_dir: str, name: str, path: str):
    """Generate HTTP response for file download (common for Code/Paper)"""
    from fastapi.responses import JSONResponse, Response
    b64_data, mime, error = mode_file_download(base_dir, name, path)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    filename = path.split('/')[-1] if '/' in path else path
    encoded = quote(filename)
    return Response(
        content=base64.b64decode(b64_data), media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"})


def mode_delete_file(base_dir: str, name: str, path: str, name_pattern: str = r'^[a-zA-Z0-9_-]+$'):
    """Delete file (common for Code/Paper)"""
    if not name or not path:
        return {"error": "Project and file path are required."}, 400
    if ".." in path or path.startswith("/"):
        return {"error": "Invalid file path."}, 400
    if not re.match(name_pattern, name):
        return {"error": "Invalid name."}, 400
    safe = _shell_quote_path(base_dir, name, path)
    success, output = run_local_command(f"rm -rf {safe}")
    if not success:
        return {"error": f"File deletion failed: {output}"}, 500
    return {"success": True, "message": f"'{path}' has been deleted."}, 200


def mode_create_file(base_dir: str, name: str, path: str, filename: str, name_pattern: str = r'^[a-zA-Z0-9_-]+$'):
    """Create file (common for Code/Paper)"""
    if not name or not filename:
        return {"error": "Project and filename are required."}, 400
    if ".." in path or (path.startswith("/") and path != "/") or ".." in filename or "/" in filename:
        return {"error": "Invalid path."}, 400
    if not re.match(name_pattern, name):
        return {"error": "Invalid name."}, 400
    # If path is "/" then root, otherwise subdirectory path (remove trailing slash)
    clean_path = path.rstrip('/')
    rel = filename if (not clean_path or clean_path == '') else f"{clean_path}/{filename}"
    safe = _shell_quote_path(base_dir, name, rel)
    # Create parent directory then create file
    parent = '/'.join(rel.split('/')[:-1])
    if parent:
        parent_safe = _shell_quote_path(base_dir, name, parent)
        run_local_command(f"mkdir -p {parent_safe}")
    success, output = run_local_command(f"touch {safe}")
    if not success:
        return {"error": f"File creation failed: {output}"}, 500
    return {"success": True, "message": f"'{rel}' has been created."}, 200


def mode_create_folder(base_dir: str, name: str, path: str, foldername: str, name_pattern: str = r'^[a-zA-Z0-9_-]+$'):
    """Create folder (common for Code/Paper)"""
    if not name or not foldername:
        return {"error": "Project and folder name are required."}, 400
    if ".." in path or (path.startswith("/") and path != "/") or ".." in foldername or "/" in foldername:
        return {"error": "Invalid path."}, 400
    if not re.match(name_pattern, name):
        return {"error": "Invalid name."}, 400
    clean_path = path.rstrip('/')
    rel = foldername if (not clean_path or clean_path == '') else f"{clean_path}/{foldername}"
    safe = _shell_quote_path(base_dir, name, rel)
    success, output = run_local_command(f"mkdir -p {safe}")
    if not success:
        return {"error": f"Folder creation failed: {output}"}, 500
    return {"success": True, "message": f"'{rel}' has been created."}, 200


def mode_list_directories(base_dir: str, name: str, find_extra: str = ""):
    """List directories in project (for selecting creation location)"""
    if not name:
        return ["/"]
    cmd = f"cd {base_dir}/{name} && find . -maxdepth 4 {find_extra}-type d -print | grep -v '/\\.' | sort"
    success, output = run_local_command(cmd)
    dirs = ["/"]
    if success and output.strip():
        for line in output.split('\n'):
            line = line.strip()
            if not line or line == '.':
                continue
            path = line[2:] if line.startswith('./') else line
            if path:
                dirs.append(path + '/')
    return dirs


def render_file_tree_html(base_dir: str, name: str, mode: str, find_extra: str = "",
                          ext_colors: dict = None):
    """Generate file tree HTML (common for Code/Paper)
    mode: 'code' or 'paper' (determines JS handler names)
    find_extra: prune options to add to find command
    ext_colors: color mapping by file extension"""
    if not name:
        return '<div class="text-claude-text-secondary text-xs">Select a project</div>'

    # Get all directories, limit files to 1000
    dir_cmd = f"cd {base_dir}/{name} && find . -maxdepth 4 {find_extra}-type d -print | grep -v '^\\.$' | grep -v '/\\.' | sort"
    file_cmd = f"cd {base_dir}/{name} && find . -maxdepth 4 {find_extra}-type f -print | grep -v '/\\.' | sort | head -1000"
    success_d, output_d = run_local_command(dir_cmd)
    success_f, output_f = run_local_command(file_cmd)

    if not success_d or not success_f:
        return f'<div class="text-red-500 text-xs">Error: {output_d if not success_d else output_f}</div>'

    dir_lines = [l.strip() for l in output_d.split('\n') if l.strip()] if output_d.strip() else []
    file_lines = [l.strip() for l in output_f.split('\n') if l.strip()] if output_f.strip() else []
    file_truncated = len(file_lines) >= 1000

    if not dir_lines and not file_lines:
        return '<div class="text-claude-text-secondary text-xs py-4 text-center">Empty project</div>'

    dir_set = set(dir_lines)
    lines = sorted(dir_lines + file_lines)

    # Determine JS handler names
    open_fn = f"_openFile('{mode}', "
    delete_fn = f"_deleteFile('{mode}', "

    if ext_colors is None:
        ext_colors = {'py': 'text-yellow-600', 'js': 'text-yellow-500', 'ts': 'text-blue-500',
                      'html': 'text-orange-500', 'css': 'text-blue-400', 'json': 'text-green-500',
                      'md': 'text-gray-500', 'tex': 'text-green-600'}

    dirs_with_children = set()
    for line in lines:
        path = line[2:] if line.startswith('./') else line
        if '/' in path:
            dirs_with_children.add('/'.join(path.split('/')[:-1]))

    html = '<div class="space-y-0.5 file-tree-container">'

    for line in lines:
        path = line[2:] if line.startswith('./') else line
        if not path:
            continue

        depth = path.count('/')
        indent = depth * 12
        file_name = path.split('/')[-1]
        parent_path = '/'.join(path.split('/')[:-1]) if '/' in path else ''
        is_dir = line in dir_set
        escaped_path = path.replace("'", "\\'")
        escaped_path_attr = html_lib.escape(path, quote=True)
        escaped_parent_attr = html_lib.escape(parent_path, quote=True) if parent_path else ''
        parent_attr = f'data-parent="{escaped_parent_attr}"' if parent_path else ''
        hidden_class = 'hidden' if depth > 0 else ''

        escaped_name_attr = html_lib.escape(file_name.replace("'", "\\'"), quote=True)
        ctx_type = 'file' if not is_dir else 'dir'
        more_btn = f'''<button @click.stop="openFileContextMenu($event, '{ctx_type}', {{mode: '{mode}', path: '{escaped_path}', name: '{escaped_name_attr}'}})" class="hidden sm:block opacity-0 group-hover:opacity-100 p-0.5 text-claude-text-secondary hover:text-claude-text rounded transition-opacity" title="More">
                    <svg class="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                        <circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/>
                    </svg>
                </button>'''

        if is_dir:
            has_children = path in dirs_with_children
            chevron = f'''<svg class="w-3 h-3 text-claude-text-secondary shrink-0 folder-chevron transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/>
                </svg>''' if has_children else '<span class="w-3"></span>'

            html += f'''
            <div class="group flex items-center gap-1 py-0.5 hover:bg-claude-border rounded px-1 {hidden_class}"
                 data-path="{escaped_path_attr}" {parent_attr} style="padding-left: {indent}px"
                 @click="toggleFolder('{escaped_path}', '{mode}')"
                 @touchstart="startLongPress($event, 'dir', {{mode: '{mode}', path: '{escaped_path}', name: '{escaped_name_attr}'}})"
                 @touchend="endLongPress($event)" @touchmove="cancelLongPress()" @contextmenu.prevent>
                {chevron}
                <svg class="w-4 h-4 text-blue-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"/>
                </svg>
                <span class="truncate text-claude-text flex-1">{html_lib.escape(file_name)}</span>
                {more_btn}
            </div>
            '''
        else:
            ext = file_name.split('.')[-1].lower()
            color = ext_colors.get(ext, 'text-gray-400')

            html += f'''
            <div @click="{open_fn}'{escaped_path}')" class="group flex items-center gap-1 py-0.5 hover:bg-claude-border rounded px-1 cursor-pointer {hidden_class}"
                 data-path="{escaped_path_attr}" {parent_attr} style="padding-left: {indent + 12}px"
                 @touchstart="startLongPress($event, 'file', {{mode: '{mode}', path: '{escaped_path}', name: '{escaped_name_attr}'}})"
                 @touchend="endLongPress($event)" @touchmove="cancelLongPress()" @contextmenu.prevent>
                <svg class="w-4 h-4 {color} shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                <span class="truncate text-claude-text flex-1">{html_lib.escape(file_name)}</span>
                {more_btn}
            </div>
            '''

    if file_truncated:
        html += '<div class="text-claude-text-secondary text-xs py-2 px-2 text-center">Too many files, showing only 1000</div>'

    html += '</div>'
    return html


def mode_clear_session(mode: str, name: str, param_label: str):
    """Common session reset handler (Code/Paper)"""
    if name:
        db.clear_mode_session(mode, name)
        new_cli_session_id = str(uuid.uuid4())
        db.set_setting(f"cli_session_{mode}_{name}", new_cli_session_id)
        return {"success": True, "session_id": f"{mode}_{name}"}
    return None  # 400 error handling is done by the caller


def mode_context_percent(mode: str, name: str):
    """Common handler for querying context percentage"""
    if not name:
        return {"contextPercent": 0}
    return {"contextPercent": db.get_mode_context_percent(mode, name)}


def mode_messages_html(mode: str, name: str, path_prefix: str):
    """Common handler for returning messages HTML"""
    if not name:
        return ""
    messages = db.get_mode_messages(mode, name)
    return render_mode_messages_html(
        messages, f"{path_prefix}/{html_lib.escape(name)}"
    )


def render_mode_messages_html(messages: list, path_label: str):
    """Render Code/Paper mode messages as HTML"""
    connection_html = f"""
    <div class="bg-claude-accent/5 border border-claude-accent/20 rounded-lg p-4 flex items-center gap-3 mb-4">
        <div class="w-3 h-3 bg-claude-accent rounded-full animate-pulse"></div>
        <div>
            <div class="font-medium text-claude-text">Claude Code connected</div>
            <div class="text-sm text-claude-text-secondary">{path_label}</div>
        </div>
    </div>
    """

    if not messages:
        return connection_html

    html = connection_html
    for msg in messages:
        if msg["role"] == "user":
            html += render_user_message_html(msg["content"], include_script=False)
        else:
            events = []
            if msg.get("reasoning"):
                try:
                    events = json.loads(msg["reasoning"])
                except (json.JSONDecodeError, ValueError):
                    pass
            events_html = render_tool_events_html(events)
            has_text_events = any(evt.get("type") == "text" for evt in events)
            if msg["content"] and not has_text_events:
                html += f'<div class="mb-4 space-y-2">{events_html}<div class="bg-white border border-claude-border rounded-lg p-4 text-claude-text text-sm"><div class="markdown-body" data-raw="{html_lib.escape(msg["content"])}">{html_lib.escape(msg["content"])}</div></div></div>'
            elif events_html:
                html += f'<div class="mb-4 space-y-2">{events_html}</div>'

    return html


def mode_chat_handler(mode: str, name: str, message: str, mcp_tools: str, file_map: str,
                      work_dir: str, responses: dict, streams: dict, get_session_lock,
                      label: str, status_text: str, model: str = "sonnet"):
    """Common chat handler for Code/Paper mode: preprocess message -> start thread -> return HTML"""
    if not name or not message:
        return f'<div class="text-red-500">{label} is required.</div>'

    # Model validation
    allowed_models = ("haiku", "sonnet", "opus")
    if model not in allowed_models:
        model = "sonnet"

    cleanup_old_responses(responses)
    message = replace_command_placeholders(message)
    fmap = parse_file_map(file_map)

    db.add_mode_message(mode, name, "user", message)
    cli_message = replace_file_placeholders(message, fmap) if fmap else message

    _, is_first_message, cli_session_id = get_or_create_mode_session(mode, name, lambda p: db.get_mode_messages(mode, p))
    response_id = str(uuid.uuid4())[:8]

    item_key = "paper" if mode == "paper" else "project"
    responses[response_id] = {
        item_key: name, "status": "pending", "events": [], "final_result": "",
        "context_percent": 0, "error": None, "cancelled": False, "created_at": time.time()
    }
    streams[response_id] = {"cancelled": False, "process": None}

    mode_opts = {"event_filter": {"text", "tool_use", "edit_result", "bash_result"}}
    thread = threading.Thread(
        target=run_mode_generation,
        args=(response_id, mode, name, cli_message, is_first_message, cli_session_id, mcp_tools,
              work_dir, responses, streams, get_session_lock,
              lambda n, pct: db.update_mode_context_percent(mode, n, pct),
              lambda n, role, content, events=None: db.add_mode_message(mode, n, role, content, events),
              model, mode_opts)
    )
    thread.daemon = True
    thread.start()

    escaped_name = html_lib.escape(name)
    return f"""
    {render_user_message_html(message)}
    <div id="{mode}-response-{response_id}" class="mb-4 space-y-3">
        <div id="status-{response_id}" class="flex items-center gap-2 text-claude-text-secondary text-sm">
            <svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span id="status-text-{response_id}">{status_text}</span>
            <span id="timer-{response_id}" class="text-claude-text-secondary">(0s)</span>
        </div>
        <div id="events-{response_id}" class="space-y-3"></div>
        <div id="final-{response_id}" class="hidden"></div>
    </div>
    <script>setupSSEHandlers({{responseId: "{response_id}", mode: "{mode}", {item_key}: "{escaped_name}"}});</script>
    """


# ========== Chat mode specific functions ==========

def generate_session_title(user_message: str) -> str:
    """Generate session title based on first message (Claude CLI Haiku, 5s timeout)"""
    fallback = user_message[:12] + "..." if len(user_message) > 12 else user_message
    try:
        prompt = f"Create a short 2-4 word English title for the following question. Output only the title.\n\nQuestion: {user_message[:200]}\n\nTitle:"
        escaped_prompt = prompt.replace("'", "'\"'\"'")
        cli_cmd = f"echo '{escaped_prompt}' | claude -p --model haiku --output-format stream-json --verbose --max-turns 1"
        result = subprocess.run(
            ["bash", "-c", cli_cmd],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                try:
                    data = json.loads(line)
                    if data.get("type") == "result":
                        title = data.get("result", "").strip().strip('"\'').strip()
                        if title.startswith("Title:"): title = title[6:].strip()
                        if title and len(title) <= 30:
                            return title
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return fallback


def chat_handler(session_id: str, message: str, mcp_tools: str, file_map: str,
                 chat_dir: str, system_prompt: str, responses: dict, streams: dict,
                 get_session_lock, model: str = "sonnet"):
    """Chat mode handler: preprocess -> start thread -> return (response_id, session_id, message)"""
    allowed_models = ("haiku", "sonnet", "opus")
    if model not in allowed_models:
        model = "sonnet"

    cleanup_old_responses(responses)
    message = replace_command_placeholders(message)
    fmap = parse_file_map(file_map)

    if not session_id:
        session_id = str(uuid.uuid4())
        db.create_session(session_id, mode="chat")
    elif not db.get_session(session_id):
        db.create_session(session_id, mode="chat")

    db.add_message(session_id, "user", message)
    cli_message = replace_file_placeholders(message, fmap) if fmap else message
    is_first_message = db.count_user_messages(session_id) == 1

    response_id = str(uuid.uuid4())[:8]
    responses[response_id] = {
        "session_id": session_id,
        "status": "pending",
        "content": "",
        "title": None,
        "message_id": None,
        "error": None,
        "cancelled": False,
        "created_at": time.time(),
        "events": []
    }
    streams[response_id] = {"cancelled": False, "process": None}

    escaped_prompt = system_prompt.replace("'", "'\"'\"'")
    extra_cli_flags = f"--system-prompt '{escaped_prompt}' --tools 'WebSearch,Read'"

    def on_first(rid, msg):
        def _gen_title():
            title = generate_session_title(msg)
            db.update_session_title(session_id, title)
            responses[rid]["title"] = title
        threading.Thread(target=_gen_title, daemon=True).start()

    def add_msg_fn(_name, role, content, events=None):
        events_json = json.dumps(events) if events else None
        msg_id = db.add_message(session_id, role, content, reasoning=events_json)
        responses[response_id]["message_id"] = msg_id

    def update_ctx_fn(_name, pct):
        db.update_context_percent_by_session(session_id, pct)

    def on_text(text, full):
        responses[response_id]["content"] = full

    mode_opts = {
        "extra_cli_flags": extra_cli_flags,
        "status_running": "running",
        "lock_key": session_id,
        "on_first_message": on_first,
        "work_dir_suffix": "",
        "on_text": on_text,
        "event_filter": None,
    }

    thread = threading.Thread(
        target=run_mode_generation,
        args=(response_id, "chat", session_id, cli_message, is_first_message,
              session_id, mcp_tools, chat_dir, responses, streams,
              get_session_lock, update_ctx_fn, add_msg_fn, model, mode_opts)
    )
    thread.daemon = True
    thread.start()

    return response_id, session_id, message


def make_chat_extra_events_fn():
    """Factory for additional chat SSE events (title, session_id, status)"""
    state = {"title_sent": False, "last_status": ""}
    def fn(resp):
        events = []
        status = resp.get("status", "pending")
        if status != state["last_status"]:
            if status == "running":
                events.append({"event": "status", "data": "Claude is responding..."})
            state["last_status"] = status
        if not state["title_sent"] and resp.get("title"):
            events.append({"event": "title", "data": resp["title"]})
            events.append({"event": "session_id", "data": resp.get("session_id", "")})
            state["title_sent"] = True
        return events
    return fn
