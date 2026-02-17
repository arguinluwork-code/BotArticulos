import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import feedparser
import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DailyArticleBot/1.0; +https://github.com)"
    )
}


def _parse_published(entry) -> datetime | None:
    """Return a timezone-aware datetime from a feedparser entry, or None."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _extract_with_newspaper(url: str) -> tuple[str, str]:
    """
    Try to download and parse an article with newspaper3k.
    Returns (text, summary). Both may be empty strings on failure.
    """
    try:
        from newspaper import Article

        article = Article(url, request_timeout=15)
        article.download()
        article.parse()
        article.nlp()
        return article.text or "", article.summary or ""
    except Exception as exc:
        logger.debug("newspaper3k failed for %s: %s", url, exc)
        return "", ""


def _estimate_reading_min(text: str, wpm: int) -> float:
    words = len(text.split())
    return words / wpm if words else 0.0


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url


def _clean_summary(raw: str) -> str:
    """Strip basic HTML tags from a summary string."""
    import re
    return re.sub(r"<[^>]+>", "", raw).strip()


def fetch_all_feeds(config: dict, state: dict) -> list[dict]:
    """
    Fetch articles from all RSS feeds in config.
    Filters by date window, reading time range, and already-sent links.
    Returns a list of article dicts.
    """
    sent_links: set = {item["link"] for item in state.get("sent", [])}

    lookback = timedelta(days=config["lookback_days"])
    cutoff = datetime.now(timezone.utc) - lookback
    min_min = config["min_reading_minutes"]
    max_min = config["max_reading_minutes"]
    wpm = config["words_per_minute"]

    articles: list[dict] = []

    for feed_url in config["feeds"]:
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", feed_url, exc)
            continue

        source = _domain(feed_url)

        for entry in feed.entries:
            link = getattr(entry, "link", None)
            if not link:
                continue
            if link in sent_links:
                continue

            published = _parse_published(entry)
            if published and published < cutoff:
                continue

            title = getattr(entry, "title", "").strip()
            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            summary = _clean_summary(raw_summary)

            # Estimate reading time from RSS summary first
            reading_min = _estimate_reading_min(summary, wpm)
            estimated = True  # assume estimated unless we get full text

            # If summary is thin, try newspaper3k for full text
            if reading_min < min_min:
                text, np_summary = _extract_with_newspaper(link)
                if text:
                    reading_min = _estimate_reading_min(text, wpm)
                    estimated = False
                    if not summary and np_summary:
                        summary = np_summary

            # Filter by reading time — if we genuinely can't estimate, include anyway
            if reading_min > 0:
                if reading_min < min_min or reading_min > max_min:
                    continue

            articles.append({
                "title": title,
                "link": link,
                "summary": summary[:500],
                "published": published.isoformat() if published else "",
                "source": source,
                "estimated_reading_min": round(reading_min, 1),
                "reading_time_estimated": estimated,
            })

    logger.info("Total raw candidates after filtering: %d", len(articles))
    return articles
