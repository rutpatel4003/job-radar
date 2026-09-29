"""Community-maintained GitHub job lists (Simplify, speedyapply).

These cover big-tech career sites we can't read directly (Google, Meta, Apple, Microsoft, TikTok...)
and are also how weekly discovery learns about new companies.
"""
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from functools import partial
from urllib.parse import urlparse

from ..config import ROOT
from ..filters import SIMPLIFY_SPONSOR
from ..http import SESSION, NotFound, get_text
from ..ids import any_uid
from ..models import FetchResult, Job


def _iso(ts):
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()
    except Exception:
        return None


def simplify_listings(url):
    return json.loads(get_text(url, timeout=90))


def fetch_simplify(url, max_age_days=120, listings=None):
    owner = url.split("/")[3] if url.count("/") > 3 else "simplify"
    board = f"community:{owner.lower()}"
    try:
        data = listings if listings is not None else simplify_listings(url)
    except Exception as e:
        return FetchResult(board, "Simplify", [], ok=False, error=f"{type(e).__name__}: {e}"[:200])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
    jobs, closed = [], set()
    for x in data:
        if not x.get("url") or not x.get("is_visible", True):
            continue
        uid = any_uid(x["url"])
        if not x.get("active"):
            if (x.get("date_updated") or 0) >= cutoff:
                closed.add(uid)
            continue
        if (x.get("date_posted") or 0) < cutoff:
            continue
        jobs.append(Job(
            uid=uid,
            company=x.get("company_name") or "",
            title=(x.get("title") or "").strip(),
            url=x["url"],
            locations=x.get("locations") or [],
            source=owner.lower(),
            board=board,
            posted_at=_iso(x.get("date_posted")),
            sponsorship_hint=SIMPLIFY_SPONSOR.get(x.get("sponsorship")),
            degree_hint=x.get("degrees") or [],
        ))
    # "complete=False": a job missing from Simplify doesn't mean it closed; we rely on explicit inactive flags.
    return FetchResult(board, owner, jobs, complete=False, closed_uids=closed)


_ROW = re.compile(r"^\|(.+)\|\s*$")
_HREF = re.compile(r'href="([^"]+)"')
_STRONG = re.compile(r"<strong>(.*?)</strong>")
_TAGS = re.compile(r"<[^>]+>")


# ── Generic markdown-table lists (jobright-ai, zapplyjobs, and most other "New Grad" repos) ──
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_COLS = {
    "company": ("company",),
    "title": ("role", "position", "job title", "title"),
    "location": ("location",),
    "posted": ("posted", "date posted", "age", "date"),
    "apply": ("apply", "application", "link", "posting"),
}


def _cell_text(c):
    c = _MD_LINK.sub(lambda m: m.group(1), c)
    return html.unescape(_TAGS.sub("", c)).replace("**", "").strip()


def _cell_url(c):
    m = _HREF.search(c) or _MD_LINK.search(c)
    if not m:
        return None
    return html.unescape(m.group(1) if m.re is _HREF else m.group(2))


def _posted(s):
    s = (s or "").strip().lower()
    now = datetime.now(timezone.utc)
    m = re.match(r"(\d+)\s*(m|h|d|w|mo)\b", s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n),
                 "w": timedelta(weeks=n), "mo": timedelta(days=30 * n)}[u]
        return (now - delta).isoformat()
    for fmt in ("%b %d", "%B %d"):
        try:
            d = datetime.strptime(s.title(), fmt).replace(year=now.year, tzinfo=timezone.utc)
            if d > now + timedelta(days=2):
                d = d.replace(year=now.year - 1)
            return d.isoformat()
        except ValueError:
            pass
    return None


def parse_markdown_tables(md):
    header, last_company = None, ""
    for line in md.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            header = None if line.strip() == "" else header
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        low = [_cell_text(c).lower() for c in cells]
        if any(l in ("company",) for l in low):
            header = {}
            for key, names in _COLS.items():
                for i, l in enumerate(low):
                    if any(l == n or l.startswith(n) for n in names):
                        header.setdefault(key, i)
            continue
        if header is None or set("".join(cells)) <= set("-: "):
            continue
        get = lambda k: cells[header[k]] if k in header and header[k] < len(cells) else ""
        company = _cell_text(get("company"))
        if company in ("↳", "") and last_company:
            company = last_company
        last_company = company
        url = _cell_url(get("apply")) or _cell_url(get("title"))
        title = _cell_text(get("title"))
        if not (url and title and company):
            continue
        yield {"company": company, "title": title, "location": _cell_text(get("location")),
               "url": url, "posted": _posted(_cell_text(get("posted")))}


def fetch_markdown_list(url, resolve_budget_s=90):
    parts = url.split("/")
    board = f"community:{parts[3]}/{parts[4]}" if len(parts) > 4 else f"community:{url}"
    try:
        md = get_text(url)
    except Exception as e:
        return FetchResult(board, board, [], ok=False, error=f"{type(e).__name__}: {e}"[:200])
    rows = list(parse_markdown_tables(md))
    real = resolve_redirects([r["url"] for r in rows], budget_s=resolve_budget_s)
    jobs = []
    for r in rows:
        link = real.get(r["url"]) or r["url"]
        locs = [re.sub(r"\s*\+\d+$", "", r["location"])] if r["location"] else []
        jobs.append(Job(uid=any_uid(link), company=r["company"], title=r["title"], url=link,
                        locations=locs, source=parts[3] if len(parts) > 3 else "list", board=board,
                        posted_at=r["posted"]))
    return FetchResult(board, board, jobs, complete=False)


# ── Redirect links → the real job-board link ──────────────────────────────────
# zapply.jobs rows link to zapply.jobs/l/d/<id>, which redirects to the company's own posting (Workday,
# Greenhouse…). Resolving it gives the real job id (so it merges with the same job from the company board)
# and lets the job-board API fill in the description. Results are cached in data/redirects.json.
REDIRECT_HOSTS = ("zapply.jobs",)
_REDIR = {"cache": None, "dirty": False}
_REDIR_LOCK = threading.Lock()


def _redir_path():
    return ROOT / "data" / "redirects.json"


def _redir_key(url):
    return url.split("?", 1)[0].rstrip("/")


def _redir_cache():
    with _REDIR_LOCK:
        if _REDIR["cache"] is None:
            try:
                _REDIR["cache"] = json.loads(_redir_path().read_text())
            except Exception:
                _REDIR["cache"] = {}
        return _REDIR["cache"]


def cached_redirect(url):
    """Resolved link from the cache only (no network); used by discovery."""
    return _redir_cache().get(_redir_key(url))


def _resolve_one(url):
    try:
        r = SESSION.get(url, timeout=(8, 15), allow_redirects=True, stream=True,
                        headers={"Accept": "text/html,*/*"})
        final = r.url
        r.close()
        host = urlparse(final).netloc.lower()
        return None if any(h in host for h in REDIRECT_HOSTS) else final
    except Exception:
        return None


def resolve_redirects(urls, budget_s=90, max_new=400):
    """{original_url: real_url} for redirect-style links; unknown ones are resolved (time-boxed)."""
    cache = _redir_cache()
    want = [u for u in dict.fromkeys(urls) if any(h in urlparse(u).netloc.lower() for h in REDIRECT_HOSTS)]
    todo = [u for u in want if _redir_key(u) not in cache][:max_new]
    if todo:
        deadline = time.time() + budget_s
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_resolve_one, u): u for u in todo}
            try:
                for f in as_completed(futs, timeout=max(5.0, budget_s)):
                    final = f.result()
                    if final:
                        with _REDIR_LOCK:
                            cache[_redir_key(futs[f])] = final
                            _REDIR["dirty"] = True
                    if time.time() > deadline:
                        break
            except FuturesTimeout:
                pass
            for f in futs:
                f.cancel()
    return {u: cache[_redir_key(u)] for u in want if _redir_key(u) in cache}


def save_redirects():
    if _REDIR["dirty"] and _REDIR["cache"] is not None:
        with _REDIR_LOCK:
            _redir_path().write_text(json.dumps(_REDIR["cache"], indent=0, sort_keys=True) + "\n")
            _REDIR["dirty"] = False


# ── ApplyGuy (github.com/ApplyGuy/2027-New-Grad-Jobs): JSON with the real job-board link + verified date ──
def fetch_applyguy(url):
    board = "community:applyguy"
    try:
        data = json.loads(get_text(url))
    except Exception as e:
        return FetchResult(board, "ApplyGuy", [], ok=False, error=f"{type(e).__name__}: {e}"[:200])
    items = data.get("jobs") if isinstance(data, dict) else data
    jobs = []
    for x in items or []:
        link = x.get("listingUrl") or x.get("url")
        if not (link and x.get("title") and x.get("company")):
            continue
        posted = x.get("posted")
        if posted and len(posted) == 10:
            posted += "T12:00:00+00:00"
        jobs.append(Job(uid=any_uid(link), company=x["company"].strip(), title=x["title"].strip(), url=link,
                        locations=[x["location"]] if x.get("location") else [], source="applyguy", board=board,
                        posted_at=posted))
    return FetchResult(board, "ApplyGuy", jobs, complete=False)


# ── jobright.ai job pages: no link to the original posting, but a structured summary of it ──
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def jobright_detail(url):
    html_text = get_text(url, timeout=(8, 20), headers={"Accept": "text/html"})
    m = _NEXT.search(html_text)
    if not m:
        return {"description": ""}
    jr = ((((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("dataSource") or {})
          .get("jobResult") or {})
    if not jr or jr.get("isDeleted"):
        raise NotFound(url)
    q = jr.get("qualifications") or {}
    lines = [jr.get("jobSummary") or "", jr.get("jdResponsibilitySummary") or ""]
    for head, items in (("Responsibilities", jr.get("coreResponsibilities")),
                        ("Requirements", q.get("mustHave") if isinstance(q, dict) else None),
                        ("Preferred", q.get("preferredHave") if isinstance(q, dict) else None),
                        ("Skills", jr.get("skillSummaries"))):
        if items:
            lines.append(f"\n{head}:\n" + "\n".join(f"• {str(i).lstrip('� ').strip()}" for i in items))
    if jr.get("salaryDesc"):
        lines.append(f"\nPay: {jr['salaryDesc']}")
    text = "\n".join(l for l in lines if l).strip()
    return {"description": ("(Summary from jobright.ai; the original posting may say more.)\n\n" + text) if text else ""}


def jobright_fetcher_from_url(url):
    return partial(jobright_detail, url) if "jobright.ai/jobs/info/" in url else None
