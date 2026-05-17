"""Article content fetcher.

The scanner gives us RSS headlines and links. When the AI operator needs to
read what an article actually says (to verify a structural keyword, to
summarize for the user, to convert into a research/ note), this module
pulls the article body.

Approach: best-effort HTML strip with a tiny readability heuristic. No heavy
dependencies. If a site is JS-rendered or paywalled, we surface the failure
rather than fabricate content.

The AI operator is responsible for interpreting the content. The Python
engine does not analyze article text — that's an LLM job.
"""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ArticleFetchResult:
    url: str
    final_url: str
    title: Optional[str]
    text: Optional[str]
    char_count: int
    error: Optional[str] = None


def fetch_article(url: str, timeout: int = 25, max_chars: int = 8000) -> ArticleFetchResult:
    """Fetch an article and return a plain-text body (best-effort).

    Returns a result with an `error` field set if fetch or parse fails. The
    caller (AI operator) should check `error` and decide what to do.
    """
    if not url or not url.startswith(("http://", "https://")):
        return ArticleFetchResult(url=url, final_url=url, title=None, text=None, char_count=0, error="invalid url")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; fonbot-article-fetcher/1.0)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            content_type = response.headers.get("content-type") or ""
            raw = response.read()
    except Exception as exc:
        return ArticleFetchResult(url=url, final_url=url, title=None, text=None, char_count=0, error=f"{type(exc).__name__}: {exc}")
    encoding = "utf-8"
    match = re.search(r"charset=([\w\-]+)", content_type)
    if match:
        encoding = match.group(1)
    try:
        raw_text = raw.decode(encoding, errors="replace")
    except LookupError:
        raw_text = raw.decode("utf-8", errors="replace")
    title = _extract_title(raw_text)
    body = _extract_text(raw_text)
    if not body.strip():
        return ArticleFetchResult(url=url, final_url=final_url, title=title, text=None, char_count=0, error="no readable text extracted (likely JS-rendered or paywalled)")
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "\n\n[truncated]"
    return ArticleFetchResult(url=url, final_url=final_url, title=title, text=body, char_count=len(body))


def _extract_title(raw: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = html.unescape(match.group(1).strip())
    return re.sub(r"\s+", " ", title)[:300] or None


def _extract_text(raw: str) -> str:
    # Strip scripts and styles first.
    raw = re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<style[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<nav[^>]*>.*?</nav>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<header[^>]*>.*?</header>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<footer[^>]*>.*?</footer>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"<aside[^>]*>.*?</aside>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    # Prefer article/main if present
    article = re.search(r"<article[^>]*>(.*?)</article>", raw, re.IGNORECASE | re.DOTALL)
    if article:
        body = article.group(1)
    else:
        main = re.search(r"<main[^>]*>(.*?)</main>", raw, re.IGNORECASE | re.DOTALL)
        body = main.group(1) if main else raw
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"</p>", "\n\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()
