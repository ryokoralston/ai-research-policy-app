# AI Policy Research Assistant

A web application for AI policy research, powered by Claude (Anthropic) and Tavily. Research, analyze, and generate reports — all in one place.

## Screenshots

| Dashboard | Research Agent |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Research](docs/screenshots/research.png) |

| Document Library | Multi-Persona Debate |
|---|---|
| ![Library](docs/screenshots/library.png) | ![Debate](docs/screenshots/debate.png) |

## Features

- **Research** — Automatically decomposes queries into sub-searches, runs parallel web searches via Tavily, and synthesizes results with Claude (streaming output)
- **Document Library** — Upload PDFs, web pages, and YouTube transcripts to build a searchable knowledge base backed by ChromaDB (RAG). The Ask Documents chat cites sources with sentence-level numbered citations (e.g. `[1]`) — hover any citation for the source title, page, and excerpt
- **Analysis** — Generate in-depth analysis grounded in your document library, streamed in real time. Each analysis includes a citation confidence score that flags any claims not actually supported by the source material
- **AI Policy Debate** — Simulate a multi-persona debate (e.g. pro-regulation vs. tech-optimist) on any AI policy topic. A Consensus Meter summarizes where participants actually agreed or diverged on each key claim
- **Reports** — Generate PDF reports from three templates: Congressional Brief, Policy Memo, and Risk Assessment. Reports also include a citation confidence score grounding check
- **Daily Digest** — Receive a daily email summarizing the latest developments on topics you define, including relevant federal rules and notices from the Federal Register (optional)
- **Settings** — Configure models (claude-opus-4-6 / claude-haiku) and API keys from the browser

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 · TypeScript · Tailwind CSS |
| Backend | FastAPI · Python 3.12 |
| AI | Anthropic Claude API (claude-opus-4-6 / claude-haiku-4-5) |
| Web Search | Tavily API |
| Vector DB | ChromaDB + sentence-transformers |
| Database | SQLite (SQLAlchemy) |
| Scheduler | APScheduler |

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- [Anthropic API key](https://console.anthropic.com)
- [Tavily API key](https://app.tavily.com) (free tier: 1,000 requests/month)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/ryokoralston/ai-research-policy-app.git
cd ai-research-policy-app

# 2. Set up environment variables
cp backend/.env.example backend/.env
# Open backend/.env and add your API keys

# 3. Install backend dependencies
cd backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..

# 4. Install frontend dependencies
cd frontend
npm install
cd ..
```

### Running

```bash
./start.sh
```

- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs

Or start each server individually:

```bash
# Backend
cd backend && source venv/bin/activate
uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm run dev
```

To stop all servers:

```bash
./stop.sh
```

## Environment Variables

Copy `backend/.env.example` to `backend/.env` and fill in the values below.

| Variable | Description | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key | ✅ |
| `TAVILY_API_KEY` | Tavily search API key | ✅ |
| `CLAUDE_MODEL` | Main model (default: `claude-opus-4-6`) | |
| `CLAUDE_FAST_MODEL` | Fast model (default: `claude-haiku-4-5-20251001`) | |
| `CORS_ORIGINS` | Frontend URL (default: `http://localhost:3000`) | |
| `DIGEST_EMAIL_TO` | Recipient address for daily digest | |
| `DIGEST_EMAIL_FROM` | Gmail address to send from | |
| `DIGEST_SMTP_PASSWORD` | Gmail app password | |
| `DIGEST_TOPICS` | Comma-separated topics to monitor | |

> To generate a Gmail app password, go to **Google Account → Security → 2-Step Verification → App passwords**.

## Secret Key Rotation (for ownership transfer)

`SECRET_ENCRYPTION_KEY` (see `services/secret_crypto.py`) encrypts stored API
keys and the digest SMTP password at rest, and signs login tokens. It can
**never** be swapped in place — flipping the env var without re-encrypting
first leaves every existing secret permanently undecryptable (see the
warning in `.env.example`).

When transferring ownership of this app to a new party, don't hand over the
existing key indefinitely — generate a new one for the incoming owner and
rotate to it:

1. Find the current key. If `SECRET_ENCRYPTION_KEY` is set as an env var, it's
   right there. If it isn't, the app auto-generated one on first run and
   persisted it to `<data dir>/.secret_key` (on Render: open a Shell on the
   service and `cat /data/.secret_key`).
2. Generate a new key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
3. Dry-run the rotation first (writes nothing):
   ```
   cd backend && ./venv/bin/python -m scripts.rotate_secret_key --old-key <current> --new-key <new>
   ```
4. Review the output, then apply it:
   ```
   ./venv/bin/python -m scripts.rotate_secret_key --old-key <current> --new-key <new> --apply
   ```
5. Set `SECRET_ENCRYPTION_KEY` to the new value wherever the app runs (e.g.
   Render's Environment tab) and restart it. Everyone currently logged in
   will need to log in again — expected, not a bug.
6. Hand the new key to the incoming owner through a secure channel (a shared
   password manager entry, not chat/email in plaintext), and set it as an
   explicit env var rather than leaving it to auto-generate onto disk —
   an env var survives infrastructure changes (redeploys, disk swaps) more
   reliably than a file on a persistent disk that might not always be as
   persistent as expected.

## License

MIT
