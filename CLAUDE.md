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

## Bug Fix Workflow (7 Steps)

When fixing any bug, follow these steps **in order**. Do not start fixing code until Step 3 is complete.
At the end of each step, report what was found and what comes next.
If information is insufficient, ask — do not guess.
Use simple explanations so non-engineers can understand.

1. **現象の整理** — Summarize the problem in plain language. Ask if any information is missing.
2. **再現の確認** — Describe exact steps to reproduce. Identify required conditions (data, environment, sequence).
3. **原因の調査** — Identify root cause. Do not write any fix until this step is complete.
4. **修正** — Fix the code, targeting the root cause identified in Step 3.
5. **テスト・確認** — Verify the fix works and check for side effects. Run relevant evals/tests.
6. **リリース・本番反映** — Commit and push after tests pass (following commit rules above).
7. **振り返り・再発防止** — Note why it happened and what can prevent recurrence.

## Stack Quick Reference

- **Backend**: Python + FastAPI — `cd backend && source venv/bin/activate && uvicorn main:app --reload --port 8000`
- **Frontend**: Next.js 14 + TypeScript + Tailwind — `cd frontend && npm run dev` (port 3000)
- **Models**: claude-opus-4-6 (synthesis/reports), claude-haiku-3-5 (per-source summaries), claude-haiku-4-5 (dataset generation)
- **Evals**: `python -m evals.eval_research_queries` / `eval_synthesis_quality` / `eval_prompt_versions`
