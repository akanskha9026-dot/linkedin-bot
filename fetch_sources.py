"""
Pulls candidate stories for the daily LinkedIn post.

Priority order:
  1. RECENT NEWS about ART / IVF / embryology / fertility (Google News RSS
     searches + a few health-news RSS feeds), limited to the last
     NEWS_MAX_AGE_DAYS days.
  2. PubMed research abstracts, only as a FALLBACK if there is too little news.

Anything whose stable ID already appears in posted_log.json is filtered out.

Each candidate is normalized to:
    {
        "id": str,          # stable dedupe key (news:<url> or pmid:<id>)
        "source": str,      # publisher / source name
        "title": str,
        "abstract": str,    # news summary, or PubMed abstract
        "url": str,
        "published": str,   # ISO date if known, else ""
        "kind": str,        # "news" or "research"
    }
"""

import calendar
import datetime
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_sources")

# ---------------------------------------------------------------------------
# News settings (kept here so config.py does not need to change)
# ---------------------------------------------------------------------------

NEWS_MAX_AGE_DAYS = 7
MIN_NEWS_CANDIDATES = 5  # below this, PubMed research is added as a fallback

GOOGLE_NEWS_QUERIES = [
    "IVF",
    "in vitro fertilization",
    "embryo",
    "embryology",
    "assisted reproductive technology",
    "fertility clinic",
    "egg freezing",
    "preimplantation genetic testing",
    "infertility treatment",
    "IVF law OR policy OR court",
]

# Extra direct news feeds. If one is down or the URL changes, it is skipped
# with a warning and the others still work.
EXTRA_NEWS_FEEDS = [
    ("ScienceDaily Fertility", "https://www.sciencedaily.com/rss/health_medicine/fertility.xml"),
    ("Medical Xpress IVF", "https://medicalxpress.com/rss-feed/search/?search=IVF&searchtype=news"),
    ("Medical Xpress Embryo", "https://medicalxpress.com/rss-feed/search/?search=embryo&searchtype=news"),
]

USER_AGENT = "Mozilla/5.0 (compatible; embryology-linkedin-bot)"


def load_posted_log() -> dict:
    path = Path(config.LOG_PATH)
    if not path.exists():
        return {"posted_ids": [], "posts": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _entry_datetime(entry):
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.datetime.fromtimestamp(calendar.timegm(parsed), tz=datetime.timezone.utc)


def _parse_feed(url: str):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _title_key(title: str) -> str:
    """Normalized title used to collapse the same story from several outlets."""
    return re.sub(r"[^a-z0-9]", "", title.lower())[:60]


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def _google_news_url(query: str) -> str:
    q = quote_plus(f"{query} when:{NEWS_MAX_AGE_DAYS}d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _entries_to_candidates(parsed, feed_name: str, is_google: bool, posted_log: dict) -> list:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=NEWS_MAX_AGE_DAYS)
    results = []

    for entry in parsed.entries:
        link = entry.get("link", "")
        if not link:
            continue

        article_id = f"news:{link}"
        if article_id in posted_log.get("posted_ids", []):
            continue

        published_dt = _entry_datetime(entry)
        if published_dt is not None and published_dt < cutoff:
            continue

        title = _clean(entry.get("title", ""))
        if not title:
            continue

        source = feed_name
        if is_google and " - " in title:
            title, source = title.rsplit(" - ", 1)
            title, source = title.strip(), source.strip()

        summary = _clean(entry.get("summary", "") or entry.get("description", ""))
        # Google News summaries usually just repeat the headline.
        abstract = summary if len(summary) > len(title) + 30 else title

        results.append({
            "id": article_id,
            "source": source,
            "title": title,
            "abstract": abstract,
            "url": link,
            "published": published_dt.date().isoformat() if published_dt else "",
            "kind": "news",
        })
    return results


def fetch_news_candidates(posted_log: dict) -> list:
    results = []

    for query in GOOGLE_NEWS_QUERIES:
        try:
            parsed = _parse_feed(_google_news_url(query))
            found = _entries_to_candidates(parsed, "Google News", True, posted_log)
            log.info("Google News %r -> %d recent items", query, len(found))
            results.extend(found)
        except Exception as exc:  # noqa: BLE001
            log.warning("Google News query %r failed: %s", query, exc)
        time.sleep(0.5)

    for name, url in EXTRA_NEWS_FEEDS:
        try:
            parsed = _parse_feed(url)
            found = _entries_to_candidates(parsed, name, False, posted_log)
            log.info("%s -> %d recent items", name, len(found))
            results.extend(found)
        except Exception as exc:  # noqa: BLE001
            log.warning("News feed %s failed: %s", name, exc)

    # Newest first
    results.sort(key=lambda c: c["published"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# PubMed (fallback only)
# ---------------------------------------------------------------------------

def _pubmed_esearch(query: str) -> list:
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": config.PUBMED_RETMAX_PER_QUERY,
        "sort": "pub+date",
        "retmode": "json",
        "email": config.PUBMED_CONTACT_EMAIL,
        "tool": "embryology-linkedin-bot",
    }
    resp = requests.get(config.PUBMED_ESEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _pubmed_efetch(pmids: list) -> list:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "email": config.PUBMED_CONTACT_EMAIL,
        "tool": "embryology-linkedin-bot",
    }
    resp = requests.get(config.PUBMED_EFETCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    articles = []
    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//PMID")
        pmid = pmid_el.text.strip() if pmid_el is not None else None
        if not pmid:
            continue

        title_el = art.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""

        abstract_parts = art.findall(".//Abstract/AbstractText")
        abstract = " ".join("".join(p.itertext()).strip() for p in abstract_parts).strip()

        year_el = art.find(".//JournalIssue/PubDate/Year")
        medline_date_el = art.find(".//JournalIssue/PubDate/MedlineDate")
        published = ""
        if year_el is not None:
            published = year_el.text.strip()
        elif medline_date_el is not None:
            published = medline_date_el.text.strip()

        if not title or not abstract:
            continue

        articles.append({
            "id": f"pmid:{pmid}",
            "source": "PubMed",
            "title": title,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "published": published,
            "kind": "research",
        })
    return articles


def fetch_pubmed_candidates(posted_log: dict) -> list:
    results = []
    for query in config.PUBMED_QUERIES:
        try:
            pmids = _pubmed_esearch(query)
        except Exception as exc:  # noqa: BLE001
            log.warning("PubMed esearch failed for query %r: %s", query, exc)
            continue

        pmids = [p for p in pmids if f"pmid:{p}" not in posted_log.get("posted_ids", [])]
        if not pmids:
            continue

        try:
            articles = _pubmed_efetch(pmids)
        except Exception as exc:  # noqa: BLE001
            log.warning("PubMed efetch failed for query %r: %s", query, exc)
            continue

        results.extend(articles)
        time.sleep(0.4)
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_candidates() -> list:
    """Return a deduplicated list of not-yet-posted candidates, news first."""
    posted_log = load_posted_log()

    news = fetch_news_candidates(posted_log)

    # Collapse the same story reported by several outlets
    seen_titles = set()
    unique_news = []
    for c in news:
        key = _title_key(c["title"])
        if key in seen_titles:
            continue
        seen_titles.add(key)
        unique_news.append(c)

    research = []
    if len(unique_news) < MIN_NEWS_CANDIDATES:
        log.info("Only %d news items found; adding PubMed research as fallback", len(unique_news))
        research = fetch_pubmed_candidates(posted_log)

    all_candidates = unique_news + research

    seen_ids = set()
    unique_candidates = []
    for c in all_candidates:
        if c["id"] in posted_log.get("posted_ids", []):
            continue
        if c["id"] in seen_ids:
            continue
        seen_ids.add(c["id"])
        unique_candidates.append(c)

    log.info("Fetched %d fresh candidates (%d news, %d research)",
             len(unique_candidates), len(unique_news), len(research))
    return unique_candidates


if __name__ == "__main__":
    candidates = get_candidates()
    for c in candidates[:10]:
        print(f"- [{c['kind']}] [{c['source']}] {c['published']}  {c['title'][:90]}")
    print(f"\nTotal fresh candidates: {len(candidates)}")
