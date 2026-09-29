"""iCIMS "Jibe" career sites (AMD: careers.amd.com, …) — public JSON at /api/jobs, full descriptions included.

companies.yaml:  {name: AMD, ats: jibe, host: careers.amd.com}
The whole US list is read (AMD ≈ 650 jobs = 7 requests), so a job missing from it has closed.
"""
import time

from ..filters import html_to_text
from ..http import get_json
from ..models import Job, Partial

COMPLETE = True


def fetch(c, cfg):
    host = c["host"]
    path = c.get("path", "careers-home")
    short = host.split(".")[1] if host.count(".") >= 2 else host.split(".")[0]
    jobs, page = [], 1
    while page <= 40:
        if c.get("_deadline") and time.time() > c["_deadline"]:
            return Partial(jobs)                               # incomplete list must not close jobs
        d = get_json(f"https://{host}/api/jobs?location=United%20States&limit=100&page={page}"
                     f"&sortBy=posted_date&descending=true")
        items = d.get("jobs") or []
        for it in items:
            x = it.get("data") or {}
            jid = str(x.get("slug") or x.get("req_id") or "")
            if not jid:
                continue
            desc = "\n\n".join(html_to_text(x.get(k)) for k in ("description", "responsibilities", "qualifications")
                               if x.get(k))
            loc = x.get("full_location") or ", ".join(p for p in (x.get("city"), x.get("state")) if p)
            jobs.append(Job(
                uid=f"jibe:{short}:{jid}", company=c.get("name") or short,
                title=(x.get("title") or "").strip(),
                url=f"https://{host}/{path}/jobs/{jid}",
                locations=[loc] if loc else [], source="jibe", board=f"jibe:{host}",
                posted_at=(x.get("posted_date") or "").replace("+0000", "+00:00") or None,
                country=x.get("country_code") or x.get("country"),
                employment=(x.get("employment_type") or "").replace("_", " ").title() or None,
                description=desc,
            ))
        page += 1
        if not items or len(jobs) >= (d.get("totalCount") or 0):
            break
    return jobs


def detail_fetcher_from_url(url):
    return None
