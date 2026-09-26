"""Hourly run:  python -m tracker.run  [--dry-run] [--no-notify] [--only waymo,scaleai]"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import TimeoutError as FuturesTimeout
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .config import ROOT, env, load_companies, load_config
from .export import export_dashboard, write_detail
from .filters import (ResumeMatcher, TitleFilter, analyze_description, classify_start, fingerprint,
                      location_status, norm_company)
from .ai_filter import AIFilter, compact
from .h1b import H1B
from .notify import notify
from .ids import board_key
from .sources import detail_from_url, fetch_board
from .sources.aggregators import fetch_all as fetch_aggregators
from .sources.community import fetch_markdown_list, fetch_simplify
from types import SimpleNamespace
from zlib import crc32

from .http import NotFound
from .sources.pagewatch import fetch_pages, fetch_texts
from .store import Store

DATA = ROOT / "data"
CLEARANCE_TITLE = re.compile(r"ts/sci|clearance|\bsecret\b|polygraph|u\.?s\.? citizen|\bus person", re.I)


def run_pool(tasks, workers, budget_s, label):
    """Run (key, fn) tasks in parallel but stop waiting after budget_s seconds.
    Returns (results, unfinished_keys). Stragglers are abandoned (process exits with os._exit)."""
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(fn): key for key, fn in tasks}
    out, t_start, last = [], time.time(), time.time()
    try:
        for f in as_completed(futs, timeout=max(5.0, budget_s)):
            try:
                out.append(f.result())
            except Exception as e:
                print(f"  {label} task {futs[f]} crashed: {type(e).__name__}: {e}")
            if time.time() - last > 30:
                last = time.time()
                print(f"  … {label}: {len(out)}/{len(futs)} done after {time.time() - t_start:.0f}s")
    except FuturesTimeout:
        pass
    unfinished = [k for f, k in futs.items() if not f.done()]
    ex.shutdown(wait=False, cancel_futures=True)
    if unfinished:
        print(f"  {label}: time budget reached, {len(unfinished)} tasks deferred to the next run")
    return out, unfinished


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state():
    p = DATA / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"boards_seen": [], "dead_boards": {}}


def save_state(state):
    (DATA / "state.json").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def recent(iso, hours):
    if not iso:
        return False
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - d <= timedelta(hours=hours)
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't save or notify")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--only", help="comma-separated board tokens/names to fetch (debugging)")
    ap.add_argument("--skip-pages", action="store_true", help="skip headless-browser career pages")
    ap.add_argument("--pages-only", action="store_true", help="only scan headless-browser career pages")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    t0 = time.time()
    cfg = load_config()
    tf = TitleFilter(cfg)
    companies = load_companies()
    if args.only:
        wanted = {w.strip().lower() for w in args.only.split(",")}
        companies = [c for c in companies if c.get("name", "").lower() in wanted
                     or (c.get("token") or c.get("tenant") or "").lower() in wanted]
    store = Store(DATA / "jobs.json")
    state = load_state()
    boards_seen = set(state.get("boards_seen", []))
    dead = dict(state.get("dead_boards", {}))
    first_ever = store.is_empty()
    if store.jobs and not any("seeded" in r for r in store.jobs.values()):
        first_scan = min(r["first_seen"] for r in store.jobs.values())
        for r in store.jobs.values():
            r["seeded"] = r["first_seen"] == first_scan
        store.dirty = True
    now = now_iso()

    # ── 1. Fetch everything in parallel (time-boxed) ──
    rt = cfg.get("runtime", {})
    prio = [norm_company(p) for p in cfg.get("priority_companies") or [] if norm_company(p)]
    is_prio = lambda name: any(norm_company(name).startswith(p) for p in prio)
    SEARCH_ATS = ("workday", "oracle", "eightfold")
    slot = (datetime.now(timezone.utc).hour // 3) % 8
    rot = max(1, int(rt.get("search_board_rotation", 2)))
    results = []
    tasks = []
    if not args.only and not args.pages_only:
        cl = cfg.get("community_lists", {})
        for u in cl.get("json", []):
            tasks.append((u, lambda u=u: fetch_simplify(u, cl.get("max_age_days", 120))))
        for u in cl.get("markdown", []):
            tasks.append((u, lambda u=u: fetch_markdown_list(u)))
        tasks.append(("aggregators", lambda: fetch_aggregators(cfg)))
    if not args.pages_only:
        # cheap full-list boards first, then search-based ones; priority companies before the rest.
        # Non-priority search boards (Workday/Oracle/Eightfold) are split across runs (each every `rot` runs).
        def order(c):
            return (c["ats"] in SEARCH_ATS, not is_prio(c.get("name", "")))
        skipped = 0
        for c in sorted(companies, key=order):
            if c["ats"] in SEARCH_ATS and not is_prio(c.get("name", "")) and not args.only \
                    and crc32(board_key(c).encode()) % rot != slot % rot:
                skipped += 1
                continue
            tasks.append((board_key(c), lambda c=c: fetch_board(c, cfg)))
        if skipped:
            print(f"[fetch] {skipped} non-priority Workday/Oracle/Eightfold boards rotate to the next run")
    fetched, unfinished = run_pool(tasks, rt.get("workers", 32),
                                   float(rt.get("fetch_budget_minutes", 6)) * 60, "fetch")
    for r in fetched:
        results.extend(r if isinstance(r, list) else [r])
    slow = sorted((r for r in results if getattr(r, "elapsed", 0)), key=lambda r: -r.elapsed)[:5]
    if slow:
        print("  slowest feeds: " + ", ".join(f"{r.board} {r.elapsed:.0f}s" for r in slow))
    deadline = t0 + float(rt.get("time_budget_minutes", 10)) * 60
    pages = [p for p in cfg.get("career_pages") or [] if p.get("enabled", True)]
    every = max(1, int(rt.get("career_pages_every_n_runs", 1)))
    if pages and not args.pages_only and (datetime.now(timezone.utc).hour // 3) % every:
        pages = []                                   # budget knob: career sites only every Nth run
    if pages and not args.only and not args.skip_pages:
        tp = time.time()
        results.extend(fetch_pages(pages, lambda uid, url: store.find(uid, url) is not None,
                                   rt.get("career_page_details", 25),
                                   wanted=lambda title: tf.evaluate(title)[0], deadline=deadline - 180))
        print(f"[pages] {len(pages)} career sites in {time.time() - tp:.0f}s", flush=True)
    # ATS boards first so their richer data becomes the primary record; community lists after.
    results.sort(key=lambda r: (r.board.startswith(("community:", "aggregator:")), r.board.startswith("page:")))

    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    print(f"[fetch] {len(ok)}/{len(results)} feeds ok, {sum(len(r.jobs) for r in ok)} postings "
          f"in {time.time() - t0:.0f}s")
    if failed:
        from collections import Counter
        print("  failures by source:", dict(Counter(r.board.split(":")[0] for r in failed)))
        for r in failed[:10]:
            print(f"  ✗ {r.board}: {r.error}")
    for r in results:
        if r.not_found:
            dead.setdefault(r.board, now[:10])
        elif r.ok:
            dead.pop(r.board, None)

    # ── 2. Filter + dedup ──
    pending = {}          # uid -> dict(job, cats, tags, repost_of, seed)
    pending_fp = {}
    events = []           # (event, record)
    presence = {}         # board -> set(uids) for complete feeds
    stats = {"relevant": 0, "merged": 0}
    alive = set()         # records seen active somewhere in this run

    for res in ok:
        if res.complete:
            presence[res.board] = {j.uid for j in res.jobs}
        seed = first_ever or (res.board not in boards_seen)
        for job in res.jobs:
            rec = store.find(job.uid, job.url)
            if rec:
                alive.add(rec["uid"])
                store.note_source(rec, job)
                if job.uid != rec["uid"] and job.uid not in rec.get("aliases", []):
                    store.add_alias(rec, job)
                if res.complete or rec["board"] == res.board or rec["board"].startswith("community:"):
                    if store.seen_again(rec) and not rec.get("hidden") and rec.get("start") != "too_early":
                        rec["reopened_at"] = now
                        events.append(("reopened", rec))
                continue
            if job.uid in pending:                      # same job from a second source this run
                pending[job.uid]["twins"].append(job)
                continue
            keep, cats, tags = tf.evaluate(job.title)
            if not keep and job.source == "hn":
                # HN first lines look like "Acme | Senior SWE, New Grad ML Engineer | SF | ONSITE"
                for seg in re.split(r"\s*\|\s*|,\s*|;\s*", job.title):
                    keep, cats, tags = tf.evaluate(seg)
                    if keep:
                        job.title = seg.strip()
                        break
            if not keep:
                continue
            if job.employment and "intern" in job.employment.lower():
                continue
            loc = location_status(job.locations, job.country)
            if loc == "non_us":
                continue
            fp = fingerprint(job.company, job.title, job.locations)
            if fp in pending_fp:                       # twin posting in this same run
                pending[pending_fp[fp]]["twins"].append(job)
                stats["merged"] += 1
                continue
            match = store.find_fp(fp)
            repost_of = None
            if match:
                if match["status"] == "open":
                    alive.add(match["uid"])
                    store.add_alias(match, job)        # duplicate listing, not new
                    stats["merged"] += 1
                    continue
                repost_of = match
            stats["relevant"] += 1
            pending[job.uid] = {"job": job, "cats": cats, "tags": tags, "loc": loc, "fp": fp,
                                "repost_of": repost_of, "seed": seed, "twins": []}
            pending_fp[fp] = job.uid

    use_browser = bool(pages) and not args.only and not args.skip_pages

    # ── 3. Fetch descriptions for genuinely new jobs (sponsorship / years / PhD) ──
    def enrich(item):
        job = item["job"]
        fetcher = job.fetch_detail or (detail_from_url(job.url) if job.description is None else None)
        if job.description is None and fetcher:
            try:
                d = fetcher() or {}
                job.description = d.get("description") or ""
                if d.get("locations"):
                    job.locations = list(dict.fromkeys(job.locations + d["locations"]))
                if d.get("country"):
                    job.country = d["country"]
            except Exception as e:
                item["detail_error"] = type(e).__name__
        return item

    seed_hours = cfg.get("notifications", {}).get("seed_notify_hours", 48)
    urgent = [i for i in pending.values() if not i["seed"] or recent(i["job"].posted_at, seed_hours)]
    later = len(pending) - len(urgent)
    if later:
        print(f"[details] {later} older jobs from newly added boards: descriptions back-filled over the next runs")
    run_pool([(i["job"].uid, lambda i=i: enrich(i)) for i in urgent], rt.get("detail_workers", 24),
             max(60.0, deadline - 240 - time.time()), "details")

    derr = [i for i in pending.values() if i.get("detail_error")]
    if derr:
        print(f"[details] {len(derr)} description fetches failed (e.g. {derr[0]['job'].uid}: {derr[0]['detail_error']})")

    # 3b. Jobs with no API description (Google/Apple/Microsoft/TikTok links from community lists, custom sites):
    #     open the posting in the headless browser so sponsorship / start date / match can still be checked.
    if use_browser:
        need = [i for i in urgent if len((i["job"].description or "").strip()) < 200]
        need.sort(key=lambda i: (not is_prio(i["job"].company), "newgrad" not in i["tags"]))
        need = need[: int(rt.get("browser_descriptions", 80))]
        if need:
            tb = time.time()
            texts = fetch_texts([i["job"].url for i in need], deadline=deadline - 120)
            for i in need:
                i["browser_tried"] = i["job"].url in texts or time.time() < deadline - 120
                if texts.get(i["job"].url):
                    i["job"].description = texts[i["job"].url]
            print(f"[browser] descriptions for {sum(1 for v in texts.values() if v)}/{len(need)} new jobs "
                  f"in {time.time() - tb:.0f}s")
    exp = cfg.get("experience", {})
    ncfg = cfg.get("notifications", {})
    seed_hours = ncfg.get("seed_notify_hours", 48)
    matcher = ResumeMatcher(cfg)
    scfg = cfg.get("start_date") or {}
    ym = lambda v, d: tuple(int(x) for x in str(v or d).split("-")[:2])
    grad, earliest = ym(scfg.get("graduation"), "2027-05"), ym(scfg.get("earliest_start"), "2027-06")
    h1b = H1B()
    aliases = cfg.get("h1b_aliases") or {}
    if h1b:
        for r in store.jobs.values():
            if r.get("h1b") is None:
                r["h1b"] = h1b.lookup(r["company"], aliases)
                store.dirty = True
    try:
        tracking = json.loads((DATA / "tracking.json").read_text()).get("jobs") or {}
    except Exception:
        tracking = {}

    def my_status(rec):
        for u in [rec["uid"]] + rec.get("aliases", []):
            if u in tracking:
                return tracking[u].get("status")
        return None

    seeded = 0
    for uid, item in pending.items():
        job = item["job"]
        loc = location_status(job.locations, job.country)
        if loc == "non_us":
            continue
        info = analyze_description(job.description)
        tm = CLEARANCE_TITLE.search(job.title)
        if tm and info["sponsorship"] != "citizen":
            info["sponsorship"], info["sponsorship_evidence"] = "citizen", f"Job title says: “{job.title}”"
        start, start_ev = classify_start(job.title, job.description, grad, earliest)
        info["start_evidence"] = start_ev
        community = job.sponsorship_hint or next((t.sponsorship_hint for t in item["twins"] if t.sponsorship_hint), None)
        if "PhD" in (job.degree_hint or []) and len(job.degree_hint) == 1:
            info["phd"] = True
        hidden_reason = None
        if not ncfg.get("include_hidden") and info["min_years"] and info["min_years"] >= exp.get("hide_min_years", 5):
            hidden_reason = f"asks for {info['min_years']}+ years"
        match = matcher.score(job.description)
        rec = {
            "uid": uid, "company": job.company, "title": job.title, "url": job.url,
            "locations": job.locations[:8], "loc_status": loc, "categories": item["cats"],
            "tags": item["tags"], "source": job.source, "sources": [job.source], "board": job.board,
            "posted_at": job.posted_at, "first_seen": now, "status": "open", "miss": 0,
            # sponsorship comes ONLY from the job's own text; community labels are kept separately
            "sponsorship": info["sponsorship"],
            "community_label": community,
            "h1b": h1b.lookup(job.company, aliases) if h1b else None,
            "min_years": info["min_years"] if (info["min_years"] or 0) >= exp.get("flag_min_years", 3) else None,
            "phd": info["phd"], "salary": info["salary"], "fp": item["fp"], "aliases": [], "alt_urls": [],
            "has_desc": bool(job.description and len(job.description) > 200),
            "desc_tried": item.get("browser_tried") or None,
            "start": start,
            "priority": is_prio(job.company),
        }
        if match:
            rec.update(match)
        if item["seed"]:
            rec["seeded"] = True          # part of an initial backlog: found time ≠ posting time
        if hidden_reason:
            rec["hidden"] = True
            rec["hidden_reason"] = hidden_reason
        prev_status = None
        if item["repost_of"]:
            rec["repost_of"] = item["repost_of"]["uid"]
            rec["repost_first_seen"] = item["repost_of"]["first_seen"]
            prev_status = my_status(item["repost_of"])
            if prev_status:
                rec["prev_status"] = prev_status
        store.add(rec)
        for twin in item["twins"]:
            store.add_alias(rec, twin)
        if not args.dry_run and not hidden_reason:
            rec["d"] = write_detail(rec, job, info)
        if hidden_reason:
            continue
        if start == "too_early" and not scfg.get("notify_too_early"):
            continue
        if prev_status == "not_interested":
            continue
        if item["seed"] and not recent(job.posted_at, seed_hours):
            seeded += 1
            continue
        events.append(("repost" if item["repost_of"] else "new", rec))

    # ── 3d. AI screening of this run's new jobs (before deciding what to notify) ──
    ai = AIFilter(cfg)
    ai_hide = (cfg.get("ai_filter") or {}).get("hide_rejected", True)
    ai_kept = ai_rejected = 0

    def apply_ai(rec, v):
        nonlocal ai_kept, ai_rejected
        try:
            fit = max(1, min(5, int(v.get("fit") or 3)))
        except (TypeError, ValueError):
            fit = 3
        rec["ai"] = {"v": v["verdict"], "fit": fit, "why": str(v.get("reason") or "")[:140],
                     "level": v.get("level"), "at": now[:10], "model": v.get("model")}
        sp = v.get("sponsorship")
        if rec.get("sponsorship", "unknown") == "unknown" and sp in ("no", "citizens_only"):
            rec["sponsorship"] = "citizen" if sp == "citizens_only" else "no_sponsor"
            rec["sponsorship_src"] = "ai"
        if v["verdict"] == "reject":
            ai_rejected += 1
            if ai_hide and not rec.get("hidden"):
                rec["hidden"], rec["hidden_reason"], rec["ai_hidden"] = True, "AI: " + rec["ai"]["why"], True
        else:
            ai_kept += 1
            if rec.get("ai_hidden"):
                for k in ("hidden", "hidden_reason", "ai_hidden"):
                    rec.pop(k, None)
        store.dirty = True

    if ai.enabled and not args.dry_run:
        batch = []
        for uid, item in pending.items():
            rec = store.jobs.get(uid)
            text = item["job"].description or ""
            if rec and not rec.get("hidden") and len(text) > 200:
                batch.append({"id": uid, "title": rec["title"], "company": rec["company"],
                              "location": "; ".join(rec.get("locations") or [])[:120], "text": compact(text)})
        batch.sort(key=lambda j: not store.jobs[j["id"]].get("priority"))
        for jid, v in ai.screen(batch, deadline - 150).items():
            if jid in store.jobs:
                apply_ai(store.jobs[jid], v)
        events = [(e, r) for e, r in events if not r.get("ai_hidden")]

    # ── 3c. Re-apply today's filters to jobs saved earlier (e.g. after editing config.yaml) ──
    hide_years = cfg.get("experience", {}).get("hide_min_years", 5)
    refiltered = restored = 0
    for r in store.jobs.values():
        if r["status"] != "open" or r["uid"] in pending:
            continue
        keep = tf.evaluate(r["title"])[0] or r.get("source") == "hn"
        years = r.get("min_years") or 0
        reason = None if keep and years < hide_years else \
            ("no longer matches your title/level filters" if not keep else f"asks for {years}+ years")
        if reason and not r.get("hidden"):
            r["hidden"], r["hidden_reason"], r["auto_hidden"] = True, reason, True
            store.dirty = True
            refiltered += 1
        elif not reason and r.get("auto_hidden"):
            for k in ("hidden", "hidden_reason", "auto_hidden"):
                r.pop(k, None)
            store.dirty = True
            restored += 1
    if refiltered or restored:
        print(f"[filters] hid {refiltered} saved jobs that no longer match your settings, restored {restored}")

    # ── 4. Closed-job detection ──
    closed = 0
    for board, uids in presence.items():
        for rec in list(store.by_board.get(board, [])):
            if rec["status"] != "open":
                continue
            if rec["uid"] in uids or any(a in uids for a in rec.get("aliases", [])):
                continue
            store.missed(rec, now)
            closed += rec["status"] == "closed"
    complete_boards = set(presence)
    for res in ok:
        for uid in res.closed_uids:
            rec = store.find(uid)
            if rec and rec["status"] == "open" and rec["board"] not in complete_boards \
                    and rec["uid"] not in alive and rec["uid"] not in pending:
                store.close(rec, now)
                closed += 1

    # ── 4b. Daily re-check of jobs from partial feeds (Workday, Oracle, community lists…) ──
    # Their feeds are searches, so a missing job doesn't prove it closed. Instead, each open job's own
    # posting is re-opened once a day (1/8 of them every 3-hour run); two "not found" answers = closed.
    rechecked = gone = 0
    if not args.only and not args.pages_only and time.time() < deadline - 60:
        slot = (datetime.now(timezone.utc).hour // 3) % 8
        cands = [r for r in store.jobs.values()
                 if r["status"] == "open" and not r.get("hidden") and r["board"] not in presence
                 and r["uid"] not in pending and crc32(r["uid"].encode()) % 8 == slot]
        cands = cands[: int(rt.get("recheck_per_run", 900))]

        def check(rec):
            f = detail_from_url(rec["url"])
            if not f:
                return rec, None
            try:
                f()
                return rec, True
            except NotFound:
                return rec, False
            except Exception:
                return rec, None

        checked, _ = run_pool([(r["uid"], lambda r=r: check(r)) for r in cands], rt.get("detail_workers", 24),
                              max(10.0, deadline - 90 - time.time()), "re-check")
        if True:
            for rec, alive in checked:
                if alive is None:
                    continue
                rechecked += 1
                if alive and rec.get("miss"):
                    rec["miss"] = 0
                    store.dirty = True
                elif not alive:
                    store.missed(rec, now)
                    gone += rec["status"] == "closed"
        print(f"[recheck] {rechecked} postings re-checked, {gone} found closed")
        closed += gone

    # ── 4c. Jobs still missing a description (e.g. the first run's backlog) ──
    #   first through the job board's own API (cheap), then with the browser while time remains
    if not args.only and not args.dry_run and time.time() < deadline - 60:
        todo = [r for r in store.jobs.values()
                if r["status"] == "open" and not r.get("hidden") and not r.get("has_desc")
                and not r.get("api_tried") and r["uid"] not in pending]
        todo.sort(key=lambda r: (not r.get("priority"), r["first_seen"]))
        todo = todo[: int(rt.get("api_backfill", 800))]

        def api_fill(rec):
            f = detail_from_url(rec["url"])
            if not f:
                return rec, "none"
            try:
                return rec, f()
            except NotFound:
                return rec, "gone"
            except Exception:
                return rec, None

        done, _ = run_pool([(r["uid"], lambda r=r: api_fill(r)) for r in todo], rt.get("detail_workers", 24),
                           max(10.0, deadline - 60 - time.time()), "api back-fill")
        filled = 0
        for rec, d in done:
            if d is None:
                continue                                   # transient error: try again next run
            rec["api_tried"] = True
            store.dirty = True
            if d == "gone":
                store.missed(rec, now)
            elif isinstance(d, dict) and len(d.get("description") or "") > 200:
                reanalyze(rec, d["description"], matcher, (grad, earliest), cfg)
                filled += 1
        if todo:
            print(f"[back-fill] {filled} descriptions via job-board APIs ({len(todo)} tried)")

    if use_browser and time.time() < deadline - 90:
        old_need = [r for r in store.jobs.values()
                    if r["status"] == "open" and not r.get("hidden") and not r.get("has_desc")
                    and not r.get("desc_tried") and (r.get("api_tried") or not detail_from_url(r["url"]))]
        old_need.sort(key=lambda r: (not r.get("priority"), r["first_seen"]), reverse=False)
        old_need = old_need[: int(rt.get("browser_backfill", 60))]
        if old_need:
            texts = fetch_texts([r["url"] for r in old_need], deadline=deadline - 30)
            filled = 0
            for r in old_need:
                if r["url"] not in texts:
                    continue
                r["desc_tried"] = True
                store.dirty = True
                if texts[r["url"]] is None:          # 404 → posting is gone
                    store.missed(r, now)
                    continue
                reanalyze(r, texts[r["url"]], matcher, (grad, earliest), cfg, write=not args.dry_run)
                filled += 1
            print(f"[browser] back-filled descriptions for {filled} older jobs")

    # ── 4d. AI screening of earlier jobs (backlog), newest postings and top companies first ──
    if ai.enabled and not args.dry_run and not ai.stopped and time.time() < deadline - 45:
        todo = [r for r in store.jobs.values()
                if r["status"] == "open" and not r.get("hidden") and r.get("has_desc") and r.get("d")
                and "ai" not in r and r["uid"] not in pending]
        todo.sort(key=lambda r: (not r.get("priority"), -(datetime.fromisoformat(
            (r.get("posted_at") or r["first_seen"]).replace("Z", "+00:00")).timestamp()
            if (r.get("posted_at") or r["first_seen"])[:4].isdigit() else 0)))
        batch = []
        for r in todo[: ai.per_req * max(0, ai.max_req - ai.requests)]:
            try:
                d = json.loads((DATA / "dashboard" / "details" / f"{r['d']}.json").read_text())
            except Exception:
                continue
            if len(d.get("description") or "") > 200:
                batch.append({"id": r["uid"], "title": r["title"], "company": r["company"],
                              "location": "; ".join(r.get("locations") or [])[:120],
                              "text": compact(d["description"])})
        for jid, v in ai.screen(batch, deadline - 20).items():
            if jid in store.jobs:
                apply_ai(store.jobs[jid], v)
    if ai.enabled:
        print(f"[ai] {ai.requests} requests: kept {ai_kept}, rejected {ai_rejected}"
              + (f" (stopped: {ai.stopped})" if ai.stopped else ""))
    elif (cfg.get("ai_filter") or {}).get("enabled"):
        print("[ai] enabled in config.yaml but no GEMINI_API_KEY secret found, so skipped")

    # ── 5. Save, export, notify ──
    new_boards = {r.board for r in ok} - boards_seen
    print(f"[result] {stats['relevant']} new relevant, {stats['merged']} merged duplicates, "
          f"{seeded} silently seeded, {closed} closed, {len(events)} to notify")
    if args.dry_run:
        for ev, r in events[:50]:
            print(f"  [{ev}] {r['company']} | {r['title']} | {r['locations'][:1]} | {r['sponsorship']} | "
                  f"h1b={((r.get('h1b') or {}).get('tech'))} | match={r.get('match')} {r.get('resume', '')}")
        return

    state_changed = bool(new_boards) or dead != state.get("dead_boards", {})
    if store.dirty or not (DATA / "dashboard" / "jobs.json").exists():
        store.save()
        export_dashboard(store, len(companies), cfg.get("regions"))
    if state_changed:
        state["boards_seen"] = sorted(boards_seen | new_boards)
        state["dead_boards"] = dead
        save_state(state)

    if args.no_notify:
        return
    max_age = ncfg.get("max_post_age_days")
    if max_age:
        events = [(e, r) for e, r in events
                  if e != "new" or not r.get("posted_at") or recent(r["posted_at"], 24 * float(max_age))]
    events.sort(key=lambda e: (not e[1].get("priority"), "newgrad" not in e[1].get("tags", []),
                               e[1].get("start") != "fits", e[1]["company"].lower()))
    cap = ncfg.get("max_jobs_per_run", 60)
    dash = env("DASHBOARD_URL")
    if first_ever:
        header = (f"<b>Job tracker is live.</b> Tracking {len(store.jobs)} roles across "
                  f"{len(companies)} company boards + community lists."
                  + (f" Here are the {min(len(events), cap)} posted in the last {seed_hours}h:" if events else ""))
        notify(events[:cap] or [], dash, header) if events else notify_text(header, dash)
    elif events:
        extra = f" (showing {cap} of {len(events)})" if len(events) > cap else ""
        notify(events[:cap], dash, f"<b>🆕 {len(events)} new role{'s' if len(events) != 1 else ''}{extra}</b>")


def reanalyze(rec, text, matcher, dates, cfg, write=True):
    """Re-run description analysis for an existing record once its description becomes available."""
    grad, earliest = dates
    exp = cfg.get("experience", {})
    info = analyze_description(text)
    start, sev = classify_start(rec["title"], text, grad, earliest)
    info["start_evidence"] = sev
    if info["sponsorship"] != "unknown":
        rec["sponsorship"] = info["sponsorship"]
    my = info["min_years"]
    rec["min_years"] = my if (my or 0) >= exp.get("flag_min_years", 3) else None
    rec["phd"] = info["phd"]
    rec["salary"] = info["salary"] or rec.get("salary")
    if start != "unknown" or rec.get("start") == "unknown":
        rec["start"] = start
    rec["has_desc"] = len(text) > 200
    m = matcher.score(text)
    if m:
        rec.update(m)
    if my and my >= exp.get("hide_min_years", 5) and not cfg.get("notifications", {}).get("include_hidden"):
        rec["hidden"], rec["hidden_reason"] = True, f"asks for {my}+ years"
    if write:
        job = SimpleNamespace(description=text, employment=None, locations=rec.get("locations") or [], degree_hint=None)
        rec["d"] = write_detail(rec, job, info)


def notify_text(text, dash):
    from .notify import send_telegram
    send_telegram([text + (f"\n<a href=\"{dash}\">Open dashboard</a>" if dash else "")])


if __name__ == "__main__":
    main()
    print("[done]", flush=True)
    sys.stdout.flush()
    os._exit(0)          # don't wait for abandoned network threads (they're past their time budget)
