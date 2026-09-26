"""Source adapters. Each ATS module exposes fetch(company, cfg) -> FetchResult."""
from ..ids import board_key
from ..http import NotFound
from ..models import FetchResult
from . import amazon, ashby, bamboohr, eightfold, greenhouse, lever, oracle, recruitee, rippling, smartrecruiters, workable, workday

ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "amazon": amazon,
    "workable": workable,
    "recruitee": recruitee,
    "bamboohr": bamboohr,
    "rippling": rippling,
    "oracle": oracle,
    "eightfold": eightfold,
}


def fetch_board(company, cfg):
    key = board_key(company)
    mod = ADAPTERS.get(company["ats"])
    if not mod:
        return FetchResult(key, company.get("name", key), [], ok=False, error="unknown ats")
    try:
        jobs = mod.fetch(company, cfg)
        return FetchResult(key, company.get("name", key), jobs, complete=getattr(mod, "COMPLETE", True))
    except NotFound as e:
        return FetchResult(key, company.get("name", key), [], ok=False, not_found=True, error=str(e))
    except Exception as e:  # network hiccup, bad JSON, rate limit...
        return FetchResult(key, company.get("name", key), [], ok=False, error=f"{type(e).__name__}: {e}"[:200])


def detail_from_url(url):
    """Best-effort description fetcher for jobs found via community lists."""
    for mod in (greenhouse, lever, smartrecruiters, workday, bamboohr, oracle):
        f = mod.detail_fetcher_from_url(url)
        if f:
            return f
    return None
