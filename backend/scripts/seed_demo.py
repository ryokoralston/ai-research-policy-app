"""Provision a demo instance: reviewer accounts and a starting document library.

Talks to a running deployment over HTTP as an admin rather than writing to the
database directly, so it goes through the same validation, hashing, and
indexing paths a real user would — and works against Render without a shell.

Nothing here fabricates content: the library is built by ingesting real
published documents through the app's own pipeline. Research sessions, briefs,
and debates are deliberately *not* seeded, because the only honest way to have
those is to run them, which costs API money and is better done by hand once the
instance is up.

Usage (from the backend directory):

    ./venv/bin/python -m scripts.seed_demo \\
        --base-url https://your-demo.onrender.com \\
        --admin-email you@example.com \\
        --reviewers 3

On a freshly deployed instance with no users yet, this claims the admin account
via /api/auth/bootstrap — run it immediately after the first deploy, since that
endpoint is open to whoever reaches it first. On an instance that already has
an admin, it signs in instead.

Reviewer passwords are generated here and printed once. They are not stored
anywhere else; if you lose them, reset from the Users page.
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
import time
from urllib.parse import urlparse

import httpx

# Real, publicly published AI-policy source material, verified reachable
# 2026-08-12. Each is downloaded here and uploaded under a curated filename:
# the library labels a document by the name it arrived with, and left to
# themselves these sources supply things like "L_202401689EN.000101.fmx.xml".
DEFAULT_DOCS: list[tuple[str, str]] = [
    (
        "NIST AI 100-1 - AI Risk Management Framework 1.0.pdf",
        "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf",
    ),
    (
        "NIST AI 600-1 - Generative AI Profile.pdf",
        "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf",
    ),
    (
        "EU AI Act - Regulation (EU) 2024-1689.html",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202401689",
    ),
]

MEDIA_TYPES = {".pdf": "application/pdf", ".html": "text/html", ".txt": "text/plain"}

TIMEOUT = httpx.Timeout(120.0, connect=30.0)


def _fail(message: str) -> None:
    print(f"\nERROR: {message}", file=sys.stderr)
    sys.exit(1)


def _authenticate(client: httpx.Client, base_url: str, email: str, password: str) -> str:
    """Return a bearer token, claiming the admin account if none exists yet."""
    resp = client.get(f"{base_url}/api/auth/status")
    resp.raise_for_status()
    status = resp.json()

    if status.get("setup_required"):
        print("  no accounts exist yet — claiming the admin account via /auth/bootstrap")
        endpoint = "/api/auth/bootstrap"
    else:
        print("  an admin already exists — signing in")
        endpoint = "/api/auth/login"

    resp = client.post(f"{base_url}{endpoint}", json={"email": email, "password": password})
    if resp.status_code != 200:
        _fail(f"{endpoint} returned {resp.status_code}: {resp.text}")

    if not status.get("demo_mode"):
        print(
            "  WARNING: this deployment does not report demo_mode. Check that you are "
            "pointing at the demo instance and not production."
        )
    quota = status.get("demo_run_quota") or 0
    if quota <= 0:
        print(
            "  WARNING: DEMO_RUN_QUOTA is unset, so reviewer accounts can start "
            "unlimited billable runs. Set it before sharing any credentials."
        )
    else:
        print(f"  run quota in effect: {quota} runs per account per UTC day")

    return resp.json()["token"]


def _create_reviewers(client: httpx.Client, base_url: str, headers: dict, emails: list[str]) -> list[tuple[str, str]]:
    created: list[tuple[str, str]] = []
    for email in emails:
        password = secrets.token_urlsafe(12)
        resp = client.post(
            f"{base_url}/api/users/",
            headers=headers,
            json={"email": email, "password": password, "role": "member"},
        )
        if resp.status_code == 409:
            print(f"  {email}: already exists, left untouched")
            continue
        if resp.status_code != 200:
            _fail(f"creating {email} returned {resp.status_code}: {resp.text}")
        created.append((email, password))
        print(f"  {email}: created")
    return created


def _upload(client: httpx.Client, base_url: str, headers: dict, name: str, url: str) -> str | None:
    """Download `url` and upload it to the library as `name`."""
    print(f"  downloading {name} …")
    src = client.get(url, follow_redirects=True)
    if src.status_code != 200:
        print(f"    SKIPPED: download returned {src.status_code}")
        return None

    ext = os.path.splitext(name)[1].lower()
    resp = client.post(
        f"{base_url}/api/documents/upload",
        headers=headers,
        files={"file": (name, src.content, MEDIA_TYPES.get(ext, "application/octet-stream"))},
    )
    if resp.status_code != 200:
        print(f"    SKIPPED: upload returned {resp.status_code} {resp.text[:200]}")
        return None
    print("    accepted, indexing in the background")
    return resp.json()["document_id"]


def _ingest_url(client: httpx.Client, base_url: str, headers: dict, url: str) -> str | None:
    """Hand `url` to the app's own scraper. HTML only — it does not parse PDF."""
    print(f"  ingesting {url} …")
    resp = client.post(f"{base_url}/api/documents/ingest-url", headers=headers, json={"url": url})
    if resp.status_code != 200:
        print(f"    SKIPPED: {resp.status_code} {resp.text[:200]}")
        return None
    print("    accepted, indexing in the background")
    return resp.json()["document_id"]


def _seed_documents(
    client: httpx.Client,
    base_url: str,
    headers: dict,
    docs: list[tuple[str, str]],
    extra_urls: list[str],
) -> list[str]:
    doc_ids = [_upload(client, base_url, headers, name, url) for name, url in docs]
    doc_ids += [_ingest_url(client, base_url, headers, url) for url in extra_urls]
    return [d for d in doc_ids if d]


def _wait_for_indexing(client: httpx.Client, base_url: str, headers: dict, doc_ids: list[str], timeout_s: int) -> None:
    """Poll until every seeded document leaves 'processing', or time out.

    Indexing runs as a background task, so a document is not searchable the
    moment upload returns. Timing out is reported, not treated as failure —
    a large PDF can outlast a reasonable wait.
    """
    deadline = time.time() + timeout_s
    pending = set(doc_ids)
    while pending and time.time() < deadline:
        resp = client.get(f"{base_url}/api/documents/", headers=headers)
        if resp.status_code != 200:
            break
        by_id = {d["id"]: d for d in resp.json()}
        for doc_id in sorted(pending):
            doc = by_id.get(doc_id)
            if doc and doc.get("status") != "processing":
                print(f"  {doc.get('title') or doc.get('filename')}: {doc.get('status')}")
                pending.discard(doc_id)
        if pending:
            time.sleep(5)

    if pending:
        print(
            f"  {len(pending)} document(s) still indexing after {timeout_s}s — "
            "check the Library page later."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="e.g. https://your-demo.onrender.com")
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-password", help="prompted for if omitted")
    parser.add_argument("--reviewers", type=int, default=3, help="how many reviewer accounts to generate (default 3)")
    parser.add_argument(
        "--reviewer-email",
        action="append",
        default=[],
        metavar="EMAIL",
        help="explicit reviewer address; repeatable. Overrides --reviewers.",
    )
    parser.add_argument("--email-domain", default="demo.invalid", help="domain for generated addresses")
    parser.add_argument(
        "--doc-url",
        action="append",
        default=[],
        metavar="URL",
        help="extra HTML page to scrape into the library; repeatable. Added to the defaults.",
    )
    parser.add_argument("--no-default-docs", action="store_true", help="seed only --doc-url pages")
    parser.add_argument("--skip-documents", action="store_true")
    parser.add_argument("--index-timeout", type=int, default=180, help="seconds to wait for indexing (default 180)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    password = args.admin_password or getpass.getpass(f"Password for {args.admin_email}: ")

    emails = args.reviewer_email or [
        f"reviewer-{i}@{args.email_domain}" for i in range(1, args.reviewers + 1)
    ]
    docs = [] if (args.skip_documents or args.no_default_docs) else DEFAULT_DOCS
    extra_urls = [] if args.skip_documents else args.doc_url

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        print(f"\nConnecting to {base_url}")
        token = _authenticate(client, base_url, args.admin_email, password)
        headers = {"Authorization": f"Bearer {token}"}

        print(f"\nCreating {len(emails)} reviewer account(s)")
        created = _create_reviewers(client, base_url, headers, emails)

        doc_ids: list[str] = []
        if docs or extra_urls:
            print(f"\nSeeding {len(docs) + len(extra_urls)} document(s) into the library")
            doc_ids = _seed_documents(client, base_url, headers, docs, extra_urls)
            if doc_ids:
                print("\nWaiting for indexing to finish")
                _wait_for_indexing(client, base_url, headers, doc_ids, args.index_timeout)

    print("\n" + "=" * 68)
    if created:
        print("Reviewer credentials — shown once, not stored anywhere else:\n")
        for email, pw in created:
            print(f"  {email}\n    {pw}\n")
    else:
        print("No new reviewer accounts were created.\n")

    print("Next, sign in as the admin and run each feature once, so reviewers")
    print("land on a populated app: a research session, a report from one of the")
    print("three templates, a risk analysis, and a debate. Those cost API money")
    print("and cannot be faked, which is why this script does not seed them.")
    print("=" * 68)


if __name__ == "__main__":
    main()
