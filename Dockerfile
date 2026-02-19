FROM python:3.12-slim

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-lang-korean \
    && rm -rf /var/lib/apt/lists/*

# Node.js (Claude CLI + MCP stdio 서버용)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Chrome (headless, DevTools MCP용)
RUN curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*

# Claude CLI 설치
RUN npm install -g @anthropic-ai/claude-code

# 일반 유저 생성 (Claude CLI가 root에서 --dangerously-skip-permissions 불가)
RUN useradd -m -s /bin/bash ddoli

# 작업 디렉토리
WORKDIR /app

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스
COPY . .

# 데이터 디렉토리 생성 (ddoli 유저 소유)
RUN mkdir -p /home/ddoli/chat /home/ddoli/workspace /home/ddoli/papers /home/ddoli/paper-templates /home/ddoli/.claude /tmp/ddoli-attachments uploads \
    && chown -R ddoli:ddoli /home/ddoli /tmp/ddoli-attachments /app

# 기본 논문 템플릿을 별도 경로에 보관 (볼륨 마운트 시 덮어쓰기 방지)
COPY paper-templates/ /opt/default-templates/
RUN chown -R ddoli:ddoli /opt/default-templates

USER ddoli

EXPOSE 8000

# 시작 시: 템플릿이 비어있으면 기본 템플릿 복사 → Chrome headless 시작 → 앱 실행
CMD ["bash", "-c", "cp -rn /opt/default-templates/* /home/ddoli/paper-templates/ 2>/dev/null; google-chrome --headless --disable-gpu --no-sandbox --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 & python main.py"]
