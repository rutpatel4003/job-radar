"""Build data/h1b.json from the U.S. Department of Labor's public H-1B (LCA) disclosure files.

Why: community lists' sponsorship labels are often wrong. An employer's *actual* H-1B filing history
for computer occupations (SOC 15-xxxx) is the best free signal of whether they sponsor.

    python -m tracker.h1b            # runs monthly in GitHub Actions (takes ~10–25 min)

Output: {normalized_employer: [tech_filings, entry_level_tech_filings, total_filings, display_name]}
 - tech_filings:        certified LCAs for SOC 15-xxxx (software/ML/data roles)
 - entry_level_tech:    of those, prevailing-wage level I or II (typical new-grad levels)
"""
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from datetime import date

from .config import ROOT
from .filters import norm_company
from .http import SESSION

PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
# dol.gov's FY2026 file is published with a typo ("Dislclosure"), so both spellings are tried.
FILES = ["https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx",
         "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Dislclosure_Data_FY{fy}_Q{q}.xlsx"]
LINK = re.compile(r'(?:href="|/)([^"\s]*LCA_Dis\w*_Data_FY(\d{4})_Q(\d)[^"\s]*\.xlsx)', re.I)


class Blocked(Exception):
    """dol.gov refuses this machine (it blocks many cloud servers, including GitHub's)."""


class Getter:
    """Plain HTTP first; if dol.gov blocks scripts (403), switch to a real headless browser."""

    def __init__(self):
        self.pw = self.ctx = None

    def _browser(self):
        if self.ctx is None:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            b = self.pw.chromium.launch(headless=True)
            self.ctx = b.new_context(user_agent=SESSION.headers["User-Agent"], accept_downloads=True)
            self.ctx.new_page().goto("https://www.dol.gov/", timeout=60_000)   # pick up cookies
        return self.ctx

    def status(self, url):
        """HTTP status for a tiny ranged GET (some servers reject HEAD)."""
        rng = {"Range": "bytes=0-1023"}
        if self.ctx is None:
            try:
                with SESSION.get(url, headers=rng, stream=True, timeout=30) as r:
                    if r.status_code in (200, 206, 404):
                        return r.status_code
                    print(f"  dol.gov answered {r.status_code} to a script; switching to headless browser")
            except Exception as e:
                print(f"  request failed ({type(e).__name__}); switching to headless browser")
        try:
            return self._browser().request.get(url, headers=rng, timeout=60_000).status
        except Exception as e:
            print(f"  browser request failed: {type(e).__name__}")
            return 0

    def exists(self, url):
        return self.status(url) in (200, 206)

    def download(self, url, path):
        if self.ctx is None:
            try:
                with SESSION.get(url, stream=True, timeout=900) as r:
                    if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                        with open(path, "wb") as f:
                            for chunk in r.iter_content(1 << 20):
                                f.write(chunk)
                        return
            except Exception:
                pass
        r = self._browser().request.get(url, timeout=900_000)
        if not r.ok:
            raise RuntimeError(f"download failed: HTTP {r.status} for {url}")
        with open(path, "wb") as f:
            f.write(r.body())

    def close(self):
        if self.pw:
            self.pw.stop()


def latest_files(getter, n=2):
    """Newest available quarter for each of the last `n` fiscal years (FY starts in October)."""
    today = date.today()
    fy_now = today.year + (1 if today.month >= 10 else 0)
    found = {}
    try:                                            # 1) links on the page, if they're in the HTML
        html = SESSION.get(PAGE, timeout=60).text
        for href, fy, q in LINK.findall(html):
            url = href if href.startswith("http") else "https://www.dol.gov/" + href.lstrip("/")
            found.setdefault(int(fy), {})[int(q)] = url
    except Exception:
        pass
    out, blocked = [], []
    for fy in range(fy_now, fy_now - 4, -1):        # 2) otherwise try the standard file names
        if len(out) >= n:
            break
        if fy in found:
            q = max(found[fy])
            out.append((fy, q, found[fy][q]))
            continue
        hit = None
        for q in (4, 3, 2, 1):
            for pattern in FILES:
                url = pattern.format(fy=fy, q=q)
                code = getter.status(url)
                print(f"  {url.rsplit('/', 1)[-1]}: HTTP {code} "
                      + {200: "found", 206: "found", 404: "not published"}.get(code, "BLOCKED"))
                if code in (200, 206):
                    hit = (fy, q, url)
                    break
                if code != 404:
                    blocked.append(code)
            if hit or len(blocked) >= 4:
                break
        if hit:
            out.append(hit)
        if len(blocked) >= 4:
            raise Blocked(blocked[0])
    return out


def aggregate(path, agg, names):
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().upper() for h in next(rows)]
    col = {h: i for i, h in enumerate(header)}
    i_emp, i_soc = col.get("EMPLOYER_NAME"), col.get("SOC_CODE")
    i_status, i_lvl = col.get("CASE_STATUS"), col.get("PW_WAGE_LEVEL")
    if i_emp is None or i_soc is None:
        raise RuntimeError(f"unexpected columns: {header[:20]}")
    n = 0
    for r in rows:
        n += 1
        status = str(r[i_status] or "") if i_status is not None else "Certified"
        if not status.lower().startswith("certified"):
            continue
        emp = str(r[i_emp] or "").strip()
        key = norm_company(emp)
        if not key:
            continue
        a = agg[key]
        a[2] += 1
        if str(r[i_soc] or "").startswith("15-"):
            a[0] += 1
            lvl = str(r[i_lvl] or "").strip().upper() if i_lvl is not None else ""
            if lvl in ("I", "II"):
                a[1] += 1
        names.setdefault(key, emp.title())
        if n % 200_000 == 0:
            print(f"  … {n:,} rows", flush=True)
    wb.close()
    return n


HOWTO = """dol.gov is blocking this machine (it blocks many cloud servers, including GitHub Actions).
Do this instead, about once a quarter (~5 minutes):
  A) On your laptop, inside the repo:
       pip install requests PyYAML openpyxl playwright && python -m playwright install chromium
       python -m tracker.h1b
       git add data/h1b.json && git commit -m "h1b data" && git push
  B) If that is blocked too: download the newest 'LCA Programs (H-1B, H-1B1, E-3)' .xlsx file(s) from
     https://www.dol.gov/agencies/eta/foreign-labor/performance (Disclosure Data tab) in your browser, then:
       python -m tracker.h1b --files ~/Downloads/LCA_Dislclosure_Data_FY2026_Q3.xlsx
       git add data/h1b.json && git commit -m "h1b data" && git push
The job tracker keeps working meanwhile; sponsorship then comes only from job descriptions."""


def remind():
    """Telegram nudge when the automatic download is blocked."""
    try:
        from .notify import send_telegram
        send_telegram(["<b>H-1B data refresh needs you (≈5 min)</b>\ndol.gov blocks GitHub's servers. "
                       "On your laptop in the repo run:\n<code>python -m tracker.h1b</code>\nthen commit + push "
                       "<code>data/h1b.json</code>. Details in the workflow log / README."])
    except Exception:
        pass


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", help="already-downloaded LCA_Disclosure_Data_*.xlsx files")
    ap.add_argument("--url", nargs="*", help="direct link(s) to LCA disclosure .xlsx file(s)")
    ap.add_argument("--years", type=int, default=2, help="how many fiscal years to combine (default 2)")
    args = ap.parse_args()

    agg = defaultdict(lambda: [0, 0, 0])
    names = {}
    sources = []
    if args.files:
        for f in args.files:
            print(f"Parsing {f}…", flush=True)
            print(f"  parsed {aggregate(os.path.expanduser(f), agg, names):,} rows", flush=True)
            m = re.search(r"FY(\d{4})_Q(\d)", f)
            sources.append(f"FY{m.group(1)} Q{m.group(2)}" if m else os.path.basename(f))
    else:
        getter = Getter()
        try:
            urls = [u for u in (args.url or []) if u.strip()]
            if urls:
                files = [(0, 0, u) for u in urls]
            else:
                files = latest_files(getter, args.years)
            if not files:
                print("No LCA disclosure files were found under the usual names.\n"
                      "Paste the direct link from the dol.gov page with --url (or the workflow's URL box).")
                sys.exit(1)
            for fy, q, url in files:
                print(f"Downloading {url}", flush=True)
                with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
                    getter.download(url, tmp.name)
                    print(f"  {os.path.getsize(tmp.name) / 1e6:.0f} MB, parsing…", flush=True)
                    print(f"  parsed {aggregate(tmp.name, agg, names):,} rows", flush=True)
                m = re.search(r"FY(\d{4})_Q(\d)", url)
                sources.append(f"FY{m.group(1)} Q{m.group(2)}" if m else url.rsplit("/", 1)[-1])
        except Blocked as e:
            if not os.environ.get("GITHUB_ACTIONS"):
                print(f"""
BLOCKED (HTTP {e}): dol.gov refuses automated downloads from this network too.
Download the file(s) in your normal browser instead (2 minutes):
  1. Open https://www.dol.gov/agencies/eta/foreign-labor/performance → "Disclosure Data" tab
  2. Under "LCA Programs (H-1B, H-1B1, E-3)" download the newest FY .xlsx (optionally last year's too)
  3. Run (WSL sees Windows downloads under /mnt/c/Users/<you>/Downloads):
       python -m tracker.h1b --files /mnt/c/Users/<you>/Downloads/LCA_Dislclosure_Data_FY2026_Q3.xlsx
       git add data/h1b.json && git commit -m "h1b data" && git push""")
                sys.exit(1)
            print(f"\nBLOCKED (HTTP {e}).\n{HOWTO}")
            if os.environ.get("GITHUB_ACTIONS"):
                print("::warning title=H-1B data needs a manual refresh::dol.gov blocks GitHub's servers. "
                      "Run `python -m tracker.h1b` on your laptop and push data/h1b.json (see README).")
                remind()
                sys.exit(0)          # not a failure of your tracker — just needs the laptop step
            sys.exit(1)
        finally:
            getter.close()
    out = {k: [v[0], v[1], v[2], names.get(k, k)] for k, v in agg.items() if v[0] > 0 or v[2] >= 5}
    (ROOT / "data" / "h1b.json").write_text(json.dumps({"_source": sources, **out}, separators=(",", ":"), sort_keys=True))
    print(f"Wrote {len(out):,} employers to data/h1b.json  (sources: {', '.join(sources)})")


class H1B:
    """Lookup helper used by the hourly run."""

    def __init__(self):
        p = ROOT / "data" / "h1b.json"
        self.data = json.loads(p.read_text()) if p.exists() else {}
        self.source = self.data.pop("_source", None)
        self._keys = sorted(k for k in self.data if len(k) >= 4)
        self._cache = {}

    def __bool__(self):
        return bool(self.data)

    def lookup(self, company, aliases=None):
        key = norm_company(company)
        if not key or not self.data:
            return None
        if key in self._cache:
            return self._cache[key]
        cands = [key] + [norm_company(a) for a in (aliases or {}).get(key, [])]
        best = None
        for c in cands:
            if c in self.data:
                v = self.data[c]
                best = v if not best or v[0] > best[0] else best
        if best is None and len(key) >= 5:
            # "meta" → "metaplatforms", "waymo" → "waymollc" (after suffix removal) etc.
            import bisect
            i = bisect.bisect_left(self._keys, key)
            while i < len(self._keys) and self._keys[i].startswith(key):
                v = self.data[self._keys[i]]
                if not best or v[0] > best[0]:
                    best = v
                i += 1
        res = {"tech": best[0], "entry": best[1], "total": best[2], "name": best[3]} if best else \
            {"tech": 0, "entry": 0, "total": 0, "name": None}
        self._cache[key] = res
        return res


if __name__ == "__main__":
    main()