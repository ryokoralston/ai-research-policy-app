# Claude Code Instructions — AI Policy Research App

## Commit Rules

**Always run all affected tests before committing.**
- If you edit an eval file, run that eval and confirm it exits successfully before `git commit`.
- If you edit a service file (e.g. `research_agent.py`, `risk_analyzer.py`), run the related eval.
- Never commit code that has not been executed at least once in this session.

## Project Preferences

- No LangChain — use Anthropic SDK directly.
- Streaming via SSE (Server-Sent Events).
- App UI must always be in English — never add Japanese text to the app.
- Japanese responses preferred when user writes in Japanese.

## Stack Quick Reference

- **Backend**: Python + FastAPI — `cd backend && source venv/bin/activate && uvicorn main:app --reload --port 8000`
- **Frontend**: Next.js 14 + TypeScript + Tailwind — `cd frontend && npm run dev` (port 3000)
- **Models**: claude-opus-4-6 (synthesis/reports), claude-haiku-3-5 (per-source summaries), claude-haiku-4-5 (dataset generation)
- **Evals**: `python -m evals.eval_research_queries` / `eval_synthesis_quality` / `eval_prompt_versions`
