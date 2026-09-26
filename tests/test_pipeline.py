"""Offline end-to-end test (no network): python -m pytest -q  (or: python tests/test_pipeline.py)

Simulates several hourly runs against fake job boards and checks the dedup / repost / closed /
reopened logic plus sponsorship-evidence precedence over community labels.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import export, h1b, run  # noqa: E402
from tracker.config import load_config  # noqa: E402
from tracker.models import FetchResult, Job  # noqa: E402

NOW = "2026-09-24T12:00:00+00:00"


def gh(jid, title, loc, desc="", posted=NOW):
    return Job(uid=f"gh:{jid}", company="Acme Robotics", title=title,
               url=f"https://job-boards.greenhouse.io/acme/jobs/{jid}", locations=[loc],
               source="greenhouse", board="greenhouse:acme", posted_at=posted, description=desc)


class World:
    def __init__(self):
        self.boards = {}
        self.community = []
        self.sent = []


def make_run(world, tmp):
    cfg = load_config()
    cfg["career_pages"] = []
    cfg["community_lists"] = {"json": [], "markdown": []}
    cfg["aggregators"] = {"hn": False, "themuse": False, "remoteok": False, "adzuna": False}

    def fake_fetch_board(c, _cfg):
        jobs = world.boards.get(c["token"], [])
        return FetchResult(f"greenhouse:{c['token']}", c["name"], list(jobs))

    def fake_aggr(_cfg):
        return [FetchResult("community:simplifyjobs", "Simplify", list(world.community), complete=False)]

    patches = [
        mock.patch.object(run, "load_config", lambda: cfg),
        mock.patch.object(run, "load_companies", lambda: [{"name": "Acme Robotics", "ats": "greenhouse", "token": "acme"}]),
        mock.patch.object(run, "fetch_board", fake_fetch_board),
        mock.patch.object(run, "fetch_aggregators", fake_aggr),
        mock.patch.object(run, "DATA", tmp / "data"),
        mock.patch.object(export, "ROOT", tmp),
        mock.patch.object(export, "OUT", tmp / "data" / "dashboard"),
        mock.patch.object(export, "DETAILS", tmp / "data" / "dashboard" / "details"),
        mock.patch.object(export, "TRACKING", tmp / "data" / "tracking.json"),
        mock.patch.object(h1b, "ROOT", tmp),
        mock.patch.object(run, "notify", lambda ev, dash=None, header=None: world.sent.append([(e, r["title"]) for e, r in ev])),
        mock.patch.object(run, "notify_text", lambda *a: world.sent.append([("text", a[0])])),
        mock.patch.object(sys, "argv", ["run"]),
    ]

    def go():
        for p in patches:
            p.start()
        try:
            (tmp / "data").mkdir(exist_ok=True)
            run.main()
        finally:
            for p in reversed(patches):
                p.stop()
        return world.sent[-1] if world.sent else None
    return go


def test_pipeline():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    (tmp / "data" / "h1b.json").write_text(json.dumps({"_source": ["FY2026 Q3"], "acmerobotics": [42, 30, 60, "Acme Robotics Inc"]}))
    w = World()
    go = make_run(w, tmp)

    A = gh(1, "Machine Learning Engineer, New Grad", "San Francisco, CA",
           "Build perception with PyTorch and OpenCV. We are unable to sponsor visas for this role.")
    w.boards["acme"] = [A,
                        gh(2, "Senior Machine Learning Engineer", "San Francisco, CA"),
                        gh(3, "Software Engineer", "London, UK"),
                        gh(4, "Computer Vision Engineer", "Seattle, WA", "PyTorch, CUDA, C++. Visa sponsorship is available.",
                           posted="2026-01-01T00:00:00+00:00")]
    # the same job A also appears in a community list claiming it sponsors (label is wrong)
    w.community = [Job(uid="gh:1", company="Acme Robotics", title=A.title, url="https://acme.com/careers?gh_jid=1",
                       locations=["San Francisco, CA"], source="simplifyjobs", board="community:simplifyjobs",
                       sponsorship_hint="sponsors")]

    with mock.patch.object(run, "now_iso", lambda: NOW), mock.patch.object(run, "recent", lambda iso, h: iso == NOW):
        sent = go()
    db = json.loads((tmp / "data" / "jobs.json").read_text())
    assert set(db) == {"gh:1", "gh:4"}, db.keys()
    assert db["gh:1"]["sponsorship"] == "no_sponsor"            # JD wins over community label
    assert db["gh:1"]["community_label"] == "sponsors"
    assert db["gh:1"]["h1b"]["tech"] == 42
    assert db["gh:4"]["sponsorship"] == "sponsors"
    assert "newgrad" in db["gh:1"]["tags"]
    assert db["gh:1"]["resume"] == "ML/AI"
    assert ("new", A.title) in sent and all(t != "Computer Vision Engineer" for _, t in sent)  # old post seeded silently

    # run 2: A disappears (miss 1); two twin postings of a new role → one entry
    E1 = gh(10, "Software Engineer I", "Austin, TX", "Go, Python, AWS, Docker")
    E2 = gh(11, "Software Engineer I", "Austin, TX", "Go, Python, AWS, Docker")
    EARLY = gh(12, "Software Engineer, New Grad (2026 Start)", "Boston, MA", "Python.")
    FITS = gh(13, "Perception Engineer", "Pittsburgh, PA", "Open to candidates graduating between Dec 2026 and Jun 2027.")
    w.boards["acme"] = [gh(4, "Computer Vision Engineer", "Seattle, WA"), E1, E2, EARLY, FITS]
    w.community = []
    sent = go()
    db = json.loads((tmp / "data" / "jobs.json").read_text())
    assert sorted(sent) == [("new", "Perception Engineer"), ("new", "Software Engineer I")], sent   # 2026-start role not pinged
    assert db["gh:12"]["start"] == "too_early" and db["gh:13"]["start"] == "fits" and db["gh:10"]["start"] == "unknown"
    assert db["gh:10"]["aliases"] == ["gh:11"]
    w.boards["acme"] = [gh(4, "Computer Vision Engineer", "Seattle, WA"), E1, E2, EARLY, FITS]
    assert db["gh:1"]["status"] == "open" and db["gh:1"]["miss"] == 1

    # run 3: A still missing → closed. No notification.
    n_before = len(w.sent)
    go()
    db = json.loads((tmp / "data" / "jobs.json").read_text())
    assert db["gh:1"]["status"] == "closed"
    assert len(w.sent) == n_before

    # you had applied to A and got rejected (written by the dashboard)
    (tmp / "data" / "tracking.json").write_text(json.dumps({"jobs": {"gh:1": {"status": "rejected"}}}))
    # run 4: company reposts A under a new id → one "repost" ping linked to the original
    A2 = gh(99, "Machine Learning Engineer, New Grad (2027)", "San Francisco, CA", "Same role.")
    w.boards["acme"] = [gh(4, "Computer Vision Engineer", "Seattle, WA"), E1, E2, A2, EARLY, FITS]
    sent = go()
    db = json.loads((tmp / "data" / "jobs.json").read_text())
    assert sent == [("repost", A2.title)], sent
    assert db["gh:99"]["repost_of"] == "gh:1" and db["gh:99"]["prev_status"] == "rejected"

    # run 5 + 6: E disappears twice (closed), run 7: comes back → "reopened"
    w.boards["acme"] = [gh(4, "Computer Vision Engineer", "Seattle, WA"), A2, EARLY, FITS]
    go(); go()
    assert json.loads((tmp / "data" / "jobs.json").read_text())["gh:10"]["status"] == "closed"
    w.boards["acme"] = [gh(4, "Computer Vision Engineer", "Seattle, WA"), A2, E1, EARLY, FITS]
    sent = go()
    assert sent == [("reopened", "Software Engineer I")], sent

    # run 8: nothing changed → no ping, database untouched
    before = (tmp / "data" / "jobs.json").read_text()
    n_before = len(w.sent)
    go()
    assert len(w.sent) == n_before
    assert (tmp / "data" / "jobs.json").read_text() == before

    dash = json.loads((tmp / "data" / "dashboard" / "jobs.json").read_text())
    assert any(j.get("d") for j in dash["jobs"])
    assert any(j["uid"] == "gh:1" for j in dash["jobs"])        # tracked job stays on the dashboard
    print("pipeline test passed ✔")


def test_recheck_and_backfill():
    """Partial-feed jobs (e.g. Workday) are re-checked daily and closed after two 'not found' answers;
    jobs without a description get one from the headless browser and are re-analysed."""
    from datetime import datetime, timezone
    from tracker.http import NotFound
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    w = World()
    go = make_run(w, tmp)
    wd = Job(uid="wd:acme:r1", company="Acme Robotics", title="Perception Engineer, New Grad",
             url="https://acme.wd5.myworkdayjobs.com/External/job/Austin/Perception-Engineer_R1",
             locations=["Austin, TX"], source="workday", board="workday:acme/external", description="x" * 300)
    w.boards["acme"] = []
    w.community = [wd]
    go()                                            # seeds the job (board: workday → partial feed)
    slot = (datetime.now(timezone.utc).hour // 3) % 8
    w.community = []                                # the search no longer returns it

    def gone():
        raise NotFound("x")
    with mock.patch.object(run, "crc32", lambda b: slot), mock.patch.object(run, "detail_from_url", lambda u: gone):
        go()
        db = json.loads((tmp / "data" / "jobs.json").read_text())
        assert db["wd:acme:r1"]["status"] == "open" and db["wd:acme:r1"]["miss"] == 1
        go()
    db = json.loads((tmp / "data" / "jobs.json").read_text())
    assert db["wd:acme:r1"]["status"] == "closed", db["wd:acme:r1"]

    # browser back-fill for an older job without description
    from tracker.config import load_config
    from tracker.filters import ResumeMatcher
    rec = {"uid": "url:1", "title": "Software Engineer", "locations": ["NYC"], "sponsorship": "unknown", "start": "unknown"}
    run.reanalyze(rec, "Class of 2027 welcome. We are unable to sponsor visas. Python, Go, AWS, Docker.",
                  ResumeMatcher(load_config()), ((2027, 5), (2027, 6)), load_config(), write=False)
    assert rec["sponsorship"] == "no_sponsor" and rec["start"] == "fits" and rec["resume"] == "SDE", rec
    print("recheck/backfill test passed ✔")


if __name__ == "__main__":
    test_pipeline()
    test_recheck_and_backfill()
