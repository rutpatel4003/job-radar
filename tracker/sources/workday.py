"""Workday career sites (NVIDIA, Adobe, Salesforce, Intel, ...).

Workday boards can hold thousands of postings, so instead of listing everything we run a
handful of targeted searches (config.yaml → workday.queries). Because that's a partial view,
Workday results never auto-mark jobs as closed.
"""
import re
from datetime import datetime, timedelta, timezone
from functools import partial

from ..filters import html_to_text
from ..http import NotFound, get_json, post_json
from ..ids import workday_parts
from ..models import Job

COMPLETE = False


def api_base(c):
    return f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"


def public_url(c, path):
    if c["host"].endswith("myworkdaysite.com"):
        return f"https://{c['host']}/recruiting/{c['tenant']}/{c['site']}{path}"
    return f"https://{c['host']}/{c['site']}{path}"


def posted_on_to_iso(text):
    if not text:
        return None
    t = text.lower()
    now = datetime.now(timezone.utc)
    if "today" in t:
        d = now
    elif "yesterday" in t:
        d = now - timedelta(days=1)
    else:
        m = re.search(r"(\d+)\+?\s+day", t)
        if not m:
            return None
        d = now - timedelta(days=int(m.group(1)))
    return d.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def req_from_path(path):
    last = path.rstrip("/").split("/")[-1]
    return (last.rsplit("_", 1)[-1] if "_" in last else last).lower()


def fetch(c, cfg):
    wcfg = cfg.get("workday", {})
    queries = wcfg.get("queries") or [""]
    cap = int(wcfg.get("max_results_per_query", 60))
    base = api_base(c)
    found = {}
    errors = 0
    for q in queries:
        offset = 0
        while offset < cap:
            if c.get('_deadline') and __import__('time').time() > c['_deadline']:
                break
            try:
                data = post_json(base + "/jobs", {"appliedFacets": {}, "limit": 20,
                                                  "offset": offset, "searchText": q})
            except NotFound:
                raise
            except Exception:
                errors += 1
                if errors >= 3:
                    raise
                break
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if path and path not in found:
                    found[path] = p
            if len(posts) < 20:
                break
            offset += 20
    jobs = []
    for path, p in found.items():
        jobs.append(Job(
            uid=f"wd:{c['tenant'].lower()}:{req_from_path(path)}",
            company=c.get("name") or c["tenant"],
            title=(p.get("title") or "").strip(),
            url=public_url(c, path),
            locations=[p.get("locationsText")] if p.get("locationsText") else [],
            source="workday",
            board=f"workday:{c['tenant'].lower()}/{c['site'].lower()}",
            posted_at=posted_on_to_iso(p.get("postedOn")),
            fetch_detail=partial(detail, c, path),
        ))
    return jobs


def detail(c, path):
    d = get_json(api_base(c) + path)
    info = d.get("jobPostingInfo") or {}
    if not info:
        raise NotFound(path)                     # posting removed
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    return {"description": html_to_text(info.get("jobDescription")),
            "locations": [l for l in locs if l],
            "country": (info.get("country") or {}).get("descriptor")}


def detail_fetcher_from_url(url):
    wd = workday_parts(url)
    if not wd or not wd[3]:
        return None
    host, tenant, site, path = wd
    return partial(detail, {"host": host, "tenant": tenant, "site": site}, path)
