from functools import partial
from urllib.parse import urlparse

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    jobs, offset = [], 0
    while offset < 1000:
        if c.get('_deadline') and __import__('time').time() > c['_deadline']:
            break
        data = get_json(API.format(token=token) + f"?country=us&limit=100&offset={offset}")
        items = data.get("content") or []
        for p in items:
            loc = p.get("location") or {}
            label = ", ".join(x for x in [loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()] if x)
            if loc.get("remote"):
                label = (label + " (Remote)").strip()
            jobs.append(Job(
                uid=f"sr:{p['id']}",
                company=c.get("name") or (p.get("company") or {}).get("name") or token,
                title=(p.get("name") or "").strip(),
                url=f"https://jobs.smartrecruiters.com/{token}/{p['id']}",
                locations=[label] if label else [],
                source="smartrecruiters",
                board=f"smartrecruiters:{token.lower()}",
                posted_at=p.get("releasedDate"),
                country=loc.get("country"),
                employment=(p.get("typeOfEmployment") or {}).get("label"),
                fetch_detail=partial(detail, token, p["id"]),
            ))
        offset += 100
        if offset >= (data.get("totalFound") or 0) or not items:
            break
    return jobs


def detail(token, pid):
    d = get_json(API.format(token=token) + f"/{pid}")
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    text = "\n".join(html_to_text((sections.get(k) or {}).get("text"))
                     for k in ("jobDescription", "qualifications", "additionalInformation"))
    return {"description": text}


def detail_fetcher_from_url(url):
    u = urlparse(url)
    if "smartrecruiters.com" not in u.netloc:
        return None
    segs = [s for s in u.path.split("/") if s]
    if len(segs) >= 2 and segs[1].split("-")[0].isdigit():
        return partial(detail, segs[0], segs[1].split("-")[0])
    return None
