"""
Pulls candidate articles from:
  1. PubMed E-utilities (esearch + efetch) across the configured MeSH queries
  2. A fixed pool of free RSS feeds (ESHRE, ASRM, bioRxiv, Frontiers, Human
     Reproduction, Fertility and Sterility, RBMOnline, Archives of Women's
     Mental Health)

and filters out anything whose stable ID already appears in posted_log.json.

Each candidate is normalized to:
    {
        "id": str,          # stable dedupe key (PMID or article URL)
        "source": str,       # human-readable source name
        "title": str,
        "abstract": str,     # abstract text, or RSS summary if no abstract
        "url": str,
        "published": str,    # ISO date if known, else ""
    }
"""

import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import feedparser
import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch_sources")


def load_posted_log() -> dict:
    path = Path(config.LOG_PATH)
    if not path.exists():
        return {"posted_ids": [], "posts": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _already_posted(article_id: str, posted_log: dict) -> bool:
    return article_id in posted_log.get("posted_ids", [])


# ---------------------------------------------------------------------------
# PubMed
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
    data = resp.json()
    return data.get("esearchresult", {}).get("idlist", [])


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
            # Skip anything without a real abstract — we need grounded text,
            # not just a title, to write an accurate post.
            continue

        articles.append({
            "id": f"pmid:{pmid}",
            "source": "PubMed",
            "title": title,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "published": published,
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
        time.sleep(0.4)  # be polite to NCBI's rate limits (free tier: ~3 req/s)

    return results


# ---------------------------------------------------------------------------
# RSS feeds
# ---------------------------------------------------------------------------

def fetch_rss_candidates(posted_log: dict) -> list:
    results = []
    for feed_url in config.RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to parse feed %s: %s", feed_url, exc)
            continue

        if getattr(parsed, "bozo", False) and not parsed.entries:
            log.warning("Feed %s returned no entries (bozo=%s)", feed_url, parsed.bozo_exception)
            continue

        source_name = parsed.feed.get("title", feed_url)

        for entry in parsed.entries:
            article_url = entry.get("link", "")
            if not article_url:
                continue
            article_id = f"url:{article_url}"
            if _already_posted(article_id, posted_log):
                continue

            title = entry.get("title", "").strip()
            summary = entry.get("summary", "") or entry.get("description", "")
            summary = summary.strip()

            if not title or not summary:
                continue

            published = entry.get("published", "") or entry.get("updated", "")

            results.append({
                "id": article_id,
                "source": source_name,
                "title": title,
                "abstract": summary,
                "url": article_url,
                "published": published,
            })

    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_candidates() -> list:
    """Return a deduplicated list of not-yet-posted candidate articles."""
    posted_log = load_posted_log()

    pubmed_candidates = fetch_pubmed_candidates(posted_log)
    rss_candidates = fetch_rss_candidates(posted_log)

    all_candidates = pubmed_candidates + rss_candidates

    # Extra safety: drop anything that slipped through with an id already logged
    seen_ids = set()
    unique_candidates = []
    for c in all_candidates:
        if c["id"] in posted_log.get("posted_ids", []):
            continue
        if c["id"] in seen_ids:
            continue
        seen_ids.add(c["id"])
        unique_candidates.append(c)

    log.info("Fetched %d fresh candidates (%d PubMed, %d RSS)",
              len(unique_candidates), len(pubmed_candidates), len(rss_candidates))
    return unique_candidates


if __name__ == "__main__":
    candidates = get_candidates()
    for c in candidates[:5]:
        print(f"- [{c['source']}] {c['title'][:90]}")
    print(f"\nTotal fresh candidates: {len(candidates)}")
