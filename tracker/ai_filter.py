"""AI screening: an LLM reads each job description and decides whether it's actually for you.

Default provider: Google Gemini (free tier, Flash-Lite ≈ 500 requests/day). Get a key at
https://aistudio.google.com/apikey and save it as the GEMINI_API_KEY repository secret.
Any OpenAI-compatible API works (Groq, OpenRouter, Anthropic's OpenAI-compatible endpoint, ...):
change base_url / model / api_key_env in config.yaml → ai_filter.

Several jobs are graded per request (jobs_per_request) to stretch the free quota. Only public job text
and the short profile from config.yaml are sent — never your name, contact details or notes.
"""
import json
import re
import time

from .config import env
from .http import SESSION

VERDICTS = ("keep", "reject")

SYSTEM = """You screen job postings for one specific candidate and answer ONLY with JSON.

Candidate:
{profile}

For each job decide "keep" or "reject".
REJECT only when the posting clearly shows at least one of:
- it hard-requires more than 2 years of full-time professional experience, with no degree alternative
  (if a Master's/PhD can substitute for the years, it is NOT a reason to reject)
- it is senior / staff / lead / manager level, or clearly mid-level (e.g. "Engineer II/III", 3+ yrs)
- it is not a hands-on software, ML/AI, or computer-vision engineering / research-engineering role
  (sales, support, IT, recruiting, product/program management, pure data analyst, hardware-only, etc.)
- it is an internship, co-op, contract or part-time role
- it requires U.S. citizenship, a security clearance, or "U.S. person" status
- it hard-requires a PhD
- it must start well before {earliest_start} (e.g. "start immediately", "December 2026 graduates only")
- the location is outside the United States
When unsure, KEEP. Do not reject just because sponsorship is not mentioned.

Also report: fit 1-5 (5 = great match for this candidate's skills and level), level
("entry" | "mid" | "senior" | "unclear"), sponsorship as stated in the posting
("yes" | "no" | "citizens_only" | "unclear"), and a reason of at most 12 words.

Reply with exactly: {{"results": [{{"id": "...", "verdict": "keep|reject", "fit": 1-5,
"level": "...", "sponsorship": "...", "reason": "..."}}, ...]}} — one entry per job, same ids."""

KEY_LINES = re.compile(
    r"year|experience|degree|bachelor|master|ph\.?d|graduat|qualif|require|must|sponsor|visa|citizen|"
    r"clearance|authoriz|start|intern|senior|level|location|remote|on-?site|salary|compensation", re.I)


def compact(text, limit=1800):
    """Keep the opening (what the role is) plus every line that talks about requirements."""
    text = re.sub(r"[ \t\u00a0]+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:600]
    rest = [ln.strip() for ln in text[600:].split("\n") if ln.strip()]
    picked = [ln[:300] for ln in rest if KEY_LINES.search(ln)]
    body = "\n".join(picked)
    return (head + "\n…\n" + body)[:limit]


class RateLimited(Exception):
    pass


class AIFilter:
    def __init__(self, cfg):
        self.c = cfg.get("ai_filter") or {}
        self.key = env(self.c.get("api_key_env", "GEMINI_API_KEY"))
        self.enabled = bool(self.c.get("enabled", False) and self.key)
        self.models = [self.c.get("model", "gemini-flash-lite-latest")] + list(self.c.get("fallback_models") or [])
        self.base = self.c.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
        self.per_req = int(self.c.get("jobs_per_request", 6))
        self.max_req = int(self.c.get("max_requests_per_run", 40))
        self.gap = 60.0 / max(1, int(self.c.get("requests_per_minute", 10)))
        sd = cfg.get("start_date") or {}
        self.system = SYSTEM.format(profile=(self.c.get("profile") or "").strip(),
                                    earliest_start=sd.get("earliest_start", "2027-06"))
        self.requests = 0
        self.stopped = None           # reason we stopped calling the API this run
        self._last = 0.0

    def _call(self, jobs):
        user = "\n\n".join(
            f"### id: {j['id']}\nTitle: {j['title']}\nCompany: {j['company']}\nLocation: {j['location']}\n"
            f"Description:\n{j['text']}" for j in jobs)
        wait = self._last + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        last_err = None
        for model in self.models:
            self._last = time.time()
            r = SESSION.post(f"{self.base}/chat/completions", timeout=(10, 90),
                             headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                             json={"model": model, "temperature": 0,
                                   "response_format": {"type": "json_object"},
                                   "messages": [{"role": "system", "content": self.system},
                                                {"role": "user", "content": user}]})
            self.requests += 1
            if r.status_code == 429:
                raise RateLimited(r.text[:200])
            if r.status_code in (400, 404) and "model" in r.text.lower():
                last_err = f"model {model} unavailable"
                continue                                    # try the fallback model
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            content = r.json()["choices"][0]["message"]["content"] or ""
            content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
            data = json.loads(content)
            items = data.get("results", data) if isinstance(data, dict) else data
            out = {}
            for it in items if isinstance(items, list) else []:
                if isinstance(it, dict) and str(it.get("id")) and it.get("verdict") in VERDICTS:
                    out[str(it["id"])] = it
            return out, model
        raise RuntimeError(last_err or "no model available")

    def screen(self, jobs, deadline):
        """jobs: list of dicts {id, title, company, location, text}. Returns {id: verdict dict}."""
        results = {}
        if not self.enabled:
            return results
        for i in range(0, len(jobs), self.per_req):
            if self.requests >= self.max_req:
                self.stopped = f"request cap ({self.max_req}/run) reached"
                break
            if time.time() + self.gap + 20 > deadline:
                self.stopped = "run time budget reached"
                break
            batch = jobs[i: i + self.per_req]
            try:
                got, model = self._call(batch)
            except RateLimited as e:
                self.stopped = f"rate limited by provider ({e})"
                break
            except Exception as e:
                self.stopped = f"error: {type(e).__name__}: {str(e)[:150]}"
                break
            for jid, v in got.items():
                v["model"] = model
                results[jid] = v
        return results
