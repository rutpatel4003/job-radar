"""Weekly company discovery → data/companies.json

    python -m tracker.discover              # harvest + probe (needs internet access to ATS APIs)
    python -m tracker.discover --no-probe   # harvest only (from community list URLs)

1. Harvest: every apply-link in the community lists reveals a company's hiring platform and board
   name (e.g. jobs.ashbyhq.com/openai → Ashby board "openai"). Companies that posted at least one
   relevant US role (AI/ML/CV/SDE titles) in the last year are kept.
2. Probe: companies whose links go to custom domains (careers.withwaymo.com, ...) and every name
   you list in companies.yaml are looked up on Greenhouse / Ashby / Lever by name.
3. Prune: boards that returned "not found" for 3+ days in the hourly run are dropped.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import yaml

from .config import ROOT, load_config
from .filters import TitleFilter, location_status, norm_company
from .http import get_json
from .ids import ats_from_url, board_key
from .sources.community import cached_redirect, parse_markdown_tables, simplify_listings
from .http import get_text

DATA = ROOT / "data"
SUFFIX = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "technologies", "the", "group"}
MAX_WORKDAY = 500
MAX_ORACLE = 120
MAX_PROBES = 4000
YC_HIRING = "https://yc-oss.github.io/api/companies/hiring.json"


def harvest(cfg, tf):
    cl = cfg.get("community_lists", {})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).timestamp()
    boards = {}
    hits = Counter()
    names = defaultdict(Counter)
    no_ats = Counter()

    def consider(company, title, locations, url):
        company = (company or "").strip()
        if not tf.categories(title) or location_status(locations) == "non_us":
            return
        c = ats_from_url(url)
        if c:
            k = board_key(c)
            boards.setdefault(k, c)
            hits[k] += 1
            names[k][company] += 1
        elif company:
            no_ats[company] += 1

    for u in cl.get("json", []) + [x for x in cl.get("discovery_only", []) if x.endswith(".json")]:
        try:
            for x in simplify_listings(u):
                if (x.get("date_posted") or 0) >= cutoff and x.get("url"):
                    consider(x.get("company_name", ""), x.get("title", ""), x.get("locations") or [], x["url"])
            print(f"[harvest] {u.split('/')[4]}: ok")
        except Exception as e:
            print(f"[harvest] {u}: {e}")
    for u in cl.get("markdown", []) + [x for x in cl.get("discovery_only", []) if not x.endswith(".json")]:
        try:
            for r in parse_markdown_tables(get_text(u)):
                consider(r["company"], r["title"], [r["location"]], cached_redirect(r["url"]) or r["url"])
            print(f"[harvest] {u.split('/')[4]}: ok")
        except Exception as e:
            print(f"[harvest] {u}: {e}")
    for u in cl.get("applyguy", []):
        try:
            data = json.loads(get_text(u))
            for x in (data.get("jobs") if isinstance(data, dict) else data) or []:
                if x.get("listingUrl"):
                    consider(x.get("company", ""), x.get("title", ""), [x.get("location") or ""], x["listingUrl"])
            print("[harvest] applyguy: ok")
        except Exception as e:
            print(f"[harvest] {u}: {e}")

    prio = [norm_company(p) for p in cfg.get("priority_companies") or []]
    is_prio = lambda n: any(norm_company(n).startswith(p) for p in prio if p)
    out = []
    for k, c in boards.items():
        name = names[k].most_common(1)[0][0]
        if c["ats"] in ("workday", "oracle") and hits[k] < 1 and not is_prio(name):
            continue
        out.append(dict(c, name=name, hits=hits[k], origin="auto"))
    # search-based platforms are costlier per board: keep priority companies + the most active ones
    capped = []
    for ats, cap in (("workday", MAX_WORKDAY), ("oracle", MAX_ORACLE)):
        group = sorted([c for c in out if c["ats"] == ats], key=lambda c: (not is_prio(c["name"]), -c["hits"]))
        capped += group[:cap]
    out = [c for c in out if c["ats"] not in ("workday", "oracle")] + capped
    # Y Combinator companies that are hiring in the US (lots of SF startups) → probed by name
    try:
        yc = get_json(YC_HIRING, timeout=60)
        n0 = len(no_ats)
        for co in yc:
            locs = " ".join(co.get("all_locations") or [co.get("location") or ""]) if isinstance(co, dict) else ""
            if co.get("name") and re.search(r"USA|United States|, CA|, NY|, WA|Remote", locs):
                no_ats[co["name"]] += 1
        print(f"[harvest] YC hiring list: +{len(no_ats) - n0} names")
    except Exception as e:
        print(f"[harvest] YC list unavailable: {e}")
    known = {norm_company(c["name"]) for c in out}
    unresolved = [n for n, _ in no_ats.most_common() if norm_company(n) not in known]
    return out, unresolved


def slugs(name):
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in SUFFIX]
    if not words:
        return [], []
    full = ["".join(words), "-".join(words)]
    short = [words[0]] if len(words) > 1 and len(words[0]) >= 4 else []
    return list(dict.fromkeys(full)), short


def _similar(a, b):
    a, b = norm_company(a), norm_company(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a))))


def probe(name):
    full, short = slugs(name)
    for s in full + short:
        try:
            d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{s}", timeout=15)
            if _similar(d.get("name", ""), name):
                return {"ats": "greenhouse", "token": s}
        except Exception:
            pass
    for s in full:
        try:
            d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{s}", timeout=15)
            if isinstance(d, dict) and "jobs" in d:
                return {"ats": "ashby", "token": s}
        except Exception:
            pass
        try:
            d = get_json(f"https://api.lever.co/v0/postings/{s}?mode=json&limit=1", timeout=15)
            if isinstance(d, list):
                return {"ats": "lever", "token": s}
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-probe", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    tf = TitleFilter(cfg)

    found, unresolved = harvest(cfg, tf)
    print(f"[harvest] {len(found)} boards from community lists "
          f"({Counter(c['ats'] for c in found)}), {len(unresolved)} names without a known ATS")

    manual = yaml.safe_load((ROOT / "companies.yaml").read_text()) or {}
    manual_names = [e if isinstance(e, str) else e.get("name") for e in manual.get("companies") or []
                    if isinstance(e, str) or (isinstance(e, dict) and "ats" not in e and "url" not in e)]

    cache_p = DATA / "probe_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    today = date.today()
    probed = []
    if not args.no_probe:
        todo = []
        for n in manual_names + unresolved:
            key = norm_company(n)
            hit = cache.get(key)
            stale = not hit or (hit.get("r") is None and
                                (today - date.fromisoformat(hit["t"])).days > 30)
            if key and stale and n not in todo:
                todo.append(n)
        todo = todo[:MAX_PROBES]
        print(f"[probe] checking {len(todo)} company names on Greenhouse/Ashby/Lever…")
        with ThreadPoolExecutor(max_workers=16) as ex:
            for n, r in zip(todo, ex.map(probe, todo)):
                cache[norm_company(n)] = {"n": n, "r": r, "t": today.isoformat()}
        cache_p.write_text(json.dumps(cache, indent=0, sort_keys=True))
    for key, v in cache.items():
        if v.get("r"):
            probed.append(dict(v["r"], name=v["n"], origin="probe"))

    state_p = DATA / "state.json"
    dead = json.loads(state_p.read_text()).get("dead_boards", {}) if state_p.exists() else {}
    old_dead = {b for b, d in dead.items() if (today - date.fromisoformat(d)).days >= 3}

    boards = {}
    for c in found + probed:
        k = board_key(c)
        if k in old_dead:
            continue
        boards.setdefault(k, c)
    out = sorted(boards.values(), key=lambda c: (c["ats"], c["name"].lower()))
    (DATA / "companies.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"[done] {len(out)} boards → data/companies.json  {dict(Counter(c['ats'] for c in out))}"
          + (f"  (pruned {len(old_dead)} dead)" if old_dead else ""))


if __name__ == "__main__":
    main()
