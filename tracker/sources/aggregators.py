"""Free job aggregators with public APIs.

  hn        – Hacker News "Ask HN: Who is hiring?" monthly thread (Algolia API, no key). Great for startups.
  themuse   – The Muse public API, entry-level software/data roles (no key).
  remoteok  – RemoteOK public feed (remote roles).
  adzuna    – Adzuna US search API. Huge coverage (pulls from thousands of boards). Needs a FREE key:
              set ADZUNA_APP_ID and ADZUNA_APP_KEY secrets (https://developer.adzuna.com).
LinkedIn / Indeed / Glassdoor / Handshake have no public API and forbid scraping — use their own email alerts.
"""
import html
import re
from urllib.parse import quote

from ..config import env
from ..filters import html_to_text
from ..http import get_json
from ..models import FetchResult, Job


def _res(board, name, fn):
    try:
        return FetchResult(board, name, fn(), complete=False)
    except Exception as e:
        return FetchResult(board, name, [], ok=False, error=f"{type(e).__name__}: {e}"[:200])


# ── Hacker News: Who is hiring ──
def _hn():
    stories = get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=10")
    story = next((h for h in stories.get("hits", []) if "who is hiring" in (h.get("title") or "").lower()), None)
    if not story:
        return []
    item = get_json(f"https://hn.algolia.com/api/v1/items/{story['objectID']}", timeout=60)
    jobs = []
    for ch in item.get("children") or []:
        text = ch.get("text") or ""
        if not text:
            continue
        plain = html_to_text(text)
        first = plain.strip().split("\n", 1)[0]
        parts = [html.unescape(p).strip() for p in re.split(r"\s*\|\s*", first) if p.strip()]
        if len(parts) < 2:
            continue
        company = re.sub(r"\(.*?\)", "", parts[0]).strip()[:80]
        jobs.append(Job(
            uid=f"hn:{ch['id']}", company=company,
            title=" | ".join(parts[1:])[:200],       # run.py picks the best matching segment
            url=f"https://news.ycombinator.com/item?id={ch['id']}",
            locations=[p for p in parts[1:] if re.search(r"remote|onsite|hybrid|,|\b[A-Z]{2}\b|usa|us\b|sf|nyc", p, re.I)][:2],
            source="hn", board="aggregator:hn", posted_at=ch.get("created_at"), description=plain,
        ))
    return jobs


# ── The Muse ──
def _themuse(cfg):
    jobs = {}
    cats = ["Software Engineering", "Data and Analytics", "Data Science"]
    q = "&".join(f"category={quote(c)}" for c in cats)
    for page in range(cfg.get("themuse_pages", 5)):
        data = get_json(f"https://www.themuse.com/api/public/jobs?{q}&level=Entry%20Level&page={page}")
        for r in data.get("results") or []:
            jid = r.get("id")
            if not jid or jid in jobs:
                continue
            jobs[jid] = Job(
                uid=f"muse:{jid}", company=(r.get("company") or {}).get("name") or "",
                title=(r.get("name") or "").strip(),
                url=(r.get("refs") or {}).get("landing_page") or f"https://www.themuse.com/jobs/{jid}",
                locations=[l.get("name") for l in r.get("locations") or [] if l.get("name")],
                source="themuse", board="aggregator:themuse", posted_at=r.get("publication_date"),
                description=html_to_text(r.get("contents")))
        if page + 1 >= (data.get("page_count") or 0):
            break
    return list(jobs.values())


# ── RemoteOK ──
def _remoteok():
    data = get_json("https://remoteok.com/api")
    jobs = []
    for r in data[1:] if isinstance(data, list) else []:
        if not r.get("id"):
            continue
        jobs.append(Job(
            uid=f"remoteok:{r['id']}", company=r.get("company") or "", title=(r.get("position") or "").strip(),
            url=r.get("url") or f"https://remoteok.com/remote-jobs/{r['id']}",
            locations=[r.get("location") or "Remote"], source="remoteok", board="aggregator:remoteok",
            posted_at=r.get("date"), description=html_to_text(r.get("description"))))
    return jobs


# ── Adzuna ──
def _adzuna(cfg):
    app_id, key = env("ADZUNA_APP_ID"), env("ADZUNA_APP_KEY")
    if not (app_id and key):
        return []
    jobs = {}
    for what in cfg.get("adzuna_queries", []):
        for page in (1, 2):
            data = get_json(f"https://api.adzuna.com/v1/api/jobs/us/search/{page}?app_id={app_id}&app_key={key}"
                            f"&what={quote(what)}&max_days_old=3&results_per_page=50&sort_by=date"
                            f"&content-type=application/json")
            for r in data.get("results") or []:
                jid = str(r.get("id"))
                if jid in jobs:
                    continue
                jobs[jid] = Job(
                    uid=f"adzuna:{jid}", company=(r.get("company") or {}).get("display_name") or "",
                    title=html.unescape(r.get("title") or "").strip(), url=r.get("redirect_url") or "",
                    locations=[(r.get("location") or {}).get("display_name") or ""], source="adzuna",
                    board="aggregator:adzuna", posted_at=r.get("created"), country="US",
                    description=html_to_text(r.get("description")))  # Adzuna only gives a snippet
            if len(data.get("results") or []) < 50:
                break
    return list(jobs.values())


def fetch_all(cfg):
    a = cfg.get("aggregators", {})
    out = []
    if a.get("hn", True):
        out.append(_res("aggregator:hn", "HN Who's Hiring", _hn))
    if a.get("themuse", True):
        out.append(_res("aggregator:themuse", "The Muse", lambda: _themuse(a)))
    if a.get("remoteok", True):
        out.append(_res("aggregator:remoteok", "RemoteOK", _remoteok))
    if a.get("adzuna", True) and env("ADZUNA_APP_ID"):
        out.append(_res("aggregator:adzuna", "Adzuna", lambda: _adzuna(a)))
    return out
