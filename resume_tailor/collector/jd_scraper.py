"""JD URL scraper (trafilatura) — the only input that needs scraping.

The GitHub side uses the REST API (structured), but job-description URLs have
no structured API, so trafilatura extracts the article text server-side.
"""
from __future__ import annotations


def scrape_jd_url(url: str) -> str:
    """Fetch a JD URL and return its extracted plain text ("" if empty)."""
    import trafilatura

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    return trafilatura.extract(downloaded) or ""


def extract_jd_html(html: str) -> str:
    """Extract JD text from a raw HTML string (used when we already have HTML)."""
    import trafilatura

    return trafilatura.extract(html) or ""
