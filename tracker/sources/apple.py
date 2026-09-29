"""jobs.apple.com JSON API (replaces the headless-browser scrape of Apple's search page).

Search needs a session CSRF token (fetched first); job details are public. Searched newest-first with
keywords, so a job missing from the results doesn't mean it closed (closures come from the daily re-check).
"""
import re
import time
from functools import partial
from urllib.parse import urlparse

import requests

from ..filters import html_to_text
from ..http import UA, NotFound, _check
from ..models import Job

COMPLETE = False
API = "https://jobs.apple.com/api/v1"
FMT = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    r = s.get(f"{API}/CSRFToken", timeout=(8, 20))
    _check(r)
    s.headers.update({"x-apple-csrf-token": r.headers.get("x-apple-csrf-token", ""),
                      "Origin": "https://jobs.apple.com", "Referer": "https://jobs.apple.com/en-us/search",
                      "Content-Type": "application/json"})
    return s


def _loc(l):
    parts = [l.get("city") or l.get("name"), l.get("stateProvince"), l.get("countryName")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def fetch(c, cfg):
    acfg = cfg.get("apple") or {}
    queries = acfg.get("queries") or ["machine learning", "software engineer", "computer vision"]
    pages = int(acfg.get("pages_per_query", 5))
    s = _session()
    found = {}
    for q in queries:
        for page in range(1, pages + 1):
            if c.get("_deadline") and time.time() > c["_deadline"]:
                break
            r = s.post(f"{API}/search", timeout=(8, 20), json={
                "query": q, "filters": {"locations": ["postLocation-USA"]}, "page": page,
                "locale": "en-us", "sort": "newest", "format": FMT})
            _check(r)
            items = ((r.json() or {}).get("res") or {}).get("searchResults") or []
            for x in items:
                if x.get("positionId"):
                    found.setdefault(str(x["positionId"]), x)
            if len(items) < 20:
                break
    jobs = []
    for pid, x in found.items():
        slug = x.get("transformedPostingTitle") or "role"
        jobs.append(Job(
            uid=f"apple:{pid}", company="Apple", title=(x.get("postingTitle") or "").strip(),
            url=f"https://jobs.apple.com/en-us/details/{pid}/{slug}",
            locations=[_loc(l) for l in x.get("locations") or [] if _loc(l)][:8],
            source="apple", board="apple:apple", posted_at=x.get("postDateInGMT"), country="US",
            fetch_detail=partial(detail, x.get("id") or pid),
        ))
    return jobs


def detail(jid):
    r = requests.get(f"{API}/jobDetails/{jid}?locale=en-us", timeout=(8, 20),
                     headers={"User-Agent": UA, "Accept": "application/json"})
    _check(r)
    d = (r.json() or {}).get("res") or {}
    if not d:
        raise NotFound(jid)
    parts = [("", d.get("jobSummary")), ("Description", d.get("description")),
             ("Responsibilities", d.get("responsibilities")),
             ("Minimum qualifications", d.get("minimumQualifications")),
             ("Preferred qualifications", d.get("preferredQualifications"))]
    text = "\n\n".join((f"{h}\n" if h else "") + html_to_text(v) for h, v in parts if v)
    return {"description": text}


def detail_fetcher_from_url(url):
    p = urlparse(url)
    if p.netloc.endswith("jobs.apple.com"):
        m = re.search(r"/details/([A-Z]*-?\d+)", p.path)
        if m:
            return partial(detail, m.group(1))
    return None
