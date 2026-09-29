"""Local AI review of saved jobs, run on YOUR PC:  python -m tracker.audit   (or scripts/daily_audit.sh)

A local LLM (Qwen3.8-27B served by HyperQwen on your GPU, or any OpenAI-compatible server) reads each job's
description next to your two resumes and writes data/ai_review.json. The dashboard shows it as an "AI fit"
column, a sort and a filter, and a panel in each job's details.

Why you can trust what it shows
  • Annotate-only: it never hides, closes or deletes anything. The regex pipeline stays in charge.
  • Every factual claim (years required, sponsorship, start date, staffing agency) must come with an exact
    quote from the description. Python checks the quote really is in the description; if not, the claim is
    discarded and listed as "unverified". Skills are kept only if they appear in the description / resume.
  • The fit score and reason are the model's opinion and are labelled as such.
  • Nothing leaves your PC: the model runs locally, and only public job text + your skills summary are sent to it.

Options:  --limit N      review at most N jobs this run
          --all          re-review everything (ignores earlier reviews)
          --dry-run      review 5 jobs and print the results without saving
          --no-digest    don't send the Telegram "best matches" message
          --base-url / --model   override config.yaml → local_ai
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

from .config import ROOT, load_config

DATA = ROOT / "data"
OUT = DATA / "ai_review.json"
VERSION = 1

SYSTEM = """You review job postings for ONE candidate and answer with a single JSON object only.

CANDIDATE
{profile}

RESUME "SDE"
{sde}

RESUME "ML/AI"
{ml}

HOW TO ANSWER
- fit (1-5): how good a match this job is for this candidate right now. 5 = entry-level role squarely in the
  candidate's skills; 3 = plausible but a stretch or unclear level; 1 = clearly not for them (senior, wrong field,
  not software/ML, hardware-only, sales/support).
- resume: which resume to send, "SDE" or "ML/AI".
- level: "entry" | "mid" | "senior" | "unclear", judged from the posting.
- years_required: the minimum years of professional experience the posting REQUIRES (not "preferred"), or null.
  If a degree can substitute ("3 years or a Master's"), use the smallest requirement that applies to someone with
  an MS. Ignore ages ("18 years of age").
- sponsorship: "yes" (says it sponsors visas), "no" (says it won't sponsor / needs no sponsorship now or later),
  "citizens_only" (U.S. citizenship, U.S. person, ITAR or a security clearance required), or "not_mentioned".
- start: "fits" (start date / graduation window allows starting June 2027), "too_early" (must start or graduate
  before that), or "not_mentioned".
- staffing_agency: true only if the posting is from a staffing / consulting body shop placing people at a client
  (mentions C2C, W2 through the agency, "our client", "end client"…).
- For years_required, sponsorship, start and staffing_agency give a *_quote: a short EXACT copy (5-30 words) of the
  sentence in the posting that shows it, copied character for character. Use null when not mentioned.
  Never paraphrase a quote. If you cannot quote it, the answer is null / "not_mentioned" / false.
- matched_skills: up to 8 skills the posting asks for that appear on the chosen resume.
- missing_skills: up to 6 important skills the posting asks for that are NOT on either resume.
- reason: at most 20 words, why this fit score.

Return exactly these keys:
{{"fit": 1, "resume": "SDE", "level": "entry", "years_required": null, "years_quote": null,
"sponsorship": "not_mentioned", "sponsorship_quote": null, "start": "not_mentioned", "start_quote": null,
"staffing_agency": false, "staffing_quote": null, "matched_skills": [], "missing_skills": [], "reason": ""}}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer", "minimum": 1, "maximum": 5},
        "resume": {"type": "string", "enum": ["SDE", "ML/AI"]},
        "level": {"type": "string", "enum": ["entry", "mid", "senior", "unclear"]},
        "years_required": {"type": ["integer", "null"]},
        "years_quote": {"type": ["string", "null"]},
        "sponsorship": {"type": "string", "enum": ["yes", "no", "citizens_only", "not_mentioned"]},
        "sponsorship_quote": {"type": ["string", "null"]},
        "start": {"type": "string", "enum": ["fits", "too_early", "not_mentioned"]},
        "start_quote": {"type": ["string", "null"]},
        "staffing_agency": {"type": "boolean"},
        "staffing_quote": {"type": ["string", "null"]},
        "matched_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "missing_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "reason": {"type": "string"},
    },
    "required": ["fit", "resume", "level", "years_required", "years_quote", "sponsorship", "sponsorship_quote",
                 "start", "start_quote", "staffing_agency", "staffing_quote", "matched_skills", "missing_skills",
                 "reason"],
}


# ── quote verification ──────────────────────────────────────────────────────

def _norm(s):
    s = (s or "").lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[–—−]", "-", s)
    s = re.sub(r"[^a-z0-9$%+#'.\-/ ]+", " ", s)
    return " ".join(s.split())


def quote_ok(quote, text_norm, min_share=0.9):
    """True if the quote (normalised) appears in the description, allowing tiny differences
    (a dropped word or punctuation) — at least `min_share` of it must match one continuous stretch."""
    q = _norm(quote)
    if len(q) < 8:
        return False
    if q in text_norm:
        return True
    m = SequenceMatcher(None, q, text_norm, autojunk=False).find_longest_match(0, len(q), 0, len(text_norm))
    return m.size >= min_share * len(q)


def verify(raw, text, resumes_text):
    """Keep only claims backed by a real quote; return the compact review record."""
    tn = _norm(text)
    dropped = []

    def claim(value, quote, empty, name):
        if value in (None, empty, False):
            return empty, None
        if quote and quote_ok(quote, tn):
            return value, " ".join(str(quote).split())[:300]
        dropped.append(name)
        return empty, None

    years = raw.get("years_required")
    try:
        years = int(years) if years is not None else None
    except (TypeError, ValueError):
        years = None
    if years is not None and not 0 <= years <= 20:
        years = None
    years, yq = claim(years, raw.get("years_quote"), None, "years")
    if years is not None and yq and not re.search(r"\b%d\b|\b(one|two|three|four|five|six|seven|eight|nine|ten)\b" % years, yq, re.I):
        years, yq = None, None           # the quote must actually contain the number
        dropped.append("years")
    spons, sq = claim(raw.get("sponsorship") if raw.get("sponsorship") in ("yes", "no", "citizens_only") else None,
                      raw.get("sponsorship_quote"), None, "sponsorship")
    start, stq = claim(raw.get("start") if raw.get("start") in ("fits", "too_early") else None,
                       raw.get("start_quote"), None, "start")
    staff, stfq = claim(True if raw.get("staffing_agency") is True else None, raw.get("staffing_quote"),
                        False, "staffing")
    low = text.lower()
    rlow = resumes_text.lower()
    matched = [s for s in (raw.get("matched_skills") or []) if isinstance(s, str) and s.strip()
               and s.lower().strip() in low and s.lower().strip() in rlow][:8]
    missing = [s for s in (raw.get("missing_skills") or []) if isinstance(s, str) and s.strip()
               and s.lower().strip() in low and s.lower().strip() not in rlow][:6]
    try:
        fit = max(1, min(5, int(raw.get("fit"))))
    except (TypeError, ValueError):
        fit = None
    out = {"fit": fit, "resume": raw.get("resume") if raw.get("resume") in ("SDE", "ML/AI") else None,
           "level": raw.get("level") if raw.get("level") in ("entry", "mid", "senior", "unclear") else "unclear",
           "why": " ".join(str(raw.get("reason") or "").split())[:200],
           "years": years, "years_q": yq, "spons": spons, "spons_q": sq, "start": start, "start_q": stq,
           "staffing": bool(staff), "staffing_q": stfq, "matched": matched, "missing": missing}
    if dropped:
        out["unverified"] = sorted(set(dropped))
    return {k: v for k, v in out.items() if v not in (None, [], "", False) or k in ("fit",)}


# ── talking to the local model ─────────────────────────────────────────────

def load_env_file(path=ROOT / ".env.local"):
    """KEY=value lines from .env.local (never committed) into os.environ, without overriding."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class LocalLLM:
    def __init__(self, base_url, model="auto", api_key=None, timeout=180):
        self.base = base_url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json"})
        if api_key:
            self.s.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout
        self.model = model
        self.mode = "json_schema"          # falls back to json_object, then plain, if the server refuses

    def connect(self):
        r = self.s.get(f"{self.base}/models", timeout=15)
        r.raise_for_status()
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
        if not ids:
            raise RuntimeError("the server lists no models")
        if self.model in (None, "", "auto"):
            self.model = ids[0]
        return self.model

    def ask(self, system, user):
        body = {"model": self.model, "temperature": 0, "max_tokens": 700,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "chat_template_kwargs": {"enable_thinking": False}}     # Qwen: answer directly, no <think>
        for mode in ("json_schema", "json_object", "plain"):
            if ["json_schema", "json_object", "plain"].index(mode) < ["json_schema", "json_object", "plain"].index(self.mode):
                continue
            b = dict(body)
            if mode == "json_schema":
                b["response_format"] = {"type": "json_schema", "json_schema": {"name": "review", "schema": SCHEMA}}
            elif mode == "json_object":
                b["response_format"] = {"type": "json_object"}
            r = self.s.post(f"{self.base}/chat/completions", json=b, timeout=self.timeout)
            if r.status_code == 400 and mode != "plain":
                self.mode = {"json_schema": "json_object", "json_object": "plain"}[mode]
                continue
            r.raise_for_status()
            content = r.json()["choices"][0]["message"].get("content") or ""
            return parse_json(content)
        raise RuntimeError("server rejected every request format")


def parse_json(content):
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0) if m else content)


# ── main ────────────────────────────────────────────────────────────────────

def load_reviews():
    try:
        d = json.loads(OUT.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("reviews"), dict):
            return d
    except Exception:
        pass
    return {"version": VERSION, "reviews": {}}


def save_reviews(data, jobs):
    # forget reviews of jobs that left the database, so the file doesn't grow forever
    data["reviews"] = {u: v for u, v in data["reviews"].items() if u in jobs}
    data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")


def _ts(iso):
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def candidates(jobs, reviews, lc, redo_all=False):
    again = timedelta(days=float(lc.get("review_again_after_days", 30)))
    now = datetime.now(timezone.utc)
    out, no_desc = [], 0
    for uid, r in jobs.items():
        if r.get("status") != "open" or not r.get("d"):
            if r.get("status") == "open" and not r.get("d"):
                no_desc += 1
            continue
        if r.get("hidden") and not lc.get("include_hidden", True):
            continue
        if r.get("staffing") and lc.get("skip_staffing", True):
            continue
        prev = reviews.get(uid)
        if prev and not redo_all and prev.get("title") == r.get("title") and now - _ts(prev.get("at")) < again:
            continue
        out.append(r)
    # visible jobs before hidden ones, top companies first, newest postings first — so a capped run covers
    # what matters and the backlog clears over a few days
    out.sort(key=lambda r: (bool(r.get("hidden")), not r.get("priority"),
                            -_ts(r.get("posted_at") or r.get("first_seen")).timestamp()))
    return out, no_desc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local AI review of saved jobs (writes data/ai_review.json)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-digest", action="store_true")
    ap.add_argument("--base-url")
    ap.add_argument("--model")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass
    load_env_file()
    cfg = load_config()
    lc = cfg.get("local_ai") or {}
    llm = LocalLLM(args.base_url or lc.get("base_url", "http://localhost:18020/v1"),
                   args.model or lc.get("model", "auto"), os.environ.get(lc.get("api_key_env", "VLLM_API_KEY")))
    try:
        model = llm.connect()
    except Exception as e:
        print(f"[audit] can't reach the local model at {llm.base} ({type(e).__name__}: {e}).\n"
              f"        Start it first:  cd ~/HyperQwen && docker compose --profile batch up -d")
        return 2
    print(f"[audit] model: {model} at {llm.base}")

    jobs = json.loads((DATA / "jobs.json").read_text(encoding="utf-8"))
    data = load_reviews()
    todo, no_desc = candidates(jobs, data["reviews"], lc, args.all)
    limit = 5 if args.dry_run else (args.limit or int(lc.get("max_jobs_per_run", 3000)))
    todo = todo[:limit]
    print(f"[audit] {len(todo)} jobs to review ({no_desc} open jobs have no description yet and are skipped)")
    if not todo:
        return 0

    resumes = lc.get("resumes") or {}
    profile = ((cfg.get("ai_filter") or {}).get("profile") or "").strip()
    system = SYSTEM.format(profile=profile, sde=(resumes.get("SDE") or "").strip(), ml=(resumes.get("ML/AI") or "").strip())
    resumes_text = " ".join(resumes.values()) + " " + " ".join(
        str(s).lstrip("=") for sk in ((cfg.get("profile") or {}).get("resumes") or {}).values() for s in sk)
    chars = int(lc.get("desc_chars", 6000))
    details = DATA / "dashboard" / "details"

    def one(r):
        try:
            text = json.loads((details / f"{r['d']}.json").read_text(encoding="utf-8")).get("description") or ""
        except Exception:
            return r, None, "no description file"
        if len(text) < 200:
            return r, None, "description too short"
        text = re.sub(r"\n{3,}", "\n\n", text)[:chars]
        user = (f"Company: {r['company']}\nTitle: {r['title']}\nLocation: {'; '.join(r.get('locations') or [])[:200]}\n"
                f"\nJOB POSTING:\n{text}")
        try:
            raw = llm.ask(system, user)
        except Exception as e:
            return r, None, f"{type(e).__name__}: {str(e)[:120]}"
        rev = verify(raw, text, resumes_text)
        rev.update(at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), title=r["title"], model=model)
        return r, rev, None

    t0, done, errors, reviewed = time.time(), 0, 0, []
    with ThreadPoolExecutor(max_workers=int(lc.get("concurrency", 32))) as ex:
        futs = [ex.submit(one, r) for r in todo]
        for f in as_completed(futs):
            r, rev, err = f.result()
            done += 1
            if err:
                errors += 1
                if errors <= 5:
                    print(f"  ✗ {r['company']} | {r['title'][:50]}: {err}")
            else:
                data["reviews"][r["uid"]] = rev
                reviewed.append((r, rev))
                if args.dry_run:
                    print(json.dumps({"job": f"{r['company']} | {r['title']}", **rev}, ensure_ascii=False, indent=1))
            if done % 50 == 0 or done == len(todo):
                rate = done / max(1e-6, time.time() - t0)
                print(f"  … {done}/{len(todo)} reviewed ({rate * 60:.0f}/min, {errors} errors)")
                if not args.dry_run and done % 250 == 0:
                    save_reviews(data, jobs)             # progress survives Ctrl-C
    if errors == len(todo):
        print("[audit] every request failed; nothing saved")
        return 1
    if not args.dry_run:
        data["model"] = model
        save_reviews(data, jobs)
        unver = sum(1 for _, v in reviewed if v.get("unverified"))
        print(f"[audit] saved {len(reviewed)} reviews to data/ai_review.json in {time.time() - t0:.0f}s "
              f"({unver} had a claim discarded because its quote wasn't in the posting)")
        if not args.no_digest:
            digest(reviewed, jobs, int(lc.get("digest_top", 15)))
    return 0


def digest(reviewed, jobs, top):
    """Telegram message: the best new matches from this run (needs TELEGRAM_* in .env.local)."""
    from .notify import send_telegram
    try:
        tracked = set((json.loads((DATA / "tracking.json").read_text()).get("jobs") or {}).keys())
    except Exception:
        tracked = set()
    good = [(r, v) for r, v in reviewed
            if (v.get("fit") or 0) >= 4 and not r.get("hidden") and not r.get("staffing") and r["uid"] not in tracked
            and r.get("sponsorship") not in ("no_sponsor", "citizen") and v.get("spons") not in ("no", "citizens_only")
            and v.get("start") != "too_early" and r.get("start") != "too_early"]
    good.sort(key=lambda x: (-(x[1].get("fit") or 0), not x[0].get("priority"),
                             -_ts(x[0].get("posted_at") or x[0].get("first_seen")).timestamp()))
    if not good:
        print("[audit] no fit-4+ jobs in this run, no digest sent")
        return
    import html
    lines = [f"<b>🤖 Local AI review: {len(good)} strong match{'es' if len(good) != 1 else ''}</b>"
             + (f" (top {top})" if len(good) > top else "")]
    for r, v in good[:top]:
        extra = ", ".join(x for x in [f"use {v.get('resume')}" if v.get("resume") else "",
                                      f"{v['years']}+ yrs" if v.get("years") else "",
                                      "sponsors ✓" if v.get("spons") == "yes" else ""] if x)
        lines.append(f"\n{'🔥 ' if r.get('priority') else ''}<b>{html.escape(r['company'])}</b> · fit {v['fit']}/5\n"
                     f"<a href=\"{html.escape(r['url'], quote=True)}\">{html.escape(r['title'])}</a>\n"
                     f"{html.escape(v.get('why') or '')}" + (f"\n{html.escape(extra)}" if extra else ""))
    dash = os.environ.get("DASHBOARD_URL")
    if dash:
        lines.append(f"\n<a href=\"{dash}\">Open dashboard</a>")
    if send_telegram(lines):
        print(f"[audit] Telegram digest sent ({min(top, len(good))} jobs)")
    else:
        print("[audit] no TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env.local, digest not sent")


if __name__ == "__main__":
    sys.exit(main())
