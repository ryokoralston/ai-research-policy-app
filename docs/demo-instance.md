# Running a demo instance

How to stand up a copy of this app that people outside the project — reviewers,
hiring panels, prospective users — can sign in to and actually use, without
exposing production data or an unbounded API bill.

## What protects the instance

The gate is **an account, not a secret URL**. There is no public sign-up: the
only ways in are `POST /api/auth/login` and the one-time `POST /api/auth/bootstrap`
that claims the first admin on an empty database. Accounts are created by an
admin (`POST /api/users/`, Users page). Sharing a link to the demo therefore
grants nothing on its own.

Layered on that:

- **Login rate limiting**, per IP (`services/auth.check_login_rate_limit`).
- **A per-user daily run cap** (`services/quota.py`), applied to every endpoint
  that spends money: research, report generation, risk analysis, debate, Data
  Lab, and manual digest sends. Set by `DEMO_RUN_QUOTA`; admins are exempt.
- **Admin-only global settings.** `model_settings` and `digest_settings` are
  single rows shared by the whole deployment, so their writes are restricted to
  admins — a reviewer cannot swap the API key, change the model, or redirect
  the daily digest. The matching UI is hidden from members.

## What this does *not* do

**Data is not scoped per user.** Research sessions, documents, reports,
debates, and analyses are visible to every signed-in account. On a dedicated
demo instance seeded with public material that is acceptable — reviewers see
each other's runs — but it is the reason a demo must never be a second set of
accounts on the production deployment. Say this plainly when handing out
credentials, so it reads as a property of the demo rather than a surprise.

## Setting one up

### 1. Your own API keys, with a hard spend cap

Create keys used by nothing else:

- Anthropic: a new key in the Console, plus a **spend limit** on it. This is the
  real backstop — the run quota bounds a well-behaved account, the spend cap
  bounds everything else.
- Tavily: a separate key, on the free tier if its request allowance suits the
  expected traffic.

Never reuse production keys. A measured research run costs about $0.44
(5 sources, Opus synthesis), so a handful of reviewers exploring for a few
weeks is small — but only if a cap makes that true by construction.

### 2. A separate Render service

Create it **in the dashboard, as its own service** — do not add it to
`render.yaml`, which is production's blueprint. Match production's runtime and
build command, give it its own persistent disk mounted at `/data`, and set:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `sqlite:////data/research.db` |
| `CHROMA_PERSIST_DIR` | `/data/chroma` |
| `UPLOADS_DIR` | `/data/uploads` |
| `ANTHROPIC_API_KEY` | the demo key from step 1 |
| `TAVILY_API_KEY` | the demo key from step 1 |
| `SECRET_ENCRYPTION_KEY` | a fresh Fernet key, **not** production's |
| `CORS_ORIGINS` | the demo frontend's URL (step 4) |
| `DEMO_MODE` | `true` |
| `DEMO_RUN_QUOTA` | e.g. `5` |

Generate the Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Leave the digest variables unset. `_run_digest` skips silently when no
recipient is configured, which is what keeps an unattended instance from making
scheduled API calls on its own.

### 3. Deploy, then claim the admin account immediately

`/api/auth/bootstrap` is open until the first user exists, and hosting URLs get
scanned. **Deploy and bootstrap in the same sitting** — do not leave a fresh
instance sitting empty overnight. Step 4 does the claiming.

### 4. The frontend

The backend alone is not usable. Deploy a second frontend (a separate Vercel
project from the same repo) with `NEXT_PUBLIC_API_URL` pointing at the demo
backend, then set the backend's `CORS_ORIGINS` to that frontend's URL.

### 5. Seed accounts and a starting library

```bash
cd backend
./venv/bin/python -m scripts.seed_demo \
    --base-url https://your-demo-backend.onrender.com \
    --admin-email you@example.com \
    --reviewers 3
```

This claims the admin account (or signs in if one exists), creates reviewer
accounts with generated passwords, and ingests three published AI-policy
documents — the NIST AI RMF, its Generative AI Profile, and the EU AI Act —
into the library through the app's own pipeline. It warns if `DEMO_MODE` or
`DEMO_RUN_QUOTA` are missing. **Passwords are printed once and stored nowhere
else**; if lost, reset from the Users page.

The script deliberately does not seed research sessions, reports, or debates.
Those exist only by running them, which costs money and cannot be faked. Sign
in as the admin and run each once, so reviewers arrive at a populated app
rather than an empty one.

### 6. Hand out credentials

Give each reviewer their own account. Worth stating in the handoff:

- runs are capped per account per day, and the cap resets at 00:00 UTC;
- everyone signed in shares one workspace and can see each other's runs;
- the library is seeded with public documents, and uploads are welcome.

## Afterwards

Delete the Render service and its disk, revoke the demo API keys, and delete
the Vercel project. Nothing in the demo is referenced by production.
