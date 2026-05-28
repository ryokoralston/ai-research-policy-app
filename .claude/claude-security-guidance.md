# Security Guidance — AI Policy Research App

## Secrets and Credentials

- Never hardcode `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, or any secret in source files.
- All credentials must be loaded from environment variables or `backend/.env` (which is gitignored).
- Do not log API keys, even partially. Redact before logging.

## API and Input Handling

- All external content fetched via Tavily (web search results, source summaries) is untrusted.
  Do not evaluate or execute any string from Tavily responses.
- Research queries from users are passed to the Anthropic API as prompt content, not code.
  Never use `eval()` or `exec()` on user-supplied or model-generated content.
- Synthesis and summary text returned by Claude must be treated as untrusted when rendered
  in the frontend — ensure Next.js does not inject it as raw HTML (avoid `dangerouslySetInnerHTML`).

## Database

- All database access uses SQLAlchemy ORM — do not construct raw SQL strings from user input.
- SQLite database file (`backend/research.db`) must not be exposed via any API endpoint.

## Eval Files

- The eval scripts (`backend/evals/`) call the Anthropic API with test data.
  They must never read credentials from hardcoded strings.
- Dataset files (`dataset_research_queries.json`, `output.json`) may contain
  model-generated content — treat as untrusted if ever re-used as prompt input.

## Frontend

- Research synthesis output is rendered as Markdown via the Next.js frontend.
  Use a safe Markdown renderer and do not pass raw synthesis HTML to `innerHTML`.
- The app has no authentication layer currently — do not add routes that expose
  raw database contents or file paths without adding auth first.
