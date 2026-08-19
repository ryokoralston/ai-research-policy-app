# Security Guidance — AI Policy Research App

Rules to follow **while writing code**. For a full after-the-fact review, run
the `security-audit` skill (`.claude/skills/security-audit/`).

Last reviewed 2026-08-19.

## Data ownership — the rule most easily broken

Every row in `documents`, `reports`, `research_sessions`, `risk_analyses`,
`debates` and `reminders` belongs to a user (`user_id`, plus `org_id` for the
coming org tenancy).

- **Every** query against those tables filters by the acting user. New endpoint,
  new background task, new helper — no exceptions.
- Filtering is `user_id == current_user.id` for **everyone, admins included**.
  Never write `if admin:` to skip the filter; admin power is confined to the
  `users` / `audit_log` / `admin_personas` routers and settings writes.
- A row belonging to someone else returns **404**, not 403 — a 403 confirms the
  id exists.
- Creating a row sets `user_id` **and** `org_id`.
- Global by design, do not scope: `model_settings`, `digest_settings`,
  `custom_personas`, `model_catalog_entries`.
- RAG retrieval is scoped by passing the caller's own `doc_ids`. `[]` means
  "match nothing"; `None` means "no filter". Never let an empty list fall
  through to an unfiltered search.

The paths that get missed are the ones that never take an id from the caller:
bulk operations, generators that accept another object's id, internally-created
records, and separate processes (MCP). Check those too.

## Auth

Per-user accounts (`services/auth.py`, `models/user.py`) — this app **has** an
auth layer; every `/api` route requires a bearer token. Public surface is
`/health` and `/api/auth/{status,bootstrap,login}` only. Do not mount a router
without `Depends(get_current_user)`; admin routers use `require_admin`.

Frontend calls go through `authFetch`/`api.*` in `lib/api.ts`. A raw `fetch()`
misses the Authorization header and silently 401s, which renders as an empty
list rather than an error — this has shipped as a real bug once.

## Secrets

- Never hardcode `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, or any secret. Load from
  env or `backend/.env` (gitignored). Never log a key, even partially.
- Secrets at rest are Fernet-encrypted; API keys are masked (`mask_secret`) in
  both GET and PUT responses.
- **Never swap `SECRET_ENCRYPTION_KEY` in place** — `decrypt_secret()` returns
  the stored ciphertext unchanged on a key mismatch, so the damage is silent.
  Use `backend/scripts/rotate_secret_key.py`.

## Untrusted content

Web results (Tavily), uploaded documents, and model output are all untrusted.

- Never `eval()`/`exec()` anything from them.
- Wherever external content enters a prompt, `UNTRUSTED_CONTENT_GUARD` belongs
  in the system prompt.
- Rendering: react-markdown escapes HTML and sanitizes URLs by default. **Do not
  add `rehype-raw`**, and do not use `dangerouslySetInnerHTML` for any content
  that is not a fixed literal. A custom `urlTransform` must defer to
  `defaultUrlTransform` for everything but its own synthetic scheme.
- Do not return raw exception text to a client (`str(e)` in an SSE error event
  or an HTTP detail). Log it server-side; send a generic message.

## Database

SQLAlchemy ORM only — never build SQL from strings or f-strings. The SQLite
file must never be reachable through an endpoint.

## Deployment config

`render.yaml` is **not** self-applying for this service; the Render dashboard is
the source of truth, and env vars have gone missing twice. Any new setting whose
default is a **relative path** will silently write to ephemeral container storage
instead of `/data` — set it explicitly in both places and verify against the
running service, not the file.
