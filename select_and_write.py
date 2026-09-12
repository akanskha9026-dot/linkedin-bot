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

WRITE_SYSTEM_PROMPT = """You are a practicing expert embryologist who occasionally writes short \
LinkedIn posts about developments in embryology, reproductive biology, and \
reproductive/perinatal psychology. You are writing in your own professional \
voice — not as a marketing account, not as an AI assistant.

Hard rules:
- Base the post STRICTLY on the title and abstract provided. Do not invent, \
extrapolate, or exaggerate findings. If the abstract is preliminary, in \
vitro, animal-model, or small-sample, say so plainly rather than implying a \
stronger or more clinical result than was shown.
- Write in flowing paragraphs. No bullet points, no numbered lists, no \
hashtags, no emoji.
- Avoid AI-sounding stock phrases entirely, including but not limited to: \
"exciting news", "let's dive into", "delve into", "furthermore", "in \
today's world", "game changer", "unlock", generic closing questions like \
"What do you think?", and overused em dashes used as a crutch.
- It is fine, occasionally, to include a brief, genuine professional opinion \
or reaction — but the post should stay grounded and substantive, not \
promotional.
- Do not add a call to action, a question to readers, or a hashtag block at \
the end. End the post when the thought is finished.
- Length: between {min_words} and {max_words} words.

Respond ONLY with the post text itself — no title, no preamble, no quotation \
marks around it, no explanation.
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
    result = groq_client.chat_json(messages, temperature=0.2)

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


def _contains_banned_phrase(text: str) :
    lowered = text.lower()
    for phrase in config.BANNED_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def write_post(article: dict, max_attempts: int = 3) -> str:
    """Writes the LinkedIn post text for the chosen article, retrying if the
    draft breaks a hard rule (word count, banned phrasing)."""
    system_prompt = WRITE_SYSTEM_PROMPT.format(
        min_words=config.MIN_WORDS, max_words=config.MAX_WORDS
    )
    user_prompt = (
        f"SOURCE: {article['source']}\n"
        f"TITLE: {article['title']}\n"
        f"ABSTRACT: {article['abstract']}\n\n"
        "Write the LinkedIn post now."
    )

    last_draft = ""
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
                    f"({last_draft[:60]}...). Rewrite it from scratch, "
                    f"strictly following every rule, especially length "
                    f"({config.MIN_WORDS}-{config.MAX_WORDS} words) and "
                    f"avoiding stock AI phrasing."
                ),
            })

        draft = groq_client.chat(messages, temperature=0.6).strip()
        last_draft = draft

        wc = _word_count(draft)
        banned = _contains_banned_phrase(draft)

        if config.MIN_WORDS <= wc <= config.MAX_WORDS and not banned:
            return draft

        log.warning(
            "Draft attempt %d rejected (word_count=%d, banned_phrase=%s)",
            attempt, wc, banned,
        )

    raise RuntimeError(
        f"Could not produce a valid post after {max_attempts} attempts. "
        f"Last draft:\n{last_draft}"
    )
