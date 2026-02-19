"""
Code mode API router
- Claude Code CLI control
- Project/file management
- SSE streaming
"""
from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse
import re

from app import db
from app.config import WORKSPACE_DIR, run_local_command
from app.shared import (
    mode_chat_handler, mode_stream_sse,
    mode_status_response, mode_active_response, mode_file_content, mode_file_write,
    make_file_raw_response, make_file_download_response,
    mode_delete_file, mode_create_file, mode_create_folder, mode_list_directories,
    render_file_tree_html,
    mode_clear_session, mode_context_percent, mode_messages_html
)

router = APIRouter()

# Session lock function injected from main.py
get_session_lock = None

# Shared state (accessed from main.py)
active_streams = {}  # {response_id: {"cancelled": bool, "process": subprocess or None}}
code_responses = {}  # {response_id: {"project", "status", "events", ...}}


# ========== Project Management ==========

@router.post("/code/new-project")
async def create_new_project(project_name: str = Form(...)):
    """Create a new project folder"""
    if not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
        return JSONResponse({"error": "Project name can only contain letters, numbers, hyphens, and underscores."}, status_code=400)
    success, output = run_local_command(f"mkdir -p {WORKSPACE_DIR}/{project_name} && echo 'created'")
    if success:
        return JSONResponse({"success": True, "project": project_name})
    return JSONResponse({"error": f"Folder creation failed: {output}"}, status_code=500)


@router.post("/code/clone")
async def clone_project(source: str = Form(""), target: str = Form("")):
    """Clone a project (copy files, new session)"""
    if not source or not target:
        return JSONResponse({"error": "Source and target project names are required."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', target):
        return JSONResponse({"error": "Project name can only contain letters, numbers, hyphens, and underscores."}, status_code=400)
    # Check source exists
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{source} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"Source project '{source}' not found."}, status_code=404)
    # Check target does not already exist
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{target} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"Project '{target}' already exists."}, status_code=409)
    # Copy
    success, output = run_local_command(f"cp -r {WORKSPACE_DIR}/{source} {WORKSPACE_DIR}/{target}")
    if not success:
        return JSONResponse({"error": f"Clone failed: {output}"}, status_code=500)
    return JSONResponse({"success": True, "project": target})


@router.post("/code/rename")
async def rename_project(old_name: str = Form(""), new_name: str = Form("")):
    """Rename a project (mv + delete existing DB session)"""
    if not old_name or not new_name:
        return JSONResponse({"error": "Old name and new name are required."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        return JSONResponse({"error": "Project name can only contain letters, numbers, hyphens, and underscores."}, status_code=400)
    # Check source exists
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{old_name} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"Project '{old_name}' not found."}, status_code=404)
    # Check target does not already exist
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{new_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"Project '{new_name}' already exists."}, status_code=409)
    # Rename
    success, output = run_local_command(f"mv {WORKSPACE_DIR}/{old_name} {WORKSPACE_DIR}/{new_name}")
    if not success:
        return JSONResponse({"error": f"Rename failed: {output}"}, status_code=500)
    # Delete existing DB session (reset conversation)
    db.delete_mode_project("code", old_name)
    return JSONResponse({"success": True, "project": new_name})


@router.delete("/code/project")
async def delete_project(project: str = ""):
    """Delete a project (folder + DB)"""
    if not project:
        return JSONResponse({"error": "Project name is required."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', project):
        return JSONResponse({"error": "Invalid project name."}, status_code=400)
    success, output = run_local_command(f"rm -rf {WORKSPACE_DIR}/{project}")
    if not success:
        return JSONResponse({"error": f"Folder deletion failed: {output}"}, status_code=500)
    db.delete_mode_project("code", project)
    return JSONResponse({"success": True, "message": f"Project '{project}' has been deleted."})


@router.delete("/code/file")
async def delete_file(project: str = "", path: str = ""):
    """Delete a file"""
    result, status_code = mode_delete_file(WORKSPACE_DIR, project, path)
    return JSONResponse(result, status_code=status_code)


@router.post("/code/create-file")
async def create_file(project: str = Form(""), path: str = Form(""), filename: str = Form("")):
    """Create a file within the project"""
    result, status_code = mode_create_file(WORKSPACE_DIR, project, path, filename)
    return JSONResponse(result, status_code=status_code)


@router.post("/code/create-folder")
async def create_folder(project: str = Form(""), path: str = Form(""), foldername: str = Form("")):
    """Create a folder within the project"""
    result, status_code = mode_create_folder(WORKSPACE_DIR, project, path, foldername)
    return JSONResponse(result, status_code=status_code)


@router.get("/code/list-dirs")
async def list_dirs(project: str = ""):
    """List directories within the project"""
    find_extra = "\\( -name 'venv' -o -name '__pycache__' -o -name 'node_modules' -o -name '.git' \\) -prune -o "
    dirs = mode_list_directories(WORKSPACE_DIR, project, find_extra)
    return JSONResponse(dirs)


@router.get("/code/projects-json")
async def get_projects_json():
    """List projects (excluding archived)"""
    success, output = run_local_command(f"mkdir -p {WORKSPACE_DIR} && ls -1 {WORKSPACE_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    archived = set(db.get_archived_projects("code"))
    projects = [p.strip() for p in output.split('\n') if p.strip() and p.strip() not in archived]
    return JSONResponse(projects)


# ========== File Explorer ==========

@router.get("/code/files", response_class=HTMLResponse)
async def get_files(project: str = ""):
    """Get project directory structure"""
    find_extra = "\\( -name 'venv' -o -name '__pycache__' -o -name 'node_modules' -o -name '.git' \\) -prune -o "
    return HTMLResponse(render_file_tree_html(WORKSPACE_DIR, project, "code", find_extra))


@router.get("/code/file-content")
async def get_file_content(project: str = "", path: str = ""):
    """Get file content"""
    return JSONResponse(mode_file_content(WORKSPACE_DIR, project, path))


@router.get("/code/file-raw")
async def get_file_raw(project: str = "", path: str = ""):
    """Get media file binary (images/videos)"""
    return make_file_raw_response(WORKSPACE_DIR, project, path)


@router.post("/code/file-write")
async def write_file(project: str = Form(""), path: str = Form(""), content: str = Form("")):
    """Save file content"""
    return JSONResponse(mode_file_write(WORKSPACE_DIR, project, path, content))


@router.get("/code/file-download")
async def download_file(project: str = "", path: str = ""):
    """Download a file"""
    return make_file_download_response(WORKSPACE_DIR, project, path)


# ========== Code Mode Chat ==========

@router.post("/code", response_class=HTMLResponse)
async def code_chat(project: str = Form(""), message: str = Form(""), mcp_tools: str = Form(""), file_map: str = Form(""), model: str = Form("sonnet")):
    """Send a message to Claude Code CLI"""
    return HTMLResponse(mode_chat_handler(
        "code", project, message, mcp_tools, file_map,
        WORKSPACE_DIR, code_responses, active_streams, get_session_lock,
        "project and message", "Running Claude Code...", model=model
    ))


@router.get("/code/stream")
async def code_stream(id: str = "", start_from: int = 0):
    """Stream Claude Code CLI output via SSE"""
    return await mode_stream_sse(id, code_responses, active_streams, start_from)


@router.get("/code/status/{response_id}")
async def get_code_status(response_id: str):
    """Get status of an in-progress code response"""
    return JSONResponse(mode_status_response(response_id, code_responses, "project"))


@router.get("/code/active")
async def get_active_codes(project: str = ""):
    """Get list of active response IDs for a project"""
    return JSONResponse(mode_active_response(code_responses, "project", project))


# ========== Session Management ==========

@router.post("/code/clear")
async def clear_code_session_endpoint(project: str = Form("")):
    """Clear code mode session"""
    result = mode_clear_session("code", project, "Project")
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "Project is required."}, status_code=400)


@router.get("/code/context")
async def get_context_percent(project: str = ""):
    """Get context percent for the project"""
    return JSONResponse(mode_context_percent("code", project))


@router.get("/code/messages", response_class=HTMLResponse)
async def get_code_messages(project: str = ""):
    """Get code messages HTML for the project"""
    return HTMLResponse(mode_messages_html("code", project, WORKSPACE_DIR))
