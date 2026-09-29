"""
Two-step AI pipeline:

  1. SELECT - given today's candidate stories (mostly recent news, with
     PubMed research only as a fallback), pick the single best one to
     discuss: recent, genuinely about human ART / IVF / embryology, and not
     tangled up in an active legal/political controversy.

  2. WRITE - write the LinkedIn post as an embryology student explaining and
     discussing that story: bold headline, 3-4 short sections with bold
     headings, a closing question inviting readers' views, source line and
     hashtags. Total length 200-250 words (excluding source line + hashtags).

Both steps are logged so a human can audit why a story was chosen.
"""

import logging
import re
import time

import config
import groq_client

log = logging.getLogger("select_and_write")

# Length limits for the visible post text: headline + section headings +
# section paragraphs + closing question (source line and hashtags excluded).
MIN_WORDS = 200
MAX_WORDS = 250
TARGET_WORDS = 225

SELECT_SYSTEM_PROMPT = """You are the editorial filter for a LinkedIn account run by an \
embryology student who explains and discusses recent news in assisted reproductive \
technology (ART), IVF and embryology. You will be given a numbered list of candidate \
stories (kind, date, source, title, summary). Choose exactly ONE to feature today.

Selection criteria, in order of importance:
1. The story must genuinely be about HUMAN assisted reproduction, IVF, embryology, \
fertility treatment or reproductive biology. Reject unrelated items, animal/livestock \
stories, celebrity gossip, clinic advertising, product promotion, listicles and \
generic lifestyle content.
2. Prefer RECENT NEWS (kind = news) with a clear, concrete development that a student \
can explain and discuss: a new technique or study result, a clinical milestone, a \
regulatory or guideline update, a notable trend. Choose a "research" kind story only \
if there is no suitable news.
3. Skip anything centered on an active legal or political controversy (court cases, \
abortion or IVF legislation, personhood law, criminal charges, elections) - that \
needs human review, so it is out of scope.
4. Prefer stories whose text gives enough concrete detail to discuss accurately. Do \
not pick a story you could only discuss by guessing.
5. Prefer topics that would spark discussion among people interested in fertility \
and reproductive science.
6. If NONE of the candidates are suitable, say so explicitly.

Respond ONLY with a JSON object, no other text:
{
  "suitable": true or false,
  "selected_index": <integer index from the list, or null if none suitable>,
  "reasoning": "<1-3 sentences explaining the choice>"
}
"""

WRITE_SYSTEM_PROMPT = """You are a graduate student studying embryology and reproductive \
biology (something like an MSc in Assisted Reproductive Technology). You post on \
LinkedIn about recent news in ART, IVF and embryology, explaining what happened and \
discussing it. You know the science well, but you write like a real person sharing \
something with peers - curious, plain-spoken, a little informal - not like a polished \
institutional account and not like an AI assistant.

Hard rules:
- Ground the post in the TITLE and SUMMARY provided. Every specific claim about THIS \
story (numbers, names, dates, places, quotes, results) must come from that text. Do \
not invent or exaggerate. If the summary only repeats the headline, you know only what \
the headline says. If a finding is preliminary, small, in vitro or animal-based, say so.
- You may explain well-established background science (for example what a blastocyst \
is, or what preimplantation genetic testing does) to help readers understand the \
story. Keep that clearly as background, never as a claim about the story.
- Structure: 3 or 4 sections. Each section has a short heading (2-4 words, sentence \
case, for example "What happened", "The science behind it", "Why it matters", \
"My take") and one short paragraph of 2-4 sentences.
- Include a personal reaction or opinion as a student, in one of the sections.
- Then a closing question: one sentence that invites readers to share their views, \
using a lead-in such as "I'd love to hear your views:" followed by ONE specific \
question tied to THIS story. Never a generic closer like "What do you think?".
- No bullet points, no numbered lists, no emoji, no hashtags inside the text.
- Avoid AI-sounding phrases: "exciting news", "let's dive into", "delve into", \
"furthermore", "in today's world", "game changer", "unlock", and em dashes used as a \
crutch.
- LENGTH: the headline + all section headings + all section paragraphs + the closing \
question must total between __MIN__ and __MAX__ words. Aim for about __TARGET__ words.

Respond ONLY with a JSON object, no other text:
{
  "headline": "<short, punchy, plain-sentence-case headline under 12 words stating the core news - no hashtags, no emoji, no quotation marks>",
  "sections": [
    {"heading": "<2-4 word heading>", "text": "<one short paragraph>"}
  ],
  "closing_question": "<one sentence: lead-in plus one specific question>",
  "hashtags": ["<5-8 hashtags including the # symbol: 2-3 broad tags like #IVF or #Embryology plus more specific tags tied to this story>"]
}
"""


def _format_candidates(candidates: list) -> str:
    lines = []
    for i, c in enumerate(candidates):
        abstract = c["abstract"][:300]  # trimmed for free-tier TPM limits
        lines.append(
            f"[{i}] KIND: {c.get('kind', 'research')} | DATE: {c.get('published', '')} "
            f"| SOURCE: {c['source']}\nTITLE: {c['title']}\nSUMMARY: {abstract}\n"
        )
    return "\n".join(lines)


def select_candidate(candidates: list):
    """Returns the chosen candidate dict (with 'selection_reasoning'), or None."""
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


# ---------------------------------------------------------------------------
# Writing helpers
# ---------------------------------------------------------------------------

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


def _contains_banned_phrase(text: str):
    lowered = text.lower()
    for phrase in getattr(config, "BANNED_PHRASES", []):
        if phrase in lowered:
            return phrase
    return None


def _parse_draft(result: dict):
    """Validate the model's JSON. Returns (headline, sections, closing, hashtags)
    or None if any required piece is missing or malformed."""
    headline = str(result.get("headline", "")).strip()
    closing = str(result.get("closing_question", "")).strip()
    raw_sections = result.get("sections")

    if not headline or not closing or not isinstance(raw_sections, list):
        return None

    sections = []
    for s in raw_sections:
        if not isinstance(s, dict):
            return None
        heading = str(s.get("heading", "")).strip()
        text = str(s.get("text", "")).strip()
        if not heading or not text:
            return None
        sections.append((heading, text))

    if not 3 <= len(sections) <= 4:
        return None

    raw_tags = result.get("hashtags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []
    hashtags = []
    for h in raw_tags:
        tag = re.sub(r"\W", "", str(h).lstrip("#"))
        if tag and f"#{tag}" not in hashtags:
            hashtags.append(f"#{tag}")

    return headline, sections, closing, hashtags


def _draft_word_count(headline: str, sections: list, closing: str) -> int:
    total = _word_count(headline) + _word_count(closing)
    for heading, text in sections:
        total += _word_count(heading) + _word_count(text)
    return total


def _assemble_post(article: dict, headline: str, sections: list, closing: str, hashtags: list) -> str:
    parts = [_to_bold_unicode(headline)]
    for heading, text in sections:
        parts.append(f"{_to_bold_unicode(heading)}\n{text}")
    parts.append(closing)
    parts.append(f"Source: {article['source']}")
    hashtag_max = getattr(config, "HASHTAG_MAX", 8)
    if hashtags:
        parts.append(" ".join(hashtags[:hashtag_max]))
    return "\n\n".join(parts).strip()


def write_post(article: dict, max_attempts: int = 6) -> str:
    """Writes the LinkedIn post (bold headline, headed sections, closing
    question, source line, hashtags), retrying if a draft breaks a hard rule
    (word count, banned phrasing, missing pieces)."""
    system_prompt = (
        WRITE_SYSTEM_PROMPT
        .replace("__MIN__", str(MIN_WORDS))
        .replace("__MAX__", str(MAX_WORDS))
        .replace("__TARGET__", str(TARGET_WORDS))
    )
    user_prompt = (
        f"KIND: {article.get('kind', 'research')}\n"
        f"DATE: {article.get('published', '')}\n"
        f"SOURCE: {article['source']}\n"
        f"TITLE: {article['title']}\n"
        f"SUMMARY: {article['abstract']}\n\n"
        "Write the LinkedIn post now."
    )

    feedback = ""
    last_draft_text = ""
    for attempt in range(1, max_attempts + 1):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if feedback:
            messages.append({"role": "user", "content": feedback})

        try:
            result = groq_client.chat_json(messages, temperature=0.7, max_tokens=1800, reasoning_effort="low")
        except groq_client.GroqError as exc:
            log.warning("Draft attempt %d failed at the API level (%s); retrying.", attempt, exc)
            feedback = ""
            time.sleep(6)
            continue

        parsed = _parse_draft(result)
        if parsed is None:
            log.warning("Draft attempt %d rejected (missing or malformed fields).", attempt)
            feedback = (
                "Your last reply was missing required fields or had the wrong shape. "
                "Return the JSON exactly as specified: headline, 3 or 4 sections "
                "(each with heading and text), closing_question, hashtags."
            )
            continue

        headline, sections, closing, hashtags = parsed
        wc = _draft_word_count(headline, sections, closing)
        full_text = " ".join([headline, closing] + [f"{h} {t}" for h, t in sections])
        last_draft_text = full_text
        banned = _contains_banned_phrase(full_text)

        if MIN_WORDS <= wc <= MAX_WORDS and not banned:
            log.info("Draft accepted on attempt %d (%d words).", attempt, wc)
            return _assemble_post(article, headline, sections, closing, hashtags)

        log.warning("Draft attempt %d rejected (word_count=%d, banned_phrase=%s)", attempt, wc, banned)
        problems = []
        if wc < MIN_WORDS:
            problems.append(
                f"it was {wc} words in total, which is too short; make it longer, aiming for about {TARGET_WORDS}"
            )
        elif wc > MAX_WORDS:
            problems.append(
                f"it was {wc} words in total, which is too long; shorten it, aiming for about {TARGET_WORDS}"
            )
        if banned:
            problems.append(f"it contained the banned phrase '{banned}'; rewrite without it")
        feedback = (
            "Your previous draft was rejected: " + "; ".join(problems) + ". "
            "Rewrite the whole post following every rule. The word total counts the headline, "
            "all headings, all paragraphs and the closing question."
        )

    raise RuntimeError(
        f"Could not produce a valid post after {max_attempts} attempts. "
        f"Last draft text:\n{last_draft_text}"
    )
