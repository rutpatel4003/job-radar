# Job radar

A tracker for new-grad **AI/ML, computer vision, and software engineering** roles in the US. It runs every 3 hours on GitHub Actions in a **private** repo, pings your phone when something new appears, and gives you a password-protected dashboard. The dashboard shows the full job description, the exact sentence behind every sponsorship flag, a match score against your resumes, and an application tracker with statuses and notes.

## What it does every 3 hours

1. It reads about 1,600 company job boards directly: Greenhouse, Ashby, Lever, SmartRecruiters, Workday, Oracle Recruiting Cloud, Eightfold, Workable, Rippling, BambooHR, Recruitee, and Amazon's search API. Examples:
   - OpenAI, Anthropic, xAI, SpaceX, NVIDIA and Stripe
   - Databricks, Waymo, Zoox, Hayden AI and Solace Health
   - JPMorgan, Morgan Stanley, Capital One and Bank of America
   - Microsoft, Qualcomm and Netflix (via Eightfold)
2. It opens custom career sites in a headless browser: Google, Meta, Apple, Microsoft, TikTok, ByteDance, Goldman Sachs, Tesla, Qualcomm, Netflix, AMD, and Uber.
3. It reads the community GitHub lists (Simplify, vanshb03, speedyapply, jobright, zapply) and the free aggregator APIs (Hacker News "Who is hiring", The Muse, RemoteOK, and Adzuna if you add a free key).
4. It keeps only relevant US roles at new-grad level, removes duplicates, spots reposts, and fetches each new job's description. Postings that have no public API (Google, Apple, Microsoft, TikTok links from community lists, and custom sites) are opened in the browser, so their sponsorship, start date, and resume match get checked too.
5. It re-opens roughly an eighth of the open Workday, Oracle, and community-listed postings each run, so every job gets checked about once a day. A posting is marked closed after two "not found" answers.
6. It sends one Telegram message listing the new roles and updates the dashboard.

**Priority companies.** FAANG+, AI labs, autonomy/CV companies, and big banks are listed in `config.yaml → priority_companies`. Their alerts come first and are marked 🔥, and the dashboard has a "Top companies" filter and a location filter (Bay Area, Seattle, New York, Los Angeles, Remote).

**Startups.** The weekly discovery job also pulls Y Combinator's list of currently hiring companies (about 1,400, many in San Francisco) and looks up each one's job board. Hacker News "Who is hiring" covers startups that post nowhere else.

## Privacy and security

- **Private repo.** All job data, your statuses, and your notes live in a **private** GitHub repository. Nobody else can see them.
- **The dashboard page holds no data.** It's a lock screen until you paste a fine-grained GitHub token that can access *only this repo*. Your browser then loads the jobs straight from the private repo through GitHub's API. Anyone who finds the page's URL sees only the lock screen.
- **What the dashboard checks.** It refuses to run if the repo is public or the token can't write.
- **Where the token goes.** It is sent only to `api.github.com`. The page's security policy blocks every other connection, and it doesn't send your dashboard address to job sites when you open a link.
- **Locking.** "Remember on this device" keeps you signed in on your own phone or laptop. **Lock** signs out. On shared computers, untick "Remember".
- **Statuses and notes sync across devices.** Edits save to `data/tracking.json` in the repo. If your phone and laptop both save at the same moment, the two are merged (the newer edit per job wins).

## Setup (about 25 minutes)

**1. GitHub Pro.** This setup is tuned for Pro's 3,000 Actions minutes a month. Pro is free with the GitHub Student Developer Pack.

**2. Create a *private* repo and push this folder.**

```bash
git init && git add -A && git commit -m "job radar"
git branch -M main
git remote add origin git@github.com:<you>/job-radar.git
git push -u origin main
```

**3. Set up Telegram notifications.**

1. Message **@BotFather** in Telegram, send `/newbot`, and copy the token it gives you.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the `chat.id` number.
4. In the repo, go to **Settings → Secrets and variables → Actions** and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

Optional secrets: `NTFY_TOPIC`, `DISCORD_WEBHOOK_URL`, `ADZUNA_APP_ID` + `ADZUNA_APP_KEY`.

**4. Host the dashboard page.** It contains no data, so hosting it publicly is safe. Pick one:

| Option | Steps | Dashboard address |
|---|---|---|
| **A. With GitHub Pro** | Settings → Pages → Deploy from a branch → `main` / `/docs` | `https://<you>.github.io/job-radar/` |
| **B. On the free plan** | Create a second, *public* repo (e.g. `job-radar-app`) containing only `docs/index.html`, then enable Pages on it | `https://<you>.github.io/job-radar-app/` |
| **C. Laptop only** | Double-click `docs/index.html` | Your local file |

Then, under **Settings → Secrets and variables → Actions → Variables**, add `DASHBOARD_URL` with your dashboard address so Telegram alerts link to it.

**5. Create the dashboard token.**

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
2. Repository access: **Only select repositories** → `job-radar`.
3. Permissions → Repository → **Contents: Read and write**. Nothing else.
4. Set an expiration (e.g. 1 year).
5. Open the dashboard and paste the repo name (`<you>/job-radar`) and the token.

Use a separate token per device if you like; each one can be revoked on its own.

**6. Run the workflows once, in this order, from the Actions tab:**

1. **Job tracker (every 3 hours)** with *Only send a test notification* ticked. Your phone should buzz.
2. **H-1B sponsor data (quarterly)**. Takes about 20 minutes.
3. **Discover companies (twice a week)**.
4. **Job tracker (every 3 hours)**. The first real scan saves everything already open without pinging you for each job. The first day's runs spend their spare time filling in descriptions for existing jobs.

### Actions minute budget (GitHub Pro, 3,000 min/month)

| Workflow | When | Minutes/month |
|---|---|---|
| Job tracker (boards, lists, career sites, browser descriptions, re-checks) | every 3 hours, ~10–11.5 min per run | ~2,500 |
| Discover companies | Sundays and Wednesdays | ~60 |
| H-1B data | quarterly | ~10 |
| **Total** | | **~2,570** |

The run stops its optional browser work and re-checks once it reaches `runtime.time_budget_minutes` (10 by default), so a busy day can't blow the budget.

If usage runs high (check **Settings → Billing → Usage**), set `career_pages_every_n_runs: 2` in `config.yaml`. That scans Google, Meta, Apple and the other custom sites every 6 hours and saves about 500 minutes a month.

## How it decides things

**Relevance.** A title must contain a field keyword (ML, CV, or SDE terms) and a role word (engineer, scientist, developer…). Senior, staff, lead, manager, level III+, and internship titles are removed. Titles like "new grad", "university graduate", "early career", or "Engineer I" get a ⭐ and sort first. "Engineer II" is kept but flagged. Roles whose description asks for 5+ years are hidden, and 3+ years are flagged. All of this is set in `config.yaml`.

**Start date.** This is set for a May 2027 graduation and a June 2027 earliest start (`config.yaml → start_date`). Each job's title and description are checked for start dates, start seasons, and graduation windows:

| Status | Examples | What happens |
|---|---|---|
| 📅 **Fits** | "New Grad 2027", "(2027 Start)", "Summer 2027", "graduating Dec 2026 – Aug 2027", "class of 2027", "degree in 2024 or later" | Green badge; sorts like any other job. |
| ⚠️ **Too early** | "New Grad 2026", "December 2026 graduates", "Winter/January 2027 start", "must have graduated by June 2026", "start immediately" | No notification; hidden on the dashboard unless you untick the filter. |
| **Not stated** | Most postings, especially "Software Engineer I" or "University Graduate" roles that hire on a rolling basis | Kept and notified normally. Ask the recruiter about a June 2027 start. |

Any sign that a job fits wins over signs that it doesn't, so you won't lose a role because one sentence mentions 2026. The dashboard shows the sentence each decision came from.

**Duplicates and reposts.** One job can appear on the company's board and in several lists; it is stored once. The job ID comes from the apply URL, so Greenhouse, Ashby, Lever, SmartRecruiters and Workday IDs match across sources. As a fallback, a job counts as a match when company, title, and location are the same.

| Situation | What happens |
|---|---|
| Same role, still open, found somewhere else | Merged as a second link. No ping. |
| Same role reappears after the original closed | 🔁 **Repost** ping, linked to the original date. |
| Same job ID comes back after being taken down | ↩️ **Reopened** ping. |

A job is marked closed after it's missing from its company's full board on two runs in a row.

**Sponsorship.** Community labels are often wrong, so they never decide anything. The order of evidence is:

1. **The job's own description.** Phrases like "unable to sponsor", "without the need for sponsorship", "must be a U.S. citizen", "clearance", or "visa sponsorship is available" set the flag. The dashboard quotes the exact sentence that triggered it.
2. **Clearance or citizenship words in the title** (TS/SCI, clearance, and similar).
3. **Real H-1B filing history.** This is the company's certified H-1B applications for computer occupations (SOC 15-xxxx) from the Department of Labor, including how many were at entry-level wages. It is shown as "H-1B history 1,240" or "No H-1B record".
4. **Community list labels.** These are shown only in the detail panel and marked *unverified*.

The default dashboard filter hides roles that explicitly say no sponsorship or citizens-only. Switch to "Any sponsorship" to see everything.

**Resume match.** The tool counts which recognized tech terms in the description appear on each resume. The skill lists in `config.yaml → profile` were built from your SDE resume; edit the `ML/AI` list to mirror your ML resume. The tool tells you which resume to use and which skills the job asks for that you didn't list. It's keyword overlap, not magic, but it's useful for triage.

## Using the dashboard

The dashboard has two tabs.

**Jobs** is every open role, as a sortable table. Click any column header to sort (click again to reverse); on a phone, use the "Sort" menu. Choose 25, 50, 100, or 200 rows per page. Filters:

- Role type
- Sponsorship
- When the job was found
- Location (Bay Area, Seattle, New York, Los Angeles, Remote)
- Top companies
- New-grad titles
- Starts before June 2027
- Jobs you're already tracking
- Closed postings

Rows with yellow corner brackets are new since your last visit.

**My applications** is your tracker. Status chips (with counts) filter the list. It is sortable by company, status, applied date, follow-up date, last update, and notes. Follow-ups that are due are highlighted. **Export CSV** downloads everything.

**Opening a job** shows:
- The full description.
- The sentence behind each sponsorship and start-date flag.
- H-1B history, pay, and experience requirements.
- Which resume to use.
- A tracking panel:
  - Status: Saved, Applied, Assessment, Interviewing, Offer, Rejected, No response, Withdrawn, or Not interested.
  - Applied date, filled in automatically when you choose Applied.
  - Follow-up date and free-form notes.
  - A dated history of every status change.

Jobs you track stay in your list even after the posting closes. If a company reposts a role you applied to or were rejected from, the alert says so. Roles you marked "Not interested" aren't re-announced when reposted.

**Keyboard:** `j`/`k` move, `Enter` opens details, `o` opens the application, `s` save, `a` applied, `x` not interested, `←`/`→` change page, `Esc` close.

## Customizing

| File | What to change |
|---|---|
| `config.yaml` | Keywords, levels, experience limits, community lists, aggregators, career pages, resume skills. |
| `companies.yaml` | Add a company by name (discovery finds its board), by pasting any job link (`url:` auto-detects the platform), or explicitly. |
| `data/companies.json` | Auto-generated weekly. Don't edit. |

**Career pages.** Sites like Google and Meta change their HTML. Test them locally:

```bash
pip install -r requirements-dev.txt && python -m playwright install chromium
python -m tracker.sources.pagewatch --test            # or: --test Google Meta
```

If a site shows 0 links, open it in your browser, copy one job link, and adjust `link_pattern`. Tesla is disabled by default because it blocks headless browsers.

**Run locally:**

```bash
python -m tracker.run --dry-run --skip-pages     # fetch + filter, print what would be sent, save nothing
python -m tracker.run --dry-run --only waymo,openai
python -m pytest -q                               # offline tests for dedup/repost/closure logic
python -m tracker.notify                          # send a test notification (needs the env vars)
```

## Optional: a daily Claude routine

Pro plans can run 5 Claude Code routines a day, which is too few for the scans themselves but good for a daily review. At claude.ai/code/routines, create a nightly routine on this private repo with a prompt like:

> Read `data/dashboard/jobs.json` and `data/tracking.json`. Take jobs whose `first_seen` is in the last 24 hours, status open, and sponsorship not `no_sponsor`/`citizen`. Rank the top 10 for a May 2027 USC MS CS grad targeting ML/CV/SDE roles. Use the match score, the H-1B history, and the description in `data/dashboard/details/<d>.json`. Also list applications whose follow-up date is today or earlier. For each, say which resume to send (SDE or ML/AI) and give two resume bullets worth emphasizing. Write the result to `digests/<date>.md`.

## Honest limitations

- **The Oracle (JPMorgan, Goldman lateral, AmEx) and Eightfold (Microsoft, Qualcomm, Netflix) readers are experimental.** They use those sites' public JSON endpoints, which couldn't be tested from the build environment. If the run log shows them failing, the career-page watcher and community lists still cover those companies.
- **Workday, Oracle, and custom career sites are searched, not fully listed.** They catch new postings that match your queries. Closed postings are caught by the daily re-check, not instantly.
- **Career-page patterns may need tuning.** They were written without being able to load those sites from the build environment. Run the `--test` command once after setup.
- **A few sites block headless browsers** (Tesla most of the time). Jobs from those sites keep "no description captured" and rely on H-1B history for the sponsorship signal.
- **LinkedIn, Indeed, Glassdoor, Handshake, and Wellfound have no public API and forbid scraping.** Use their own saved-search email alerts alongside this tool.
- **GitHub runs scheduled jobs on a best-effort basis.** Runs can start a few minutes late during busy periods.
