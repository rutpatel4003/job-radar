"""Write the dashboard data INSIDE the private repo (the dashboard page itself holds no data; it
reads these files through the GitHub API with your token).

data/dashboard/jobs.json          – compact list of every visible job
data/dashboard/details/<id>.json  – full description + the evidence behind each flag
data/tracking.json                – your statuses / notes, written by the dashboard (never by the bot)
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .config import ROOT

KEEP_CLOSED_DAYS = 21
OUT = ROOT / "data" / "dashboard"
DETAILS = OUT / "details"
TRACKING = ROOT / "data" / "tracking.json"


def tracked_uids():
    try:
        return set((json.loads(TRACKING.read_text()).get("jobs") or {}).keys())
    except Exception:
        return set()
FIELDS = ("uid", "d", "company", "title", "url", "locations", "loc_status", "categories", "tags",
          "sources", "posted_at", "first_seen", "status", "closed_at", "sponsorship", "community_label",
          "h1b", "min_years", "phd", "salary", "match", "resume", "missing", "repost_of",
          "repost_first_seen", "reopened_at", "alt_urls", "has_desc", "start", "priority", "prev_status", "ai", "ai_hidden", "sponsorship_src", "seeded",
          "hidden", "hidden_reason", "staffing", "clearance_likely")


def detail_id(uid):
    return hashlib.sha1(uid.encode()).hexdigest()[:16]


def write_detail(rec, job, info):
    did = detail_id(rec["uid"])
    DETAILS.mkdir(parents=True, exist_ok=True)
    body = {
        "description": (job.description or "").strip()[:20000],
        "sponsorship_evidence": info.get("sponsorship_evidence"),
        "years_evidence": info.get("years_evidence"),
        "start_evidence": info.get("start_evidence"),
        "employment": job.employment,
        "all_locations": job.locations,
        "degrees": job.degree_hint or None,
        "matched": rec.get("matched"),
    }
    (DETAILS / f"{did}.json").write_text(json.dumps({k: v for k, v in body.items() if v},
                                                    ensure_ascii=False, separators=(",", ":")))
    return did


def export_dashboard(store, company_count, regions=None, dash=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
    rows, keep_ids = [], set()
    tracked = tracked_uids()
    for r in store.jobs.values():
        mine = r["uid"] in tracked
        # Hidden jobs are exported too (the dashboard hides them unless you tick "Show hidden"), so a wrongly
        # hidden job can be spotted and its reason / evidence checked. Closed hidden jobs are dropped.
        if r.get("hidden") and r["status"] == "closed" and not mine:
            continue
        if r["status"] == "closed" and (r.get("closed_at") or "") < cutoff and not mine:
            continue
        rows.append({k: r[k] for k in FIELDS if r.get(k) not in (None, [], "", False)})
        if r.get("d"):
            keep_ids.add(r["d"])
    rows.sort(key=lambda r: r["first_seen"], reverse=True)
    out = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "boards": company_count,
        "open": sum(r["status"] == "open" and not r.get("hidden") for r in rows),
        "regions": regions or {},
        "stale_days": (dash or {}).get("stale_days", 180),
        "jobs": rows,
    }
    p = OUT / "jobs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    if DETAILS.exists():   # prune detail files of jobs that dropped off the dashboard
        for f in DETAILS.glob("*.json"):
            if f.stem not in keep_ids:
                f.unlink()
