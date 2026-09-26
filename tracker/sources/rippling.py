"""Rippling ATS boards (ats.rippling.com/<slug>/jobs). EXPERIMENTAL: endpoint not officially documented."""
from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = True
API = "https://api.rippling.com/platform/api/ats/v1/board/{token}/jobs"


def fetch(c, cfg):
    token = c["token"]
    data = get_json(API.format(token=token))
    items = data if isinstance(data, list) else data.get("items") or data.get("jobs") or []
    jobs = []
    for p in items:
        jid = p.get("uuid") or p.get("id")
        if not jid:
            continue
        loc = p.get("workLocation") or {}
        label = loc.get("label") if isinstance(loc, dict) else str(loc)
        jobs.append(Job(
            uid=f"rippling:{str(jid).lower()}",
            company=c.get("name") or token,
            title=(p.get("name") or p.get("title") or "").strip(),
            url=p.get("url") or f"https://ats.rippling.com/{token}/jobs/{jid}",
            locations=[label] if label else [],
            source="rippling", board=f"rippling:{token.lower()}",
            description=html_to_text(p.get("description")) if p.get("description") else None,
        ))
    return jobs


def detail_fetcher_from_url(url):
    return None
