# Security & Deployment Notes

This app handles API keys, an SMTP password, and (when deployed) is reachable on
the public internet. The controls below are already implemented — this document
records how they work and **what you must configure when deploying**.

## ⚠️ Required environment variables for any public deployment

Set these in the Render dashboard (they are `sync: false` in `render.yaml`, so
they are never committed):

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `APP_PASSWORD` | **Yes (for deploy)** | The login password. If unset, auth is **disabled** and every `/api` route is public (a warning is logged at startup). |
| `SECRET_ENCRYPTION_KEY` | Recommended | Fernet key used to encrypt secrets at rest. If unset, a key file is auto-generated on the persistent `/data` disk. |
| `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` | Yes | Model + web search. |
| `CORS_ORIGINS` | Yes | Comma-separated allowed origins. Must include the frontend URL, e.g. `https://ai-research-policy-app.vercel.app` (origin only — no trailing slash/path). |
| `SESSION_TTL_HOURS` | No (default 12) | Bearer-token lifetime. |

### 🔴 Never rotate `SECRET_ENCRYPTION_KEY`

Secrets are encrypted at rest with this key. **If you change it, all previously
encrypted secrets become unrecoverable** (decryption fails and the stored
ciphertext is returned unchanged). Pick it once and keep it. Keep `APP_PASSWORD`
in a password manager — losing it locks you out of the app.

Generate a key with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## How the controls work

- **Authentication** — `POST /api/auth/login` exchanges `APP_PASSWORD` for a
  Fernet bearer token; all `/api` routes require it (`services/auth.py`,
  `routers/auth.py`). `/health` and `/api/auth/*` are public. The frontend stores
  the token in `localStorage`, attaches it via `authFetch`/`authHeaders`
  (`frontend/src/lib/api.ts`), guards routes with `AuthGuard`, and offers a Sign
  out button. A 401 clears the token and redirects to `/login`.
- **Secrets at rest** — `EncryptedString` (Fernet) transparently encrypts the
  model API keys and the digest SMTP password (`services/secret_crypto.py`).
  Legacy plaintext rows are read as-is and **auto-upgraded to ciphertext at
  startup** by `database.encrypt_legacy_secrets()` (idempotent). The digest SMTP
  password is masked (`***`) in API responses.
- **SSRF protection** — `/api/documents/ingest-url` validates that the target
  host resolves to a public IP and pins the connection to that IP (Host + TLS SNI
  preserved), which blocks loopback/private/link-local/metadata targets and
  closes the DNS-rebinding window. Redirects are re-validated; responses are size
  capped (`backend/routers/documents.py`).
- **Upload limits** — uploads capped at 25 MB, remote fetches at 10 MB.
- **Prompt injection** — external content is wrapped in XML tags and every
  request that embeds it appends `UNTRUSTED_CONTENT_GUARD` to the system prompt,
  instructing the model to treat tag contents as data only
  (`services/anthropic_client.py`).
- **Email output** — the daily digest HTML escapes externally sourced titles,
  URLs, and headlines (`services/digest_service.py`).

## Deploy checklist

1. Set the env vars above in the Render dashboard (at least `APP_PASSWORD`).
2. Push to `main` → Render auto-deploys (builds take a few minutes; the build
   downloads the sentence-transformers model). The startup migration encrypts any
   legacy plaintext secrets automatically.
3. Confirm: `GET /health` → 200, `GET /api/auth/status` → `{"auth_required":true}`,
   a protected route without a token → 401.
4. Log in at the frontend `/login`, then verify a Research run works (proves the
   stored Anthropic key decrypts).
