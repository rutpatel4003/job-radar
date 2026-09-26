from functools import partial

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    data = get_json(f"https://{token}.bamboohr.com/careers/list")
    jobs = []
    for p in data.get("result", []):
        loc = p.get("atsLocation") or p.get("location") or {}
        label = ", ".join(x for x in [loc.get("city"), loc.get("state") or loc.get("province"), loc.get("country")] if x)
        if p.get("isRemote"):
            label = (label + " (Remote)").strip()
        jobs.append(Job(
            uid=f"bamboohr:{token.lower()}:{p['id']}",
            company=c.get("name") or token,
            title=(p.get("jobOpeningName") or "").strip(),
            url=f"https://{token}.bamboohr.com/careers/{p['id']}",
            locations=[label] if label else [],
            source="bamboohr", board=f"bamboohr:{token.lower()}",
            country=loc.get("country"),
            employment=p.get("employmentStatusLabel"),
            fetch_detail=partial(detail, token, p["id"]),
        ))
    return jobs


def detail(token, jid):
    d = get_json(f"https://{token}.bamboohr.com/careers/{jid}/detail")
    op = (d.get("result") or {}).get("jobOpening") or {}
    return {"description": html_to_text(op.get("description"))}


def detail_fetcher_from_url(url):
    from urllib.parse import urlparse
    u = urlparse(url)
    segs = [x for x in u.path.split("/") if x]
    if u.netloc.endswith(".bamboohr.com") and len(segs) >= 2 and segs[0] == "careers" and segs[1].isdigit():
        return partial(detail, u.netloc.split(".")[0], segs[1])
    return None
