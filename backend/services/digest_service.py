"""
Daily AI Policy Digest Service.

Flow:
  1. Search each topic via Tavily (last 24h)
  2. Deduplicate by URL, pick top 5 by score
  3. Generate a 1-2 sentence English headline per article via Claude Haiku
  4. Build HTML email body
  5. Send via Gmail SMTP (STARTTLS, port 587)
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from services.anthropic_client import generate_text
from services.tavily_client import SearchResult, TavilyClient

logger = logging.getLogger(__name__)


async def _fetch_top_articles(topics: list[str], max_total: int = 5) -> list[SearchResult]:
    """Search all topics and return deduplicated top articles sorted by score."""
    client = TavilyClient()
    seen_urls: set[str] = set()
    all_results: list[SearchResult] = []

    for topic in topics:
        try:
            results = await client.search(
                query=topic,
                max_results=5,
                include_raw_content=False,
                search_depth="basic",
            )
            for r in results:
                if r.url not in seen_urls:
                    seen_urls.add(r.url)
                    all_results.append(r)
        except Exception:
            logger.exception("Tavily search failed for topic: %s", topic)

    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:max_total]


async def _generate_headline(article: SearchResult) -> str:
    """Ask Claude Haiku to write a 1-2 sentence English headline summary."""
    prompt = (
        "Read the following article snippet and write a 1-2 sentence English "
        "headline summary from an AI policy perspective. "
        "Be objective and concise.\n\n"
        f"Title: {article.title}\n"
        f"Snippet: {article.snippet[:500]}"
    )
    try:
        return await generate_text(prompt, max_tokens=200)
    except Exception:
        logger.exception("Claude headline generation failed for: %s", article.url)
        return article.snippet[:200]


def _build_html(articles: list[tuple[SearchResult, str]], date_str: str) -> str:
    """Render the digest as an HTML email body."""
    items_html = ""
    for i, (article, headline) in enumerate(articles, start=1):
        # Article fields come from external web search — escape before embedding
        # into the email HTML to prevent markup/attribute injection.
        safe_title = html.escape(article.title or "")
        safe_url = html.escape(article.url or "", quote=True)
        safe_headline = html.escape(headline or "")
        items_html += f"""
        <tr>
          <td style="padding:16px 0; border-bottom:1px solid #e2e8f0;">
            <p style="margin:0 0 4px; font-size:15px; font-weight:600; color:#1e293b;">
              {i}. {safe_title}
            </p>
            <p style="margin:0 0 6px; font-size:12px; color:#64748b;">
              <a href="{safe_url}" style="color:#3b82f6;">{safe_url}</a>
            </p>
            <p style="margin:0; font-size:14px; color:#334155; line-height:1.6;">
              {safe_headline}
            </p>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#f8fafc; margin:0; padding:0;">
  <table width="600" cellpadding="0" cellspacing="0"
         style="margin:32px auto; background:#ffffff;
                border-radius:8px; overflow:hidden;
                box-shadow:0 1px 3px rgba(0,0,0,.1);">
    <!-- Header -->
    <tr>
      <td style="background:#1e40af; padding:24px 32px;">
        <h1 style="margin:0; font-size:20px; color:#ffffff;">
          AI Policy Daily Digest
        </h1>
        <p style="margin:4px 0 0; font-size:13px; color:#bfdbfe;">
          {date_str} &nbsp;|&nbsp; 5:00 AM ET
        </p>
      </td>
    </tr>
    <!-- Articles -->
    <tr>
      <td style="padding:0 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          {items_html}
        </table>
      </td>
    </tr>
    <!-- Footer -->
    <tr>
      <td style="padding:20px 32px; background:#f1f5f9;
                 font-size:12px; color:#94a3b8; text-align:center;">
        Powered by <strong>IAPS Research App</strong> &nbsp;·&nbsp;
        Claude Haiku + Tavily
      </td>
    </tr>
  </table>
</body>
</html>"""


async def send_digest(
    email_to: str,
    email_from: str,
    smtp_password: str,
    topics: list[str],
) -> dict:
    """
    Main entry point: fetch articles, generate headlines, send email.
    Returns a dict with 'sent_at', 'article_count', and 'recipient'.
    Raises RuntimeError if required params are missing.
    """
    if not email_to:
        raise RuntimeError("email_to is not configured — skipping digest.")
    if not email_from or not smtp_password:
        raise RuntimeError("email_from / smtp_password not configured.")

    logger.info("Fetching digest articles for topics: %s", topics)
    articles = await _fetch_top_articles(topics)

    if not articles:
        raise RuntimeError("No articles found — digest not sent.")

    # Generate headlines concurrently
    import asyncio
    headlines = await asyncio.gather(*[_generate_headline(a) for a in articles])
    pairs = list(zip(articles, headlines))

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%B %-d, %Y")
    subject = f"AI Policy Daily Digest – {now.strftime('%Y-%m-%d')}"

    html_body = _build_html(pairs, date_str)

    # Build MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Send via Gmail SMTP with STARTTLS
    await aiosmtplib.send(
        msg,
        hostname="smtp.gmail.com",
        port=587,
        start_tls=True,
        username=email_from,
        password=smtp_password,
    )

    sent_at = now.isoformat()
    logger.info("Digest sent to %s at %s", email_to, sent_at)
    return {
        "sent_at": sent_at,
        "article_count": len(articles),
        "recipient": email_to,
    }
