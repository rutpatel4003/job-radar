"""Turn apply URLs into stable job IDs and detect which hiring platform (ATS) a company uses.

The same job often shows up in several places (the company's Greenhouse board, the Simplify list,
the speedyapply list). Greenhouse / Lever / Ashby / SmartRecruiters job IDs are globally unique, so
deriving the ID from the URL makes all copies collapse into one entry automatically.
"""
import hashlib
import re
from urllib.parse import parse_qs, urlparse

LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _parts(url):
    p = urlparse(url.strip())
    host = (p.netloc or "").lower().split(":")[0]
    segs = [s for s in p.path.split("/") if s]
    q = parse_qs(p.query)
    return p, host, segs, q


def workday_parts(url):
    """Return (host, tenant, site, job_path) for Workday URLs, else None."""
    _, host, segs, _ = _parts(url)
    if host.endswith("myworkdayjobs.com"):
        tenant = host.split(".")[0]
        segs = [s for s in segs if not LOCALE.match(s)]
        if not segs or segs[0] in ("wday",):
            return None
        site = segs[0]
        rest = segs[1:]
    elif host.endswith("myworkdaysite.com"):
        segs = [s for s in segs if not LOCALE.match(s)]
        if len(segs) < 3 or segs[0] != "recruiting":
            return None
        tenant, site, rest = segs[1], segs[2], segs[3:]
    else:
        return None
    job_path = "/" + "/".join(rest) if rest and rest[0] in ("job", "details") else None
    return host, tenant, site, job_path


def ats_from_url(url):
    """Detect the ATS board behind an apply URL. Returns a company-config dict (without name) or None."""
    try:
        _, host, segs, q = _parts(url)
    except Exception:
        return None
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and segs:
        if segs[0] == "embed":
            tok = (q.get("for") or [None])[0]
            return {"ats": "greenhouse", "token": tok} if tok else None
        return {"ats": "greenhouse", "token": segs[0]}
    if host == "jobs.lever.co" and segs:
        return {"ats": "lever", "token": segs[0]}
    if host == "jobs.ashbyhq.com" and segs:
        return {"ats": "ashby", "token": segs[0]}
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and segs:
        return {"ats": "smartrecruiters", "token": segs[0]}
    if host == "apply.workable.com" and segs and segs[0] not in ("api", "j"):
        return {"ats": "workable", "token": segs[0]}
    if host.endswith(".recruitee.com") and host.count(".") == 2:
        return {"ats": "recruitee", "token": host.split(".")[0]}
    if host.endswith(".bamboohr.com") and host.count(".") == 2:
        return {"ats": "bamboohr", "token": host.split(".")[0]}
    if host == "ats.rippling.com" and segs:
        return {"ats": "rippling", "token": segs[0]}
    if ".oraclecloud.com" in host and "sites" in segs:
        i = segs.index("sites")
        if i + 1 < len(segs):
            return {"ats": "oracle", "host": host, "site": segs[i + 1]}
    if host.endswith(".eightfold.ai") and segs[:2] == ["careers", "job"]:
        return {"ats": "eightfold", "host": host, "domain": host.split(".")[0] + ".com"}
    wd = workday_parts(url)
    if wd:
        h, tenant, site, _ = wd
        return {"ats": "workday", "host": h, "tenant": tenant, "site": site}
    return None


def board_key(c):
    """Stable key for a company board config."""
    if c["ats"] == "workday":
        return f"workday:{c['tenant'].lower()}/{c['site'].lower()}"
    if c["ats"] == "amazon":
        return "amazon:amazon"
    if c["ats"] == "oracle":
        return f"oracle:{c['host'].split('.')[0]}/{c['site'].lower()}"
    if c["ats"] == "eightfold":
        return f"eightfold:{c['domain'].lower()}"
    return f"{c['ats']}:{c['token'].lower()}"


def canonical_url(url):
    p, host, segs, q = _parts(url)
    host = host.removeprefix("www.")
    keep = sorted((k, v[0]) for k, v in q.items() if re.search(r"(id|jid|job|req)", k, re.I))
    tail = "&".join(f"{k}={v}" for k, v in keep)
    return f"{host}/{'/'.join(segs)}" + (f"?{tail}" if tail else "")


def uid_from_url(url):
    """Stable, source-independent job id derived from the apply URL (or None if unknown format)."""
    try:
        p, host, segs, q = _parts(url)
    except Exception:
        return None
    if "gh_jid" in q:
        return f"gh:{q['gh_jid'][0]}"
    if "greenhouse.io" in host:
        m = re.search(r"/jobs/(\d+)", p.path)
        if m:
            return f"gh:{m.group(1)}"
        if "token" in q:
            return f"gh:{q['token'][0]}"
    if host.endswith("lever.co") and len(segs) >= 2 and UUID.match(segs[1]):
        return f"lever:{segs[1].lower()}"
    if "ashby_jid" in q:
        return f"ashby:{q['ashby_jid'][0].lower()}"
    if host == "jobs.ashbyhq.com" and len(segs) >= 2 and UUID.match(segs[1]):
        return f"ashby:{segs[1].lower()}"
    if "smartrecruiters.com" in host and len(segs) >= 2:
        m = re.match(r"(\d{6,})", segs[1])
        if m:
            return f"sr:{m.group(1)}"
    wd = workday_parts(url)
    if wd and wd[3]:
        _, tenant, _, job_path = wd
        last = job_path.rstrip("/").split("/")[-1]
        req = last.rsplit("_", 1)[-1] if "_" in last else last
        return f"wd:{tenant.lower()}:{req.lower()}"
    if host == "apply.workable.com" and "j" in segs:
        i = segs.index("j")
        if i + 1 < len(segs):
            return f"workable:{segs[0].lower()}:{segs[i + 1].lower()}"
    if host.endswith(".bamboohr.com") and len(segs) >= 2 and segs[0] == "careers" and segs[1].isdigit():
        return f"bamboohr:{host.split('.')[0]}:{segs[1]}"
    if host == "ats.rippling.com" and len(segs) >= 3 and segs[1] == "jobs":
        return f"rippling:{segs[2].lower()}"
    if ".oraclecloud.com" in host and "job" in segs:
        i = segs.index("job")
        if i + 1 < len(segs) and segs[i + 1].isdigit():
            return f"oracle:{host.split('.')[0]}:{segs[i + 1]}"
    m = re.search(r"/careers/job/(\d{6,})", p.path)
    if m:                                                   # Eightfold (Microsoft, Qualcomm, Netflix, ...)
        return f"ef:{m.group(1)}"
    if host.endswith("amazon.jobs"):
        m = re.search(r"/jobs/(\d+)", p.path)
        if m:
            return f"amazon:{m.group(1)}"
    return None


def url_hash_uid(url):
    return "url:" + hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def any_uid(url):
    return uid_from_url(url) or url_hash_uid(url)
