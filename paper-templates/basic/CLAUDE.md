# {paper_name} - LaTeX Paper Project

## Build

```bash
# Build from the contents folder (run twice for proper TOC/references)
cd contents
pdflatex main.tex
pdflatex main.tex
```

## View PDF

After building, long-press the paper in the sidebar and select "View PDF" to open `contents/main.pdf`.

## File Structure

```
{paper_name}/
├── CLAUDE.md           # This file (build/usage guide)
├── contents/           # LaTeX source files
│   ├── main.tex        # Main file (connects sections via \input)
│   ├── abstract.tex    # Abstract
│   └── introduction.tex # Introduction
├── figures/            # Image files
└── references/         # References (PDF, BibTeX, etc.)
    └── references.bib  # BibTeX file
```

## Section Management

Each section is managed as a separate .tex file and connected in main.tex via `\input{sectionfile}`.
To add a new section:
1. Create `sectionname.tex` in the `contents/` folder
2. Add `\input{sectionname}` to `main.tex`

## Adding References

1. When the user provides a DOI or paper info, add a BibTeX entry to `references/references.bib`
2. Save reference PDFs in the `references/` folder
3. Cite in the body with `\cite{key}`

## Adding Figures

1. When the user attaches an image, save it to the `figures/` directory
2. Insert in the body as follows:
```latex
\begin{figure}[h]
    \centering
    \includegraphics[width=0.8\textwidth]{../figures/filename.png}
    \caption{Figure description}
    \label{fig:label}
\end{figure}
```

## Notes

- If build errors occur, check the `.log` file to identify the cause
- Build artifacts (`.aux`, `.log`, `.out`, etc.) should be excluded from distribution
