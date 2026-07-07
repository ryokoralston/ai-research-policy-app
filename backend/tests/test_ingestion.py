"""Tests for services.ingestion (F-6): SSRF-guarded fetch + YouTube ID parsing.

Moved verbatim out of routers/documents.py, which had no unit test coverage
of its own for these security-sensitive helpers. Pins the pure functions
(_extract_youtube_id, _ip_is_blocked) and _resolve_public_ip's validation
behavior — all offline, no network calls.

Run from the backend directory:
    ./venv/bin/python -m tests.test_ingestion
"""
import ipaddress
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("DATABASE_URL", "sqlite://")

from services.ingestion import _extract_youtube_id, _ip_is_blocked, _resolve_public_ip


# ── _extract_youtube_id ────────────────────────────────────────────────────────

def test_extract_youtube_id_watch_url():
    assert _extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_watch_url_with_extra_params():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123"
    assert _extract_youtube_id(url) == "dQw4w9WgXcQ"


def test_extract_youtube_id_short_url():
    assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_short_url_with_query():
    assert _extract_youtube_id("https://youtu.be/dQw4w9WgXcQ?t=10") == "dQw4w9WgXcQ"


def test_extract_youtube_id_embed_url():
    assert _extract_youtube_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_shorts_url():
    assert _extract_youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_id_non_youtube_url_returns_none():
    assert _extract_youtube_id("https://example.com/article/123") is None


def test_extract_youtube_id_youtube_homepage_returns_none():
    assert _extract_youtube_id("https://www.youtube.com/") is None


# ── _ip_is_blocked ──────────────────────────────────────────────────────────────

def test_ip_is_blocked_loopback():
    assert _ip_is_blocked(ipaddress.ip_address("127.0.0.1"))


def test_ip_is_blocked_private_ranges():
    assert _ip_is_blocked(ipaddress.ip_address("10.0.0.1"))
    assert _ip_is_blocked(ipaddress.ip_address("192.168.1.1"))
    assert _ip_is_blocked(ipaddress.ip_address("172.16.0.1"))


def test_ip_is_blocked_link_local_and_cloud_metadata():
    # 169.254.169.254 is the AWS/GCP/Azure instance-metadata endpoint — a
    # classic SSRF target if link-local addresses weren't blocked.
    assert _ip_is_blocked(ipaddress.ip_address("169.254.169.254"))


def test_ip_is_blocked_reserved_and_multicast_and_unspecified():
    assert _ip_is_blocked(ipaddress.ip_address("240.0.0.1"))   # reserved
    assert _ip_is_blocked(ipaddress.ip_address("224.0.0.1"))   # multicast
    assert _ip_is_blocked(ipaddress.ip_address("0.0.0.0"))     # unspecified


def test_ip_is_blocked_ipv6_loopback_and_link_local():
    assert _ip_is_blocked(ipaddress.ip_address("::1"))
    assert _ip_is_blocked(ipaddress.ip_address("fe80::1"))


def test_ip_is_not_blocked_public_address():
    assert not _ip_is_blocked(ipaddress.ip_address("8.8.8.8"))
    assert not _ip_is_blocked(ipaddress.ip_address("1.1.1.1"))


# ── _resolve_public_ip ───────────────────────────────────────────────────────────

def test_resolve_public_ip_rejects_non_http_scheme():
    try:
        _resolve_public_ip("file:///etc/passwd")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "http" in str(e).lower()


def test_resolve_public_ip_rejects_url_with_no_host():
    try:
        _resolve_public_ip("http://")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "host" in str(e).lower()


def test_resolve_public_ip_rejects_loopback_hostname():
    # "localhost" resolves to 127.0.0.1 — must be rejected before any fetch.
    try:
        _resolve_public_ip("http://localhost/")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "non-public" in str(e).lower() or "resolve" in str(e).lower()


def test_resolve_public_ip_rejects_metadata_ip_literal():
    try:
        _resolve_public_ip("http://169.254.169.254/latest/meta-data/")
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "non-public" in str(e).lower()


# ── Test runner ───────────────────────────────────────────────────────────────

_PASSED: list[str] = []
_FAILED: list[str] = []


def _run(name, fn):
    try:
        fn()
        _PASSED.append(name)
        print(f"  PASS  {name}")
    except Exception as exc:
        _FAILED.append(name)
        print(f"  FAIL  {name}: {exc}")


if __name__ == "__main__":
    print("\nRunning ingestion (SSRF guard / YouTube ID) tests...\n")

    _run("extract youtube id: watch url", test_extract_youtube_id_watch_url)
    _run("extract youtube id: watch url with extra params", test_extract_youtube_id_watch_url_with_extra_params)
    _run("extract youtube id: short url", test_extract_youtube_id_short_url)
    _run("extract youtube id: short url with query", test_extract_youtube_id_short_url_with_query)
    _run("extract youtube id: embed url", test_extract_youtube_id_embed_url)
    _run("extract youtube id: shorts url", test_extract_youtube_id_shorts_url)
    _run("extract youtube id: non-youtube url returns None", test_extract_youtube_id_non_youtube_url_returns_none)
    _run("extract youtube id: youtube homepage returns None", test_extract_youtube_id_youtube_homepage_returns_none)
    _run("ip is blocked: loopback", test_ip_is_blocked_loopback)
    _run("ip is blocked: private ranges", test_ip_is_blocked_private_ranges)
    _run("ip is blocked: link-local / cloud metadata", test_ip_is_blocked_link_local_and_cloud_metadata)
    _run("ip is blocked: reserved / multicast / unspecified", test_ip_is_blocked_reserved_and_multicast_and_unspecified)
    _run("ip is blocked: ipv6 loopback / link-local", test_ip_is_blocked_ipv6_loopback_and_link_local)
    _run("ip is not blocked: public address", test_ip_is_not_blocked_public_address)
    _run("resolve_public_ip: rejects non-http scheme", test_resolve_public_ip_rejects_non_http_scheme)
    _run("resolve_public_ip: rejects no-host url", test_resolve_public_ip_rejects_url_with_no_host)
    _run("resolve_public_ip: rejects loopback hostname", test_resolve_public_ip_rejects_loopback_hostname)
    _run("resolve_public_ip: rejects metadata ip literal", test_resolve_public_ip_rejects_metadata_ip_literal)

    total = len(_PASSED) + len(_FAILED)
    print(f"\n{'=' * 50}")
    print(f"Results: {len(_PASSED)}/{total} passed, {len(_FAILED)} failed")
    if _FAILED:
        print(f"Failed: {', '.join(_FAILED)}")
        sys.exit(1)
    else:
        print("All tests passed.")
