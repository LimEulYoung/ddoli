/**
 * Ddoli - Main Application JavaScript
 * Alpine.js 상태 관리 + 공통 유틸리티
 */

// ============================================================
// 공통 유틸리티
// ============================================================

// BasicAuth URL(user:pass@host)에서 fetch 실패 방지: credentials 제거된 절대 URL 생성
function safeUrl(relativeOrAbsoluteUrl) {
    try {
        return new URL(relativeOrAbsoluteUrl, window.location.origin).href;
    } catch(e) {
        return relativeOrAbsoluteUrl;
    }
}

async function fetchJSON(url, options = {}) {
    try {
        const res = await fetch(safeUrl(url), options);
        return await res.json();
    } catch(e) {
        console.error('fetchJSON error:', e);
        return null;
    }
}

async function fetchText(url) {
    try {
        const res = await fetch(safeUrl(url));
        return await res.text();
    } catch(e) {
        console.error('fetchText error:', e);
        return '';
    }
}

// ============================================================
// PDF.js 렌더링 (Alpine proxy 회피를 위해 순수 함수로 분리)
// ============================================================
async function _loadPdfDoc(arrayBuffer) {
    const pdf = await window.pdfjsLib.getDocument({ data: arrayBuffer }).promise;
    window._pdfDoc = pdf;
    return pdf.numPages;
}

async function _renderPdfPageRaw(pageNum) {
    if (!window._pdfDoc) return false;
    const page = await window._pdfDoc.getPage(pageNum);
    const canvas = document.getElementById('pdf-canvas');
    const container = document.getElementById('pdf-canvas-container');
    if (!canvas || !container || container.clientWidth <= 0) return false;
    let containerWidth = container.clientWidth - 16;
    if (containerWidth <= 0) containerWidth = window.innerWidth - 32;
    const viewport = page.getViewport({ scale: 1 });
    const dpr = Math.max(window.devicePixelRatio || 1, 2);
    const scale = containerWidth / viewport.width;
    const scaledViewport = page.getViewport({ scale: scale * dpr });
    canvas.width = Math.floor(scaledViewport.width);
    canvas.height = Math.floor(scaledViewport.height);
    canvas.style.width = Math.floor(scaledViewport.width / dpr) + 'px';
    canvas.style.height = Math.floor(scaledViewport.height / dpr) + 'px';
    const ctx = canvas.getContext('2d');
    if (!ctx) return false;
    await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise;
    return true;
}

// ============================================================
// 파일 확장자 → 언어 매핑 (openFile에서 사용)
// ============================================================
const LANG_MAP = {
    'py': 'python', 'js': 'javascript', 'ts': 'typescript',
    'jsx': 'javascript', 'tsx': 'typescript', 'html': 'html',
    'htm': 'html', 'css': 'css', 'scss': 'scss', 'less': 'less',
    'json': 'json', 'md': 'markdown', 'sh': 'bash', 'bash': 'bash',
    'zsh': 'bash', 'sql': 'sql', 'java': 'java', 'kt': 'kotlin',
    'go': 'go', 'rs': 'rust', 'cpp': 'cpp', 'c': 'c', 'h': 'c',
    'hpp': 'cpp', 'cs': 'csharp', 'rb': 'ruby', 'php': 'php',
    'swift': 'swift', 'yaml': 'yaml', 'yml': 'yaml', 'xml': 'xml',
    'toml': 'toml', 'ini': 'ini', 'dockerfile': 'dockerfile',
    'makefile': 'makefile', 'r': 'r', 'lua': 'lua', 'perl': 'perl',
    'vue': 'html', 'svelte': 'html', 'tex': 'latex', 'bib': 'bibtex'
};

// ============================================================
// 모드별 설정 (chat/code/paper 통합용)
// ============================================================
function _modeConfig(mode, paramName, overrides = {}) {
    const cap = paramName.charAt(0).toUpperCase() + paramName.slice(1);
    const prefix = mode === 'code' ? '' : mode;
    return {
        streamingKey: `${mode}Streaming`,
        contextKey: prefix ? `${mode}ContextPercent` : 'contextPercent',
        currentKey: `current${cap}`,
        listKey: `${paramName}s`,
        messagesElId: `${mode}-messages`,
        fileTreeElId: `${prefix ? mode + '-' : ''}file-tree-content`,
        eventSourceKey: `${mode}EventSource`,
        timerKey: `${mode}TimerInterval`,
        responsePrefix: `${mode}-response-`,
        resumePrefix: `${mode}-resume-`,
        streamUrl: `/${mode}/stream?id=`,
        submitUrl: `/${mode}`,
        submitTarget: `#${mode}-messages`,
        endpoints: Object.fromEntries(['messages','context','clear','active','status/','files','file-content','file-raw','file-write','file','project','create-file','create-folder','list-dirs']
            .map((ep, i) => [['messages','context','clear','active','status','files','fileContent','fileRaw','fileWrite','deleteFile','deleteProject','createFile','createFolder','listDirs'][i], `/${mode}/${ep}`])),
        paramName,
        sessionKey: `current${cap}`,
        loadListMethod: `load${cap}s`,
        expandedKey: prefix ? `${mode}ExpandedFolders` : 'expandedFolders',
        ...overrides,
    };
}

const MODE_CONFIG = {
    chat: {
        streamingKey: 'chatStreaming', contextKey: 'chatContextPercent',
        messagesElId: 'chat-messages-inner', eventSourceKey: 'chatEventSource',
        timerKey: 'chatTimerInterval', streamUrl: '/stream?id=',
        submitUrl: '/chat', submitTarget: '#chat-messages-inner',
        paramName: 'session_id', sessionKey: 'chatSessionId',
        statusUrl: '/chat/status/',
    },
    code: _modeConfig('code', 'project', { itemLabel: '프로젝트' }),
    paper: _modeConfig('paper', 'paper', { itemLabel: '논문' }),
};

// SSE cleanup 헬퍼
function createSSECleanup(mode, eventSource, alpineRoot) {
    const cfg = MODE_CONFIG[mode];
    return (isDone) => {
        if (window[cfg.timerKey]) {
            clearInterval(window[cfg.timerKey]);
            window[cfg.timerKey] = null;
        }
        eventSource.close();
        window[cfg.eventSourceKey] = null;
        if (mode === 'chat') window.chatResponseId = null;
        if (alpineRoot?._x_dataStack) {
            alpineRoot._x_dataStack[0][cfg.streamingKey] = false;
            // done 없이 끊긴 경우 플래그 설정 (visibilitychange에서 감지용)
            if (!isDone) alpineRoot._x_dataStack[0]._sseDisconnected = mode;
        }
    };
}

// 스크롤 컨테이너 찾기
function getScrollContainer(mode) {
    const cfg = MODE_CONFIG[mode];
    const el = document.getElementById(cfg.messagesElId);
    return el?.parentElement;
}

// ============================================================
// 도구 카드 생성 (통합) - 6곳에서 중복되던 코드를 하나로
// ============================================================
const ToolCards = {
    iconMap: {
        Edit: ['edit', 'text-yellow-600'], Read: ['file', 'text-blue-600'],
        Write: ['plus', 'text-green-600'], Bash: ['terminal', 'text-claude-accent'],
        Glob: ['search', 'text-purple-600'], Grep: ['search', 'text-purple-600'],
        WebSearch: ['globe', 'text-green-600'], WebFetch: ['link', 'text-blue-500'],
    },

    getIcon(name) {
        const [iconName, color] = this.iconMap[name] || ['bolt', 'text-claude-text-secondary'];
        return icon(iconName, 'w-4 h-4 ' + color);
    },

    titles: {
        Edit: '파일 수정',
        Read: '파일 읽기',
        Write: '파일 생성',
        Bash: '명령 실행',
        Glob: '검색',
        Grep: '검색',
        WebSearch: '웹 검색',
        WebFetch: '웹 페이지'
    },

    detailKeys: {
        Edit: 'file_path', Read: 'file_path', Write: 'file_path',
        Bash: 'command', Glob: 'pattern', Grep: 'pattern',
        WebSearch: 'query', WebFetch: 'url'
    },

    getDetail(name, input) {
        const key = this.detailKeys[name];
        if (key) {
            const val = input[key] || '';
            return key === 'file_path' ? val.split('/').pop() : val;
        }
        // MCP 등 알려지지 않은 도구: input 파라미터 표시
        if (!input || Object.keys(input).length === 0) return '';
        return Object.entries(input)
            .map(([k, v]) => {
                const val = typeof v === 'string' ? v : JSON.stringify(v);
                return k + ': ' + val;
            })
            .join('\n');
    },

    escapeHtml(str) {
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    },

    getTitle(name) {
        if (this.titles[name]) return this.titles[name];
        // MCP 도구: mcp__server__tool → tool (언더스코어를 공백으로)
        if (name.startsWith('mcp__') || name.startsWith('mcp_')) {
            const parts = name.split('__');
            const toolPart = parts.length >= 3 ? parts.slice(2).join('__') : parts[parts.length - 1];
            return toolPart.replace(/_/g, ' ');
        }
        return name;
    },

    create(name, input, status = '진행 중...') {
        const card = document.createElement('div');
        card.className = 'bg-white rounded-lg overflow-hidden border border-claude-border';

        const iconHtml = this.getIcon(name);
        const title = this.getTitle(name);
        const detail = this.getDetail(name, input);
        const detailHtml = detail
            ? `<div class="px-3 py-2 bg-slate-100 border-t border-claude-border"><pre class="text-xs text-slate-600 font-mono whitespace-pre-wrap break-all">${this.escapeHtml(detail)}</pre></div>`
            : '';

        const statusClass = status === '완료' ? 'text-claude-accent' : 'text-claude-text-secondary';
        card.innerHTML = `<div class="px-3 py-2 flex items-center gap-2 bg-claude-sidebar">${iconHtml}<span class="text-sm text-claude-text">${title}</span><span class="text-xs ${statusClass} ml-auto">${status}</span></div>${detailHtml}`;

        return card;
    },

    createEditResult(data) {
        const fileName = (data.filePath || '').split('/').pop();
        let diffHtml = '';
        (data.patch || []).forEach(p => {
            (p.lines || []).forEach(line => {
                const escaped = this.escapeHtml(line);
                if (line.startsWith('-')) {
                    diffHtml += `<div class="bg-red-100 text-red-700 px-2 font-mono text-xs">${escaped}</div>`;
                } else if (line.startsWith('+')) {
                    diffHtml += `<div class="bg-green-100 text-green-700 px-2 font-mono text-xs">${escaped}</div>`;
                } else {
                    diffHtml += `<div class="text-claude-text-secondary px-2 font-mono text-xs">${escaped}</div>`;
                }
            });
        });

        const card = document.createElement('div');
        card.className = 'bg-white rounded-lg overflow-hidden border border-claude-accent';
        card.innerHTML = `<div class="px-3 py-2 bg-claude-accent/5 flex items-center gap-2">${icon('check', 'w-4 h-4 text-claude-accent')}<span class="text-sm text-claude-text">파일 수정됨</span><span class="text-xs text-claude-text-secondary">${fileName}</span></div><div class="max-h-40 overflow-y-auto">${diffHtml}</div>`;

        return card;
    },

    updateBashResult(card, data) {
        const output = data.stdout || data.stderr || '';
        const isError = data.exitCode !== 0 || data.stderr;
        const cmd = data.command || '';
        const statusColor = isError ? 'text-red-500' : 'text-claude-accent';
        const statusText = isError ? '실패' : '완료';

        card.className = 'bg-white border border-claude-border rounded-lg overflow-hidden';
        card.innerHTML = `
            <div class="px-3 py-2 flex items-center gap-2 bg-claude-sidebar">
                ${icon('terminal', 'w-4 h-4 ' + statusColor)}
                <span class="text-sm text-claude-text">명령 실행</span>
                <span class="text-xs ${statusColor} ml-auto">${statusText}</span>
            </div>
            ${cmd ? `<div class="px-3 py-2 bg-slate-100 border-t border-claude-border"><pre class="text-xs text-slate-600 font-mono">$ ${this.escapeHtml(cmd)}</pre></div>` : ''}
            ${output ? `<div class="px-3 py-2 max-h-60 overflow-y-auto border-t border-claude-border"><pre class="text-xs text-claude-text whitespace-pre-wrap">${this.escapeHtml(output)}</pre></div>` : ''}
        `;
    },

    updateToolOutput(card, data) {
        const output = data.output || '';
        const badge = card.querySelector('.text-claude-text-secondary.ml-auto, .ml-auto');
        if (badge) {
            badge.textContent = '완료';
            badge.className = 'text-xs text-claude-accent ml-auto';
        }
        if (output) {
            const existing = card.querySelector('.tool-output-section');
            if (existing) existing.remove();
            const section = document.createElement('div');
            section.className = 'tool-output-section px-3 py-2 max-h-60 overflow-y-auto border-t border-claude-border';
            section.innerHTML = `<pre class="text-xs text-claude-text whitespace-pre-wrap break-all">${this.escapeHtml(output)}</pre>`;
            card.appendChild(section);
        }
    },

};

// ============================================================
// UI 아이콘 헬퍼 (index.html에서 x-html로 재사용)
// ============================================================
window.icon = function(name, cls) {
    const paths = {
        close: 'M6 18L18 6M6 6l12 12',
        plus: 'M12 4v16m8-8H4',
        trash: 'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16',
        chat: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
        code: 'M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4',
        folder: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z',
        menu: 'M4 6h16M4 12h16M4 18h16',
        refresh: 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15',
        send: 'M5 10l7-7m0 0l7 7m-7-7v18',
        chevronRight: 'M9 5l7 7-7 7',
        chevronDown: 'M19 9l-7 7-7-7',
        chevronLeft: 'M15 19l-7-7 7-7',
        copy: 'M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z',
        file: 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
        edit: 'M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z',
        check: 'M5 13l4 4L19 7',
        terminal: 'M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z',
        search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
        globe: 'M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9',
        link: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1',
        bolt: 'M13 10V3L4 14h7v7l9-11h-7z',
        camera: 'M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9zM15 13a3 3 0 11-6 0 3 3 0 016 0z',
        image: 'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z',
        settings: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z',
        download: 'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4',
        mic: 'M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z',
        chevronUp: 'M5 15l7-7 7 7',
        monitor: 'M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
        command: 'M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z',
    };
    if (name === 'more') {
        cls = cls || 'w-4 h-4';
        return '<svg class="' + cls + '" fill="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
    }
    if (name === 'stop') {
        cls = cls || 'w-4 h-4';
        return '<svg class="' + cls + '" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
    }
    cls = cls || 'w-4 h-4';
    return '<svg class="' + cls + '" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="' + (paths[name] || '') + '"/></svg>';
};

// ============================================================
// 마크다운 렌더링 유틸리티
// ============================================================
function renderMarkdown(text) {
    if (typeof marked !== 'undefined') {
        return marked.parse(text);
    }
    return text.replace(/\n/g, '<br>');
}

function highlightCodeBlocks(container) {
    container.querySelectorAll('pre').forEach(pre => {
        if (pre.querySelector('.code-copy-btn')) return;
        const code = pre.querySelector('code');
        if (!code) return;
        if (typeof hljs !== 'undefined') hljs.highlightElement(code);
        const btn = document.createElement('button');
        btn.className = 'code-copy-btn';
        btn.innerHTML = icon('copy', 'w-4 h-4');
        btn.addEventListener('click', () => {
            navigator.clipboard.writeText(code.textContent).then(() => {
                btn.innerHTML = icon('check', 'w-4 h-4');
                setTimeout(() => btn.innerHTML = icon('copy', 'w-4 h-4'), 1500);
            });
        });
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
}

function renderMarkdownElements(container) {
    container.querySelectorAll('.markdown-body').forEach(el => {
        if (el.dataset.raw) {
            el.innerHTML = renderMarkdown(el.dataset.raw);
            highlightCodeBlocks(el);
        }
    });
}

function createTextBlock(text) {
    const div = document.createElement('div');
    div.className = 'bg-white rounded-lg border border-claude-border p-4 mb-2';
    div.innerHTML = '<div class="markdown-body text-claude-text">' + renderMarkdown(text) + '</div>';
    highlightCodeBlocks(div);
    return div;
}

function appendRawContent(rawContent, text) {
    if (rawContent) {
        if (rawContent.value) rawContent.value += '\n\n';
        rawContent.value += text;
    }
}

function bindCopyButton(btn, getText) {
    btn.addEventListener('click', function() {
        navigator.clipboard.writeText(getText()).then(() => {
            this.innerHTML = icon('check', 'w-4 h-4 text-claude-accent');
            setTimeout(() => this.innerHTML = icon('copy'), 1500);
        });
    });
}

// ============================================================
// SSE 공통 헬퍼
// ============================================================
const SPINNER_SVG = (cls) => `<svg class="${cls} animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;

function createIdxChecker() {
    let lastIdx = -1;
    return (data) => {
        if (data._idx !== undefined && data._idx <= lastIdx) return false;
        if (data._idx !== undefined) lastIdx = data._idx;
        return true;
    };
}

function handleToolEvent(type, d, ctx) {
    const update = ctx.updateStatus || (() => {});
    if (type === 'tool_use') {
        if (ctx.displayedToolIds.has(d.id)) return;
        ctx.displayedToolIds.add(d.id);
        update(d.name + ' 실행 중...');
        const card = ToolCards.create(d.name, d.input);
        card.id = 'tool-' + d.id;
        ctx.eventsEl.appendChild(card);
    } else if (type === 'edit_result') {
        document.getElementById('tool-' + d.toolId)?.remove();
        ctx.eventsEl.appendChild(ToolCards.createEditResult(d));
    } else if (type === 'read_result') {
        const card = document.getElementById('tool-' + d.toolId);
        if (card) ToolCards.updateToolOutput(card, {});
    } else if (type === 'tool_output') {
        const card = document.getElementById('tool-' + d.toolId);
        if (card) ToolCards.updateToolOutput(card, d);
    } else if (type === 'bash_result') {
        const card = document.getElementById('tool-' + d.toolId);
        if (card) ToolCards.updateBashResult(card, d);
    } else if (type === 'text' && d.text) {
        update('응답 생성 중...');
        appendRawContent(ctx.rawContent, d.text);
        ctx.eventsEl.appendChild(createTextBlock(d.text));
    }
}

function bindToolSSEListeners(eventSource, ctx) {
    let currentToolId = null;
    const check = ctx.checkIdx || (() => true);
    const scroll = ctx.scrollToBottom || (() => {});
    ['tool_use', 'edit_result', 'read_result', 'tool_output', 'bash_result', 'text'].forEach(type => {
        eventSource.addEventListener(type, e => {
            try {
                const data = JSON.parse(e.data);
                if (!check(data)) return;
                if (type === 'tool_use') currentToolId = data.id;
                else if (!data.toolId && currentToolId) data.toolId = currentToolId;
                handleToolEvent(type, data, ctx);
                scroll();
            } catch(err) {}
        });
    });
}

function replayToolEvents(events, ctx) {
    (events || []).forEach(evt => handleToolEvent(evt.type, evt.data || {}, ctx));
}

// ============================================================
// SSE 핸들러 설정 (채팅/코드/paper 모드 공용)
// config: { responseId, mode: 'chat'|'code'|'paper', project?, paper? }
// ============================================================
// 세션 전환 감지용 세대 카운터
window._chatGen = 0;

function setupSSEHandlers(config) {
    const { responseId, mode } = config;
    const cfg = MODE_CONFIG[mode];
    const isChat = mode === 'chat';

    // 세션 전환으로 이 응답이 무효화된 경우 SSE 시작하지 않음
    if (isChat && window._pendingChatGen !== window._chatGen) return;

    const statusEl = document.getElementById('status-' + responseId);
    const statusTextEl = document.getElementById('status-text-' + responseId);
    const timerEl = document.getElementById('timer-' + responseId);
    const eventsEl = document.getElementById('events-' + responseId);

    const eventSource = new EventSource(cfg.streamUrl + responseId);
    const alpineRoot = document.querySelector('[x-data]');
    const scrollContainer = getScrollContainer(mode);

    // Alpine 상태 업데이트
    if (alpineRoot?._x_dataStack) alpineRoot._x_dataStack[0][cfg.streamingKey] = true;
    window[cfg.eventSourceKey] = eventSource;
    if (isChat) window.chatResponseId = responseId;

    // 타이머 설정
    const startTime = Date.now();
    const formatElapsed = () => {
        const s = Math.floor((Date.now() - startTime) / 1000);
        const m = Math.floor(s / 60);
        return m > 0 ? m + '분 ' + (s % 60) + '초' : s + '초';
    };
    window[cfg.timerKey] = setInterval(() => {
        if (timerEl) timerEl.textContent = '(' + Math.floor((Date.now() - startTime) / 1000) + 's)';
    }, 1000);

    const rawContent = { value: '' };
    const displayedToolIds = new Set();
    const cleanup = createSSECleanup(mode, eventSource, alpineRoot);

    // 공통 도구 이벤트 바인딩
    bindToolSSEListeners(eventSource, {
        eventsEl, displayedToolIds, rawContent,
        scrollToBottom: () => { if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight; },
        checkIdx: isChat ? createIdxChecker() : undefined,
        updateStatus: (text) => { if (statusTextEl) statusTextEl.textContent = text; }
    });

    // 모드별 이벤트 핸들러
    if (isChat) {
        eventSource.addEventListener('title', e => {
            document.getElementById('session-title').textContent = e.data;
        });
        eventSource.addEventListener('session_id', e => {
            if (document.body._x_dataStack) {
                document.body._x_dataStack[0].chatSessionId = e.data;
                document.body._x_dataStack[0].loadChatSessions();
            }
        });
        eventSource.addEventListener('status', e => { if (statusTextEl) statusTextEl.textContent = e.data; });
        eventSource.addEventListener('result', e => {
            try {
                const data = JSON.parse(e.data);
                if (data.contextPercent !== undefined && document.body._x_dataStack)
                    document.body._x_dataStack[0].chatContextPercent = data.contextPercent;
            } catch(err) {}
        });
    } else {
        eventSource.addEventListener('init', () => { if (statusTextEl) statusTextEl.textContent = '세션 시작됨'; });
        eventSource.addEventListener('result', e => {
            try {
                const data = JSON.parse(e.data);
                const lastCard = eventsEl.lastElementChild;
                if (lastCard?.classList.contains('bg-white')) {
                    const timeDiv = document.createElement('div');
                    timeDiv.className = 'text-xs text-claude-text-secondary mt-2 pt-2 border-t border-claude-border';
                    timeDiv.textContent = '소요시간: ' + formatElapsed();
                    lastCard.appendChild(timeDiv);
                }
                if (data.contextPercent !== undefined && document.body._x_dataStack)
                    document.body._x_dataStack[0][cfg.contextKey] = data.contextPercent;
            } catch(err) {}
        });
        eventSource.addEventListener('error_msg', e => {
            clearInterval(window[cfg.timerKey]);
            if (statusEl) statusEl.innerHTML = '<span class="text-red-500">오류 발생</span>';
            const finalEl = document.getElementById('final-' + responseId);
            if (finalEl) {
                finalEl.className = 'bg-red-50 border border-red-300 rounded-lg p-4 text-red-700';
                finalEl.textContent = e.data;
            }
        });
    }

    eventSource.addEventListener('done', e => {
        cleanup(true);
        if (statusEl) statusEl.remove();
        if (isChat) {
            const footer = document.createElement('div');
            footer.className = 'flex items-center justify-between mt-4';
            footer.innerHTML = `<div class="flex gap-1"><button class="copy-btn p-1.5 text-claude-text-secondary hover:text-claude-text hover:bg-claude-sidebar rounded-lg transition-all" title="전체 복사">${icon('copy')}</button></div><span class="text-xs text-claude-text-secondary/60">${formatElapsed()}</span>`;
            eventsEl.appendChild(footer);
            bindCopyButton(footer.querySelector('.copy-btn'), () => rawContent.value);
            const container = document.getElementById('chat-response-' + responseId);
            if (e.data && container) container.dataset.messageId = e.data;
        } else {
            const footer = document.createElement('div');
            footer.className = 'flex items-center justify-between mt-2';
            footer.innerHTML = `<div class="flex gap-1"><button class="copy-btn p-1.5 text-claude-text-secondary hover:text-claude-text hover:bg-claude-sidebar rounded-lg transition-all" title="전체 복사">${icon('copy')}</button></div><span class="text-xs text-claude-text-secondary/60">${formatElapsed()}</span>`;
            eventsEl.appendChild(footer);
            bindCopyButton(footer.querySelector('.copy-btn'), () => rawContent.value);
            const itemName = mode === 'paper' ? config.paper : config.project;
            htmx.ajax('GET', cfg.endpoints.files + '?' + cfg.paramName + '=' + itemName, {target: '#' + cfg.fileTreeElId, swap: 'innerHTML'});
        }
    });

    eventSource.onerror = () => cleanup(false);
}

// ============================================================
// Alpine.js 앱 데이터
// ============================================================
window.appData = function() {
    const data = {
        // 공통 상태
        sidebarOpen: false,
        message: '',
        mode: 'chat',
        _sseDisconnected: null,  // SSE가 done 없이 끊긴 모드 ('chat'|'code'|'paper'|null)

        // 채팅 모드
        chatSessionId: '',
        chatSessionTitle: '새 대화',
        chatSessions: [],
        chatStreaming: false,
        chatHasMessages: false,
        chatContextPercent: 0,

        // 코드 모드
        currentProject: null,
        projects: [],
        contextPercent: 0,
        codeStreaming: false,
        expandedFolders: [],  // 펼쳐진 폴더 경로 목록

        // 모델 선택
        selectedModel: 'sonnet',
        modelOptions: [
            { value: 'haiku', label: 'Haiku' },
            { value: 'sonnet', label: 'Sonnet' },
            { value: 'opus', label: 'Opus' },
        ],

        // Paper 모드
        currentPaper: null,
        papers: [],  // [{name}]
        paperContextPercent: 0,
        paperStreaming: false,
        paperTemplates: [],
        paperExpandedFolders: [],  // Paper 모드 펼쳐진 폴더 경로 목록

        // 새 프로젝트 모달
        showNewProjectModal: false,
        newProjectName: '',

        // 새 논문 모달
        showNewPaperModal: false,
        newPaperName: '',
        selectedTemplate: '',
        paperNameError: '',

        // 파일 내용 모달
        showFileModal: false,
        fileModalPath: '',
        fileModalContent: '',
        fileModalHighlighted: '',
        fileModalLoading: false,
        fileModalMediaType: '', // 'image', 'video', 'pdf', '' (텍스트)
        fileModalMediaUrl: '',
        fileModalEditing: false,
        fileModalEditContent: '',
        fileModalSaving: false,
        fileModalMode: '', // 어떤 모드에서 열었는지 (code/paper)

        // PDF.js 상태 (pdfDoc은 window._pdfDoc에 저장 — Alpine proxy 회피)
        pdfPage: 1,
        pdfTotalPages: 0,

        // MCP 도구
        mcpTools: [],
        mcpServers: [],
        selectedMcpServer: null,
        newMcpServer: { key: '', name: '', type: 'sse', url: '', command: '', argsText: '', modes: ['chat', 'code', 'paper'] },
        mcpServerAdding: false,
        mcpServerEditMode: 'add', // 'add' | 'edit'
        allMcpServers: [], // 모드 무관 전체 서버 목록 (관리용)

        // 첨부 패널
        attachPanelOpen: false,
        attachPanelView: 'main',  // 'main' | 'commands' | 'tools' | 'tools-detail' | 'add-mcp-server'
        attachPanelStyle: '',
        attachItems: [
            { icon: icon('camera', 'w-5 h-5 text-claude-text-secondary'), label: '카메라', action: 'pickCamera' },
            { icon: icon('image', 'w-5 h-5 text-claude-text-secondary'), label: '이미지', action: 'pickImage' },
            { icon: icon('file', 'w-5 h-5 text-claude-text-secondary'), label: '파일', action: 'pickFile' },
        ],

        // 첨부 파일
        attachedFiles: [],  // [{id, name, saveName, type, previewUrl}]
        dragOver: false,
        dragOverCount: 0,

        // 컨텍스트 메뉴 (롱프레스)
        contextMenu: { show: false, x: 0, y: 0, type: '', target: null },
        longPressTimer: null,
        longPressTriggered: false,

        // 도구 메뉴
        showToolsMenu: false,

        // 음성 → voice.js (voiceMixin), 터미널 → terminal.js (terminalMixin)

        // 아카이브
        showArchiveModal: false,
        archiveMode: 'code',
        archivedItems: [],

        // 새로 만들기 모달
        showCreateModal: false,
        createMode: 'code',       // 현재 모드 (code/paper)
        createType: 'file',       // 'file' | 'folder'
        createPath: '/',          // 위치 (디렉토리 경로)
        createName: '',           // 파일/폴더명
        createDirs: ['/'],        // 선택 가능한 디렉토리 목록
        createDirsFiltered: ['/'],// 필터링된 목록
        createDirsOpen: false,    // 드롭다운 열림 여부

        // 명령어 기능
        commands: [],
        showCommandModal: false,
        commandModalMode: 'create',  // 'create' | 'edit'
        editingCommand: null,
        newCommandName: '',
        newCommandContent: '',

        // ==================== 롱프레스 컨텍스트 메뉴 ====================

        _contextMenuHeight(type) {
            if (type === 'file') return 176;  // 경로복사 + 파일명복사 + 다운로드 + 삭제
            if (type === 'dir') return 132;   // 경로복사 + 폴더명복사 + 삭제
            if (type === 'command') return 88;
            if (type === 'project') return 176;  // 이름변경 + 복제 + 아카이브 + 삭제
            if (type === 'paper') return 220;  // PDF보기 + 이름변경 + 복제 + 아카이브 + 삭제
            return 44;
        },

        openFileContextMenu(e, type, target) {
            e.stopPropagation();
            const btn = e.currentTarget;
            const rect = btn.getBoundingClientRect();
            const menuWidth = 140;
            const menuHeight = this._contextMenuHeight(type);
            this.contextMenu = {
                show: true,
                x: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
                y: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menuHeight - 8)),
                type,
                target,
            };
        },

        startLongPress(e, type, target) {
            this.longPressTriggered = false;
            const rect = e.currentTarget.getBoundingClientRect();
            this.longPressTimer = setTimeout(() => {
                this.longPressTriggered = true;
                const menuWidth = 140;
                const menuHeight = this._contextMenuHeight(type);
                this.contextMenu = {
                    show: true,
                    x: Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8)),
                    y: Math.max(8, Math.min(rect.top, window.innerHeight - menuHeight - 8)),
                    type,
                    target,
                };
                if (navigator.vibrate) navigator.vibrate(30);
            }, 500);
        },

        endLongPress(e) {
            clearTimeout(this.longPressTimer);
            if (this.longPressTriggered) {
                e.preventDefault();
                this.longPressTriggered = false;
            }
        },

        cancelLongPress() {
            clearTimeout(this.longPressTimer);
        },

        contextMenuDelete() {
            const { type } = this.contextMenu;
            const target = this._closeContextMenu();
            const mockEvt = { stopPropagation() {} };
            if (type === 'chat') this.deleteChatSession(target, mockEvt);
            else if (type === 'project') this._deleteItem('code', target, mockEvt);
            else if (type === 'paper') this._deleteItem('paper', target, mockEvt);
            else if (type === 'command') this.deleteCommand(target, mockEvt);
            else if (type === 'file' || type === 'dir') this._deleteFile(target.mode, target.path, mockEvt);
        },

        async contextMenuRename() {
            const { type } = this.contextMenu;
            const target = this._closeContextMenu();
            const mode = type === 'project' ? 'code' : 'paper';
            const itemName = typeof target === 'string' ? target : target.name;
            const label = mode === 'code' ? '프로젝트' : '논문';
            const newName = prompt(`${label} 이름 변경`, itemName);
            if (!newName || newName.trim() === '' || newName.trim() === itemName) return;
            const trimmed = newName.trim();
            const formData = new FormData();
            formData.append('old_name', itemName);
            formData.append('new_name', trimmed);
            try {
                const res = await fetch(safeUrl(`/${mode}/rename`), { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    const cfg = MODE_CONFIG[mode];
                    await this[cfg.loadListMethod]();
                    this._selectItem(mode, mode === 'code' ? trimmed : { name: trimmed });
                } else {
                    alert(data.error || '이름 변경 실패');
                }
            } catch (e) {
                console.error('이름 변경 실패:', e);
                alert('이름 변경 중 오류가 발생했습니다.');
            }
        },

        async contextMenuClone() {
            const { type } = this.contextMenu;
            const target = this._closeContextMenu();
            const mode = type === 'project' ? 'code' : 'paper';
            const itemName = typeof target === 'string' ? target : target.name;
            // -2, -3, ... 형태로 이름 생성
            const cfg = MODE_CONFIG[mode];
            const existingNames = new Set((this[cfg.listKey] || []).map(i => typeof i === 'string' ? i : i.name));
            let cloneName = itemName;
            let n = 2;
            do {
                cloneName = `${itemName}-${n}`;
                n++;
            } while (existingNames.has(cloneName));

            const formData = new FormData();
            formData.append('source', itemName);
            formData.append('target', cloneName);
            try {
                const res = await fetch(safeUrl(`/${mode}/clone`), { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    await this[cfg.loadListMethod]();
                    this._selectItem(mode, mode === 'code' ? cloneName : { name: cloneName });
                } else {
                    alert(data.error || '복제 실패');
                }
            } catch (e) {
                console.error('복제 실패:', e);
                alert('복제 중 오류가 발생했습니다.');
            }
        },

        async contextMenuArchive() {
            const { type } = this.contextMenu;
            const target = this._closeContextMenu();
            const mode = type === 'project' ? 'code' : 'paper';
            const itemName = typeof target === 'string' ? target : target.name;
            const data = await fetchJSON(`/archive/${mode}/${encodeURIComponent(itemName)}`, { method: 'POST' });
            if (data?.success) {
                const cfg = MODE_CONFIG[mode];
                this[cfg.loadListMethod]();
                if (data.archived && this[cfg.currentKey] === itemName) {
                    this[cfg.currentKey] = null;
                    this[cfg.contextKey] = 0;
                    document.getElementById(cfg.messagesElId).innerHTML = '';
                }
            }
        },

        async renderPdfPage(pageNum, _retryCount) {
            if (!window._pdfDoc) return;
            this.pdfPage = pageNum;
            const retries = _retryCount || 0;
            try {
                const ok = await _renderPdfPageRaw(pageNum);
                if (!ok && retries < 5) {
                    setTimeout(() => this.renderPdfPage(pageNum, retries + 1), 100);
                }
            } catch (e) {
                console.error('PDF render error:', e);
            }
        },

        async _openPdfInModal(mode, itemName, path) {
            this.fileModalPath = path;
            this.fileModalContent = '';
            this.fileModalHighlighted = '';
            this.fileModalMediaType = 'pdf';
            this.fileModalMediaUrl = '';
            this.fileModalLoading = true;
            this.showFileModal = true;
            window._pdfDoc = null;
            this.pdfPage = 1;
            this.pdfTotalPages = 0;

            const cfg = MODE_CONFIG[mode];
            const paramValue = itemName || this[cfg.currentKey];
            const rawParam = cfg.paramName + '=' + encodeURIComponent(paramValue) + '&path=' + encodeURIComponent(path);
            const url = `/${mode}/file-download?${rawParam}`;

            try {
                const resp = await fetch(safeUrl(url));
                if (!resp.ok) {
                    const err = await resp.json().catch(() => null);
                    this.fileModalContent = err?.error || 'PDF 파일을 불러올 수 없습니다.';
                    this.fileModalMediaType = '';
                    this.fileModalLoading = false;
                    return;
                }
                const blob = await resp.blob();
                if (this.fileModalMediaUrl) URL.revokeObjectURL(this.fileModalMediaUrl);
                this.fileModalMediaUrl = URL.createObjectURL(blob);

                if (window.pdfjsLib) {
                    const arrayBuffer = await blob.arrayBuffer();
                    const numPages = await _loadPdfDoc(arrayBuffer);
                    this.pdfTotalPages = numPages;
                    this.pdfPage = 1;
                    this.fileModalPath = path;
                    this.fileModalLoading = false;
                    this.$nextTick(() => this.renderPdfPage(1));
                } else {
                    this.fileModalContent = 'PDF.js 미로드 (pdfjsLib=' + typeof window.pdfjsLib + ')';
                    this.fileModalMediaType = '';
                    this.fileModalLoading = false;
                }
            } catch (e) {
                console.error('PDF load error:', e);
                this.fileModalContent = 'PDF 오류: ' + e.message;
                this.fileModalMediaType = '';
                this.fileModalLoading = false;
            }
        },

        _closeContextMenu() {
            const t = this.contextMenu.target;
            this.contextMenu.show = false;
            return t;
        },

        contextMenuOpenPdf() {
            const target = this._closeContextMenu();
            if (target?.name) this._openPdfInModal('paper', target.name, 'contents/main.pdf');
        },

        contextMenuDownloadFile() {
            const target = this._closeContextMenu();
            if (!target) return;
            const cfg = MODE_CONFIG[target.mode];
            const url = `/${target.mode}/file-download?${cfg.paramName}=${encodeURIComponent(this[cfg.currentKey])}&path=${encodeURIComponent(target.path)}`;
            const a = document.createElement('a');
            a.href = url; a.download = target.name;
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
        },

        contextMenuCopyPath() {
            const target = this._closeContextMenu();
            if (!target) return;
            const cfg = MODE_CONFIG[target.mode];
            const base = target.mode === 'code' ? '~/workspace' : '~/papers';
            navigator.clipboard.writeText(`${base}/${this[cfg.currentKey]}/${target.path}`);
        },

        contextMenuCopyName() {
            const target = this._closeContextMenu();
            if (target) navigator.clipboard.writeText(target.name);
        },

        // ==================== 공통 메서드 ====================

        autoResize(el) {
            el.style.height = 'auto';
            const maxH = window.innerHeight * 0.4;
            el.style.height = Math.min(el.scrollHeight, maxH) + 'px';
            el.style.overflowY = el.scrollHeight > maxH ? 'auto' : 'hidden';
            if (el.scrollHeight > maxH) el.scrollTop = el.scrollHeight;
        },

        handleEnter(e, el, streamingFlag) {
            const isMobile = window.matchMedia('(pointer: coarse)').matches && window.innerWidth <= 768;
            if (isMobile || e.shiftKey) {
                e.preventDefault();
                const start = el.selectionStart;
                const end = el.selectionEnd;
                this.message = this.message.substring(0, start) + '\n' + this.message.substring(end);
                this.$nextTick(() => {
                    el.selectionStart = el.selectionEnd = start + 1;
                    this.autoResize(el);
                });
            } else if (this.message.trim() && !this[streamingFlag]) {
                e.preventDefault();
                el.form.requestSubmit();
                el.style.height = 'auto';
                el.style.overflowY = 'hidden';
            }
        },

        submitMessage() {
            if (!this.message.trim()) return;
            const cfg = MODE_CONFIG[this.mode];
            if (this[cfg.streamingKey]) return;

            if (this.mode === 'chat') {
                this.chatHasMessages = true;
                window._pendingChatGen = window._chatGen;
            }

            const values = { message: this.message };
            values[cfg.paramName] = this[cfg.sessionKey];

            if (this.selectedModel) values.model = this.selectedModel;
            const enabledTools = this.getEnabledMcpToolNames();
            if (enabledTools.length > 0) values.mcp_tools = enabledTools.join(',');
            if (this.attachedFiles.length > 0) values.file_map = this.attachedFiles.map(f => f.shortName + ':' + f.saveName).join(',');

            htmx.ajax('POST', cfg.submitUrl, {
                target: cfg.submitTarget,
                swap: 'beforeend',
                values: values
            }).then(() => {
                const container = document.getElementById(cfg.messagesElId)?.parentElement;
                if (container) container.scrollTop = container.scrollHeight;
            });

            this.message = '';
            this.attachedFiles.forEach(f => { if (f.previewUrl) URL.revokeObjectURL(f.previewUrl); });
            this.attachedFiles = [];
            this.attachPanelOpen = false;
            const ta = this.$refs.messageTextarea;
            if (ta) { ta.value = ''; ta.style.height = 'auto'; ta.style.overflowY = 'hidden'; }
        },

        toggleAttachPanel(refName) {
            if (this.attachPanelOpen) {
                this.attachPanelOpen = false;
                this.attachPanelView = 'main';
                return;
            }
            this.attachPanelView = 'main';
            const el = this.$refs[refName];
            if (el) {
                const rect = el.getBoundingClientRect();
                this.attachPanelStyle = `bottom: ${window.innerHeight - rect.top + 8}px; left: ${rect.left}px; width: ${rect.width}px;`;
            }
            this.attachPanelOpen = true;
        },

        _pickAttach(inputId) {
            const input = document.getElementById(inputId);
            if (input) input.click();
            this.attachPanelOpen = false;
        },

        pickCamera() { this._pickAttach('file-input-camera'); },
        pickImage() { this._pickAttach('file-input-image'); },
        pickFile() { this._pickAttach('file-input-file'); },

        async uploadAndAttach(file) {
            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch(safeUrl('/upload'), { method: 'POST', body: formData });
                const data = await res.json();
                if (data.saveName) {
                    const isImage = file.type.startsWith('image/');
                    const isPdf = file.name.toLowerCase().endsWith('.pdf');
                    const prefix = isImage ? 'image' : isPdf ? 'pdf' : 'file';
                    const count = this.attachedFiles.filter(f => f.shortPrefix === prefix).length + 1;
                    const shortName = prefix + count;

                    const placeholder = `{{file:${shortName}}}`;
                    this.message += (this.message && !this.message.endsWith('\n') ? '\n' : '') + placeholder;

                    const entry = {
                        id: data.fileId,
                        name: data.fileName,
                        saveName: data.saveName,
                        shortName: shortName,
                        shortPrefix: prefix,
                        type: isImage ? 'image' : 'file',
                        previewUrl: isImage ? URL.createObjectURL(file) : null
                    };
                    this.attachedFiles.push(entry);

                    this.$nextTick(() => {
                        const ta = this.$refs.messageTextarea;
                        if (ta) this.autoResize(ta);
                    });
                }
            } catch(e) {
                console.error('파일 업로드 실패:', e);
                alert('파일 업로드에 실패했습니다.');
            }
        },

        async handleFileSelected(event) {
            const file = event.target.files[0];
            if (!file) return;
            event.target.value = '';
            await this.uploadAndAttach(file);
        },

        handlePaste(event) {
            const items = event.clipboardData?.items;
            if (!items) return;
            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    event.preventDefault();
                    const file = item.getAsFile();
                    if (file) this.uploadAndAttach(file);
                    return;
                }
            }
        },

        handleDrop(event) {
            this.dragOver = false;
            this.dragOverCount = 0;
            const files = event.dataTransfer.files;
            for (const file of files) {
                this.uploadAndAttach(file);
            }
        },

        removeAttachment(id) {
            const file = this.attachedFiles.find(f => f.id === id);
            if (file) {
                this.message = this.message.replace(`{{file:${file.shortName}}}`, '').replace(/\n{2,}/g, '\n').trim();
                if (file.previewUrl) URL.revokeObjectURL(file.previewUrl);
                this.attachedFiles = this.attachedFiles.filter(f => f.id !== id);
                this.$nextTick(() => {
                    const ta = this.$refs.messageTextarea;
                    if (ta) this.autoResize(ta);
                });
            }
        },

        stopStreaming(mode) {
            fetch('/stop', { method: 'POST', body: new URLSearchParams({ mode }) });

            const cfg = MODE_CONFIG[mode];
            if (!cfg || !window[cfg.eventSourceKey]) return;

            window[cfg.eventSourceKey].close();
            window[cfg.eventSourceKey] = null;
            this[cfg.streamingKey] = false;
            if (window[cfg.timerKey]) { clearInterval(window[cfg.timerKey]); window[cfg.timerKey] = null; }
            const statusPrefix = mode === 'chat' ? 'thinking-' : 'status-';
            document.querySelectorAll(`#${cfg.messagesElId} [id^="${statusPrefix}"]`).forEach(el => el.remove());
        },

        // ==================== 채팅 모드 메서드 ====================

        async loadChatSessions() {
            this.chatSessions = await fetchJSON('/sessions?mode=chat') || [];
        },

        newChatSession() {
            window._chatGen++;
            if (window.chatEventSource) { window.chatEventSource.close(); window.chatEventSource = null; }
            if (window.chatTimerInterval) { clearInterval(window.chatTimerInterval); window.chatTimerInterval = null; }
            this.chatStreaming = false;
            this.chatSessionId = '';
            this.chatSessionTitle = '새 대화';
            this.chatHasMessages = false;
            this.chatContextPercent = 0;
            const messagesEl = document.getElementById('chat-messages-inner');
            if (messagesEl) { htmx.trigger(messagesEl, 'htmx:abort'); messagesEl.innerHTML = ''; }
            const titleEl = document.getElementById('session-title');
            if (titleEl) titleEl.textContent = '새 대화';
            this.loadChatSessions();
            this.sidebarOpen = false;
        },

        async selectChatSession(session) {
            window._chatGen++;
            if (window.chatEventSource) { window.chatEventSource.close(); window.chatEventSource = null; }
            this.chatStreaming = false;
            this.chatSessionId = session.id;
            this.chatSessionTitle = session.title;
            this.chatContextPercent = session.context_percent || 0;
            document.getElementById('session-title').textContent = session.title;

            const html = await fetchText('/session/' + session.id + '/messages');
            const messagesInner = document.getElementById('chat-messages-inner');
            messagesInner.innerHTML = html;
            this.chatHasMessages = html.trim().length > 0;
            renderMarkdownElements(messagesInner);
            this.bindHistoryMessageButtons();

            await this.$nextTick();
            await new Promise(r => requestAnimationFrame(r));
            this.showLongMessageToggles('#chat-messages-inner');
            if (messagesInner.parentElement) messagesInner.parentElement.scrollTop = messagesInner.parentElement.scrollHeight;

            this.checkActiveChatResponses();
        },

        showLongMessageToggles(containerSelector) {
            document.querySelectorAll(containerSelector + ' .user-msg-content.collapsed').forEach(el => {
                const btn = el.nextElementSibling;
                if (btn && el.scrollHeight > el.clientHeight + 10) {
                    btn.classList.remove('hidden');
                    el.classList.remove('px-4');
                    el.classList.add('pl-4', 'pr-10');
                }
            });
        },

        bindHistoryMessageButtons() {
            document.querySelectorAll('#chat-messages-inner .copy-btn').forEach(btn => {
                bindCopyButton(btn, () => {
                    const el = btn.closest('.mb-6')?.querySelector('.markdown-body[data-raw]');
                    return el?.dataset.raw || '';
                });
            });
        },

        async deleteAllChatSessions() {
            if (!confirm('모든 대화를 삭제하시겠습니까?')) return;
            await fetch(safeUrl('/sessions/chat'), { method: 'DELETE' });
            await this.loadChatSessions();
            this.newChatSession();
            this.showToolsMenu = false;
        },

        async deleteChatSession(sessionId, e) {
            e.stopPropagation();
            if (!confirm('이 대화를 삭제하시겠습니까?')) return;
            await fetch(safeUrl('/session/' + sessionId), { method: 'DELETE' });
            this.loadChatSessions();
            if (this.chatSessionId === sessionId) this.newChatSession();
        },

        async checkActiveChatResponses() {
            if (!this.chatSessionId) return;
            const data = await fetchJSON('/chat/active?session_id=' + encodeURIComponent(this.chatSessionId));
            if (data?.active?.length > 0) {
                this._sseDisconnected = null;
                this._resumeModeStream('chat', data.active[0]);
            } else if (this._sseDisconnected === 'chat') {
                // SSE가 done 없이 끊긴 상태에서 서버 응답이 이미 완료됨 → DB에서 메시지 다시 로드
                this._sseDisconnected = null;
                this.selectChatSession({ id: this.chatSessionId, title: this.chatSessionTitle });
            } else {
                await this.loadChatSessions();
                const session = this.chatSessions.find(s => s.id === this.chatSessionId);
                if (session?.context_percent) this.chatContextPercent = session.context_percent;
            }
        },

        // ==================== 코드/Paper 모드 통합 메서드 ====================

        // 통합: 항목 선택 (code/paper)
        async _selectItem(mode, item) {
            const cfg = MODE_CONFIG[mode];
            const itemName = typeof item === 'string' ? item : item.name;

            if (this[cfg.currentKey] === itemName) { this._checkActiveResponses(mode); return; }

            if (window[cfg.eventSourceKey]) { window[cfg.eventSourceKey].close(); window[cfg.eventSourceKey] = null; }
            if (window[cfg.timerKey]) { clearInterval(window[cfg.timerKey]); window[cfg.timerKey] = null; }
            this[cfg.streamingKey] = false;
            this[cfg.expandedKey] = [];
            this[cfg.currentKey] = itemName;

            const ctxData = await fetchJSON(cfg.endpoints.context + '?' + cfg.paramName + '=' + encodeURIComponent(itemName));
            this[cfg.contextKey] = ctxData?.contextPercent || 0;

            await this._loadMessages(mode, itemName);
            this.showLongMessageToggles('#' + cfg.messagesElId);
            const scrollContainer = getScrollContainer(mode);
            if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
            this._checkActiveResponses(mode);
        },

        // 통합: 메시지 로드 (code/paper)
        async _loadMessages(mode, itemName) {
            const cfg = MODE_CONFIG[mode];
            const html = await fetchText(cfg.endpoints.messages + '?' + cfg.paramName + '=' + encodeURIComponent(itemName));
            const msgEl = document.getElementById(cfg.messagesElId);
            if (msgEl) { msgEl.innerHTML = html; renderMarkdownElements(msgEl); }
        },

        // 통합: 세션 초기화 (code/paper)
        async _clearSession(mode) {
            const cfg = MODE_CONFIG[mode];
            if (!this[cfg.currentKey]) return;
            const formData = new FormData();
            formData.append(cfg.paramName, this[cfg.currentKey]);
            await fetch(safeUrl(cfg.endpoints.clear), { method: 'POST', body: formData });
            this[cfg.contextKey] = 0;
            await this._loadMessages(mode, this[cfg.currentKey]);
        },

        // 통합: 프로젝트/논문 삭제 (code/paper)
        async _deleteItem(mode, item, e) {
            e.stopPropagation();
            const cfg = MODE_CONFIG[mode];
            const itemName = typeof item === 'string' ? item : item.name;
            if (!confirm(cfg.itemLabel + ' [' + itemName + ']을(를) 삭제하시겠습니까? 모든 파일이 삭제됩니다.')) return;

            const data = await fetchJSON(cfg.endpoints.deleteProject + '?' + cfg.paramName + '=' + encodeURIComponent(itemName), { method: 'DELETE' });
            if (data?.success) {
                this[cfg.loadListMethod]();
                if (this[cfg.currentKey] === itemName) {
                    this[cfg.currentKey] = null;
                    this[cfg.contextKey] = 0;
                    document.getElementById(cfg.messagesElId).innerHTML = '';
                }
            } else {
                alert(data?.error || '삭제 실패');
            }
        },

        // 통합: 폴더 접기/펼치기 (code/paper)
        toggleFolder(path, mode = 'code') {
            const cfg = MODE_CONFIG[mode];
            const container = document.getElementById(cfg.fileTreeElId);
            if (!container) return;

            const isExpanded = this[cfg.expandedKey].includes(path);
            const folderEl = container.querySelector(`[data-path="${path}"]`);
            const chevron = folderEl?.querySelector('.folder-chevron');

            if (isExpanded) {
                this[cfg.expandedKey] = this[cfg.expandedKey].filter(p => p !== path && !p.startsWith(path + '/'));
                container.querySelectorAll(`[data-parent="${path}"], [data-parent^="${path}/"]`).forEach(el => el.classList.add('hidden'));
                if (chevron) chevron.classList.remove('rotate-90');
            } else {
                this[cfg.expandedKey].push(path);
                container.querySelectorAll(`[data-parent="${path}"]`).forEach(el => el.classList.remove('hidden'));
                if (chevron) chevron.classList.add('rotate-90');
            }
        },

        // 통합: 파일 삭제 (code/paper)
        async _deleteFile(mode, path, e) {
            e.stopPropagation();
            const cfg = MODE_CONFIG[mode];
            if (!confirm('[' + path + ']을(를) 삭제하시겠습니까?')) return;

            const data = await fetchJSON(cfg.endpoints.deleteFile + '?' + cfg.paramName + '=' + encodeURIComponent(this[cfg.currentKey]) + '&path=' + encodeURIComponent(path), { method: 'DELETE' });
            if (data?.success) {
                htmx.ajax('GET', cfg.endpoints.files + '?' + cfg.paramName + '=' + this[cfg.currentKey], {target: '#' + cfg.fileTreeElId, swap: 'innerHTML'});
            } else {
                alert(data?.error || '삭제 실패');
            }
        },

        // 통합: 새로 만들기 모달 열기 (code/paper)
        async openCreateModal(mode) {
            const cfg = MODE_CONFIG[mode];
            if (!this[cfg.currentKey]) return;
            this.createMode = mode;
            this.createType = 'file';
            this.createPath = '/';
            this.createName = '';
            this.createDirsOpen = false;
            this.showCreateModal = true;
            // 디렉토리 목록 로드
            const dirs = await fetchJSON(cfg.endpoints.listDirs + '?' + cfg.paramName + '=' + encodeURIComponent(this[cfg.currentKey]));
            this.createDirs = dirs || ['/'];
            this.createDirsFiltered = this.createDirs;
        },

        filterCreateDirs() {
            const q = this.createPath.toLowerCase();
            this.createDirsFiltered = this.createDirs.filter(d => d.toLowerCase().includes(q));
        },

        selectCreateDir(dir) {
            this.createPath = dir;
            this.createDirsOpen = false;
        },

        async submitCreateItem() {
            const cfg = MODE_CONFIG[this.createMode];
            if (!this.createName.trim() || !this[cfg.currentKey]) return;
            const formData = new FormData();
            formData.append(cfg.paramName, this[cfg.currentKey]);
            formData.append('path', this.createPath);
            const endpoint = this.createType === 'file' ? cfg.endpoints.createFile : cfg.endpoints.createFolder;
            formData.append(this.createType === 'file' ? 'filename' : 'foldername', this.createName.trim());
            try {
                const res = await fetch(safeUrl(endpoint), { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showCreateModal = false;
                    htmx.ajax('GET', cfg.endpoints.files + '?' + cfg.paramName + '=' + this[cfg.currentKey], {target: '#' + cfg.fileTreeElId, swap: 'innerHTML'});
                } else {
                    alert(data.error || '생성 실패');
                }
            } catch (e) {
                alert('생성 중 오류가 발생했습니다.');
            }
        },

        // 통합: 파일 열기 (code/paper)
        async _openFile(mode, path) {
            const cfg = MODE_CONFIG[mode];
            this.fileModalPath = path;
            this.fileModalContent = '';
            this.fileModalHighlighted = '';
            this.fileModalMediaType = '';
            this.fileModalMediaUrl = '';
            this.fileModalEditing = false;
            this.fileModalEditContent = '';
            this.fileModalSaving = false;
            this.fileModalMode = mode;
            this.fileModalLoading = true;
            this.showFileModal = true;

            const ext = path.split('.').pop().toLowerCase();
            const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'heic', 'heif'];
            const videoExts = ['mp4', 'webm', 'mov', 'ogg'];

            if (ext === 'pdf') {
                this._openPdfInModal(mode, this[cfg.currentKey], path);
                return;
            }

            if (imageExts.includes(ext) || videoExts.includes(ext)) {
                // 미디어 파일: 바이너리로 가져와서 미리보기
                const mediaType = imageExts.includes(ext) ? 'image' : 'video';
                const rawParam = cfg.paramName + '=' + encodeURIComponent(this[cfg.currentKey]) + '&path=' + encodeURIComponent(path);
                const url = cfg.endpoints.fileRaw + '?' + rawParam;
                try {
                    const resp = await fetch(safeUrl(url));
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => null);
                        this.fileModalContent = err?.error || '파일을 불러올 수 없습니다.';
                        this.fileModalLoading = false;
                        return;
                    }
                    const blob = await resp.blob();
                    if (this.fileModalMediaUrl) URL.revokeObjectURL(this.fileModalMediaUrl);
                    this.fileModalMediaUrl = URL.createObjectURL(blob);
                    this.fileModalMediaType = mediaType;
                } catch(e) {
                    this.fileModalContent = '파일을 불러올 수 없습니다.';
                }
                this.fileModalLoading = false;
                return;
            }

            // 텍스트 파일: 기존 로직
            const data = await fetchJSON(cfg.endpoints.fileContent + '?' + cfg.paramName + '=' + encodeURIComponent(this[cfg.currentKey]) + '&path=' + encodeURIComponent(path));
            const content = data?.content || data?.error || '파일을 불러올 수 없습니다.';
            this.fileModalContent = content;

            // 마크다운 파일: 렌더링된 HTML로 표시
            if (ext === 'md' || ext === 'markdown') {
                this.fileModalMediaType = 'markdown';
                this.fileModalLoading = false;
                return;
            }

            const lang = LANG_MAP[ext];
            const lines = content.split('\n');
            if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
            const pad = String(lines.length).length;
            const toRow = (code, i) => `<tr><td class="line-num" data-line="${i + 1}">${String(i + 1).padStart(pad)}</td><td class="line-code">${code || ' '}</td></tr>`;

            if (lang && typeof hljs !== 'undefined') {
                try {
                    const hLines = hljs.highlight(content, { language: lang }).value.split('\n');
                    if (hLines.length > 1 && hLines[hLines.length - 1] === '') hLines.pop();
                    this.fileModalHighlighted = hLines.map(toRow).join('');
                } catch(e) { this.fileModalHighlighted = ''; }
            }
            if (!this.fileModalHighlighted) {
                this.fileModalHighlighted = lines.map((line, i) => toRow(ToolCards.escapeHtml(line), i)).join('');
            }
            this.fileModalLoading = false;
        },

        // 통합: 활성 응답 체크 (code/paper)
        async _checkActiveResponses(mode) {
            const cfg = MODE_CONFIG[mode];
            if (!this[cfg.currentKey]) return;
            const data = await fetchJSON(cfg.endpoints.active + '?' + cfg.paramName + '=' + encodeURIComponent(this[cfg.currentKey]));
            if (data?.active?.length > 0) {
                this._sseDisconnected = null;
                this._resumeModeStream(mode, data.active[0]);
            } else if (this._sseDisconnected === mode) {
                // SSE가 done 없이 끊긴 상태에서 서버 응답이 이미 완료됨 → 메시지 다시 로드
                this._sseDisconnected = null;
                const item = this[cfg.currentKey];
                this[cfg.currentKey] = null;
                this._selectItem(mode, item);
            }
        },

        // 통합: Resume UI 설정 (chat/code/paper 공용)
        _setupResumeUI(mode, responseId) {
            const cfg = MODE_CONFIG[mode];
            const isChat = mode === 'chat';
            const container = document.getElementById(cfg.messagesElId);
            const prefix = isChat ? 'chat-' : cfg.resumePrefix;
            let eventsEl = document.getElementById('events-' + responseId);
            let statusEl = document.getElementById('status-' + responseId);
            let alreadyDisplayed = false;

            if (document.getElementById(prefix + responseId) || (isChat && document.getElementById('chat-response-' + responseId))) {
                alreadyDisplayed = eventsEl && eventsEl.children.length > 0;
                if (!statusEl) statusEl = document.getElementById(prefix + 'status-' + responseId);
            } else {
                const wrapperId = isChat ? `chat-response-${responseId}` : `${prefix}${responseId}`;
                const cls = isChat ? 'mb-6' : 'mb-4 space-y-3';
                const spinnerHtml = isChat ? `<div class="w-5 h-5 rounded-full bg-claude-accent/20 flex items-center justify-center">${SPINNER_SVG('w-3 h-3 text-claude-accent')}</div>` : SPINNER_SVG('w-4 h-4');
                container.insertAdjacentHTML('beforeend', `
                    <div id="${wrapperId}" class="${cls}">
                        <div id="status-${responseId}" class="flex items-center gap-2 text-claude-text-secondary text-sm${isChat ? ' mb-3' : ''}">${spinnerHtml}<span id="status-text-${responseId}">재연결 중...</span></div>
                        <div id="events-${responseId}" class="space-y-2${isChat ? ' mb-3' : ''}"></div>
                    </div>`);
                eventsEl = document.getElementById('events-' + responseId);
                statusEl = document.getElementById('status-' + responseId);
            }
            const scrollParent = isChat ? container.parentElement : getScrollContainer(mode);
            const statusTextEl = document.getElementById('status-text-' + responseId);
            return { eventsEl, statusEl, alreadyDisplayed, scrollParent, updateStatusText: (text) => { if (statusTextEl) statusTextEl.textContent = text; } };
        },

        // 통합: 기존 이벤트 리플레이 (chat/code/paper 공용)
        _replayExistingEvents(status, alreadyDisplayed, eventsEl) {
            const displayedToolIds = new Set();
            const rawContent = { value: '' };
            if (alreadyDisplayed && status.events) {
                status.events.forEach(evt => {
                    if (evt.type === 'tool_use' && evt.data?.id) displayedToolIds.add(evt.data.id);
                    if (evt.type === 'text' && evt.data?.text) appendRawContent(rawContent, evt.data.text);
                });
            } else if (status.events?.length > 0) {
                replayToolEvents(status.events, { eventsEl, displayedToolIds, rawContent });
            }
            return { displayedToolIds, rawContent };
        },

        // 통합: 스트림 재연결 (chat/code/paper)
        async _resumeModeStream(mode, responseId) {
            const cfg = MODE_CONFIG[mode];
            const isChat = mode === 'chat';
            if (this[cfg.streamingKey] || window[cfg.eventSourceKey]) return;
            const statusUrl = isChat ? cfg.statusUrl : cfg.endpoints.status;
            const status = await fetchJSON(statusUrl + responseId);
            if (!status || ['not_found', 'completed', 'error'].includes(status.status)) {
                if (isChat) { if (this.chatSessionId) this.selectChatSession({ id: this.chatSessionId, title: this.chatSessionTitle }); }
                else if (this[cfg.currentKey]) { const item = this[cfg.currentKey]; this[cfg.currentKey] = null; this._selectItem(mode, item); }
                return;
            }
            this[cfg.streamingKey] = true;
            if (isChat) this.chatHasMessages = true;
            const { eventsEl, statusEl, alreadyDisplayed, updateStatusText, scrollParent } = this._setupResumeUI(mode, responseId);
            const { displayedToolIds, rawContent } = this._replayExistingEvents(status, alreadyDisplayed, eventsEl);
            if (isChat) {
                if (status.events?.length > 0) updateStatusText('진행 중...');
            } else if (!alreadyDisplayed && status.events?.length > 0) {
                const lastToolEvt = [...status.events].reverse().find(e => e.type === 'tool_use');
                if (lastToolEvt) updateStatusText((lastToolEvt.data.name || '도구') + ' 실행 중...');
            }
            const scrollToBottom = () => { if (scrollParent) scrollParent.scrollTop = scrollParent.scrollHeight; };
            scrollToBottom();

            const eventSource = new EventSource(cfg.streamUrl + responseId + '&start_from=' + (status.events?.length || 0));
            window[cfg.eventSourceKey] = eventSource;
            bindToolSSEListeners(eventSource, { eventsEl, displayedToolIds, rawContent, checkIdx: isChat ? createIdxChecker() : undefined, updateStatus: updateStatusText, scrollToBottom });
            if (!isChat) eventSource.addEventListener('result', e => { try { const d = JSON.parse(e.data); if (statusEl) statusEl.remove(); if (d.contextPercent !== undefined) this[cfg.contextKey] = d.contextPercent; } catch(err) {} });
            const cleanup = (isDone) => { eventSource.close(); window[cfg.eventSourceKey] = null; this[cfg.streamingKey] = false; if (!isDone) this._sseDisconnected = mode; };
            eventSource.addEventListener('done', () => {
                cleanup(true);
                if (isChat) { if (statusEl) statusEl.remove(); if (this.chatSessionId) this.selectChatSession({ id: this.chatSessionId, title: this.chatSessionTitle }); }
                else {
                    if (statusEl) statusEl.remove();
                    const footer = document.createElement('div');
                    footer.className = 'flex items-center justify-between mt-2';
                    footer.innerHTML = `<div class="flex gap-1"><button class="copy-btn p-1.5 text-claude-text-secondary hover:text-claude-text hover:bg-claude-sidebar rounded-lg transition-all" title="전체 복사">${icon('copy')}</button></div>`;
                    eventsEl.appendChild(footer);
                    bindCopyButton(footer.querySelector('.copy-btn'), () => rawContent.value);
                    if (this[cfg.currentKey]) { const item = this[cfg.currentKey]; this[cfg.currentKey] = null; this._selectItem(mode, item); }
                }
            });
            eventSource.onerror = () => cleanup(false);
        },

        // ==================== 코드 모드 래퍼 메서드 ====================

        switchToCodeMode() {
            this.currentProject = null;
            this.mode = 'code';
            this.loadMcpTools();
        },

        async loadProjects() {
            this.projects = await fetchJSON('/code/projects-json') || [];
        },

        // ==================== Paper 모드 메서드 ====================

        switchToPaperMode() {
            this.currentPaper = null;
            this.mode = 'paper';
            this.loadMcpTools();
        },

        async loadPapers() {
            this.papers = await fetchJSON('/paper/papers-json') || [];
        },

        async loadPaperTemplates() {
            this.paperTemplates = await fetchJSON('/paper/templates') || [];
        },

        validatePaperName() {
            const name = this.newPaperName.trim();
            if (!name) { this.paperNameError = ''; return; }
            if (!/^[a-z0-9-]+$/.test(name)) { this.paperNameError = '소문자, 숫자, 하이픈(-)만 사용 가능합니다'; return; }
            if (name.startsWith('-') || name.endsWith('-')) { this.paperNameError = '하이픈으로 시작하거나 끝날 수 없습니다'; return; }
            if (name.includes('--')) { this.paperNameError = '연속된 하이픈(--)은 사용할 수 없습니다'; return; }
            if (this.papers.some(p => p.name === name)) { this.paperNameError = '이미 같은 이름의 프로젝트가 존재합니다.'; return; }
            this.paperNameError = '';
        },

        async createNewPaper() {
            if (!this.newPaperName.trim() || this.paperNameError) return;
            const formData = new FormData();
            formData.append('paper_name', this.newPaperName.trim());
            formData.append('template', this.selectedTemplate);
            try {
                const res = await fetch(safeUrl('/paper/new-paper'), { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showNewPaperModal = false;
                    this.paperNameError = '';
                    await this.loadPapers();
                    this._selectItem('paper', {name: this.newPaperName.trim()});
                } else {
                    this.paperNameError = data.error || '생성 실패';
                }
            } catch(e) {
                console.error(e);
                this.paperNameError = '네트워크 오류';
            }
        },

        // ==================== 아카이브 관련 ====================

        async openArchiveModal(mode) {
            this.archiveMode = mode;
            this.archivedItems = await fetchJSON(`/archived/${mode}`) || [];
            this.showArchiveModal = true;
            this.sidebarOpen = false;
        },

        async unarchiveItem(itemName) {
            const data = await fetchJSON(`/archive/${this.archiveMode}/${encodeURIComponent(itemName)}`, { method: 'POST' });
            if (data?.success) {
                this.archivedItems = this.archivedItems.filter(i => i !== itemName);
                const cfg = MODE_CONFIG[this.archiveMode];
                this[cfg.loadListMethod]();
                if (this.archivedItems.length === 0) this.showArchiveModal = false;
            }
        },

        // ==================== 명령어 관련 ====================

        async loadCommands() {
            this.commands = await fetchJSON('/commands') || [];
        },

        insertCommand(cmd) {
            const placeholder = `{{cmd:${cmd.name}}}`;
            this.message += (this.message && !this.message.endsWith(' ') ? ' ' : '') + placeholder;
            this.attachPanelOpen = false;
            this.attachPanelView = 'main';
            this.$nextTick(() => {
                const ta = this.$refs.messageTextarea;
                if (ta) {
                    this.autoResize(ta);
                    ta.focus();
                    const len = ta.value.length;
                    ta.setSelectionRange(len, len);
                }
            });
        },

        openNewCommandModal() {
            this.commandModalMode = 'create';
            this.editingCommand = null;
            this.newCommandName = '';
            this.newCommandContent = '';
            this.showCommandModal = true;
        },

        openEditCommandModal(cmd) {
            this.commandModalMode = 'edit';
            this.editingCommand = cmd;
            this.newCommandName = cmd.name;
            this.newCommandContent = cmd.content;
            this.showCommandModal = true;
        },

        async saveCommand() {
            if (!this.newCommandName.trim() || !this.newCommandContent.trim()) { alert('이름과 내용을 모두 입력해주세요'); return; }
            const formData = new FormData();
            formData.append('name', this.newCommandName.trim());
            formData.append('content', this.newCommandContent.trim());
            const url = this.commandModalMode === 'create' ? '/command' : '/command/' + this.editingCommand.id;
            const method = this.commandModalMode === 'create' ? 'POST' : 'PUT';
            const data = await fetchJSON(url, { method, body: formData });
            if (data?.error) { alert(data.error); return; }
            this.showCommandModal = false;
            this.loadCommands();
        },

        async deleteCommand(cmd, e) {
            e.stopPropagation();
            if (!confirm(`명령어 [${cmd.name}]을(를) 삭제하시겠습니까?`)) return;
            const data = await fetchJSON('/command/' + cmd.id, { method: 'DELETE' });
            if (data?.error) { alert(data.error); return; }
            this.loadCommands();
        },

        // ==================== 모델 선택 ====================

        async loadSelectedModel() {
            const data = await fetchJSON('/settings/model');
            if (data?.model) this.selectedModel = data.model;
        },

        setModel(model) {
            this.selectedModel = model;
            fetch(safeUrl('/settings/model'), {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            }).catch(e => console.error('모델 설정 저장 실패:', e));
        },

        // ==================== MCP 도구 ====================

        async loadMcpTools() {
            const requestedMode = this.mode;
            const [tools, enabledNames] = await Promise.all([fetchJSON(`/mcp/tools?mode=${requestedMode}`), fetchJSON('/mcp/settings')]);
            if (!tools || this.mode !== requestedMode) return;
            const enabledSet = new Set(enabledNames || []);
            this.mcpTools = tools.map(t => ({ ...t, enabled: enabledSet.has(t.name) }));
            this.updateMcpServers();
        },

        saveMcpSettings() {
            fetch('/mcp/settings', {
                method: 'PUT', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this.mcpTools.filter(t => t.enabled).map(t => t.name))
            }).catch(e => console.error('MCP 설정 저장 실패:', e));
        },

        updateMcpServers() {
            const servers = {};
            this.mcpTools.forEach(t => {
                if (!servers[t.serverName]) servers[t.serverName] = { name: t.serverName, label: t.serverName, total: 0, enabled: 0 };
                servers[t.serverName].total++;
                if (t.enabled) servers[t.serverName].enabled++;
            });
            this.mcpServers = Object.values(servers);
        },

        selectMcpServer(serverName) { this.selectedMcpServer = { name: serverName }; this.attachPanelView = 'tools-detail'; },
        toggleMcpTool(tool) { tool.enabled = !tool.enabled; this.updateMcpServers(); this.saveMcpSettings(); },
        toggleAllMcpTools(serverName) {
            const serverTools = this.mcpTools.filter(t => t.serverName === serverName);
            const allEnabled = serverTools.every(t => t.enabled);
            serverTools.forEach(t => t.enabled = !allEnabled);
            this.updateMcpServers();
            this.saveMcpSettings();
        },
        getEnabledMcpToolNames() { return this.mcpTools.filter(t => t.enabled).map(t => t.name); },

        async openManageMcpServers() {
            try {
                const servers = await fetchJSON('/mcp/servers');
                this.allMcpServers = servers || [];
            } catch (e) { console.error('MCP 서버 목록 로드 실패:', e); }
            this.attachPanelView = 'manage-mcp-servers';
        },

        openAddMcpServer() {
            this.newMcpServer = { key: '', name: '', type: 'sse', url: '', command: '', argsText: '', modes: ['chat', 'code', 'paper'] };
            this.mcpServerEditMode = 'add';
            this.attachPanelView = 'edit-mcp-server';
        },

        async openEditMcpServer(serverKey) {
            try {
                const servers = await fetchJSON('/mcp/servers');
                const srv = servers?.find(s => s.key === serverKey);
                if (!srv) return;
                this.newMcpServer = {
                    key: srv.key, name: srv.name, type: srv.type,
                    url: srv.url || '', command: srv.command || '',
                    argsText: (srv.args || []).join('\n'),
                    modes: srv.modes || ['chat', 'code', 'paper']
                };
                this.mcpServerEditMode = 'edit';
                this.attachPanelView = 'edit-mcp-server';
            } catch (e) { console.error('MCP 서버 정보 로드 실패:', e); }
        },

        async submitMcpServer() {
            const s = this.newMcpServer;
            if (!s.name.trim()) return;
            this.mcpServerAdding = true;
            try {
                const body = { name: s.name.trim(), type: s.type, modes: s.modes };
                if (s.type === 'sse') {
                    body.url = s.url.trim();
                } else {
                    body.command = s.command.trim();
                    body.args = s.argsText.split('\n').map(a => a.trim()).filter(Boolean);
                }
                const isEdit = this.mcpServerEditMode === 'edit';
                const url = isEdit ? `/mcp/servers/${encodeURIComponent(s.key)}` : '/mcp/servers';
                const method = isEdit ? 'PUT' : 'POST';
                const resp = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                const data = await resp.json();
                if (data.success) {
                    await this.loadMcpTools();
                    this.allMcpServers = await fetchJSON('/mcp/servers') || [];
                    this.attachPanelView = 'manage-mcp-servers';
                } else {
                    alert(data.error || (isEdit ? '서버 수정 실패' : '서버 추가 실패'));
                }
            } catch (e) {
                console.error('MCP 서버 저장 실패:', e);
                alert('서버 저장 중 오류가 발생했습니다.');
            } finally {
                this.mcpServerAdding = false;
            }
        },

        async removeMcpServer(serverKey, serverName) {
            if (!confirm(`'${serverName || serverKey}' 서버를 삭제하시겠습니까?`)) return;
            try {
                const resp = await fetch(`/mcp/servers/${encodeURIComponent(serverKey)}`, { method: 'DELETE' });
                const data = await resp.json();
                if (data.success) {
                    await this.loadMcpTools();
                    this.allMcpServers = await fetchJSON('/mcp/servers') || [];
                    this.attachPanelView = 'manage-mcp-servers';
                } else {
                    alert(data.error || '서버 삭제 실패');
                }
            } catch (e) {
                console.error('MCP 서버 삭제 실패:', e);
            }
        },

        // ==================== 공통 ====================

        handleVisibilityChange() {
            if (document.visibilityState === 'visible') {
                if (this.mode === 'chat') this.checkActiveChatResponses();
                else this._checkActiveResponses(this.mode);

                if (this.wakeWordEnabled && !this.voiceRecording && !this.wakeWordListening) {
                    setTimeout(() => this.startWakeWordListening(), 500);
                }
            }
        },

        closeFileModal() {
            this.showFileModal = false;
            this.fileModalEditing = false;
            this.fileModalEditContent = '';
            this.fileModalSaving = false;
            if (this.fileModalMediaUrl) { URL.revokeObjectURL(this.fileModalMediaUrl); this.fileModalMediaUrl = ''; }
            window._pdfDoc = null;
        },
        startFileEdit() {
            this.fileModalEditContent = this.fileModalContent;
            this.fileModalEditing = true;
        },
        cancelFileEdit() {
            this.fileModalEditing = false;
            this.fileModalEditContent = '';
        },
        async saveFileEdit() {
            const mode = this.fileModalMode;
            if (!mode) return;
            const cfg = MODE_CONFIG[mode];
            if (!cfg) return;
            this.fileModalSaving = true;
            try {
                const formData = new FormData();
                formData.append(cfg.paramName, this[cfg.currentKey]);
                formData.append('path', this.fileModalPath);
                formData.append('content', this.fileModalEditContent);
                const resp = await fetch(safeUrl(cfg.endpoints.fileWrite), { method: 'POST', body: formData });
                const data = await resp.json();
                if (data.success) {
                    this.fileModalContent = this.fileModalEditContent;
                    this.fileModalEditing = false;
                    // 코드 하이라이팅 재생성
                    const ext = this.fileModalPath.split('.').pop().toLowerCase();
                    if (ext !== 'md' && ext !== 'markdown') {
                        const content = this.fileModalContent;
                        const lang = LANG_MAP[ext];
                        const lines = content.split('\n');
                        if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
                        const pad = String(lines.length).length;
                        const toRow = (code, i) => `<tr><td class="line-num" data-line="${i + 1}">${String(i + 1).padStart(pad)}</td><td class="line-code">${code || ' '}</td></tr>`;
                        this.fileModalHighlighted = '';
                        if (lang && typeof hljs !== 'undefined') {
                            try {
                                const hLines = hljs.highlight(content, { language: lang }).value.split('\n');
                                if (hLines.length > 1 && hLines[hLines.length - 1] === '') hLines.pop();
                                this.fileModalHighlighted = hLines.map(toRow).join('');
                            } catch(e) {}
                        }
                        if (!this.fileModalHighlighted) {
                            this.fileModalHighlighted = lines.map((line, i) => toRow(ToolCards.escapeHtml(line), i)).join('');
                        }
                    }
                } else {
                    alert(data.error || '저장에 실패했습니다.');
                }
            } catch(e) {
                alert('저장 중 오류가 발생했습니다: ' + e.message);
            }
            this.fileModalSaving = false;
        },
        currentContextPercent() {
            return this.mode === 'chat' ? this.chatContextPercent : (this.mode === 'paper' ? this.paperContextPercent : this.contextPercent);
        },
        init() {
            this.loadProjects();
            this.loadPapers();
            this.loadPaperTemplates();
            this.loadChatSessions();
            this.loadCommands();
            this.loadMcpTools();
            this.loadSelectedModel();
            document.addEventListener('visibilitychange', () => this.handleVisibilityChange());
            this._initVoice();
        }
    };

    return Object.assign(data, voiceMixin(), terminalMixin());
};
