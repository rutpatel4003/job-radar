from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    data = get_json(f"https://{token}.recruitee.com/api/offers/")
    jobs = []
    for p in data.get("offers", []):
        loc = ", ".join(x for x in [p.get("city"), p.get("state_code") or p.get("state_name"), p.get("country_code")] if x) \
            or p.get("location") or ""
        jobs.append(Job(
            uid=f"recruitee:{token.lower()}:{p['id']}",
            company=c.get("name") or p.get("company_name") or token,
            title=(p.get("title") or "").strip(),
            url=p.get("careers_url") or f"https://{token}.recruitee.com/o/{p.get('slug')}",
            locations=[loc] if loc else [],
            source="recruitee", board=f"recruitee:{token.lower()}",
            posted_at=p.get("published_at") or p.get("created_at"),
            description=html_to_text((p.get("description") or "") + "\n" + (p.get("requirements") or "")),
            country=p.get("country_code"),
            employment=p.get("employment_type_code"),
        ))
    return jobs


def detail_fetcher_from_url(url):
    return None
