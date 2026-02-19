"""
Paper 모드 API 라우터
- LaTeX 논문 작성/빌드/배포
- 프로젝트(논문)/파일 관리
- SSE 스트리밍
"""
from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse
import re
import json
import base64

from app import db
from app.config import PAPERS_DIR, TEMPLATES_DIR, run_local_command
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
active_paper_streams = {}  # {response_id: {"cancelled": bool, "process": subprocess or None}}
paper_responses = {}  # {response_id: {"paper", "status", "events", ...}}


# ========== 논문 프로젝트 관리 ==========

@router.get("/paper/templates")
async def get_templates():
    """사용 가능한 템플릿 목록 조회 (display_name 포함)"""
    success, output = run_local_command(f"mkdir -p {TEMPLATES_DIR} && ls -1 {TEMPLATES_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    template_dirs = [t.strip() for t in output.split('\n') if t.strip()]

    # 각 템플릿의 metadata.json에서 display_name 읽기
    templates = []
    for tpl in template_dirs:
        display_name = tpl  # fallback: 폴더 이름 그대로
        ok, meta_output = run_local_command(f"cat {TEMPLATES_DIR}/{tpl}/metadata.json 2>/dev/null")
        if ok and meta_output:
            try:
                meta = json.loads(meta_output)
                display_name = meta.get("display_name", tpl)
            except (json.JSONDecodeError, ValueError):
                pass
        templates.append({"value": tpl, "display_name": display_name})

    return JSONResponse(templates)


PAPER_CLAUDE_MD = """# {paper_name} - LaTeX 논문 프로젝트

## 빌드 방법

```bash
# contents 폴더에서 빌드 (2번 실행하면 목차/참조가 제대로 반영됨)
cd contents
pdflatex main.tex
pdflatex main.tex
```

## PDF 보기

빌드 완료 후 사이드바에서 논문을 롱프레스 → "PDF 보기"로 `contents/main.pdf`를 바로 확인할 수 있습니다.

## 파일 구조

```
{paper_name}/
├── CLAUDE.md           # 이 파일 (빌드/배포 안내)
├── contents/           # LaTeX 소스 파일
│   ├── main.tex        # 메인 파일 (\\input으로 섹션 연결)
│   ├── abstract.tex    # 초록
│   └── introduction.tex # 서론
├── figures/            # 그림 파일
└── references/         # 참고문헌 (PDF, BibTeX 등)
    └── references.bib  # BibTeX 파일
```

## 섹션 관리

각 섹션은 별도 .tex 파일로 관리하고, main.tex에서 `\\input{{섹션파일}}` 로 연결합니다.
새 섹션 추가 시:
1. `contents/` 폴더에 `섹션명.tex` 파일 생성
2. `main.tex`에 `\\input{{섹션명}}` 추가

## 참고문헌 추가

1. 사용자가 DOI나 논문 정보를 제공하면 `references/references.bib`에 BibTeX 엔트리 추가
2. 참고 논문 PDF는 `references/` 폴더에 저장
3. 본문에서 `\\cite{{key}}` 형식으로 인용

## 그림 추가

1. 사용자가 이미지를 첨부하면 `figures/` 디렉토리에 저장
2. 본문에서 아래와 같이 삽입:
```latex
\\begin{{figure}}[h]
    \\centering
    \\includegraphics[width=0.8\\textwidth]{{../figures/filename.png}}
    \\caption{{그림 설명}}
    \\label{{fig:label}}
\\end{{figure}}
```

## 주의사항

- 빌드 오류 발생 시 `.log` 파일을 확인하여 원인 파악
- 한글 사용 시 `\\usepackage{{kotex}}` 필요 (이미 템플릿에 포함됨)
- 빌드 산출물 (`.aux`, `.log`, `.out` 등)은 배포 시 제외
"""

# 기본 main.tex 템플릿
PAPER_MAIN_TEX = r"""\documentclass[11pt,a4paper]{article}
\usepackage{kotex}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}

\title{제목을 입력하세요}
\author{저자명}
\date{\today}

\begin{document}

\maketitle

\input{abstract}

\input{introduction}

% 새 섹션 추가 시: \input{섹션파일명}

\section{본론}
본론 내용을 여기에 작성합니다.

\section{결론}
결론 내용을 여기에 작성합니다.

\bibliographystyle{plain}
\bibliography{../references/references}

\end{document}
"""

# abstract.tex 템플릿
PAPER_ABSTRACT_TEX = r"""\begin{abstract}
초록을 여기에 작성합니다.
\end{abstract}
"""

# introduction.tex 템플릿
PAPER_INTRODUCTION_TEX = r"""\section{서론}
서론 내용을 여기에 작성합니다.
"""

# references.bib 샘플 템플릿
PAPER_REFERENCES_BIB = r"""% BibTeX 참고문헌 파일
% 사용법: 본문에서 \cite{key} 형식으로 인용
% 빌드: pdflatex main.tex && bibtex main && pdflatex main.tex && pdflatex main.tex

@article{sample2024,
  author  = {홍길동 and 김철수},
  title   = {딥러닝을 활용한 자연어 처리 연구},
  journal = {한국인공지능학회논문지},
  year    = {2024},
  volume  = {12},
  number  = {3},
  pages   = {45--67},
}

@inproceedings{example2023,
  author    = {이영희 and 박민수},
  title     = {대규모 언어 모델의 효율적 학습 방법},
  booktitle = {2023 한국정보과학회 학술대회},
  year      = {2023},
  pages     = {123--128},
  address   = {서울},
}

@book{textbook2022,
  author    = {최재영},
  title     = {기계학습 이론과 실제},
  publisher = {한빛미디어},
  year      = {2022},
  edition   = {2},
}

@misc{website2024,
  author       = {OpenAI},
  title        = {GPT-4 Technical Report},
  howpublished = {\url{https://openai.com/research/gpt-4}},
  year         = {2024},
  note         = {Accessed: 2024-01-15},
}
"""


@router.post("/paper/new-paper")
async def create_new_paper(paper_name: str = Form(...), template: str = Form("")):
    """새 논문 프로젝트 생성"""
    if not re.match(r'^[a-z0-9-]+$', paper_name):
        return JSONResponse({"error": "논문 이름은 소문자, 숫자, 하이픈(-)만 사용할 수 있습니다. (예: my-thesis)"}, status_code=400)
    if paper_name.startswith('-') or paper_name.endswith('-'):
        return JSONResponse({"error": "논문 이름은 하이픈으로 시작하거나 끝날 수 없습니다."}, status_code=400)
    if '--' in paper_name:
        return JSONResponse({"error": "논문 이름에 연속된 하이픈(--)을 사용할 수 없습니다."}, status_code=400)

    # 중복 확인
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{paper_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": "이미 같은 이름의 프로젝트가 존재합니다."}, status_code=409)

    # 템플릿 미지정 시 basic 사용
    if not template:
        template = "basic"

    # 프로젝트 루트 생성 후 템플릿 복사
    success, output = run_local_command(f"mkdir -p {PAPERS_DIR}/{paper_name}")
    if not success:
        return JSONResponse({"error": f"폴더 생성 실패: {output}"}, status_code=500)
    run_local_command(f"cp -r {TEMPLATES_DIR}/{template}/* {PAPERS_DIR}/{paper_name}/ 2>/dev/null || true")
    # metadata.json 제거 (템플릿 메타데이터이므로 프로젝트에 불필요)
    run_local_command(f"rm -f {PAPERS_DIR}/{paper_name}/metadata.json 2>/dev/null")
    # 템플릿 자체 CLAUDE.md가 있으면 {paper_name} 플레이스홀더 치환
    ok, claude_content = run_local_command(f"cat {PAPERS_DIR}/{paper_name}/CLAUDE.md 2>/dev/null")
    if ok and claude_content and '{paper_name}' in claude_content:
        claude_content = claude_content.replace('{paper_name}', paper_name)
        claude_b64 = base64.b64encode(claude_content.encode()).decode()
        run_local_command(f"echo '{claude_b64}' | base64 -d > {PAPERS_DIR}/{paper_name}/CLAUDE.md")
    elif not ok or not claude_content:
        # 템플릿에 CLAUDE.md가 없으면 LaTeX 기본 생성
        claude_md_content = PAPER_CLAUDE_MD.format(paper_name=paper_name)
        claude_md_b64 = base64.b64encode(claude_md_content.encode()).decode()
        run_local_command(f"echo '{claude_md_b64}' | base64 -d > {PAPERS_DIR}/{paper_name}/CLAUDE.md")

    return JSONResponse({"success": True, "paper": paper_name})


@router.post("/paper/clone")
async def clone_paper(source: str = Form(""), target: str = Form("")):
    """논문 복제 (파일 복사, 새 세션)"""
    if not source or not target:
        return JSONResponse({"error": "원본과 대상 논문 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', target):
        return JSONResponse({"error": "논문 이름은 소문자, 숫자, 하이픈(-)만 사용할 수 있습니다."}, status_code=400)
    if target.startswith('-') or target.endswith('-') or '--' in target:
        return JSONResponse({"error": "논문 이름 형식이 올바르지 않습니다."}, status_code=400)
    # 원본 존재 확인
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{source} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"원본 논문 '{source}'를 찾을 수 없습니다."}, status_code=404)
    # 대상 중복 확인
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{target} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"논문 '{target}'가 이미 존재합니다."}, status_code=409)
    # 복사
    success, output = run_local_command(f"cp -r {PAPERS_DIR}/{source} {PAPERS_DIR}/{target}")
    if not success:
        return JSONResponse({"error": f"복제 실패: {output}"}, status_code=500)
    return JSONResponse({"success": True, "paper": target})


@router.post("/paper/rename")
async def rename_paper(old_name: str = Form(""), new_name: str = Form("")):
    """논문 이름 변경 (mv + 기존 DB 세션 삭제)"""
    if not old_name or not new_name:
        return JSONResponse({"error": "기존 이름과 새 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', new_name):
        return JSONResponse({"error": "논문 이름은 소문자, 숫자, 하이픈(-)만 사용할 수 있습니다."}, status_code=400)
    if new_name.startswith('-') or new_name.endswith('-') or '--' in new_name:
        return JSONResponse({"error": "논문 이름 형식이 올바르지 않습니다."}, status_code=400)
    # 원본 존재 확인
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{old_name} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"논문 '{old_name}'를 찾을 수 없습니다."}, status_code=404)
    # 대상 중복 확인
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{new_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"논문 '{new_name}'가 이미 존재합니다."}, status_code=409)
    # 이름 변경
    success, output = run_local_command(f"mv {PAPERS_DIR}/{old_name} {PAPERS_DIR}/{new_name}")
    if not success:
        return JSONResponse({"error": f"이름 변경 실패: {output}"}, status_code=500)
    # 기존 DB 세션 삭제 (대화 초기화)
    db.delete_mode_project("paper", old_name)
    return JSONResponse({"success": True, "paper": new_name})


@router.delete("/paper/project")
async def delete_paper(paper: str = ""):
    """논문 삭제 (폴더 + DB)"""
    if not paper:
        return JSONResponse({"error": "논문 이름이 필요합니다."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', paper):
        return JSONResponse({"error": "잘못된 논문 이름입니다."}, status_code=400)

    success, output = run_local_command(f"rm -rf {PAPERS_DIR}/{paper}")
    if not success:
        return JSONResponse({"error": f"폴더 삭제 실패: {output}"}, status_code=500)

    db.delete_mode_project("paper", paper)
    return JSONResponse({"success": True, "message": f"논문 '{paper}'가 삭제되었습니다."})


@router.delete("/paper/file")
async def delete_paper_file(paper: str = "", path: str = ""):
    """파일 삭제"""
    result, status_code = mode_delete_file(PAPERS_DIR, paper, path, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.post("/paper/create-file")
async def create_paper_file(paper: str = Form(""), path: str = Form(""), filename: str = Form("")):
    """논문 내 파일 생성"""
    result, status_code = mode_create_file(PAPERS_DIR, paper, path, filename, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.post("/paper/create-folder")
async def create_paper_folder(paper: str = Form(""), path: str = Form(""), foldername: str = Form("")):
    """논문 내 폴더 생성"""
    result, status_code = mode_create_folder(PAPERS_DIR, paper, path, foldername, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.get("/paper/list-dirs")
async def list_paper_dirs(paper: str = ""):
    """논문 내 디렉토리 목록 조회"""
    dirs = mode_list_directories(PAPERS_DIR, paper)
    return JSONResponse(dirs)


@router.get("/paper/papers-json")
async def get_papers_json():
    """논문 목록 조회 (아카이브 제외)"""
    success, output = run_local_command(f"mkdir -p {PAPERS_DIR} && ls -1 {PAPERS_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    archived = set(db.get_archived_projects("paper"))
    papers = [p.strip() for p in output.split('\n') if p.strip() and p.strip() not in archived]
    return JSONResponse([{"name": p} for p in papers])


# ========== 파일 탐색 ==========

PAPER_EXT_COLORS = {
    'tex': 'text-green-600', 'bib': 'text-purple-600', 'cls': 'text-blue-500',
    'sty': 'text-blue-500', 'pdf': 'text-red-500', 'png': 'text-orange-500',
    'jpg': 'text-orange-500', 'jpeg': 'text-orange-500', 'eps': 'text-orange-500',
    'svg': 'text-orange-500', 'md': 'text-gray-500', 'txt': 'text-gray-400'
}


@router.get("/paper/files", response_class=HTMLResponse)
async def get_paper_files(paper: str = ""):
    """논문 디렉토리 구조 조회"""
    return HTMLResponse(render_file_tree_html(PAPERS_DIR, paper, "paper", ext_colors=PAPER_EXT_COLORS))


@router.get("/paper/file-content")
async def get_paper_file_content(paper: str = "", path: str = ""):
    """파일 내용 조회"""
    return JSONResponse(mode_file_content(PAPERS_DIR, paper, path))


@router.get("/paper/file-raw")
async def get_paper_file_raw(paper: str = "", path: str = ""):
    """미디어 파일 바이너리 조회 (이미지/동영상)"""
    return make_file_raw_response(PAPERS_DIR, paper, path)


@router.post("/paper/file-write")
async def write_paper_file(paper: str = Form(""), path: str = Form(""), content: str = Form("")):
    """파일 내용 저장"""
    return JSONResponse(mode_file_write(PAPERS_DIR, paper, path, content))


@router.get("/paper/file-download")
async def download_paper_file(paper: str = "", path: str = ""):
    """파일 다운로드"""
    return make_file_download_response(PAPERS_DIR, paper, path)


# ========== Paper 모드 채팅 ==========

@router.post("/paper", response_class=HTMLResponse)
async def paper_chat(paper: str = Form(""), message: str = Form(""), mcp_tools: str = Form(""), file_map: str = Form(""), model: str = Form("sonnet")):
    """Claude CLI에 메시지 전송 (논문 작성)"""
    return HTMLResponse(mode_chat_handler(
        "paper", paper, message, mcp_tools, file_map,
        PAPERS_DIR, paper_responses, active_paper_streams, get_session_lock,
        "논문과 메시지", "논문 작성 중...", model=model
    ))


@router.get("/paper/stream")
async def paper_stream(id: str = "", start_from: int = 0):
    """Claude CLI 출력을 SSE로 스트리밍"""
    return await mode_stream_sse(id, paper_responses, active_paper_streams, start_from)


@router.get("/paper/status/{response_id}")
async def get_paper_status(response_id: str):
    """진행 중인 paper 응답 상태 조회"""
    return JSONResponse(mode_status_response(response_id, paper_responses, "paper"))


@router.get("/paper/active")
async def get_active_papers(paper: str = ""):
    """논문의 진행 중인 응답 ID 목록 조회"""
    return JSONResponse(mode_active_response(paper_responses, "paper", paper))


# ========== 세션 관리 ==========

@router.post("/paper/clear")
async def clear_paper_session_endpoint(paper: str = Form("")):
    """Paper 모드 세션 초기화"""
    result = mode_clear_session("paper", paper, "논문")
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "논문이 필요합니다."}, status_code=400)


@router.get("/paper/context")
async def get_paper_context_percent(paper: str = ""):
    """논문의 컨텍스트 퍼센트 조회"""
    return JSONResponse(mode_context_percent("paper", paper))


@router.get("/paper/messages", response_class=HTMLResponse)
async def get_paper_messages(paper: str = ""):
    """논문의 메시지 HTML 반환"""
    return HTMLResponse(mode_messages_html("paper", paper, PAPERS_DIR))


