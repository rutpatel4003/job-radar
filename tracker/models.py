from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Job:
    uid: str                      # stable id, e.g. "gh:4736426005"
    company: str
    title: str
    url: str
    locations: list
    source: str                   # greenhouse | lever | ashby | smartrecruiters | workday | amazon | simplify | speedyapply
    board: str                    # which feed produced it, e.g. "greenhouse:waymo"
    posted_at: Optional[str] = None       # ISO timestamp if known
    description: Optional[str] = None     # plain text, never persisted
    country: Optional[str] = None
    sponsorship_hint: Optional[str] = None   # from community lists
    degree_hint: list = field(default_factory=list)
    employment: Optional[str] = None      # e.g. "Intern", "Full-time"
    fetch_detail: Optional[Callable] = None  # lazily returns {"description", "locations", "country"}


class Partial(list):
    """Returned by an adapter of a full-list board when it had to stop early (time budget, page cap):
    the jobs are used, but missing jobs are NOT treated as closed."""
    complete = False


@dataclass
class FetchResult:
    board: str
    company: str
    jobs: list
    ok: bool = True
    complete: bool = True         # True = full list of the board (lets us detect closed jobs)
    not_found: bool = False
    error: str = ""
    closed_uids: set = field(default_factory=set)  # explicit "this job is closed" signals
    elapsed: float = 0.0
