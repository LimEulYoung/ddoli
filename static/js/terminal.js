/**
 * Ddoli - Terminal (xterm.js + WebSocket)
 * Alpine.js mixin: terminalMixin()
 */
function terminalMixin() {
    return {
        // Terminal state
        showTerminalModal: false,
        terminalWs: null,
        terminalTerm: null,
        terminalConnected: false,
        terminalFitAddon: null,

        openTerminal() {
            this.showTerminalModal = true;
            this.sidebarOpen = false;
            this.$nextTick(() => this._initTerminal());
        },

        async _initTerminal() {
            if (this.terminalTerm && this.terminalConnected) {
                if (this.terminalFitAddon) this.terminalFitAddon.fit();
                return;
            }

            const container = document.getElementById('terminal-container');
            if (!container) return;

            if (!window.Terminal) {
                await this._loadScript('https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js');
                await this._loadScript('https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js');
                await this._loadCSS('https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css');
            }

            if (this.terminalTerm) {
                this.terminalTerm.dispose();
            }

            const term = new window.Terminal({
                cursorBlink: true,
                fontSize: 14,
                fontFamily: "'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace",
                theme: {
                    background: '#1E1E1E',
                    foreground: '#D4D4D4',
                    cursor: '#D4D4D4',
                    selectionBackground: '#264F78',
                    black: '#000000',
                    red: '#CD3131',
                    green: '#0DBC79',
                    yellow: '#E5E510',
                    blue: '#2472C8',
                    magenta: '#BC3FBC',
                    cyan: '#11A8CD',
                    white: '#E5E5E5',
                },
                scrollback: 5000,
                allowProposedApi: true,
            });

            const fitAddon = new window.FitAddon.FitAddon();
            term.loadAddon(fitAddon);
            term.open(container);
            fitAddon.fit();

            this.terminalTerm = term;
            this.terminalFitAddon = fitAddon;

            this._connectTerminalWs(term, fitAddon);

            this._terminalResizeObserver = new ResizeObserver(() => {
                if (this.terminalFitAddon && this.showTerminalModal) {
                    this.terminalFitAddon.fit();
                }
            });
            this._terminalResizeObserver.observe(container);
        },

        _connectTerminalWs(term, fitAddon) {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${location.host}/ws/terminal`);
            this.terminalWs = ws;

            ws.binaryType = 'arraybuffer';

            ws.onopen = () => {
                this.terminalConnected = true;
                term.clear();
            };

            ws.onmessage = (event) => {
                if (event.data instanceof ArrayBuffer) {
                    term.write(new Uint8Array(event.data));
                } else {
                    try {
                        const data = JSON.parse(event.data);
                        if (data.type === 'error') {
                            term.writeln('\r\n\x1b[31mError: ' + data.message + '\x1b[0m');
                        }
                    } catch(e) {
                        term.write(event.data);
                    }
                }
            };

            ws.onclose = () => {
                this.terminalConnected = false;
                if (this.showTerminalModal) {
                    term.writeln('\r\n\x1b[33mConnection closed.\x1b[0m');
                }
            };

            ws.onerror = () => {
                this.terminalConnected = false;
                term.writeln('\r\n\x1b[31mConnection error.\x1b[0m');
            };

            term.onData(data => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(data);
                }
            });

            term.onResize(size => {
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'resize',
                        cols: size.cols,
                        rows: size.rows
                    }));
                }
            });

            setTimeout(() => {
                fitAddon.fit();
                if (ws.readyState === WebSocket.OPEN) {
                    ws.send(JSON.stringify({
                        type: 'resize',
                        cols: term.cols,
                        rows: term.rows
                    }));
                }
            }, 200);
        },

        closeTerminal() {
            if (this.terminalWs) {
                this.terminalWs.close();
                this.terminalWs = null;
            }
            if (this._terminalResizeObserver) {
                this._terminalResizeObserver.disconnect();
                this._terminalResizeObserver = null;
            }
            if (this.terminalTerm) {
                this.terminalTerm.dispose();
                this.terminalTerm = null;
            }
            this.terminalFitAddon = null;
            this.terminalConnected = false;
            this.showTerminalModal = false;
        },

        reconnectTerminal() {
            if (!this.terminalTerm) return;
            if (this.terminalWs) {
                this.terminalWs.close();
                this.terminalWs = null;
            }
            this.terminalTerm.clear();
            this.terminalTerm.writeln('\x1b[33mReconnecting...\x1b[0m');
            this._connectTerminalWs(this.terminalTerm, this.terminalFitAddon);
        },

        _loadScript(src) {
            return new Promise((resolve, reject) => {
                if (document.querySelector(`script[src="${src}"]`)) { resolve(); return; }
                const s = document.createElement('script');
                s.src = src;
                s.onload = resolve;
                s.onerror = reject;
                document.head.appendChild(s);
            });
        },

        _loadCSS(href) {
            return new Promise((resolve) => {
                if (document.querySelector(`link[href="${href}"]`)) { resolve(); return; }
                const l = document.createElement('link');
                l.rel = 'stylesheet';
                l.href = href;
                l.onload = resolve;
                document.head.appendChild(l);
            });
        },
    };
}
