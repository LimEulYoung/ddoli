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

# Claude CLI 설치
RUN npm install -g @anthropic-ai/claude-code

# 작업 디렉토리
WORKDIR /app

# Python 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 소스
COPY . .

# 데이터 디렉토리 생성
RUN mkdir -p /root/chat /root/workspace /root/papers /root/paper-templates /tmp/ddoli-attachments uploads

EXPOSE 8000

CMD ["python", "main.py"]
