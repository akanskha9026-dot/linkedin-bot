"""
Two-step AI pipeline:

  1. SELECT — given today's candidate articles (title + abstract only),
     pick the single best one: real scientific/clinical substance, not
     press-release fluff, and not something tangled up in an active
     legal/political controversy.

  2. WRITE — write the actual LinkedIn post, grounded strictly in that one
     article's title/abstract, in the voice of a practicing expert
     embryologist, following the style rules in config.py.

Both steps are logged so a human can audit *why* an article was chosen.
"""

import logging
import re
import time

import config
import groq_client

log = logging.getLogger("select_and_write")

SELECT_SYSTEM_PROMPT = """You are the editorial filter for a LinkedIn account run by a practicing \
expert embryologist. You will be given a numbered list of candidate articles \
(title + abstract/summary + source). Choose exactly ONE to feature today.

Selection criteria, in order of importance:
1. Real scientific or clinical substance — a finding, mechanism, dataset, or \
clinical outcome. Reject vague press releases, conference announcements with \
no data, opinion pieces with no findings, or anything you cannot summarize \
accurately from the given text alone.
2. Skip anything centered on an active legal or political controversy \
(court cases, abortion/IVF legislation, personhood law, criminal charges, \
elections) — that requires human review before posting, so it is out of \
scope here.
3. Prefer the candidate with the clearest, most concrete, most interesting \
substance for an audience of reproductive medicine / developmental biology \
professionals.
4. If NONE of the candidates are suitable, say so explicitly.

Respond ONLY with a JSON object, no other text:
{
  "suitable": true or false,
  "selected_index": <integer index from the list, or null if none suitable>,
  "reasoning": "<1-3 sentences explaining the choice>"
}
"""

WRITE_SYSTEM_PROMPT = """You are a graduate student studying embryology and reproductive \
biology (something like an MSc in Assisted Reproductive Technology). You occasionally \
post on LinkedIn about studies you find genuinely interesting. You know the science \
well, but you write like a real person sharing something with peers -- curious, \
plain-spoken, occasionally informal -- not like a polished institutional account and \
not like an AI assistant.

Hard rules:
- Base the post STRICTLY on the title and abstract provided. Do not invent, \
extrapolate, or exaggerate findings. If the abstract is preliminary, in vitro, \
animal-model, or small-sample, say so plainly.
- Write 4-5 flowing paragraphs. No bullet points, no numbered lists, no emoji, no \
hashtags inside the body.
- Avoid AI-sounding stock phrases: "exciting news", "let's dive into", "delve into", \
"furthermore", "in today's world", "game changer", "unlock", overused em dashes used \
as a crutch.
- It's fine to include a genuine personal reaction or opinion as a student would.
- End the body with one specific, genuine question inviting readers to share their \
own view or experience related to THIS particular finding -- not a generic closer \
like "What do you think?" or "Thoughts?".
- Body length: between {min_words} and {max_words} words.

Respond ONLY with a JSON object, no other text:
{{
  "headline": "<a short, punchy, plain-sentence-case headline under 12 words, stating the core finding -- no hashtags, no emoji, no quotation marks>",
  "body": "<the 4-5 paragraph post text as described above>",
  "hashtags": ["<5-8 relevant hashtags including the # symbol, mixing 2-3 broad field tags like #Embryology or #ReproductiveBiology with more specific tags tied to this article's actual topic>"]
}}
"""


def _format_candidates(candidates: list) -> str:
    lines = []
    for i, c in enumerate(candidates):
        abstract = c["abstract"][:300]  # trimmed for free-tier TPM limits
        lines.append(
            f"[{i}] SOURCE: {c['source']}\nTITLE: {c['title']}\nABSTRACT: {abstract}\n"
        )
    return "\n".join(lines)


def select_candidate(candidates: list) :
    """Returns the chosen candidate dict (with 'reasoning' attached), or None."""
    if not candidates:
        return None

    candidates = candidates[:20]  # cap batch size to fit free-tier TPM limits
    prompt = _format_candidates(candidates)
    messages = [
        {"role": "system", "content": SELECT_SYSTEM_PROMPT},
        {"role": "user", "content": f"Candidates:\n\n{prompt}\n\nChoose one."},
    ]
    result = None
    for attempt in range(1, 4):
        try:
            result = groq_client.chat_json(messages, temperature=0.2, max_tokens=700, reasoning_effort="low")
            break
        except groq_client.GroqError as exc:
            log.warning("Selection attempt %d failed at the API level (%s); retrying.", attempt, exc)
            time.sleep(6)
    if result is None:
        log.error("Selection failed after 3 attempts; skipping today.")
        return None

    if not result.get("suitable") or result.get("selected_index") is None:
        log.info("No suitable candidate today. Reasoning: %s", result.get("reasoning"))
        return None

    idx = result["selected_index"]
    if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
        log.warning("Groq returned an out-of-range index (%s); skipping today.", idx)
        return None

    chosen = dict(candidates[idx])
    chosen["selection_reasoning"] = result.get("reasoning", "")
    log.info("Selected: [%s] %s", chosen["source"], chosen["title"][:90])
    log.info("Reasoning: %s", chosen["selection_reasoning"])
    return chosen


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


_BOLD_UPPER_BASE = 0x1D5D4
_BOLD_LOWER_BASE = 0x1D5EE
_BOLD_DIGIT_BASE = 0x1D7EC


def _to_bold_unicode(text: str) -> str:
    """Converts plain text to Mathematical Sans-Serif Bold Unicode so it
    renders as bold directly in a LinkedIn post (LinkedIn has no real
    markdown/bold formatting for text posts)."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(_BOLD_UPPER_BASE + (ord(ch) - ord("A"))))
        elif "a" <= ch <= "z":
            out.append(chr(_BOLD_LOWER_BASE + (ord(ch) - ord("a"))))
        elif "0" <= ch <= "9":
            out.append(chr(_BOLD_DIGIT_BASE + (ord(ch) - ord("0"))))
        else:
            out.append(ch)
    return "".join(out)


def _contains_banned_phrase(text: str) :
    lowered = text.lower()
    for phrase in config.BANNED_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def write_post(article: dict, max_attempts: int = 6) -> str:
    """Writes the LinkedIn post (bold headline + body + hashtags), retrying
    if the draft breaks a hard rule (word count, banned phrasing)."""
    system_prompt = WRITE_SYSTEM_PROMPT.format(
        min_words=config.MIN_WORDS, max_words=config.MAX_WORDS
    )
    user_prompt = (
        f"SOURCE: {article['source']}\n"
        f"TITLE: {article['title']}\n"
        f"ABSTRACT: {article['abstract']}\n\n"
        "Write the LinkedIn post now."
    )

    last_body = ""
    for attempt in range(1, max_attempts + 1):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if attempt > 1:
            messages.append({
                "role": "user",
                "content": (
                    f"Your previous draft did not follow the rules "
                    f"({last_body[:60]}...). Rewrite it from scratch, "
                    f"strictly following every rule, especially length "
                    f"({config.MIN_WORDS}-{config.MAX_WORDS} words for the body) "
                    f"and avoiding stock AI phrasing."
                ),
            })

        try:
            result = groq_client.chat_json(messages, temperature=0.7, max_tokens=1500, reasoning_effort="low")
        except groq_client.GroqError as exc:
            log.warning("Draft attempt %d failed at the API level (%s); retrying.", attempt, exc)
            last_body = ""
            time.sleep(6)
            continue
        headline = str(result.get("headline", "")).strip()
        body = str(result.get("body", "")).strip()
        hashtags = result.get("hashtags", [])
        if not isinstance(hashtags, list):
            hashtags = []
        hashtags = [h if str(h).startswith("#") else f"#{h}" for h in hashtags]

        last_body = body
        wc = _word_count(body)
        banned = _contains_banned_phrase(body)

        if headline and body and config.MIN_WORDS <= wc <= config.MAX_WORDS and not banned:
            bold_headline = _to_bold_unicode(headline)
            hashtag_line = " ".join(hashtags[:config.HASHTAG_MAX])
            return f"{bold_headline}\n\n{body}\n\n{hashtag_line}".strip()

        log.warning(
            "Draft attempt %d rejected (word_count=%d, banned_phrase=%s, has_headline=%s)",
            attempt, wc, banned, bool(headline),
        )

    raise RuntimeError(
        f"Could not produce a valid post after {max_attempts} attempts. "
        f"Last body:\n{last_body}"
    )
