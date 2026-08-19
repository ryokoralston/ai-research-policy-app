---
name: security-audit
description: Full read-only security audit of this app — authn/authz, cross-user data access, injection (SQL/XSS/CSRF), PII in API responses, config drift, and dependencies. Run it before a release, after a batch of feature work, or when someone asks "is this safe to expose". Reports findings; it does not fix them. Not the same as the built-in /security-review, which only reviews the pending diff.
---

# Security audit — AI Policy Research App

**Read-only.** Report findings; do not edit, do not commit. Fixing is a separate,
explicitly-requested pass — a fix mixed into an audit hides what was wrong.

## Before you start

1. Read `known-accepted.md` in this directory. It lists risks already reviewed
   and deliberately accepted. Do not re-report them as new; confirm each is
   still true and say so in the "known" bucket.
2. `git log --oneline -30` — what changed since the last audit? New routers,
   new models and new external-content paths are where findings come from.
3. Note the tree state. Auditing a dirty tree is fine, but say which.

## The one rule that matters most

**Start from data-access calls, not from the endpoint list.**

The 2026-08-19 audit worked the endpoint list first and missed four real
cross-user holes, because they lived on paths that never take an object id
from the caller: a bulk folder rename, a report generator pulling a foreign
`session_id`, an internally-created session, and a separate MCP process.

So the first command of every audit is:

```bash
cd backend && grep -rn "db\.query(\|db\.get(" routers/ services/ rag/ mcp_server.py | grep -v "^backend/venv/"
```

Every hit is either (a) filtered by the acting user, (b) reachable only through
an already-ownership-checked parent, or (c) global by design. Classify all of
them. Anything you cannot place in one of those three buckets is a finding.

## 1. Cross-user data access

Six tables carry ownership: `documents`, `reports`, `research_sessions`,
`risk_analyses`, `debates`, `reminders`. Global by design (do NOT flag):
`model_settings`, `digest_settings`, `custom_personas`, `model_catalog_entries`.

```bash
# every owned table still has both columns
cd backend && ./venv/bin/python -c "
import sqlite3; c=sqlite3.connect('data/research.db')
for t in ['documents','reports','research_sessions','risk_analyses','debates','reminders']:
    cols=[r[1] for r in c.execute(f'pragma table_info({t})')]
    print(t, [x for x in cols if x in ('user_id','org_id')] or 'MISSING')"

# no admin bypass may exist in the scoped routers
grep -rn "is_admin\|role == \"admin\"\|ROLE_ADMIN" routers/reports.py routers/research.py \
  routers/documents.py routers/debate.py routers/analysis.py routers/reminders.py
```

Ownership filtering is strictly `user_id == current_user.id` **for everyone,
admins included**. A foreign row must return **404**, never 403. If you find an
`if admin:` branch that skips the filter, that is a finding regardless of intent.

RAG is scoped by passing the caller's own `doc_ids` into `Retriever.retrieve`.
Check that `[]` still means "match nothing" and `None` means "no filter" — if a
caller with zero documents falls through to an unfiltered search, that is a
critical finding.

**Prove it live when anything in this area changed.** Unit tests can pass while
the wiring is wrong. Stand up a throwaway instance, make two accounts, and try
to read/export/delete across them over HTTP:

```bash
cd backend && SC=/tmp/audit && mkdir -p $SC && rm -f $SC/t.db && \
  DATABASE_URL="sqlite:///$SC/t.db" CHROMA_PERSIST_DIR="$SC/chroma" UPLOADS_DIR="$SC/uploads" \
  ./venv/bin/uvicorn main:app --port 8010
# bootstrap admin -> create member -> seed rows owned by A -> assert B gets 404 on
# GET / export / DELETE, that A's row survives, and that each list shows only its owner's rows.
```

## 2. Authn / authz

```bash
grep -rn "@app\.\(get\|post\|put\|patch\|delete\)" backend/   # expect only /health
grep -n -A20 "_protected\|_admin_only" backend/main.py        # every router mounted?
grep -rn "token" backend/routers/*.py | grep -i "query\|param" # token must never arrive by query string
```

Public surface should be exactly `/health` plus `/api/auth/{status,bootstrap,login}`.
Confirm `bootstrap` still self-disables once any user exists. Admin-only routers:
`users`, `audit_log`, `admin_personas`; settings writes require `require_admin`;
the member-facing `personas` router must stay read-only.

## 3. Injection

**SQL** — everything is SQLAlchemy ORM. Prove it, don't assume:

```bash
cd backend && grep -rn "text(\|execute(\|f\"SELECT\|f'SELECT" routers/ services/ database.py | grep -v venv
```

**XSS** — the app renders fetched web sources, uploaded documents and model
output as markdown, so this is the highest-consequence area:

```bash
cd frontend && grep -rn "dangerouslySetInnerHTML\|innerHTML\|eval(\|new Function" src/
grep -rn "rehype-raw\|rehypeRaw\|rehypePlugins" src/ package.json
grep -rn "urlTransform" src/
```

react-markdown escapes HTML and sanitizes URLs **by default** — a finding exists
only if something opted out. `rehype-raw` anywhere is a finding. A custom
`urlTransform` must fall through to `defaultUrlTransform` for anything but its
own synthetic scheme.

**CSRF** — auth is a Bearer token from localStorage, no cookies, so classic CSRF
does not apply. Verify that claim each time rather than restating it: check the
token is still not in a cookie, and that `cors_origins` is not a wildcard.

## 4. PII in API responses

```bash
grep -rn "password_hash" backend/routers/ backend/schemas/   # must never be serialized
grep -n "def _serialize\|return {" backend/routers/users.py backend/routers/auth.py
grep -rn "mask_secret\|MASK" backend/routers/settings.py     # API keys masked on GET *and* PUT
grep -rn "str(e)\|repr(e)" backend/routers/                  # no exception text to clients
```

## 5. Config drift  (this deployment has burned us three times)

`render.yaml` is **not** self-applying — this service is not Blueprint-managed,
so the dashboard is the source of truth. Env vars have silently gone missing
twice and a new one failed to apply once.

Compare every `envVars` key in `render.yaml` against the running service. Any
setting whose default is a **relative path** is the dangerous class: it silently
writes to ephemeral container storage instead of the mounted `/data` disk.
Today's data paths: `DATABASE_URL`, `CHROMA_PERSIST_DIR`, `UPLOADS_DIR`,
`BM25_INDEX_PATH`. Verify from the Render Shell — ask the user to run it, the
audit cannot reach the dashboard:

```bash
cd ~/project/src/backend && python3 -c "
from config import get_settings as g
s=g()
for k in ['database_url','chroma_persist_dir','uploads_dir','bm25_index_path']:
    print(k, '->', getattr(s,k))"
```

Anything printing a `./relative` path in production is a finding.

## 6. Dependencies

```bash
gh api repos/ryokoralston/ai-research-policy-app/dependabot/alerts \
  --jq '.[] | select(.state=="open") | "\(.security_advisory.severity)\t\(.dependency.package.name)\t\(.security_vulnerability.first_patched_version.identifier)"'
cd frontend && pnpm audit
```

Before proposing a version bump, check the fix's publish date against
`minimumReleaseAge` (7 days) in `pnpm-workspace.yaml`. Never suggest
`pnpm audit fix --force` — it "resolves" these by downgrading Next to 9.3.3.

## Reporting

Three buckets, in this order:

1. **New findings** — severity, `file:line`, and a concrete exploit scenario for
   each. "X is unfiltered" is not a finding; "user B calls GET /api/x/{A's id}
   and reads A's content" is.
2. **Known and accepted** — from `known-accepted.md`, each confirmed still true.
3. **Verified clean** — with the command you ran as evidence. An area with no
   findings needs proof, otherwise "clean" and "not looked at" read identically.

## Traps that have actually bitten

- **A check that cannot fail proves nothing.** Ask of every verification: could
  this pass while the thing I care about is broken? Counting a string in a
  response body "passes" against an error page. Counting routes "passes" when
  the deploy never happened.
- **zsh, not bash.** `--include=*.py` gets glob-expanded and the grep silently
  finds nothing; quote it. `set -- $var` does not word-split. Unquoted globs in
  `grep -r` need quoting.
- **`grep -c` counts lines, `grep -o | wc -l` counts occurrences.** Reporting the
  first as a count of matches understates it.
- **Run every test runner**, never a subset: a partial run has missed real
  regressions in this repo.
  `cd backend && for t in $(ls tests/test_*.py | sed 's|tests/||;s|\.py||'); do ./venv/bin/python -m tests.$t; done`
