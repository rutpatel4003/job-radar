"""Eightfold career sites (Microsoft, Qualcomm, Netflix, American Express, ...).

Job links look like https://<site>/careers/job/<id>. The site's own JSON search API needs the company's
email domain (e.g. microsoft.com). Searched with keywords, so absence doesn't mean closed.
EXPERIMENTAL: Eightfold has two API generations; we try the classic one, then the newer "pcsx" one.
"""
from datetime import datetime, timezone
from functools import partial
from urllib.parse import quote

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = False


def _positions(c, q, start):
    host, dom = c["host"], c["domain"]
    try:
        d = get_json(f"https://{host}/api/apply/v2/jobs?domain={dom}&start={start}&num=10"
                     f"&query={quote(q)}&location=United%20States&sort_by=timestamp")
        return d.get("positions") or [], d.get("count") or 0
    except Exception:
        d = get_json(f"https://{host}/api/pcsx/search?domain={dom}&query={quote(q)}"
                     f"&location=United%20States&start={start}&sort_by=timestamp")
        data = d.get("data") or d
        return data.get("positions") or [], data.get("count") or 0


def fetch(c, cfg):
    queries = (cfg.get("eightfold") or {}).get("queries") or (cfg.get("workday") or {}).get("queries") or [""]
    cap = int((cfg.get("eightfold") or {}).get("max_results_per_query", 40))
    found = {}
    for q in queries:
        start = 0
        while start < cap:
            pos, count = _positions(c, q, start)
            for p in pos:
                found.setdefault(str(p.get("id")), p)
            start += 10
            if len(pos) < 10 or start >= count:
                break
    jobs = []
    for jid, p in found.items():
        locs = p.get("locations") or ([p["location"]] if p.get("location") else [])
        ts = p.get("t_create") or p.get("postedTs")
        jobs.append(Job(
            uid=f"ef:{jid}",
            company=c.get("name") or c["domain"],
            title=(p.get("name") or p.get("title") or "").strip(),
            url=p.get("canonicalPositionUrl") or f"https://{c['host']}/careers/job/{jid}",
            locations=[l if isinstance(l, str) else l.get("name", "") for l in locs][:8],
            source="eightfold", board=f"eightfold:{c['domain']}",
            posted_at=datetime.fromtimestamp(ts, timezone.utc).isoformat() if isinstance(ts, (int, float)) else None,
            description=html_to_text(p.get("job_description")) if p.get("job_description") else None,
            fetch_detail=partial(detail, c, jid),
        ))
    return jobs


def detail(c, jid):
    d = get_json(f"https://{c['host']}/api/apply/v2/jobs/{jid}?domain={c['domain']}")
    return {"description": html_to_text(d.get("job_description"))}


def detail_fetcher_from_url(url):
    return None
