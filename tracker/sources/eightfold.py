"""Eightfold career sites (Microsoft, Qualcomm, Netflix, American Express, ...).

Job links look like https://<site>/careers/job/<id>. The site's own JSON search API needs the company's
email domain (e.g. microsoft.com). Searched with keywords, so absence doesn't mean closed.
Eightfold has two API generations and each site enables only one of them:
  classic  /api/apply/v2/jobs      (Netflix)
  pcsx     /api/pcsx/search         (Microsoft, Qualcomm)
A site on the other generation answers HTTP 200 with {"message": "... not enabled/authorized ..."}, not an
error — that is why Microsoft used to return 0 jobs. We detect it and remember which one works.
"""
import time
from datetime import datetime, timezone
from functools import partial
from urllib.parse import quote

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = False
_MODE = {}          # host -> "v2" | "pcsx"


def _v2(c, q, start):
    d = get_json(f"https://{c['host']}/api/apply/v2/jobs?domain={c['domain']}&start={start}&num=10"
                 f"&query={quote(q)}&location=United%20States&sort_by=timestamp")
    if not isinstance(d, dict) or "positions" not in d:
        raise LookupError((d or {}).get("message", "no positions") if isinstance(d, dict) else "bad reply")
    return d.get("positions") or [], d.get("count") or 0


def _pcsx(c, q, start):
    d = get_json(f"https://{c['host']}/api/pcsx/search?domain={c['domain']}&query={quote(q)}"
                 f"&location=United%20States&start={start}&sort_by=timestamp")
    data = d.get("data") if isinstance(d, dict) else None
    if not isinstance(data, dict) or "positions" not in data:
        raise LookupError((d or {}).get("message", "no positions") if isinstance(d, dict) else "bad reply")
    return data.get("positions") or [], data.get("count") or 0


def _positions(c, q, start):
    host = c["host"]
    order = [_MODE[host]] if host in _MODE else ["v2", "pcsx"]
    err = None
    for mode in order:
        try:
            out = (_v2 if mode == "v2" else _pcsx)(c, q, start)
            _MODE[host] = mode
            return out
        except Exception as e:                      # wrong generation, or the site is down
            err = e
    raise err


def fetch(c, cfg):
    queries = (cfg.get("eightfold") or {}).get("queries") or (cfg.get("workday") or {}).get("queries") or [""]
    cap = int((cfg.get("eightfold") or {}).get("max_results_per_query", 40))
    found = {}
    for q in queries:
        start = 0
        while start < cap:
            if c.get("_deadline") and time.time() > c["_deadline"]:
                break
            pos, count = _positions(c, q, start)
            for p in pos:
                found.setdefault(str(p.get("id")), p)
            start += 10
            if len(pos) < 10 or start >= count:
                break
    jobs = []
    for jid, p in found.items():
        locs = p.get("locations") or ([p["location"]] if p.get("location") else [])
        ts = p.get("t_create") or p.get("postedTs") or p.get("creationTs")
        url = p.get("canonicalPositionUrl") or p.get("publicUrl") or f"https://{c['host']}/careers/job/{jid}"
        if url.startswith("/"):
            url = f"https://{c['host']}{url}"
        jobs.append(Job(
            uid=f"ef:{jid}",
            company=c.get("name") or c["domain"],
            title=(p.get("name") or p.get("title") or "").strip(),
            url=url,
            locations=[l if isinstance(l, str) else l.get("name", "") for l in locs][:8],
            source="eightfold", board=f"eightfold:{c['domain']}",
            posted_at=datetime.fromtimestamp(ts, timezone.utc).isoformat() if isinstance(ts, (int, float)) else None,
            description=html_to_text(p.get("job_description")) if p.get("job_description") else None,
            fetch_detail=partial(detail, c, jid),
        ))
    return jobs


def detail(c, jid):
    if _MODE.get(c["host"]) != "pcsx":
        try:
            d = get_json(f"https://{c['host']}/api/apply/v2/jobs/{jid}?domain={c['domain']}")
            if isinstance(d, dict) and d.get("job_description"):
                return {"description": html_to_text(d.get("job_description"))}
        except Exception:
            pass
    d = get_json(f"https://{c['host']}/api/pcsx/position_details?position_id={jid}&domain={c['domain']}&hl=en")
    data = (d.get("data") if isinstance(d, dict) else None) or {}
    return {"description": html_to_text(data.get("jobDescription") or "")}


def detail_fetcher_from_url(url):
    return None
