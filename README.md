# Ddoli

Claude Code 기반 AI 어시스턴트 웹 앱. 채팅, 코드 편집, 논문 작성 모드를 지원합니다.

## 요구사항

- Docker & Docker Compose
- Claude 계정 (Max 구독 권장)

## 설치 및 실행

### 1. 클론

```bash
git clone https://github.com/LimEulYoung/ddoli.git
cd ddoli
```

### 2. 빌드 및 실행

```bash
docker compose up --build -d
```

첫 빌드 시 LaTeX, Chrome, Node.js 등을 설치하므로 시간이 걸릴 수 있습니다.

### 3. Claude CLI 로그인

```bash
docker exec -it ddoli-app-1 bash
claude login
```

브라우저 인증 URL이 표시되면 복사해서 로컬 브라우저에서 열어 인증합니다.

### 4. 접속

브라우저에서 `http://<서버IP>:8000` 으로 접속합니다.

## 포함된 구성요소

| 구성요소 | 용도 |
|---------|------|
| Python 3.12 + FastAPI | 웹 서버 |
| PostgreSQL 16 | 세션/메시지 저장 |
| Claude CLI | AI 응답 생성 |
| LaTeX (texlive) | 논문 PDF 빌드 |
| Chrome Headless | DevTools MCP 연결 |
| Node.js 22 | Claude CLI 및 MCP stdio 서버 |

## 주요 명령어

```bash
# 시작
docker compose up -d

# 중지
docker compose down

# 로그 확인
docker compose logs -f app

# 재빌드 (코드 변경 후)
docker compose up --build -d

# 전체 초기화 (DB, 볼륨 포함 삭제)
docker compose down -v
```

## 볼륨

| 볼륨 | 경로 | 용도 |
|------|------|------|
| ddoli-claude-auth | /home/ddoli/.claude | Claude CLI 인증 정보 |
| ddoli-chat | /home/ddoli/chat | 채팅 세션 데이터 |
| ddoli-workspace | /home/ddoli/workspace | 코드 프로젝트 |
| ddoli-papers | /home/ddoli/papers | 논문 프로젝트 |
| ddoli-templates | /home/ddoli/paper-templates | 논문 템플릿 |
| ddoli-pgdata | PostgreSQL 데이터 | DB 영속 저장 |

## 포트

- `8000` — 웹 UI (필수)
- `9222` — Chrome DevTools (컨테이너 내부 전용)
