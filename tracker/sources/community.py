"""Community-maintained GitHub job lists (Simplify, speedyapply).

These cover big-tech career sites we can't read directly (Google, Meta, Apple, Microsoft, TikTok...)
and are also how weekly discovery learns about new companies.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone

from ..filters import SIMPLIFY_SPONSOR
from ..http import get_text
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


def fetch_markdown_list(url):
    parts = url.split("/")
    board = f"community:{parts[3]}/{parts[4]}" if len(parts) > 4 else f"community:{url}"
    try:
        md = get_text(url)
    except Exception as e:
        return FetchResult(board, board, [], ok=False, error=f"{type(e).__name__}: {e}"[:200])
    jobs = []
    for r in parse_markdown_tables(md):
        locs = [re.sub(r"\s*\+\d+$", "", r["location"])] if r["location"] else []
        jobs.append(Job(uid=any_uid(r["url"]), company=r["company"], title=r["title"], url=r["url"],
                        locations=locs, source=parts[3] if len(parts) > 3 else "list", board=board,
                        posted_at=r["posted"]))
    return FetchResult(board, board, jobs, complete=False)
