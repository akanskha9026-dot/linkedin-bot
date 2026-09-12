"""
Central configuration for the embryology / reproductive-biology LinkedIn bot.

Nothing in this file is a secret. All credentials live in environment
variables (populated from GitHub Secrets in CI, or a local .env when
testing) — see README.md.
"""

import os

# ---------------------------------------------------------------------------
# Secrets / environment variables (never hardcode real values here)
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_PERSON_URN = os.environ.get("LINKEDIN_PERSON_URN", "")  # e.g. urn:li:person:abcXYZ123

# Optional — only needed if you re-run the OAuth script from CI (you won't;
# it's a local one-time step), but harmless to keep here for local testing.
LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:8080/callback")

# ---------------------------------------------------------------------------
# Groq (free tier) model used for selection + writing
# ---------------------------------------------------------------------------
# Groq's free-tier model lineup changes over time. Check https://console.groq.com/docs/models
# and update this if the model below is retired.
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ---------------------------------------------------------------------------
# PubMed E-utilities (free, no key required, but a contact email is required
# by NCBI's usage policy)
# ---------------------------------------------------------------------------
PUBMED_CONTACT_EMAIL = os.environ.get("PUBMED_CONTACT_EMAIL", "your-email@example.com")
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# MeSH-anchored search terms covering both the biological and the
# psychological/psychiatric side of reproduction. Feel free to tune.
PUBMED_QUERIES = [
    '("Embryonic Development"[Mesh]) AND ("2024/01/01"[Date - Publication] : "3000"[Date - Publication])',
    '("Gametogenesis"[Mesh])',
    '("Embryo Implantation"[Mesh])',
    '("Placentation"[Mesh] OR "Placenta"[Mesh])',
    '("Fertilization in Vitro"[Mesh] OR "Reproductive Techniques, Assisted"[Mesh])',
    '("Reproductive Endocrinology"[Mesh] OR "Ovulation Induction"[Mesh])',
    '("Infertility"[Mesh] AND "Psychology"[Subheading])',
    '("Depression, Postpartum"[Mesh] OR "Perinatal Death"[Mesh])',
    '("Grief"[Mesh] AND ("Abortion, Spontaneous"[Mesh] OR "Stillbirth"[Mesh]))',
    '("Genetic Testing"[Mesh] AND "Psychology"[Subheading])',
    '("Oocyte Donation"[Mesh] OR "Sperm Donors"[Mesh])',
]
PUBMED_RETMAX_PER_QUERY = 8

# ---------------------------------------------------------------------------
# Free RSS feeds (journals / societies) to pull from directly
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    # Frontiers in Cell and Developmental Biology
    "https://www.frontiersin.org/journals/cell-and-developmental-biology/rss",
    # Human Reproduction (OUP) latest issue / advance access
    "https://academic.oup.com/rss/site_5127/3247.xml",
    # RBMOnline (Reproductive BioMedicine Online)
    "https://www.rbmojournal.com/current.rss",
    # Fertility and Sterility
    "https://www.fertstert.org/current.rss",
    # bioRxiv — Developmental Biology subject collection
    "https://connect.biorxiv.org/biorxiv_xml.php?subject=developmental_biology",
    # ESHRE news
    "https://www.eshre.eu/rss/news",
    # ASRM news
    "https://www.asrm.org/rss/news/",
    # Archives of Women's Mental Health (Springer)
    "https://link.springer.com/search.rss?query=&facet-journal-id=737",
]

# ---------------------------------------------------------------------------
# Content / style rules enforced in the writing prompt and post-checks
# ---------------------------------------------------------------------------
MIN_WORDS = 120
MAX_WORDS = 220

BANNED_PHRASES = [
    "exciting news",
    "let's dive in",
    "let's explore",
    "furthermore",
    "in today's world",
    "in conclusion",
    "game changer",
    "game-changer",
    "unlock the",
    "delve into",
    "what do you think",
    "thoughts?",
    "thoughts on this",
    "i'd love to hear",
    "drop a comment",
    "stay tuned",
]

# Topics to actively skip even if scientifically on-topic, per your
# instruction not to post unreviewed on active legal/political controversies
# (e.g. abortion law fights, embryo personhood litigation, IVF legislation
# battles). The selection prompt is told to apply this filter itself; this
# list is a secondary keyword safety net.
CONTROVERSY_KEYWORDS = [
    "supreme court", "lawsuit", "litigation", "abortion ban", "abortion law",
    "personhood", "embryo personhood", "criminal charges", "indicted",
    "ballot measure", "legislature", "bill passed", "bill signed",
    "overturn", "roe v. wade", "election",
]

LOG_PATH = "posted_log.json"
