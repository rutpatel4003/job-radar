"""Watch custom career sites that have no public job feed (Google, Meta, Apple, Microsoft, TikTok...).

Uses a real headless Chromium (Playwright), because these pages build their job lists with JavaScript.
For each page in config.yaml → career_pages we:
  1. open the search-results URL (put your filters in the URL: location, keywords, early-career level),
  2. collect every link whose URL matches `link_pattern` (the pattern of that site's job pages),
  3. for links we've never seen, open the job page and grab its text as the description.

Test your pages:  python -m tracker.sources.pagewatch --test
Sites change their HTML; if a page starts returning 0 links, open it in your browser, copy a job link,
and adjust `link_pattern`. Some sites (e.g. Tesla) block headless browsers outright.
"""
import re
import sys
import time

from ..ids import any_uid
from ..models import FetchResult, Job

NAV_TIMEOUT = 45_000


def _clean(s):
    return " ".join((s or "").split())


def _collect_links(page, pattern):
    anchors = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [e.href, e.innerText || e.getAttribute('aria-label') || ''])")
    out = {}
    for href, text in anchors:
        if pattern.search(href):
            href = href.split("#")[0]
            text = (text or "").strip()
            if href not in out or len(text) > len(out[href]):
                out[href] = text
    return out


def _title_from(text):
    """Anchor text often contains title + location + blurb; keep the first meaningful line."""
    for line in (text or "").split("\n"):
        line = re.sub(r"^(learn more about|view job|view|apply (now )?(for|to))\s+", "", line.strip(), flags=re.I)
        if 4 <= len(line) <= 160 and not line.lower().startswith(("learn more", "apply", "view", "job details")):
            return line
    return re.sub(r"^learn more about\s+", "", _clean(text), flags=re.I)[:160]


def fetch_pages(pages, is_known, max_details=25, wanted=None, deadline=None):
    """Returns a list of FetchResult (one per page).
    is_known(uid, url) -> bool skips jobs already in the database; wanted(title) -> bool skips
    opening job pages whose link title is clearly irrelevant (e.g. "Senior Sales Manager")."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [FetchResult(f"page:{p['name']}", p["name"], [], ok=False,
                            error="playwright not installed") for p in pages]
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900}, locale="en-US")
        for p in pages:
            board = f"page:{p['name'].lower()}"
            if deadline and time.time() > deadline:
                results.append(FetchResult(board, p["name"], [], ok=False, error="skipped: run time budget reached"))
                continue
            pattern = re.compile(p["link_pattern"])
            try:
                page = ctx.new_page()
                links = {}
                for url in p["urls"] if "urls" in p else [p["url"]]:
                    page.goto(url, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=8_000)
                    except Exception:
                        pass
                    if p.get("wait_for"):
                        page.wait_for_selector(p["wait_for"], timeout=15_000)
                    for _ in range(p.get("scrolls", 2)):
                        page.mouse.wheel(0, 4000)
                        time.sleep(0.7)
                    links.update(_collect_links(page, pattern))
                jobs, details = [], 0
                for href, text in links.items():
                    uid = any_uid(href)
                    job = Job(uid=uid, company=p["name"], title=_title_from(text), url=href,
                              locations=[p["default_location"]] if p.get("default_location") else [],
                              source="careerpage", board=board)
                    if not is_known(uid, href) and details < max_details and (wanted is None or wanted(job.title)) \
                            and not (deadline and time.time() > deadline):
                        details += 1
                        try:
                            page.goto(href, timeout=NAV_TIMEOUT, wait_until="domcontentloaded")
                            try:
                                page.wait_for_load_state("networkidle", timeout=10_000)
                            except Exception:
                                pass
                            h1 = page.query_selector("h1, h2")
                            if h1 and 4 <= len(_clean(h1.inner_text())) <= 160:
                                job.title = _clean(h1.inner_text())
                            job.description = page.inner_text("body")[:30_000]
                            m = re.search(r"(?im)^\s*(?:location|locations)\s*[:\n]\s*(.+)$", job.description)
                            if m:
                                job.locations = [_clean(m.group(1))[:120]]
                        except Exception:
                            pass
                    jobs.append(job)
                page.close()
                # A search page shows only its top results, so absence != closed.
                results.append(FetchResult(board, p["name"], jobs, complete=False,
                                           ok=bool(jobs), error="" if jobs else "0 job links found"))
            except Exception as e:
                results.append(FetchResult(board, p["name"], [], ok=False, error=f"{type(e).__name__}: {e}"[:200]))
        browser.close()
    return results


if __name__ == "__main__":
    from ..config import load_config
    cfg = load_config()
    pages = [p for p in cfg.get("career_pages") or [] if p.get("enabled", True)]
    if "--test" not in sys.argv:
        print("usage: python -m tracker.sources.pagewatch --test [name]")
        sys.exit(0)
    only = [a.lower() for a in sys.argv[2:]]
    if only:
        pages = [p for p in pages if p["name"].lower() in only]
    for r in fetch_pages(pages, lambda u, h: False, max_details=1):
        status = "OK " if r.ok else "ERR"
        print(f"{status} {r.company:<12} {len(r.jobs):>4} links  {r.error}")
        for j in r.jobs[:5]:
            print(f"      {j.title[:70]:<70} {j.url[:90]}")


def _best_text(page):
    """Main content text of a job page (largest of main/article/role=main, else body)."""
    best = ""
    for sel in ("main", "article", "[role=main]", "#content", "body"):
        try:
            for el in page.query_selector_all(sel)[:3]:
                t = el.inner_text()
                if len(t) > len(best) * 1.2 or (sel == "body" and not best):
                    best = t
        except Exception:
            continue
        if len(best) > 1500 and sel != "body":
            break
    return re.sub(r"\n{3,}", "\n\n", best).strip()[:30_000]


def fetch_texts(urls, deadline=None, per_page_timeout=25_000):
    """Open each job URL in headless Chromium and return {url: text}. Stops at `deadline` (epoch secs)."""
    out = {}
    if not urls:
        return out
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return out
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
            viewport={"width": 1366, "height": 900}, locale="en-US")
        page = ctx.new_page()
        for url in urls:
            if deadline and time.time() > deadline:
                break
            try:
                resp = page.goto(url, timeout=per_page_timeout, wait_until="domcontentloaded")
                if resp and resp.status in (404, 410):
                    out[url] = None            # gone
                    continue
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                text = _best_text(page)
                if len(text) > 300:
                    out[url] = text
            except Exception:
                continue
        browser.close()
    return out
