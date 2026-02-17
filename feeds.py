import logging
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import feedparser
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; DailyArticleBot/1.0; +https://github.com)"
    )
}
MAX_WORKERS = 10   # feeds en paralelo


def _parse_published(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _extract_with_newspaper(url: str) -> tuple[str, str]:
    try:
        from newspaper import Article
        article = Article(url, request_timeout=10)
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
    return re.sub(r"<[^>]+>", "", raw).strip()


def _fetch_one_feed(feed_url: str, cutoff: datetime, sent_links: set,
                    min_min: float, max_min: float, wpm: int) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of article dicts."""
    try:
        response = requests.get(feed_url, headers=HEADERS, timeout=15, verify=False)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception as exc:
        logger.warning("Failed to fetch feed %s: %s", feed_url, exc)
        return []

    source = _domain(feed_url)
    articles = []

    for entry in feed.entries:
        link = getattr(entry, "link", None)
        if not link or link in sent_links:
            continue

        published = _parse_published(entry)
        if published and published < cutoff:
            continue

        title = getattr(entry, "title", "").strip()
        raw_summary = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        summary = _clean_summary(raw_summary)

        reading_min = _estimate_reading_min(summary, wpm)

        # Only call newspaper3k if summary is very thin AND min_min > 0
        if reading_min < min_min and min_min > 0 and len(summary) < 100:
            text, np_summary = _extract_with_newspaper(link)
            if text:
                reading_min = _estimate_reading_min(text, wpm)
                if not summary and np_summary:
                    summary = np_summary

        # Filter by reading time:
        # - If we have an estimate and it's out of range → skip
        # - If we have NO estimate (reading_min == 0) → include anyway,
        #   the LLM will judge by title + summary
        if reading_min > 0 and (reading_min < min_min or reading_min > max_min):
            continue

        articles.append({
            "title": title,
            "link": link,
            "summary": summary[:500],
            "published": published.isoformat() if published else "",
            "source": source,
            "estimated_reading_min": round(reading_min, 1),
            "reading_time_estimated": reading_min == 0,
        })

    return articles


def fetch_all_feeds(config: dict, state: dict) -> list[dict]:
    """
    Fetch all RSS feeds in parallel using ThreadPoolExecutor.
    Filters by date, reading time, and already-sent links.
    """
    sent_links: set = {item["link"] for item in state.get("sent", [])}
    cutoff  = datetime.now(timezone.utc) - timedelta(days=config["lookback_days"])
    min_min = config["min_reading_minutes"]
    max_min = config["max_reading_minutes"]
    wpm     = config["words_per_minute"]

    all_articles: list[dict] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                _fetch_one_feed, url, cutoff, sent_links, min_min, max_min, wpm
            ): url
            for url in config["feeds"]
        }
        for future in as_completed(futures):
            try:
                articles = future.result()
                all_articles.extend(articles)
            except Exception as exc:
                logger.warning("Unexpected error processing feed: %s", exc)

    logger.info("Total raw candidates after filtering: %d", len(all_articles))
    return all_articles
