from ..http import get_json
from ..models import Job

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"
COMPLETE = True


def fetch(c, cfg):
    token = c["token"]
    data = get_json(API.format(token=token))
    jobs = []
    for p in data.get("jobs", []):
        if p.get("isListed") is False:
            continue
        locs = [p.get("location")] + [s.get("location") for s in p.get("secondaryLocations") or []]
        country = (((p.get("address") or {}).get("postalAddress") or {}).get("addressCountry"))
        jobs.append(Job(
            uid=f"ashby:{p['id'].lower()}",
            company=c.get("name") or token,
            title=(p.get("title") or "").strip(),
            url=p.get("jobUrl") or f"https://jobs.ashbyhq.com/{token}/{p['id']}",
            locations=[l for l in locs if l],
            source="ashby",
            board=f"ashby:{token.lower()}",
            posted_at=p.get("publishedAt"),
            description=p.get("descriptionPlain") or "",
            country=country,
            employment=p.get("employmentType"),
        ))
    return jobs


def detail_fetcher_from_url(url):
    return None  # Ashby has no public single-posting endpoint; boards are fetched whole instead.
