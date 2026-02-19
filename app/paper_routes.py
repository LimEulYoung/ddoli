"""
Paper mode API router
- LaTeX paper writing/building/deployment
- Project (paper) / file management
- SSE streaming
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

# Session lock function injected from main.py
get_session_lock = None

# Shared state (accessed from main.py)
active_paper_streams = {}  # {response_id: {"cancelled": bool, "process": subprocess or None}}
paper_responses = {}  # {response_id: {"paper", "status", "events", ...}}


# ========== Paper Project Management ==========

@router.get("/paper/templates")
async def get_templates():
    """Retrieve list of available templates (including display_name)"""
    success, output = run_local_command(f"mkdir -p {TEMPLATES_DIR} && ls -1 {TEMPLATES_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    template_dirs = [t.strip() for t in output.split('\n') if t.strip()]

    # Read display_name from each template's metadata.json
    templates = []
    for tpl in template_dirs:
        display_name = tpl  # fallback: use folder name as-is
        ok, meta_output = run_local_command(f"cat {TEMPLATES_DIR}/{tpl}/metadata.json 2>/dev/null")
        if ok and meta_output:
            try:
                meta = json.loads(meta_output)
                display_name = meta.get("display_name", tpl)
            except (json.JSONDecodeError, ValueError):
                pass
        templates.append({"value": tpl, "display_name": display_name})

    return JSONResponse(templates)


PAPER_CLAUDE_MD = """# {paper_name} - LaTeX Paper Project

## How to Build

```bash
# Build from the contents folder (run twice to resolve TOC/references)
cd contents
pdflatex main.tex
pdflatex main.tex
```

## View PDF

After building, long-press the paper in the sidebar and select "View PDF" to open `contents/main.pdf` directly.

## File Structure

```
{paper_name}/
├── CLAUDE.md           # This file (build/deployment guide)
├── contents/           # LaTeX source files
│   ├── main.tex        # Main file (connects sections via \\input)
│   ├── abstract.tex    # Abstract
│   └── introduction.tex # Introduction
├── figures/            # Figure files
└── references/         # References (PDF, BibTeX, etc.)
    └── references.bib  # BibTeX file
```

## Section Management

Each section is managed as a separate .tex file, connected in main.tex via `\\input{{section_file}}`.
To add a new section:
1. Create a `section_name.tex` file in the `contents/` folder
2. Add `\\input{{section_name}}` to `main.tex`

## Adding References

1. When the user provides a DOI or paper info, add a BibTeX entry to `references/references.bib`
2. Save reference paper PDFs in the `references/` folder
3. Cite in the text using `\\cite{{key}}` format

## Adding Figures

1. When the user attaches an image, save it to the `figures/` directory
2. Insert in the text as follows:
```latex
\\begin{{figure}}[h]
    \\centering
    \\includegraphics[width=0.8\\textwidth]{{../figures/filename.png}}
    \\caption{{Figure description}}
    \\label{{fig:label}}
\\end{{figure}}
```

## Notes

- If a build error occurs, check the `.log` file to identify the cause
- For Korean text, `\\usepackage{{kotex}}` is required (already included in the template)
- Build artifacts (`.aux`, `.log`, `.out`, etc.) should be excluded from deployment
"""

# Default main.tex template
PAPER_MAIN_TEX = r"""\documentclass[11pt,a4paper]{article}
\usepackage{kotex}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}

\title{Enter your title}
\author{Author Name}
\date{\today}

\begin{document}

\maketitle

\input{abstract}

\input{introduction}

% To add a new section: \input{section_filename}

\section{Body}
Write the body content here.

\section{Conclusion}
Write the conclusion content here.

\bibliographystyle{plain}
\bibliography{../references/references}

\end{document}
"""

# abstract.tex template
PAPER_ABSTRACT_TEX = r"""\begin{abstract}
Write your abstract here.
\end{abstract}
"""

# introduction.tex template
PAPER_INTRODUCTION_TEX = r"""\section{Introduction}
Write the introduction content here.
"""

# references.bib sample template
PAPER_REFERENCES_BIB = r"""% BibTeX references file
% Usage: cite in text with \cite{key}
% Build: pdflatex main.tex && bibtex main && pdflatex main.tex && pdflatex main.tex

@article{sample2024,
  author  = {John Smith and Jane Doe},
  title   = {A Study on Natural Language Processing Using Deep Learning},
  journal = {Journal of Artificial Intelligence Research},
  year    = {2024},
  volume  = {12},
  number  = {3},
  pages   = {45--67},
}

@inproceedings{example2023,
  author    = {Alice Johnson and Bob Williams},
  title     = {Efficient Training Methods for Large Language Models},
  booktitle = {Proceedings of the 2023 Conference on Machine Learning},
  year      = {2023},
  pages     = {123--128},
  address   = {New York},
}

@book{textbook2022,
  author    = {David Brown},
  title     = {Machine Learning: Theory and Practice},
  publisher = {Academic Press},
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
    """Create a new paper project"""
    if not re.match(r'^[a-z0-9-]+$', paper_name):
        return JSONResponse({"error": "Paper name can only contain lowercase letters, numbers, and hyphens (-). (e.g., my-thesis)"}, status_code=400)
    if paper_name.startswith('-') or paper_name.endswith('-'):
        return JSONResponse({"error": "Paper name cannot start or end with a hyphen."}, status_code=400)
    if '--' in paper_name:
        return JSONResponse({"error": "Paper name cannot contain consecutive hyphens (--)."}, status_code=400)

    # Check for duplicates
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{paper_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": "A project with the same name already exists."}, status_code=409)

    # Use basic template if none specified
    if not template:
        template = "basic"

    # Create project root and copy template
    success, output = run_local_command(f"mkdir -p {PAPERS_DIR}/{paper_name}")
    if not success:
        return JSONResponse({"error": f"Failed to create folder: {output}"}, status_code=500)
    run_local_command(f"cp -r {TEMPLATES_DIR}/{template}/* {PAPERS_DIR}/{paper_name}/ 2>/dev/null || true")
    # Remove metadata.json (template metadata, not needed in project)
    run_local_command(f"rm -f {PAPERS_DIR}/{paper_name}/metadata.json 2>/dev/null")
    # If the template has its own CLAUDE.md, replace {paper_name} placeholder
    ok, claude_content = run_local_command(f"cat {PAPERS_DIR}/{paper_name}/CLAUDE.md 2>/dev/null")
    if ok and claude_content and '{paper_name}' in claude_content:
        claude_content = claude_content.replace('{paper_name}', paper_name)
        claude_b64 = base64.b64encode(claude_content.encode()).decode()
        run_local_command(f"echo '{claude_b64}' | base64 -d > {PAPERS_DIR}/{paper_name}/CLAUDE.md")
    elif not ok or not claude_content:
        # If template has no CLAUDE.md, generate default LaTeX one
        claude_md_content = PAPER_CLAUDE_MD.format(paper_name=paper_name)
        claude_md_b64 = base64.b64encode(claude_md_content.encode()).decode()
        run_local_command(f"echo '{claude_md_b64}' | base64 -d > {PAPERS_DIR}/{paper_name}/CLAUDE.md")

    return JSONResponse({"success": True, "paper": paper_name})


@router.post("/paper/clone")
async def clone_paper(source: str = Form(""), target: str = Form("")):
    """Clone a paper (copy files, new session)"""
    if not source or not target:
        return JSONResponse({"error": "Both source and target paper names are required."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', target):
        return JSONResponse({"error": "Paper name can only contain lowercase letters, numbers, and hyphens (-)."}, status_code=400)
    if target.startswith('-') or target.endswith('-') or '--' in target:
        return JSONResponse({"error": "Invalid paper name format."}, status_code=400)
    # Check if source exists
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{source} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"Source paper '{source}' not found."}, status_code=404)
    # Check for target duplicates
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{target} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"Paper '{target}' already exists."}, status_code=409)
    # Copy
    success, output = run_local_command(f"cp -r {PAPERS_DIR}/{source} {PAPERS_DIR}/{target}")
    if not success:
        return JSONResponse({"error": f"Clone failed: {output}"}, status_code=500)
    return JSONResponse({"success": True, "paper": target})


@router.post("/paper/rename")
async def rename_paper(old_name: str = Form(""), new_name: str = Form("")):
    """Rename a paper (mv + delete existing DB session)"""
    if not old_name or not new_name:
        return JSONResponse({"error": "Both old name and new name are required."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', new_name):
        return JSONResponse({"error": "Paper name can only contain lowercase letters, numbers, and hyphens (-)."}, status_code=400)
    if new_name.startswith('-') or new_name.endswith('-') or '--' in new_name:
        return JSONResponse({"error": "Invalid paper name format."}, status_code=400)
    # Check if source exists
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{old_name} && echo 'exists'")
    if not success or 'exists' not in output:
        return JSONResponse({"error": f"Paper '{old_name}' not found."}, status_code=404)
    # Check for target duplicates
    success, output = run_local_command(f"test -d {PAPERS_DIR}/{new_name} && echo 'exists'")
    if success and 'exists' in output:
        return JSONResponse({"error": f"Paper '{new_name}' already exists."}, status_code=409)
    # Rename
    success, output = run_local_command(f"mv {PAPERS_DIR}/{old_name} {PAPERS_DIR}/{new_name}")
    if not success:
        return JSONResponse({"error": f"Rename failed: {output}"}, status_code=500)
    # Delete existing DB session (reset conversation)
    db.delete_mode_project("paper", old_name)
    return JSONResponse({"success": True, "paper": new_name})


@router.delete("/paper/project")
async def delete_paper(paper: str = ""):
    """Delete a paper (folder + DB)"""
    if not paper:
        return JSONResponse({"error": "Paper name is required."}, status_code=400)
    if not re.match(r'^[a-z0-9-]+$', paper):
        return JSONResponse({"error": "Invalid paper name."}, status_code=400)

    success, output = run_local_command(f"rm -rf {PAPERS_DIR}/{paper}")
    if not success:
        return JSONResponse({"error": f"Failed to delete folder: {output}"}, status_code=500)

    db.delete_mode_project("paper", paper)
    return JSONResponse({"success": True, "message": f"Paper '{paper}' has been deleted."})


@router.delete("/paper/file")
async def delete_paper_file(paper: str = "", path: str = ""):
    """Delete a file"""
    result, status_code = mode_delete_file(PAPERS_DIR, paper, path, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.post("/paper/create-file")
async def create_paper_file(paper: str = Form(""), path: str = Form(""), filename: str = Form("")):
    """Create a file within a paper"""
    result, status_code = mode_create_file(PAPERS_DIR, paper, path, filename, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.post("/paper/create-folder")
async def create_paper_folder(paper: str = Form(""), path: str = Form(""), foldername: str = Form("")):
    """Create a folder within a paper"""
    result, status_code = mode_create_folder(PAPERS_DIR, paper, path, foldername, name_pattern=r'^[a-z0-9-]+$')
    return JSONResponse(result, status_code=status_code)


@router.get("/paper/list-dirs")
async def list_paper_dirs(paper: str = ""):
    """List directories within a paper"""
    dirs = mode_list_directories(PAPERS_DIR, paper)
    return JSONResponse(dirs)


@router.get("/paper/papers-json")
async def get_papers_json():
    """Retrieve paper list (excluding archived)"""
    success, output = run_local_command(f"mkdir -p {PAPERS_DIR} && ls -1 {PAPERS_DIR} 2>/dev/null")
    if not success:
        return JSONResponse([])
    archived = set(db.get_archived_projects("paper"))
    papers = [p.strip() for p in output.split('\n') if p.strip() and p.strip() not in archived]
    return JSONResponse([{"name": p} for p in papers])


# ========== File Explorer ==========

PAPER_EXT_COLORS = {
    'tex': 'text-green-600', 'bib': 'text-purple-600', 'cls': 'text-blue-500',
    'sty': 'text-blue-500', 'pdf': 'text-red-500', 'png': 'text-orange-500',
    'jpg': 'text-orange-500', 'jpeg': 'text-orange-500', 'eps': 'text-orange-500',
    'svg': 'text-orange-500', 'md': 'text-gray-500', 'txt': 'text-gray-400'
}


@router.get("/paper/files", response_class=HTMLResponse)
async def get_paper_files(paper: str = ""):
    """Retrieve paper directory structure"""
    return HTMLResponse(render_file_tree_html(PAPERS_DIR, paper, "paper", ext_colors=PAPER_EXT_COLORS))


@router.get("/paper/file-content")
async def get_paper_file_content(paper: str = "", path: str = ""):
    """Retrieve file content"""
    return JSONResponse(mode_file_content(PAPERS_DIR, paper, path))


@router.get("/paper/file-raw")
async def get_paper_file_raw(paper: str = "", path: str = ""):
    """Retrieve media file binary (images/videos)"""
    return make_file_raw_response(PAPERS_DIR, paper, path)


@router.post("/paper/file-write")
async def write_paper_file(paper: str = Form(""), path: str = Form(""), content: str = Form("")):
    """Save file content"""
    return JSONResponse(mode_file_write(PAPERS_DIR, paper, path, content))


@router.get("/paper/file-download")
async def download_paper_file(paper: str = "", path: str = ""):
    """Download a file"""
    return make_file_download_response(PAPERS_DIR, paper, path)


# ========== Paper Mode Chat ==========

@router.post("/paper", response_class=HTMLResponse)
async def paper_chat(paper: str = Form(""), message: str = Form(""), mcp_tools: str = Form(""), file_map: str = Form(""), model: str = Form("sonnet")):
    """Send message to Claude CLI (paper writing)"""
    return HTMLResponse(mode_chat_handler(
        "paper", paper, message, mcp_tools, file_map,
        PAPERS_DIR, paper_responses, active_paper_streams, get_session_lock,
        "paper and message", "Writing paper...", model=model
    ))


@router.get("/paper/stream")
async def paper_stream(id: str = "", start_from: int = 0):
    """Stream Claude CLI output via SSE"""
    return await mode_stream_sse(id, paper_responses, active_paper_streams, start_from)


@router.get("/paper/status/{response_id}")
async def get_paper_status(response_id: str):
    """Retrieve status of an in-progress paper response"""
    return JSONResponse(mode_status_response(response_id, paper_responses, "paper"))


@router.get("/paper/active")
async def get_active_papers(paper: str = ""):
    """Retrieve list of active response IDs for a paper"""
    return JSONResponse(mode_active_response(paper_responses, "paper", paper))


# ========== Session Management ==========

@router.post("/paper/clear")
async def clear_paper_session_endpoint(paper: str = Form("")):
    """Clear Paper mode session"""
    result = mode_clear_session("paper", paper, "Paper")
    if result:
        return JSONResponse(result)
    return JSONResponse({"error": "Paper is required."}, status_code=400)


@router.get("/paper/context")
async def get_paper_context_percent(paper: str = ""):
    """Retrieve context percent for a paper"""
    return JSONResponse(mode_context_percent("paper", paper))


@router.get("/paper/messages", response_class=HTMLResponse)
async def get_paper_messages(paper: str = ""):
    """Return messages HTML for a paper"""
    return HTMLResponse(mode_messages_html("paper", paper, PAPERS_DIR))
