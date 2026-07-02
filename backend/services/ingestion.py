"""SSRF-guarded URL fetching, web scraping, and YouTube transcript extraction.

Moved out of routers/documents.py (F-6): these are security-sensitive helpers
(SSRF defenses incl. DNS-rebinding protection) that don't belong mixed in
with endpoint definitions, and previously had no unit test coverage of their
own. Logic is unchanged from the original router implementation — this is a
pure relocation, verified by diffing the function bodies before and after
the move.
"""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlparse

MAX_SCRAPE_BYTES = 10 * 1024 * 1024   # 10 MB cap on remotely fetched pages
MAX_REDIRECTS = 4


def _ip_is_blocked(ip) -> bool:
    """True for SSRF-sensitive addresses: loopback, private, link-local (incl.
    the cloud metadata endpoint 169.254.169.254), reserved, multicast, etc."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_public_ip(url: str) -> tuple[str, str]:
    """Validate scheme/host, resolve the hostname, ensure EVERY resolved IP is
    public, and return (pinned_ip, hostname).

    The connection must then be made to the returned IP (not by re-resolving the
    hostname) so a DNS-rebinding attacker cannot swap in an internal address
    between this check and the actual socket connect (TOCTOU).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http:// and https:// URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("Could not resolve host")

    pinned: str | None = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _ip_is_blocked(ip):
            raise ValueError("URL resolves to a non-public address")
        if pinned is None:
            pinned = str(ip)
    if pinned is None:
        raise ValueError("Could not resolve host")
    return pinned, host


def _assert_public_url(url: str) -> None:
    """Raise ValueError if the URL is not http(s) or resolves to a non-public IP."""
    _resolve_public_ip(url)


async def _safe_fetch_bytes(url: str, headers: dict) -> bytes:
    """Fetch a URL with SSRF protection, redirect re-validation, and a size cap.

    Each hop is validated and the connection is pinned to the validated IP
    (Host header + TLS SNI preserved), which closes the DNS-rebinding window.
    """
    import httpx

    async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
        for _ in range(MAX_REDIRECTS + 1):
            pinned_ip, host = _resolve_public_ip(url)  # re-validate every hop
            req = client.build_request("GET", url, headers=headers)
            # Connect to the pre-validated IP, but keep the original Host header
            # and TLS SNI so virtual hosting and cert verification still work.
            req.url = req.url.copy_with(host=pinned_ip)
            req.headers["Host"] = host
            req.extensions["sni_hostname"] = host

            r = await client.send(req, stream=True)
            try:
                if r.is_redirect and "location" in r.headers:
                    url = str(httpx.URL(url).join(r.headers["location"]))
                    continue
                r.raise_for_status()
                total = 0
                chunks: list[bytes] = []
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_SCRAPE_BYTES:
                        raise ValueError("Remote content exceeds size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
            finally:
                await r.aclose()
    raise ValueError("Too many redirects")

def _extract_youtube_id(url: str) -> str | None:
    patterns = [
        r"youtube\.com/watch\?.*v=([^&\s]+)",
        r"youtu\.be/([^?\s]+)",
        r"youtube\.com/embed/([^?\s]+)",
        r"youtube\.com/shorts/([^?\s]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def _get_youtube_transcript(video_id: str) -> tuple[str, str]:
    """Return (title, transcript_text). Runs sync lib in thread pool."""
    import httpx

    # Fetch title via oEmbed
    title = f"YouTube – {video_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            )
            if r.status_code == 200:
                title = r.json().get("title", title)
    except Exception:
        pass

    # Fetch transcript in thread (sync library — v1.x API)
    def _fetch():
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()
        fetched = ytt.fetch(video_id)
        # FetchedTranscriptSnippet objects have .text attribute
        return " ".join(
            e.text if hasattr(e, "text") else e.get("text", "")
            for e in fetched
        )

    text = await asyncio.to_thread(_fetch)
    return title, text


async def _scrape_url(url: str) -> tuple[str, str]:
    """Scrape a web page and return (title, plain_text). SSRF-protected."""
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchBot/1.0)"}
    raw = await _safe_fetch_bytes(url, headers)

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    title = url
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = soup.get_text(separator="\n", strip=True)
    return title, text
