# Embryology / Reproductive Biology LinkedIn Bot

A fully autonomous daily LinkedIn poster covering embryology, reproductive
biology, assisted reproductive technology, reproductive endocrinology, and
reproductive/perinatal psychology. It sources from PubMed and a fixed pool
of free journal/society RSS feeds, uses Groq's free-tier API to select and
write each post grounded strictly in the source abstract, and posts via
LinkedIn's **official** UGC Posts API — no scraping, no fake-login
automation.

Runs daily via GitHub Actions. Zero paid tools required.

---

## How it works

```
fetch_sources.py   → pulls candidate articles from PubMed + RSS feeds,
                      filters out anything already in posted_log.json
select_and_write.py→ Groq selects the single best candidate, then writes
                      the post text grounded in that article's abstract
post_linkedin.py    → posts the text via LinkedIn's official UGC Posts API
main.py             → orchestrates the above, updates posted_log.json
.github/workflows/  → runs main.py daily and commits the updated log
```

There is no manual-approval step in the daily run — per your spec, it posts
automatically once a day. Review the code and run a few `--dry-run`s before
you turn on the schedule (see "Testing locally" below).

---

## Part 1 — Things only YOU can do (LinkedIn requires the account owner)

### 1.1 Create a LinkedIn Developer App

1. Go to <https://www.linkedin.com/developers/apps> and click **Create app**.
2. Fill in the app name, and link it to a **Company Page**. If you don't
   have one, create a placeholder Company Page first (Developer Apps must
   be attached to a page, even a personal-project one) — it takes two
   minutes.
3. Submit the app.

### 1.2 Request the right products

In your new app, go to the **Products** tab and request:

- **Share on LinkedIn** — lets the app post on your behalf (`w_member_social`
  scope). Usually auto-approved.
- **Sign In with LinkedIn using OpenID Connect** — lets the app fetch your
  person ID (`openid profile` scopes). Instant approval.

### 1.3 Set your redirect URL and get your credentials

In the **Auth** tab:

1. Under "OAuth 2.0 settings", add an **Authorized redirect URL**. Anything
   works as long as it matches what you use in step 2 below — the simplest
   choice is:
   ```
   http://localhost:8080/callback
   ```
2. Copy the **Client ID** and **Client Secret** shown on this page. You'll
   need them in a minute, but they never get committed to the repo or sent
   to me — only pasted into your own terminal.

### 1.4 Run the one-time OAuth script yourself

This is the step LinkedIn requires *you* to do — no one else can authorize
access to your account.

```bash
git clone <your-repo-url>
cd linkedin-embryology-bot
pip install -r requirements.txt

export LINKEDIN_CLIENT_ID=your_client_id
export LINKEDIN_CLIENT_SECRET=your_client_secret
export LINKEDIN_REDIRECT_URI=http://localhost:8080/callback   # must match step 1.3

python oauth_local_setup.py
```

The script will:

1. Print an authorization URL.
2. You open it, log in to LinkedIn, and click **Allow**.
3. LinkedIn redirects your browser to `http://localhost:8080/callback?code=...`.
   The page itself will probably fail to load (nothing is listening on that
   port) — that's fine, you only need the URL from your address bar.
4. Paste that full URL back into the terminal when prompted.
5. The script exchanges the code for an access token and looks up your
   person URN, then prints both.

You'll get output like:

```
LINKEDIN_ACCESS_TOKEN = AQV...
LINKEDIN_PERSON_URN   = urn:li:person:abcXYZ123
```

**LinkedIn access tokens obtained this way expire after about 60 days.**
When that happens, the daily workflow will fail loudly (a clear
`LinkedInAuthError` in the Actions log, and the run will show as failed) —
that's your cue to re-run `oauth_local_setup.py` and update the secret
below.

### 1.5 Get a free Groq API key

1. Go to <https://console.groq.com> and sign up (free).
2. Create an API key under **API Keys**.
3. Groq's free tier is generous enough for one selection call + one writing
   call per day.

### 1.6 Add your GitHub Secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add:

| Secret name              | Value                                      |
|---------------------------|---------------------------------------------|
| `GROQ_API_KEY`             | from step 1.5                              |
| `LINKEDIN_ACCESS_TOKEN`    | from step 1.4                              |
| `LINKEDIN_PERSON_URN`      | from step 1.4                              |
| `PUBMED_CONTACT_EMAIL`     | your email (NCBI requires a contact email for E-utilities) |

### 1.7 Push the repo to GitHub

Make the repo **public** — GitHub Actions minutes are unlimited/free for
public repos, but capped on private ones.

```bash
git init
git add .
git commit -m "Initial commit: embryology LinkedIn bot"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

The workflow in `.github/workflows/daily-post.yml` will start running on
its schedule (13:00 UTC by default — edit the `cron` line to change it), and
you can also trigger it manually from the **Actions** tab via
"Run workflow".

---

## Part 2 — Testing locally before it goes live

```bash
export GROQ_API_KEY=...
export LINKEDIN_ACCESS_TOKEN=...
export LINKEDIN_PERSON_URN=...
export PUBMED_CONTACT_EMAIL=you@example.com

# See what candidates it would pull today:
python fetch_sources.py

# Full dry run — fetches, selects, and writes a post, but does NOT post to
# LinkedIn and does NOT touch posted_log.json:
python main.py --dry-run
```

Read the printed draft carefully a few times before removing `--dry-run`
and letting it post for real. Once you're confident in the output quality,
either wait for the schedule or trigger the workflow manually.

---

## What to send back once your part is done

Nothing needs to come back to me — everything after your OAuth run is
self-contained in the repo. Once you've:

1. Added the four GitHub Secrets (§1.6), and
2. Pushed the repo (§1.7),

the bot is live. If you want help after that, the useful things to share
are:

- Any error output from a `python main.py --dry-run` run, or
- A GitHub Actions run log URL if a scheduled run fails,

so the actual failure text (auth error, empty candidate list, Groq error,
etc.) can be diagnosed precisely.

---

## Notes / things to keep an eye on

- **Token expiry**: re-run `oauth_local_setup.py` roughly every 60 days, or
  whenever a run fails with a `LinkedInAuthError`.
- **Source pool**: `config.py` has the RSS feed list and PubMed MeSH
  queries. Some journal RSS URLs occasionally change — if a feed starts
  returning nothing, check the journal's site for its current feed URL.
- **Groq model name**: `config.py` defaults to `llama-3.3-70b-versatile`.
  Check <https://console.groq.com/docs/models> if Groq retires it and set
  `GROQ_MODEL` as a repo secret/variable to override.
- **Controversy filter**: the AI selection step is instructed to skip
  articles tangled up in active legal/political fights (litigation,
  legislation, elections), per your spec that those need human review. A
  keyword list in `config.py` (`CONTROVERSY_KEYWORDS`) exists as a
  secondary reference if you want to harden this further — it isn't
  currently wired in as an automatic filter, so treat the AI's judgment
  call as the primary safeguard and revisit this if you want stricter
  automatic exclusion.
- **No repeats**: `posted_log.json` is the single source of truth for what's
  already been posted, and the workflow commits it back after every run
  that posts something.
