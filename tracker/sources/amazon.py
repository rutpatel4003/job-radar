"""amazon.jobs has a public JSON search endpoint (best effort — Amazon can change it anytime)."""
from datetime import datetime, timezone
from urllib.parse import quote

from ..filters import html_to_text
from ..http import get_json
from ..models import Job

COMPLETE = False
API = ("https://www.amazon.jobs/en/search.json?base_query={q}&country=USA&result_limit=100"
       "&sort=recent&offset=0")


def fetch(c, cfg):
    found = {}
    for q in cfg.get("amazon", {}).get("queries", []):
        data = get_json(API.format(q=quote(q)))
        for j in data.get("jobs") or []:
            jid = str(j.get("id_icims") or j.get("id") or "")
            if jid and jid not in found:
                found[jid] = j
    jobs = []
    for jid, j in found.items():
        posted = None
        try:
            posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
        desc = "\n".join(html_to_text(j.get(k)) for k in ("description", "basic_qualifications", "preferred_qualifications"))
        jobs.append(Job(
            uid=f"amazon:{jid}",
            company="Amazon",
            title=(j.get("title") or "").strip(),
            url="https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}"),
            locations=[j.get("normalized_location") or j.get("location") or ""],
            source="amazon",
            board="amazon:amazon",
            posted_at=posted,
            description=desc,
            country="US" if j.get("country_code") == "USA" else j.get("country_code"),
        ))
    return jobs


def detail_fetcher_from_url(url):
    return None
