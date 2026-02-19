FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    texlive-latex-base \
    texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*

# Node.js (for Claude CLI + MCP stdio server)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Chrome (headless, for DevTools MCP)
RUN curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb \
    && apt-get update && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm -f /tmp/chrome.deb && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user (Claude CLI disallows --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash ddoli

# Working directory
WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY . .

# Create data directories (owned by ddoli user)
RUN mkdir -p /home/ddoli/chat /home/ddoli/workspace /home/ddoli/papers /home/ddoli/paper-templates /home/ddoli/.claude /tmp/ddoli-attachments uploads \
    && chown -R ddoli:ddoli /home/ddoli /tmp/ddoli-attachments /app

# Store default paper templates separately (prevent overwrite on volume mount)
COPY paper-templates/ /opt/default-templates/
RUN chown -R ddoli:ddoli /opt/default-templates

USER ddoli

EXPOSE 8000

# On start: copy default templates if empty -> start Chrome headless -> run app
CMD ["bash", "-c", "cp -rn /opt/default-templates/* /home/ddoli/paper-templates/ 2>/dev/null; google-chrome --headless --disable-gpu --no-sandbox --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 & python main.py"]
