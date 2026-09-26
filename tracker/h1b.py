"""Build data/h1b.json from the U.S. Department of Labor's public H-1B (LCA) disclosure files.

Why: community lists' sponsorship labels are often wrong. An employer's *actual* H-1B filing history
for computer occupations (SOC 15-xxxx) is the best free signal of whether they sponsor.

    python -m tracker.h1b            # runs monthly in GitHub Actions (takes ~10–25 min)

Output: {normalized_employer: [tech_filings, entry_level_tech_filings, total_filings, display_name]}
 - tech_filings:        certified LCAs for SOC 15-xxxx (software/ML/data roles)
 - entry_level_tech:    of those, prevailing-wage level I or II (typical new-grad levels)
"""
import json
import re
import sys
import tempfile
from collections import defaultdict

from .config import ROOT
from .filters import norm_company
from .http import SESSION

PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"
LINK = re.compile(r'href="([^"]*LCA_Disclosure_Data_FY(\d{4})_Q(\d)[^"]*\.xlsx)"', re.I)


def latest_files(n=2):
    html = SESSION.get(PAGE, timeout=60).text
    found = {}
    for href, fy, q in LINK.findall(html):
        url = href if href.startswith("http") else "https://www.dol.gov" + href
        found[(int(fy), int(q))] = url
    # newest quarter of the newest FY, plus the final quarter of each earlier FY
    by_fy = {}
    for (fy, q), url in sorted(found.items()):
        by_fy[fy] = (q, url)
    return [(fy, q, url) for fy, (q, url) in sorted(by_fy.items(), reverse=True)[:n]]


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


def main():
    files = latest_files(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
    if not files:
        print("No LCA disclosure files found on", PAGE)
        sys.exit(1)
    agg = defaultdict(lambda: [0, 0, 0])
    names = {}
    for fy, q, url in files:
        print(f"Downloading FY{fy} Q{q}: {url}", flush=True)
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as tmp:
            with SESSION.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    tmp.write(chunk)
            tmp.flush()
            print(f"  parsed {aggregate(tmp.name, agg, names):,} rows", flush=True)
    out = {k: [v[0], v[1], v[2], names.get(k, k)] for k, v in agg.items() if v[0] > 0 or v[2] >= 5}
    meta = {"_source": [f"FY{fy} Q{q}" for fy, q, _ in files]}
    (ROOT / "data" / "h1b.json").write_text(json.dumps({**meta, **out}, separators=(",", ":"), sort_keys=True))
    print(f"Wrote {len(out):,} employers to data/h1b.json")


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
