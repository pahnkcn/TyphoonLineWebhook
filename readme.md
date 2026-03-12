# TyphoonLineWebhook

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

TyphoonLineWebhook powers `ใจดี`, a Thai-language LINE chatbot for supportive substance-use counseling. The current project combines LINE webhook handling, xAI Grok responses, optional Multi-AI consensus, Redis-backed session and progress tracking, MySQL persistence, a research dashboard, and a local knowledge base for retrieval-augmented responses.

## 🌟 Current Features

- **LINE chatbot for Thai users**: Accepts LINE webhook events and replies with supportive, non-judgmental guidance.
- **Risk assessment and crisis handling**: Detects risk keywords, classifies risk level, and surfaces emergency resources.
- **Registration and verification flow**: Supports `/register`, `/verify <code>`, and a form webhook for importing verification codes plus form context.
- **Session and progress tracking**: Stores chat history in MySQL and session/progress state in Redis.
- **Follow-ups and proactive check-ins**: APScheduler runs follow-up jobs and inactivity-based check-ins in the background.
- **Optional Multi-AI consensus**: Can compare responses from multiple providers and return the best-scored answer.
- **RAG knowledge base**: Loads documents from `knowledge_docs/` and supports upload/reindex APIs for `.pdf`, `.docx`, `.txt`, and `.md` files.
- **Research dashboard**: Includes `/dashboard` plus analytics, user history, Multi-AI stats, knowledge stats, and anonymized export APIs.
- **Privacy and data deletion tools**: Includes `/privacy`, `/context`, `/deletedata`, and session reset support.
- **Operational safeguards**: Rate limiting, health checks, rotating logs, Redis resilience, and optional dashboard API authentication.

## 🧱 Architecture Overview

```
LINE Messaging API
        │
        ▼
Flask app (`app/app_main.py`)
        ├── Webhook validation and command handling
        ├── Risk assessment and crisis workflows
        ├── xAI Grok chat or optional Multi-AI consensus
        ├── MySQL conversation storage
        ├── Redis session / progress / follow-up state
        ├── RAG knowledge base (`knowledge_docs/`)
        └── Dashboard + export APIs (`app/routes/dashboard.py`)
```

## 📁 Project Structure

```
TyphoonLineWebhook/
├── app/
│   ├── app_main.py                # Main Flask app and LINE webhook handling
│   ├── config.py                  # Environment loading and prompt/config settings
│   ├── chat_history_db.py         # Conversation/history data access
│   ├── database_*.py              # DB init, monitoring, optimization helpers
│   ├── risk_assessment.py         # Risk detection and progress reporting
│   ├── session_manager.py         # Session/context lifecycle
│   ├── llm/                       # Grok client, provider registry, Multi-AI consensus
│   ├── rag/                       # Knowledge base, chunking, embeddings, vector store
│   ├── routes/dashboard.py        # Dashboard and analytics APIs
│   ├── services/                  # Admin alerting, data management, proactive check-ins, Redis client
│   └── monitoring/                # Token usage tracking
├── knowledge_docs/                # Source documents for RAG ingestion
├── migrations/                    # Alembic migrations
├── templates/dashboard.html       # Research dashboard UI
├── tests/                         # Pytest suite
├── scripts/install.bat            # Windows local setup helper
├── scripts/install.sh             # Ubuntu server bootstrap script
├── docker-compose.yml             # Local/dev Docker stack
├── docker-compose.override.yml    # Dev-only Adminer service
├── docker-compose.prod.yml        # Production-style Docker stack
├── Dockerfile                     # Container image build
└── wsgi.py                        # Recommended app entry point
```

## 📋 Requirements

- **Python**: 3.11+
- **MySQL**: 8.0+
- **Redis**: 6+
- **LINE Messaging API** credentials
- **xAI API key** for Grok
- **Optional AI provider keys** for Multi-AI and embedding workflows

## 🚀 Getting Started

### Option 1: Windows local setup

Run the helper script:

```powershell
.\scripts\install.bat
```

The script creates a virtual environment, installs dependencies, creates `.env` from `.env.example` when needed, and offers shortcuts to run the app, Docker Compose, or tests.

### Option 2: Manual local setup

1. Copy `.env.example` to `.env`.
2. Fill in the required secrets.
3. Make sure MySQL and Redis are running.
4. Install dependencies and start the app.

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python wsgi.py
```

Bash:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python wsgi.py
```

The app starts on port `5000` by default.

### Option 3: Docker development

```bash
docker compose up -d --build
```

Local Docker services:

- **App**: `http://localhost:5000`
- **Health check**: `http://localhost:5000/health`
- **Adminer**: `http://localhost:8080` (from `docker-compose.override.yml`)
- **Redis**: `localhost:6379`
- **MySQL**: `localhost:3306`

### Option 4: Production-style Docker

```bash
docker compose -f docker-compose.prod.yml up -d
```

Notes:

- `docker-compose.prod.yml` expects a published image unless you override `DOCKER_IMAGE`.
- The production stack mounts `logs/` and `knowledge_docs/` into the container.
- Set `ENVIRONMENT=production` and replace all placeholder secrets.

### Option 5: Ubuntu server bootstrap

```bash
sudo bash scripts/install.sh
```

This script installs Docker, Nginx, Certbot tooling, prepares the app directory, and creates `.env` from `.env.example` when needed.

## ⚙️ Environment Configuration

The full configuration surface lives in `.env.example`. The most important variables are below.

### Required to start the app

- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_CHANNEL_SECRET`
- `XAI_API_KEY`
- `MYSQL_HOST`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DB`

### Common runtime settings

| Variable | Purpose |
|----------|---------|
| `REDIS_HOST` | Redis hostname |
| `REDIS_PORT` | Redis port |
| `REDIS_DB` | Redis database index |
| `MYSQL_PORT` | MySQL port |
| `ENVIRONMENT` | `development` or `production` |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `PORT` | App port for `wsgi.py` |
| `XAI_MODEL` | xAI model override |

### Feature and security settings

| Variable | Purpose |
|----------|---------|
| `DASHBOARD_API_KEY` | Optional bearer token for dashboard APIs |
| `FORM_WEBHOOK_KEY` | Required secret for `/api/add-verification-code` |
| `ADMIN_ALERT_ENABLED` | Enables admin alerting workflows |
| `LINE_NOTIFY_TOKEN` | LINE Notify token for admin alerts |
| `MULTI_AI_ENABLED` | Enables Multi-AI consensus mode |
| `MULTI_AI_TIMEOUT` | Total Multi-AI time budget |
| `RAG_ENABLED` | Enables retrieval-augmented generation |
| `RAG_MIN_SCORE` | Minimum retrieval score threshold |
| `RAG_CHUNK_SIZE` | Chunk size for RAG ingestion |
| `RAG_CHUNK_OVERLAP` | Overlap between RAG chunks |
| `RAG_FETCH_K` | Number of candidate chunks to fetch |
| `RAG_MAX_CONTEXT_CHARS` | Maximum injected RAG context length |
| `ENABLE_SKIP_VERIFY` | Enables `/skipverify` for testing only |

### Optional AI provider settings

Multi-AI and embedding-related options can be enabled with the keys already listed in `.env.example`, including:

- `OPENAI_API_KEY`, `OPENAI_MODEL`
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
- `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_EMBEDDING_MODEL`
- `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`
- `MOONSHOT_API_KEY`, `MOONSHOT_MODEL`
- `OPENROUTER_API_KEY`, `OPENROUTER_MODELS`, `OPENROUTER_EMBEDDING_MODEL`

## 🔌 Webhook and API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/callback` | LINE Messaging API webhook endpoint |
| `GET` | `/health` | Health check for app, DB, Redis, and xAI config status |
| `POST` | `/api/add-verification-code` | Accepts form webhook submissions with `FORM_WEBHOOK_KEY` |
| `GET` | `/dashboard` | Research dashboard page |
| `GET` | `/api/dashboard/insights` | High-level analytics and risk insights |
| `GET` | `/api/dashboard/users/<user_id>/history` | Detailed user history drill-down |
| `GET` | `/api/dashboard/multi-ai-stats` | Multi-AI usage and scoring stats |
| `GET` | `/api/dashboard/export` | Export anonymized conversation data as JSON or CSV |
| `GET` | `/api/knowledge/stats` | Knowledge base stats |
| `POST` | `/api/knowledge/reindex` | Rebuild the knowledge index |
| `POST` | `/api/knowledge/upload` | Upload knowledge files (`.pdf`, `.docx`, `.txt`, `.md`) |

Dashboard API authentication:

- If `DASHBOARD_API_KEY` is empty, dashboard APIs are open.
- If it is set, send `Authorization: Bearer <your-key>`.
- Query-string `api_key` also works, but the code warns against using it.

Form webhook authentication:

- `/api/add-verification-code` expects a JSON body containing `api_key`, `code`, and `full_form_data`.
- `api_key` must match `FORM_WEBHOOK_KEY`.
- `code` must be a 6-digit numeric verification code.

## 💬 LINE Commands

These commands are currently handled in `app/app_main.py`.

| Command | Purpose |
|---------|---------|
| `/help` | Show the help menu |
| `/register` | Show the registration instructions |
| `/verify <code>` | Verify a 6-digit registration code |
| `/status` | Show conversation and token usage summary |
| `/progress` | Show progress report and next-step guidance |
| `/context` | Show context imported from the registration form |
| `/followup` | Show follow-up status |
| `/tokens` | Show current session token usage |
| `/optimize` | Compress or optimize session context |
| `/privacy` | Show the privacy policy |
| `/deletedata` | Delete the user's stored data |
| `/emergency` | Show urgent care and hotline information |
| `/skipverify` | Dev/test-only bypass when `ENABLE_SKIP_VERIFY=true` |

## 🧠 RAG Knowledge Base

- Source documents live in `knowledge_docs/`.
- Supported document formats are `.pdf`, `.docx`, `.txt`, and `.md`.
- The app can auto-ingest documents on startup when RAG is enabled.
- Dashboard APIs support live stats, reindexing, and document upload.
- RAG configuration is controlled through the `RAG_*` environment variables.

## ⏱️ Background Jobs

The scheduler is initialized by `wsgi.py` / `app_main.py` and currently runs:

- **Follow-up processing** every 30 minutes
- **Proactive inactivity check-ins** every 6 hours

## 🧪 Testing

Run the test suite with:

```bash
pytest tests/ -v --tb=short
```

The repository includes tests for configuration validation, risk assessment, RAG behavior, Multi-AI workflows, Redis resilience, token counting, and admin alerting.

## 📝 Logging and Operations

- Logs are written to `logs/`.
- Rotating log handlers cap log files at 5 MB with backups.
- The app uses Waitress for direct serving and exposes `application = app` in `wsgi.py` for WSGI servers.
- Local Docker health checks call `GET /health`.

## 📡 LINE Webhook Setup

1. Create a bot in the [LINE Developers Console](https://developers.line.biz/).
2. Set the webhook URL to:

    ```
    https://your-domain/callback
    ```

3. Enable webhook delivery.
4. Disable conflicting auto-reply features in LINE if you want the bot to handle replies itself.

## 📄 License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## 🙏 Acknowledgements

- [LINE Messaging API](https://developers.line.biz/en/docs/messaging-api/)
- [xAI](https://x.ai)
- [Flask](https://flask.palletsprojects.com/)
- [Waitress](https://docs.pylonsproject.org/projects/waitress/en/stable/)
- [Redis](https://redis.io/)
- [MySQL](https://www.mysql.com/)
