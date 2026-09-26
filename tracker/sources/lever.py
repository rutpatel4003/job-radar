from functools import partial
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

API = "https://api.lever.co/v0/postings/{token}"
COMPLETE = True


def _text(p):
    parts = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
    for lst in p.get("lists") or []:
        parts.append(lst.get("text") or "")
        parts.append(html_to_text(lst.get("content")))
    parts.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
    return "\n".join(x for x in parts if x)


def fetch(c, cfg):
    token = c["token"]
    data = get_json(API.format(token=token) + "?mode=json")
    jobs = []
    for p in data if isinstance(data, list) else []:
        cat = p.get("categories") or {}
        locs = cat.get("allLocations") or ([cat["location"]] if cat.get("location") else [])
        created = p.get("createdAt")
        jobs.append(Job(
            uid=f"lever:{p['id'].lower()}",
            company=c.get("name") or token,
            title=(p.get("text") or "").strip(),
            url=p.get("hostedUrl") or f"https://jobs.lever.co/{token}/{p['id']}",
            locations=locs,
            source="lever",
            board=f"lever:{token.lower()}",
            posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None,
            description=_text(p),
            country=p.get("country"),
            employment=cat.get("commitment"),
        ))
    return jobs


def detail(token, pid):
    p = get_json(API.format(token=token) + f"/{pid}")
    cat = p.get("categories") or {}
    return {"description": _text(p), "locations": cat.get("allLocations") or [], "country": p.get("country")}


def detail_fetcher_from_url(url):
    u = urlparse(url)
    if not u.netloc.endswith("lever.co"):
        return None
    segs = [s for s in u.path.split("/") if s]
    return partial(detail, segs[0], segs[1]) if len(segs) >= 2 else None
