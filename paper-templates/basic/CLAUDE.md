# {paper_name} - LaTeX 논문 프로젝트

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
│   ├── main.tex        # 메인 파일 (\input으로 섹션 연결)
│   ├── abstract.tex    # 초록
│   └── introduction.tex # 서론
├── figures/            # 그림 파일
└── references/         # 참고문헌 (PDF, BibTeX 등)
    └── references.bib  # BibTeX 파일
```

## 섹션 관리

각 섹션은 별도 .tex 파일로 관리하고, main.tex에서 `\input{섹션파일}` 로 연결합니다.
새 섹션 추가 시:
1. `contents/` 폴더에 `섹션명.tex` 파일 생성
2. `main.tex`에 `\input{섹션명}` 추가

## 참고문헌 추가

1. 사용자가 DOI나 논문 정보를 제공하면 `references/references.bib`에 BibTeX 엔트리 추가
2. 참고 논문 PDF는 `references/` 폴더에 저장
3. 본문에서 `\cite{key}` 형식으로 인용

## 그림 추가

1. 사용자가 이미지를 첨부하면 `figures/` 디렉토리에 저장
2. 본문에서 아래와 같이 삽입:
```latex
\begin{figure}[h]
    \centering
    \includegraphics[width=0.8\textwidth]{../figures/filename.png}
    \caption{그림 설명}
    \label{fig:label}
\end{figure}
```

## 주의사항

- 빌드 오류 발생 시 `.log` 파일을 확인하여 원인 파악
- 한글 사용 시 `\usepackage{kotex}` 필요 (이미 템플릿에 포함됨)
- 빌드 산출물 (`.aux`, `.log`, `.out` 등)은 배포 시 제외
