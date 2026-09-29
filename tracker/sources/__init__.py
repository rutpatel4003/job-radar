"""Source adapters. Each ATS module exposes fetch(company, cfg) -> FetchResult."""
from ..ids import board_key
from ..http import NotFound
from ..models import FetchResult
from . import (amazon, apple, ashby, bamboohr, eightfold, greenhouse, jibe, lever, oracle, recruitee, rippling,
               smartrecruiters, workable, workday)
from .community import jobright_fetcher_from_url

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
    "apple": apple,
    "jibe": jibe,
}


def fetch_board(company, cfg):
    import time
    t = time.time()
    company = dict(company, _deadline=t + float((cfg.get("runtime") or {}).get("board_budget_seconds", 60)))
    res = _fetch_board(company, cfg)
    res.elapsed = time.time() - t
    return res


def _fetch_board(company, cfg):
    key = board_key(company)
    mod = ADAPTERS.get(company["ats"])
    if not mod:
        return FetchResult(key, company.get("name", key), [], ok=False, error="unknown ats")
    try:
        jobs = mod.fetch(company, cfg)
        complete = getattr(mod, "COMPLETE", True) and getattr(jobs, "complete", True)
        return FetchResult(key, company.get("name", key), list(jobs), complete=complete)
    except NotFound as e:
        return FetchResult(key, company.get("name", key), [], ok=False, not_found=True, error=str(e))
    except Exception as e:  # network hiccup, bad JSON, rate limit...
        return FetchResult(key, company.get("name", key), [], ok=False, error=f"{type(e).__name__}: {e}"[:200])


def detail_from_url(url):
    """Best-effort description fetcher for jobs found via community lists."""
    for mod in (greenhouse, lever, smartrecruiters, workday, bamboohr, oracle, apple):
        f = mod.detail_fetcher_from_url(url)
        if f:
            return f
    return jobright_fetcher_from_url(url)
