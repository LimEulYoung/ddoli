"""
코드 모드 API 라우터
- Claude Code CLI 제어
- 프로젝트/파일 관리
- SSE 스트리밍
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

# main.py에서 주입되는 세션 락 함수
get_session_lock = None

# 공유 상태 (main.py에서 접근)
active_streams = {}  # {response_id: {"cancelled": bool, "process": subprocess or None}}
code_responses = {}  # {response_id: {"project", "status", "events", ...}}


# ========== 프로젝트 관리 ==========

@router.post("/code/new-project")
async def create_new_project(project_name: str = Form(...)):
    """새 프로젝트 폴더 생성"""
    if not re.match(r'^[a-zA-Z0-9_-]+$', project_name):
        return JSONResponse({"error": "프로젝트 이름은 영문, 숫자, 하이픈, 언더스코어만 사용할 수 있습니다."}, status_code=400)
    success, output = run_local_command(f"mkdir -p {WORKSPACE_DIR}/{project_name} && echo 'created'")
    if success:
        return JSONResponse({"success": True, "project": project_name})
    return JSONResponse({"error": f"폴더 생성 실패: {output}"}, status_code=500)


@router.post("/code/clone")
async def clone_project(source: str = Form(""), target: str = Form("")):
    """프로젝트 복제 (파일 복사, 새 세션)"""
    if not source or not target:
        return JSONResponse({"error": "원본과 대상 프로젝트 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', target):
        return JSONResponse({"error": "프로젝트 이름은 영문, 숫자, 하이픈, 언더스코어만 사용할 수 있습니다."}, status_code=400)
    # 원본 존재 확인
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{source} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"원본 프로젝트 '{source}'를 찾을 수 없습니다."}, status_code=404)
    # 대상 중복 확인
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{target} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"프로젝트 '{target}'가 이미 존재합니다."}, status_code=409)
    # 복사
    success, output = run_local_command(f"cp -r {WORKSPACE_DIR}/{source} {WORKSPACE_DIR}/{target}")
    if not success:
        return JSONResponse({"error": f"복제 실패: {output}"}, status_code=500)
    return JSONResponse({"success": True, "project": target})


@router.post("/code/rename")
async def rename_project(old_name: str = Form(""), new_name: str = Form("")):
    """프로젝트 이름 변경 (mv + 기존 DB 세션 삭제)"""
    if not old_name or not new_name:
        return JSONResponse({"error": "기존 이름과 새 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        return JSONResponse({"error": "프로젝트 이름은 영문, 숫자, 하이픈, 언더스코어만 사용할 수 있습니다."}, status_code=400)
    # 원본 존재 확인
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{old_name} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"프로젝트 '{old_name}'를 찾을 수 없습니다."}, status_code=404)
    # 대상 중복 확인
    success, output = run_local_command(f"test -d {WORKSPACE_DIR}/{new_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"프로젝트 '{new_name}'가 이미 존재합니다."}, status_code=409)
    # 이름 변경
    success, output = run_local_command(f"mv {WORKSPACE_DIR}/{old_name} {WORKSPACE_DIR}/{new_name}")
    if not success:
        return JSONResponse({"error": f"이름 변경 실패: {output}"}, status_code=500)
    # 기존 DB 세션 삭제 (대화 초기화)
    db.delete_mode_project("code", old_name)
    return JSONResponse({"success": True, "project": new_name})


@router.delete("/code/project")
async def delete_project(project: str = ""):
    """프로젝트 삭제 (폴더 + DB)"""
    if not project:
        return JSONResponse({"error": "프로젝트 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-zA-Z0-9_-]+$', project):
        return JSONResponse({"error": "잘못된 프로젝트 이름입니다."}, status_code=400)
    success, output = run_local_command(f"rm -rf {WORKSPACE_DIR}/{project}")
    if not success:
        return JSONResponse({"error": f"폴더 삭제 실패: {output}"}, status_code=500)
    db.delete_mode_project("code", project)
    return JSONResponse({"success": True, "message": f"프로젝트 '{project}'가 삭제되었습니다."})


@router.delete("/code/file")
async def delete_file(project: str = "", path: str = ""):
    """파일 삭제"""
    result, status_code = mode_delete_file(WORKSPACE_DIR, project, path)
    return JSONResponse(result, status_code=status_code)


@router.post("/code/create-file")
async def create_file(project: str = Form(""), path: str = Form(""), filename: str = Form("")):
    """프로젝트 내 파일 생성"""
    result, status_code = mode_create_file(WORKSPACE_DIR, project, path, filename)
    return JSONResponse(result, status_code=status_code)


@router.post("/code/create-folder")
async def create_folder(project: str = Form(""), path: str = Form(""), foldername: str = Form("")):
    """프로젝트 내 폴더 생성"""
    result, status_code = mode_create_folder(WORKSPACE_DIR, project, path, foldername)
    return JSONResponse(result, status_code=status_code)


@router.get("/code/list-dirs")
async def list_dirs(project: str = ""):
    """프로젝트 내 디렉토리 목록 조회"""
    find_extra = "\\( -name 'venv' -o -name '__pycache__' -o -name 'node_modules' -o -name '.git' \\) -prune -o "
    dirs = mode_list_directories(WORKSPACE_DIR, project, find_extra)
    return JSONResponse(dirs)


@router.get("/code/projects-json")
async def get_projects_json():
    """프로젝트 목록 조회 (아카이브 제외)"""
    success, output = run_local_command(f"mkdir -p {WORKSPACE_DIR} && ls -1 {WORKSPACE_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    archived = set(db.get_archived_projects("code"))
    projects = [p.strip() for p in output.split('\n') if p.strip() and p.strip() not in archived]
    return JSONResponse(projects)


# ========== 파일 탐색 ==========

@router.get("/code/files", response_class=HTMLResponse)
async def get_files(project: str = ""):
    """프로젝트 디렉토리 구조 조회"""
    find_extra = "\\( -name 'venv' -o -name '__pycache__' -o -name 'node_modules' -o -name '.git' \\) -prune -o "
    return HTMLResponse(render_file_tree_html(WORKSPACE_DIR, project, "code", find_extra))


@router.get("/code/file-content")
async def get_file_content(project: str = "", path: str = ""):
    """파일 내용 조회"""
    return JSONResponse(mode_file_content(WORKSPACE_DIR, project, path))


@router.get("/code/file-raw")
async def get_file_raw(project: str = "", path: str = ""):
    """미디어 파일 바이너리 조회 (이미지/동영상)"""
    return make_file_raw_response(WORKSPACE_DIR, project, path)


@router.post("/code/file-write")
async def write_file(project: str = Form(""), path: str = Form(""), content: str = Form("")):
    """파일 내용 저장"""
    return JSONResponse(mode_file_write(WORKSPACE_DIR, project, path, content))


@router.get("/code/file-download")
async def download_file(project: str = "", path: str = ""):
    """파일 다운로드"""
    return make_file_download_response(WORKSPACE_DIR, project, path)


# ========== 코드 모드 채팅 ==========

@router.post("/code", response_class=HTMLResponse)
async def code_chat(project: str = Form(""), message: str = Form(""), mcp_tools: str = Form(""), file_map: str = Form(""), model: str = Form("sonnet")):
    """Claude Code CLI에 메시지 전송"""
    return HTMLResponse(mode_chat_handler(
        "code", project, message, mcp_tools, file_map,
        WORKSPACE_DIR, code_responses, active_streams, get_session_lock,
        "프로젝트와 메시지", "Claude Code 실행 중...", model=model
    ))


@router.get("/code/stream")
async def code_stream(id: str = "", start_from: int = 0):
    """Claude Code CLI 출력을 SSE로 스트리밍"""
    return await mode_stream_sse(id, code_responses, active_streams, start_from)


@router.get("/code/status/{response_id}")
async def get_code_status(response_id: str):
    """진행 중인 코드 응답 상태 조회"""
    return JSONResponse(mode_status_response(response_id, code_responses, "project"))


@router.get("/code/active")
async def get_active_codes(project: str = ""):
    """프로젝트의 진행 중인 응답 ID 목록 조회"""
    return JSONResponse(mode_active_response(code_responses, "project", project))


# ========== 세션 관리 ==========

@router.post("/code/clear")
async def clear_code_session_endpoint(project: str = Form("")):
    """코드 모드 세션 초기화"""
    result = mode_clear_session("code", project, "프로젝트")
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "프로젝트가 필요합니다."}, status_code=400)


@router.get("/code/context")
async def get_context_percent(project: str = ""):
    """프로젝트의 컨텍스트 퍼센트 조회"""
    return JSONResponse(mode_context_percent("code", project))


@router.get("/code/messages", response_class=HTMLResponse)
async def get_code_messages(project: str = ""):
    """프로젝트의 코드 메시지 HTML 반환"""
    return HTMLResponse(mode_messages_html("code", project, WORKSPACE_DIR))
