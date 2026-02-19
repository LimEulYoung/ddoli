/**
 * Ddoli - Voice Recognition + Wake Word
 * Alpine.js mixin: voiceMixin()
 */
function voiceMixin() {
    return {
        // Voice recognition state
        voiceRecording: false,
        voiceText: '',
        voiceInterim: '',
        recognition: null,
        silenceTimer: null,
        submitKeywordDetected: false,

        // Wake Word state
        wakeWordEnabled: false,
        wakeWordListening: false,
        wakeWordRecognition: null,
        wakeWordRestartTimer: null,

        // ==================== Voice Recognition ====================

        startVoiceInput() {
            if (this.voiceRecording) return;
            const streamingKey = MODE_CONFIG[this.mode]?.streamingKey;
            if (streamingKey && this[streamingKey]) return;

            this.stopWakeWordListening();
            if (navigator.vibrate) navigator.vibrate(50);

            this.voiceRecording = true;
            this.voiceText = '';
            this.voiceInterim = '';
            this.submitKeywordDetected = false;
            this.startSpeechRecognition();
        },

        _createRecognition(continuous) {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) return null;
            const r = new SR();
            r.continuous = continuous;
            r.interimResults = true;
            r.lang = 'en-US';
            return r;
        },

        _restartRecognition(rec, isActiveCheck, onFail, delay = 300) {
            if (!isActiveCheck()) return;
            try { rec.start(); this._recognitionRetryCount = 0; }
            catch(e) { setTimeout(() => { if (isActiveCheck()) { try { rec.start(); this._recognitionRetryCount = 0; } catch(e2) { if (onFail) onFail(); } } }, delay); }
        },

        startSpeechRecognition() {
            this.recognition = this._createRecognition(false);
            if (!this.recognition) { alert('This browser does not support voice recognition'); this.voiceRecording = false; return; }
            this.recognition.onresult = (event) => {
                const result = event.results[event.results.length - 1];
                const transcript = result[0].transcript;
                if (result.isFinal) { this.voiceText += (this.voiceText ? ' ' : '') + transcript; this.voiceInterim = ''; }
                else { this.voiceInterim = transcript; }
                this.$nextTick(() => this.scrollVoiceTextarea());
                this.checkAutoSubmit(this.voiceText + ' ' + this.voiceInterim);
            };
            this._recognitionRetryCount = 0;
            this.recognition.onerror = (event) => {
                const e = event.error;
                if (e === 'not-allowed') { alert('Microphone permission is required. Please allow microphone access in browser settings.'); this.cancelVoiceRecording(); }
                else if (e === 'network') { this._recognitionRetryCount++; if (this._recognitionRetryCount >= 3) { alert('Cannot connect to voice recognition server.'); this.cancelVoiceRecording(); } }
                else if (e === 'service-not-available') { alert('Voice recognition service is unavailable.'); this.cancelVoiceRecording(); }
            };
            this.recognition.onend = () => this._restartRecognition(this.recognition, () => this.voiceRecording, () => this.cancelVoiceRecording());
            this.recognition.start();
        },

        resetSilenceTimer() {
            clearTimeout(this.silenceTimer);
            if (this.submitKeywordDetected) this.silenceTimer = setTimeout(() => this.autoSubmitVoice(), 3000);
        },

        checkAutoSubmit(text) {
            const submitKW = ['go ahead'], cancelKW = ['scratch that'];
            const t = text.toLowerCase().trim();
            if (cancelKW.some(k => t.includes(k))) { this.cancelVoiceRecording(); return; }
            if (submitKW.some(k => t.includes(k))) {
                if (!this.submitKeywordDetected) {
                    this.submitKeywordDetected = true;
                    let clean = this.voiceText;
                    for (const kw of submitKW) clean = clean.replace(new RegExp(kw + '[.!]?', 'gi'), '').trim();
                    this.voiceText = clean;
                    this.resetSilenceTimer();
                }
                return;
            }
            if (this.submitKeywordDetected) { clearTimeout(this.silenceTimer); this.submitKeywordDetected = false; }
        },

        _stopVoice() {
            clearTimeout(this.silenceTimer);
            if (this.recognition) { this.recognition.abort(); this.recognition = null; }
            const text = this.voiceText.trim();
            this.voiceRecording = false; this.voiceText = ''; this.voiceInterim = ''; this.submitKeywordDetected = false;
            if (this.wakeWordEnabled) setTimeout(() => this.startWakeWordListening(), 500);
            return text;
        },

        autoSubmitVoice() { const text = this._stopVoice(); if (text) { this.message = text; this.$nextTick(() => this.submitMessage()); } },
        cancelVoiceRecording() { this._stopVoice(); },

        // ==================== Wake Word ====================

        toggleWakeWord() {
            this.wakeWordEnabled = !this.wakeWordEnabled;
            localStorage.setItem('wakeWordEnabled', this.wakeWordEnabled ? '1' : '0');
            this.wakeWordEnabled ? this.startWakeWordListening() : this.stopWakeWordListening();
        },

        startWakeWordListening() {
            if (this.voiceRecording || this.wakeWordListening) return;
            this.wakeWordRecognition = this._createRecognition(true);
            if (!this.wakeWordRecognition) return;

            this.wakeWordRecognition.onresult = (event) => {
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const transcript = event.results[i][0].transcript;
                    if (this.chatStreaming || this.codeStreaming || this.paperStreaming) {
                        if (['scratch that'].some(k => transcript.toLowerCase().includes(k))) {
                            if (navigator.vibrate) navigator.vibrate([50, 30, 50]);
                            this.stopStreaming(this.mode); return;
                        }
                    }
                    if (transcript.toLowerCase().includes('hey buddy')) {
                        this.stopWakeWordListening();
                        if (navigator.vibrate) navigator.vibrate([100, 50, 100]);
                        this.startVoiceInput(); return;
                    }
                }
            };
            this.wakeWordRecognition.onerror = (event) => {
                if (event.error === 'not-allowed') { this.wakeWordEnabled = false; localStorage.setItem('wakeWordEnabled', '0'); this.wakeWordListening = false; }
            };
            this.wakeWordRecognition.onend = () => {
                this.wakeWordListening = false;
                if (this.wakeWordEnabled && !this.voiceRecording) {
                    this.wakeWordRestartTimer = setTimeout(() => { if (this.wakeWordEnabled && !this.voiceRecording && !this.wakeWordListening) this.startWakeWordListening(); }, 100);
                }
            };
            try { this.wakeWordRecognition.start(); this.wakeWordListening = true; }
            catch(e) { this.wakeWordListening = false; if (this.wakeWordEnabled) setTimeout(() => this.startWakeWordListening(), 500); }
        },

        stopWakeWordListening() {
            clearTimeout(this.wakeWordRestartTimer); this.wakeWordRestartTimer = null;
            if (this.wakeWordRecognition) { try { this.wakeWordRecognition.abort(); } catch(e) {} this.wakeWordRecognition = null; }
            this.wakeWordListening = false;
        },

        scrollVoiceTextarea() {
            const ta = this.$refs.voiceTextarea;
            if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; ta.scrollTop = ta.scrollHeight; }
        },

        _initVoice() {
            if (localStorage.getItem('wakeWordEnabled') === '1') {
                this.wakeWordEnabled = true;
                setTimeout(() => this.startWakeWordListening(), 1000);
            }
        },
    };
}
