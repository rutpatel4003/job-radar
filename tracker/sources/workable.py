from ..filters import html_to_text
from ..http import get_json
from ..models import Job

API = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    data = get_json(API.format(token=token))
    jobs = []
    for p in data.get("jobs", []):
        code = p.get("shortcode") or p.get("code")
        if not code:
            continue
        locs = []
        for l in p.get("locations") or []:
            locs.append(", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x))
        if not locs:
            locs = [", ".join(x for x in [p.get("city"), p.get("state"), p.get("country")] if x)]
        jobs.append(Job(
            uid=f"workable:{token.lower()}:{code.lower()}",
            company=c.get("name") or data.get("name") or token,
            title=(p.get("title") or "").strip(),
            url=p.get("url") or p.get("shortlink") or f"https://apply.workable.com/{token}/j/{code}/",
            locations=[l for l in locs if l],
            source="workable", board=f"workable:{token.lower()}",
            posted_at=p.get("published_on") or p.get("created_at"),
            description=html_to_text(p.get("description")) if p.get("description") else None,
            country=(p.get("locations") or [{}])[0].get("countryCode") or p.get("country"),
            employment=p.get("employment_type"),
        ))
    return jobs


def detail_fetcher_from_url(url):
    return None
