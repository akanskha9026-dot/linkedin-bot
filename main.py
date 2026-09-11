"""
Daily entry point. Run by GitHub Actions on a schedule (see
.github/workflows/daily-post.yml), or manually for local testing.

Flow:
  1. fetch_sources.get_candidates()   — PubMed + RSS, deduped against log
  2. select_and_write.select_candidate() — Groq picks the best one
  3. select_and_write.write_post()    — Groq writes the grounded post
  4. post_linkedin.post_text_update() — posts via LinkedIn's UGC API
  5. Update posted_log.json with the new entry

Exit codes:
  0  — posted successfully, or a "nothing suitable today" no-op (not a failure)
  1  — a real failure (auth, API error, etc.) — GitHub Actions will mark the
       run as failed so you get notified
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import fetch_sources
import post_linkedin
import select_and_write

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("main")

DRY_RUN = "--dry-run" in sys.argv


def load_log() -> dict:
    path = Path(config.LOG_PATH)
    if not path.exists():
        return {"posted_ids": [], "posts": []}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_log(log_data: dict) -> None:
    with Path(config.LOG_PATH).open("w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


def run() -> int:
    log.info("=== Daily embryology/reproductive-biology post run started ===")

    candidates = fetch_sources.get_candidates()
    if not candidates:
        log.info("No fresh candidates found today (all sources exhausted or empty). Exiting cleanly.")
        return 0

    chosen = select_and_write.select_candidate(candidates)
    if chosen is None:
        log.info("AI selection found nothing suitable today. Exiting cleanly (no post made).")
        return 0

    post_text = select_and_write.write_post(chosen)
    word_count = len(post_text.split())
    log.info("Draft ready (%d words):\n%s", word_count, post_text)

    if DRY_RUN:
        log.info("--dry-run flag set: skipping the actual LinkedIn post and log write.")
        return 0

    try:
        result = post_linkedin.post_text_update(post_text)
    except post_linkedin.LinkedInAuthError as exc:
        # Fail LOUDLY and distinctly so the GitHub Action shows a red X and
        # you know it's a token problem, not a code bug.
        log.error("LINKEDIN AUTH FAILURE: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to post to LinkedIn: %s", exc)
        return 1

    log_data = load_log()
    log_data.setdefault("posted_ids", []).append(chosen["id"])
    log_data.setdefault("posts", []).append({
        "id": chosen["id"],
        "source": chosen["source"],
        "title": chosen["title"],
        "url": chosen["url"],
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "linkedin_post_id": result.get("post_id", ""),
        "selection_reasoning": chosen.get("selection_reasoning", ""),
        "post_text": post_text,
    })
    save_log(log_data)
    log.info("Log updated (%d total posts).", len(log_data["posts"]))
    log.info("=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(run())
