from functools import partial
from urllib.parse import urlparse

from ..filters import html_to_text
from ..http import get_json
from ..ids import uid_from_url
from ..models import Job

API = "https://boards-api.greenhouse.io/v1/boards/{token}"
COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    data = get_json(API.format(token=token) + "/jobs")
    jobs = []
    for j in data.get("jobs", []):
        jid = j.get("id")
        if not jid:
            continue
        loc = (j.get("location") or {}).get("name") or ""
        jobs.append(Job(
            uid=f"gh:{jid}",
            company=c.get("name") or j.get("company_name") or token,
            title=(j.get("title") or "").strip(),
            url=j.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{jid}",
            locations=[loc] if loc else [],
            source="greenhouse",
            board=f"greenhouse:{token.lower()}",
            posted_at=j.get("first_published") or j.get("updated_at"),
            fetch_detail=partial(detail, token, jid),
        ))
    return jobs


def detail(token, jid):
    d = get_json(API.format(token=token) + f"/jobs/{jid}")
    offices = [o.get("location") or o.get("name") for o in d.get("offices") or []]
    return {"description": html_to_text(d.get("content")),
            "locations": [o for o in offices if o]}


def detail_fetcher_from_url(url):
    p = urlparse(url)
    if "greenhouse.io" not in p.netloc:
        return None
    segs = [s for s in p.path.split("/") if s]
    uid = uid_from_url(url)
    if segs and uid and uid.startswith("gh:") and segs[0] != "embed":
        return partial(detail, segs[0], uid[3:])
    return None
