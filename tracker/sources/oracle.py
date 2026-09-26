"""Oracle Recruiting Cloud (JPMorgan Chase, Goldman Sachs lateral, American Express, Oracle, BNY,
Texas Instruments, ...). Career sites look like
  https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210706739
and are backed by a public JSON API. Like Workday, boards are searched with keywords (not fully listed).
"""
from datetime import datetime, timezone
from functools import partial
from urllib.parse import quote

from ..filters import html_to_text
from ..http import NotFound, get_json
from ..models import Job

COMPLETE = False


def _base(c):
    return f"https://{c['host']}/hcmRestApi/resources/latest"


def public_url(c, jid):
    return f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{jid}"


def fetch(c, cfg):
    queries = (cfg.get("oracle") or {}).get("queries") or (cfg.get("workday") or {}).get("queries") or [""]
    cap = int((cfg.get("oracle") or {}).get("max_results_per_query", 50))
    found = {}
    for q in queries:
        offset = 0
        while offset < cap:
            if c.get('_deadline') and __import__('time').time() > c['_deadline']:
                break
            finder = (f"findReqs;siteNumber={c['site']},facetsList=LOCATIONS,limit=25,"
                      f"keyword=\"{q}\",sortBy=POSTING_DATES_DESC,offset={offset}")
            data = get_json(f"{_base(c)}/recruitingCEJobRequisitions?onlyData=true"
                            f"&expand=requisitionList.secondaryLocations&finder={quote(finder, safe='=;,')}")
            items = (data.get("items") or [{}])[0]
            reqs = items.get("requisitionList") or []
            for r in reqs:
                found.setdefault(str(r.get("Id")), r)
            if len(reqs) < 25:
                break
            offset += 25
    jobs = []
    for jid, r in found.items():
        locs = [r.get("PrimaryLocation")] + [s.get("Name") for s in r.get("secondaryLocations") or []]
        posted = r.get("PostedDate")
        jobs.append(Job(
            uid=f"oracle:{c['host'].split('.')[0]}:{jid}",
            company=c.get("name") or c["host"],
            title=(r.get("Title") or "").strip(),
            url=public_url(c, jid),
            locations=[l for l in locs if l],
            source="oracle", board=f"oracle:{c['host'].split('.')[0]}/{c['site'].lower()}",
            posted_at=datetime.fromisoformat(posted).replace(tzinfo=timezone.utc).isoformat() if posted and len(posted) == 10 else posted,
            country=r.get("PrimaryLocationCountry"),
            fetch_detail=partial(detail, c, jid),
        ))
    return jobs


def detail(c, jid):
    finder = f'ById;Id="{jid}",siteNumber={c["site"]}'
    d = get_json(f"{_base(c)}/recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder={quote(finder, safe='=;,')}")
    if not d.get("items"):
        raise NotFound(jid)                      # requisition no longer published
    it = d["items"][0]
    text = "\n".join(html_to_text(it.get(k)) for k in
                     ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr", "CorporateDescriptionStr"))
    return {"description": text, "country": it.get("PrimaryLocationCountry")}


def detail_fetcher_from_url(url):
    from ..ids import ats_from_url, uid_from_url
    c = ats_from_url(url)
    uid = uid_from_url(url)
    if c and c["ats"] == "oracle" and uid:
        return partial(detail, c, uid.rsplit(":", 1)[-1])
    return None
