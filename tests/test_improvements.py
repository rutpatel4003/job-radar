"""Tests for the September 2026 fixes (no network):  python tests/test_improvements.py"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import audit, export  # noqa: E402
from tracker.config import compile_terms, load_config  # noqa: E402
from tracker.filters import ResumeMatcher, Staffing, TitleFilter, analyze_description, years_verdict  # noqa: E402
from tracker.ids import board_key, uid_from_url  # noqa: E402
from tracker.models import Job  # noqa: E402
from tracker.sources import apple, community, eightfold, jibe, smartrecruiters  # noqa: E402
from tracker.store import Store  # noqa: E402

CFG = load_config()


def test_years_of_age_is_not_experience():
    t = ("Basic qualifications - Must be 18 years of age or older - Experience from previous technical "
         "internship(s) or demonstrated projects")
    assert analyze_description(t)["min_years"] is None
    assert analyze_description("Applicants must be at least 18 years old. 2+ years of experience in Go.")["min_years"] == 2
    assert analyze_description("5+ years of experience building distributed systems")["min_years"] == 5


def test_sponsorship_wording():
    cases = {
        "Must obtain work authorization in country of employment at the time of hire.": "unknown",
        "Candidates must be authorized to work in the United States.": "unknown",
        "Open to U.S. citizens or permanent residents only.": "citizen",
        "Applicants must be U.S. citizens or green card holders.": "citizen",
        "This position requires a polygraph.": "citizen",
        "Must be able to obtain a DOE Q clearance.": "citizen",
        "Requires an active Secret clearance.": "citizen",
        "Public Trust background investigation required.": "unknown",       # a warning, not a clearance
        "This position is not eligible for Qualcomm immigration sponsorship.": "no_sponsor",
        "IBM will not be providing visa sponsorship for this position now or in the future.": "no_sponsor",
        "Eligibility to work is required as the company will not pursue visa sponsorship for these positions.": "no_sponsor",
        "Ability to obtain and maintain a U.S. Top Secret SCI security clearance": "citizen",
        "Must be able to obtain and hold a U.S. security clearance.": "citizen",
        "Eligible to obtain and maintain an active U.S. Secret security clearance.": "citizen",
        "We will not consider candidates who need sponsorship.": "no_sponsor",
        "This position requires verification of citizenship due to citizenship-based legal restrictions.": "citizen",
        "We are unable to sponsor visas for this role.": "no_sponsor",
        "Visa sponsorship is available.": "sponsors",
    }
    bad = {t: (analyze_description(t)["sponsorship"], want) for t, want in cases.items()
           if analyze_description(t)["sponsorship"] != want}
    assert not bad, bad
    assert "obtain / maintain work authorization in the country of employment" in audit.SYSTEM


def test_sponsorship_warnings():
    w = analyze_description("This role requires a Public Trust background investigation.")
    assert w["sponsorship"] == "unknown" and w["sponsorship_warning"] == "Public Trust background check"
    w = analyze_description("To comply with U.S. export control laws and regulations, candidates for this role may need "
                            "to meet certain legal status requirements as provided in those laws.")
    assert w["sponsorship"] == "unknown" and "export-control" in w["sponsorship_warning"]
    w = analyze_description("We are unable to sponsor visas. Public Trust clearance may apply.")
    assert w["sponsorship"] == "no_sponsor" and w["sponsorship_warning"] is None
    assert "Public Trust position or" in audit.SYSTEM and "background check (a suitability check" in audit.SYSTEM


EXPERIENCE = [
    ("BASIC QUALIFICATIONS\n- 3+ years of non-internship professional software development experience\n"
     "- 2+ years of design experience\n- 1+ years of experience in a related occupation\n"
     "PREFERRED QUALIFICATIONS\n- 5+ years of experience with AWS", 3, True),
    ("Minimum qualifications: 3+ years of experience with Python.\nPreferred qualifications: 1+ years of experience with Go.", 3, False),
    ("Requirements: 5+ years of experience building backend systems, or a Master's degree and 3 years of experience.", 3, False),
    ("Bachelor's degree with 2–5+ years of relevant experience, or Master's degree with 1–4+ years of experience", 1, False),
    ("1.5+ years experience with Python and Flask", 1, False),
    ("2 - 5 or more years of experience in Object Oriented design is required.", 2, False),
    ("Candidates must have a Bachelors' Degree and a minimum of eight (8) years of experience, or 11 years of "
     "experience in lieu of a degree.", 8, False),
    ("Minimum Qualifications:\nBachelor's degree in Engineering and 4+ years of Software Engineering experience.\nOR\n"
     "Master's degree in Engineering and 3+ years of Software Engineering experience.\nOR\n"
     "PhD in Engineering and 2+ years of Software Engineering experience.", 3, False),
    ("3+ years of experience in software engineering or a relevant field. 2+ years of experience if you have a PhD.", 3, False),
    ("3+ years of experience or a Master's degree in Computer Science", None, False),
    ("Must be 18 years of age or older. Experience from previous internships.", None, False),
    ("We have over 25 years of experience serving customers.", None, False),
    ("1 year of experience with an advanced degree.", 1, False),
    ("Nice to have:\n- 4+ years of experience with Kubernetes", None, False),
    ("Requirements\n- 2+ years of experience with React (a plus)\n- 3+ years of experience with Go", 3, False),
    ("Bachelor’s degree in computer science and 1+ years of professional experience in software engineering; "
     "OR 3+ years of professional experience in software engineering in lieu of a degree", 1, False),
    ("Three (3) to five (5) years of software development experience.", 3, False),
    ("Engineering Degree or 4+ years controls engineering experience in an automated manufacturing environment", None, False),
    ("BS or MS degree in Computer Science and minimum of 5+ years of software development experience is required", 5, False),
    ("A minimum of 3 years of R&D experience, or an equivalent graduate research background, primarily in AI", None, False),
]


def test_responsibility_sections_are_not_requirements():
    from tracker.filters import experience
    for head in ("Responsibilities", "Responsibilities:", "Key Responsibilities", "Responsibility", "Duties"):
        t = f"{head}\n- Mentor others; 10+ years of experience with distributed systems helps the team\n" \
            "Qualifications\n- 3+ years of experience with Go"
        assert experience(t)["min_years"] == 3, head
    assert experience("Key Qualifications\n- 4+ years of experience with C++")["min_years"] == 4


def test_experience_parser():
    from tracker.filters import experience
    bad = []
    for text, want, strict in EXPERIENCE:
        got = experience(text)
        if got["min_years"] != want or (strict and not got["years_strict"]):
            bad.append((text[:60], got["min_years"], got["years_strict"], want))
    assert not bad, bad
    r = experience(EXPERIENCE[0][0])
    assert r["preferred_years"] == 5 and len(r["years_quotes"]) == 3


def test_professional_years_override_newgrad_title():
    exp = CFG["experience"]
    assert years_verdict(3, ["newgrad"], exp, strict=True)[1] == "asks for 3+ years (professional)"
    assert years_verdict(3, ["newgrad"], exp, strict=False)[1] is None


def test_cohort_years_are_not_merged():
    from tracker.filters import fingerprint
    tmp = Path(tempfile.mkdtemp()) / "jobs.json"
    st = Store(tmp)
    t26, t27 = "Software Engineer Graduate - 2026 Start", "Software Engineer Graduate - 2027 Start"
    fp = fingerprint("TikTok", t26, ["San Jose, CA"])
    assert fp == fingerprint("TikTok", t27, ["San Jose, CA"])          # same fingerprint on purpose…
    st.add({"uid": "a", "url": "https://x/a", "fp": fp, "board": "b", "status": "open", "first_seen": "2026-09-01",
            "title": t26, "company": "TikTok"})
    assert st.find_fp(fp, t27) is None                                 # …but a different cohort year
    assert st.find_fp(fp, t26)["uid"] == "a"
    assert st.find_fp(fp, "Software Engineer Graduate")["uid"] == "a"  # no year → same job


def test_compact_posting_keeps_gates_under_pressure():
    lines = [f"- Experience with tool {i} in production environments at scale, including monitoring" for i in range(250)]
    body = ("Intro about the company. " * 60 + "\nMinimum Qualifications:\n- 3+ years of experience with Go\n"
            + "\n".join(lines) + "\nApplicants must be U.S. citizens.\nPreferred:\n- Rust\n"
            + "Culture text. " * 200 + "\nWe cannot sponsor visas for this role.")
    c = audit.compact_posting(body, 8000)
    assert len(c) <= 8000
    for must in ("We cannot sponsor visas", "must be U.S. citizens", "3+ years of experience with Go", "Intro about"):
        assert must in c, must
    assert c.index("[Work authorization") < c.index("[Required qualifications]")


def test_compact_posting_keeps_late_requirements():
    body = "About the team. " * 400 + "\nMinimum qualifications:\n- 3+ years of experience with Go\n" \
           "Benefits: lunch\n" + "Culture text. " * 300 + "\nWe are unable to sponsor visas for this role."
    c = audit.compact_posting(body, 4000)
    assert len(c) <= 4000 and "3+ years of experience with Go" in c and "unable to sponsor" in c
    assert audit.compact_posting("short posting", 4000) == "short posting"


def test_newgrad_titles_are_flagged_not_hidden():
    exp = CFG["experience"]
    assert years_verdict(3, ["newgrad"], exp) == (3, None)
    assert years_verdict(3, [], exp) == (3, "asks for 3+ years")
    assert years_verdict(2, [], exp) == (2, None)
    assert years_verdict(None, [], exp) == (None, None)


def test_title_filter():
    tf = TitleFilter(CFG)
    keep = ["Machine Learning Engineer Graduate - Lead Ads", "Software Engineer - Emerging Talent",
            "Associate Software Engineer - Customer Operations", "Software Engineer, AI Support Platform",
            "Software Engineer, Lead Generation", "Member of Technical Staff", "Member of Technical Staff, Inference",
            "Forward Deployed Engineer New Grad - 2027", "Engineer, New Grad", "New Grad Engineer",
            "Data Engineer", "Software Engineer, University Graduate - Customer Experience",
            "Machine Learning PhD New Grad - Machine Learning and Artificial Intelligence",
            "2027 Technology Analyst Program", "Software Engineer II (New Grad)", "Site Reliability Engineer"]
    drop = ["Senior Software Engineer", "Staff Machine Learning Engineer", "Lead Software Engineer",
            "Machine Learning Engineer, Tech Lead", "Software Engineer - Contract", "Technical Support Engineer",
            "AI Trainer", "Data Labeler - Tesla AI", "Software Engineer Intern", "Senior Member of Technical Staff",
            "Software Engineer II", "Software Engineering 5 - Ads Finance", "Ecosystem Analyst Graduate - LLM",
            "CVP Software Infrastructure and Forward Deployed Engineering", "Sales Engineer - AI",
            "Staff+ Research Engineer, RL Data Platform", "Product Engineer (Mid-Level)",
            "Associate Engineer", "Mechanical Engineer, New Grad", "Robotics Data Collection Operator"]
    bad_keep = [t for t in keep if not tf.evaluate(t)[0]]
    bad_drop = [t for t in drop if tf.evaluate(t)[0]]
    assert not bad_keep and not bad_drop, (bad_keep, bad_drop)
    assert "newgrad" in tf.evaluate("Engineer, New Grad")[2]


def test_skill_terms_are_literal_and_case_aware():
    cpp = compile_terms(["c++"])[0]
    assert not cpp.search("we build cloud services")         # used to match any letter "c"
    assert cpp.search("experience with c++ and python")
    go = compile_terms(["=Go"])[0]
    assert go.search("Services in Go and Python") and not go.search("we go beyond")
    c = compile_terms(["=C"])[0]
    assert c.search("C, C++ or Rust") and not c.search("C++ only") and not c.search("c-suite")
    m = ResumeMatcher(CFG)
    r = m.score("You will go beyond the rest of the team. Kubernetes, Terraform, Java, Kafka.")
    assert r and "go" not in r["matched"] and "rest" not in r["matched"] and "c++" not in r["matched"], r
    r = m.score("Build REST APIs in Go with PostgreSQL and Docker on AWS.")
    assert {"go", "rest", "postgresql", "docker", "aws"} <= set(r["matched"]), r


def test_staffing():
    s = Staffing(CFG)
    assert s.check("Jobsbridge", "smartrecruiters:jobsbridge1")
    assert s.check("Anything", "smartrecruiters:usm2")
    assert s.check("Acme Staffing LLC", "greenhouse:acme")
    assert s.check("Acme", "greenhouse:acme", "Position is C2C. Our client is a Fortune 500 bank.")
    assert not s.check("Acme", "greenhouse:acme", "We are a robotics company. Full-time role.")
    assert not s.check("Anthropic", "greenhouse:anthropic")


def test_store_keeps_community_label_separate():
    tmp = Path(tempfile.mkdtemp()) / "jobs.json"
    st = Store(tmp)
    rec = {"uid": "gh:1", "url": "https://x/1", "fp": "a|b|c", "board": "greenhouse:a", "status": "open",
           "first_seen": "2026-09-01", "sponsorship": "unknown"}
    st.add(rec)
    st.note_source(rec, Job(uid="gh:1", company="A", title="t", url="https://x/1", locations=[], source="simplifyjobs",
                            board="community:simplifyjobs", sponsorship_hint="sponsors"))
    assert rec["sponsorship"] == "unknown" and rec["community_label"] == "sponsors"


def test_eightfold_falls_back_to_pcsx():
    eightfold._MODE.clear()
    c = {"name": "Microsoft", "ats": "eightfold", "host": "apply.careers.microsoft.com", "domain": "microsoft.com"}

    def fake(url, **kw):
        if "/api/apply/v2/jobs?" in url:
            return {"message": "Not authorized for PCSX"}          # HTTP 200, but not a job list
        if "/api/pcsx/search" in url:
            return {"status": 200, "data": {"count": 1, "positions": [
                {"id": 1970393556929279, "name": "Software Engineer", "locations": ["United States, Washington, Redmond"],
                 "postedTs": 1790000000, "positionUrl": "/careers/job/1970393556929279"}]}}
        if "/api/pcsx/position_details" in url:
            return {"data": {"jobDescription": "<p>Build Azure services.</p>"}}
        raise AssertionError(url)
    cfg = dict(CFG, eightfold={"queries": ["software engineer"], "max_results_per_query": 10})
    with mock.patch.object(eightfold, "get_json", side_effect=fake):
        jobs = eightfold.fetch(c, cfg)
        assert len(jobs) == 1 and jobs[0].uid == "ef:1970393556929279" == uid_from_url(jobs[0].url)
        assert "Build Azure services." in jobs[0].fetch_detail()["description"]
    assert eightfold._MODE[c["host"]] == "pcsx"


def test_jibe_and_apple():
    c = {"name": "AMD", "ats": "jibe", "host": "careers.amd.com"}
    page = {"totalCount": 1, "jobs": [{"data": {"slug": "91817", "title": "Software Engineer, New Grad",
            "full_location": "Austin, Texas", "country_code": "US", "posted_date": "2026-09-28T16:53:00+0000",
            "description": "<p>Write drivers.</p>", "qualifications": "BS/MS in CS", "employment_type": "FULL_TIME"}}]}
    with mock.patch.object(jibe, "get_json", return_value=page):
        j = jibe.fetch(c, CFG)[0]
    assert j.uid == "jibe:amd:91817" == uid_from_url(j.url) and "Write drivers." in j.description
    assert j.posted_at == "2026-09-28T16:53:00+00:00" and board_key(c) == "jibe:careers.amd.com"
    assert uid_from_url("https://jobs.apple.com/en-us/details/200686238/machine-learning-engineer") == "apple:200686238"
    assert board_key({"ats": "apple"}) == "apple:apple"

    class R:
        status_code, headers = 200, {"x-apple-csrf-token": "t"}

        def __init__(self, body=None):
            self.body = body

        def json(self):
            return self.body

        def raise_for_status(self):
            pass

    class S:
        headers = {}

        def get(self, *a, **k):
            return R()

        def post(self, url, json=None, **k):
            items = [{"id": "PIPE-200686238", "positionId": "200686238", "postingTitle": "Machine Learning Engineer",
                      "transformedPostingTitle": "machine-learning-engineer", "postDateInGMT": "2026-09-28T23:41:53Z",
                      "locations": [{"city": "Cupertino", "stateProvince": "California", "countryName": "United States of America"}]}]
            return R({"res": {"searchResults": items if json["page"] == 1 else []}})
    with mock.patch.object(apple.requests, "Session", S):
        jobs = apple.fetch({"name": "Apple", "ats": "apple"}, dict(CFG, apple={"queries": ["ml"], "pages_per_query": 2}))
    assert [j.uid for j in jobs] == ["apple:200686238"] and jobs[0].locations == ["Cupertino, California, United States of America"]


def test_smartrecruiters_partial_is_not_complete():
    from tracker.sources import fetch_board
    page = {"totalFound": 5000, "content": [{"id": str(700000000000000 + i), "name": "Software Engineer",
                                             "location": {"city": "Austin", "country": "us"}} for i in range(100)]}
    with mock.patch.object(smartrecruiters, "get_json", return_value=page):
        res = fetch_board({"name": "Big", "ats": "smartrecruiters", "token": "big"}, CFG)
    assert res.ok and len(res.jobs) == 1000 and res.complete is False     # page cap → absence ≠ closed


def test_applyguy_and_redirects():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    ag = {"updatedAt": "x", "jobs": [{"company": "Amynta", "title": "Software Developer I", "location": "Baltimore, MD",
                                      "posted": "2026-09-28", "listingUrl": "https://amynta.wd5.myworkdayjobs.com/global/job/Baltimore/Software-Developer-I_2609-0540"}]}
    with mock.patch.object(community, "get_text", return_value=json.dumps(ag)):
        r = community.fetch_applyguy("https://raw.githubusercontent.com/ApplyGuy/x/main/data/new-grad-jobs.json")
    assert r.ok and r.jobs[0].uid == "wd:amynta:2609-0540" and r.jobs[0].posted_at.startswith("2026-09-28")

    md = ("| Company | Role | Location | Apply | Posted |\n|---|---|---|---|---|\n"
          "| KBR | Software Engineer | El Segundo, CA | [Apply](https://zapply.jobs/l/d/workday-kbr-R2128761?s=gh) | 1d |\n")
    real = "https://kbr.wd5.myworkdayjobs.com/KBR_Careers/job/El-Segundo-California/Software-Engineer_R2128761"
    community._REDIR.update(cache=None, dirty=False)
    calls = []
    with mock.patch.object(community, "ROOT", tmp), mock.patch.object(community, "get_text", return_value=md), \
            mock.patch.object(community, "_resolve_one", side_effect=lambda u: calls.append(u) or real):
        r1 = community.fetch_markdown_list("https://raw.githubusercontent.com/zapplyjobs/New-Grad-Jobs-2027/main/README.md")
        community.save_redirects()
        community._REDIR.update(cache=None, dirty=False)            # a later run reads the cache from disk
        r2 = community.fetch_markdown_list("https://raw.githubusercontent.com/zapplyjobs/New-Grad-Jobs-2027/main/README.md")
    assert r1.jobs[0].uid == "wd:kbr:r2128761" == r2.jobs[0].uid and r1.jobs[0].url == real
    assert len(calls) == 1                                           # second run used the cache
    community._REDIR.update(cache=None, dirty=False)


def test_jobright_summary():
    page = ('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"dataSource": {
        "jobResult": {"jobSummary": "UST is hiring.", "coreResponsibilities": ["Build APIs"],
                      "qualifications": {"mustHave": ["� Graduation Year: 2026 or 2027", "� Experience: 0-3 years"]},
                      "salaryDesc": "$40/hr"}}}}}) + "</script></html>")
    with mock.patch.object(community, "get_text", return_value=page):
        d = community.jobright_detail("https://jobright.ai/jobs/info/abc")
    assert "Build APIs" in d["description"] and "Graduation Year: 2026 or 2027" in d["description"]


def test_audit_quote_verification():
    text = ("We build perception for robots. Requirements: 3+ years of experience with C++ in production. "
            "We are unable to sponsor visas for this position. Python, PyTorch, OpenCV.")
    raw = {"fit": 4, "resume": "ML/AI", "level": "entry", "years_required": 3,
           "years_quote": "3+ years of experience with C++ in production",
           "sponsorship": "no", "sponsorship_quote": "We are unable to sponsor visas for this position",
           "start": "fits", "start_quote": "Start date is June 2027",                     # invented → dropped
           "staffing_agency": False, "staffing_quote": None,
           "matched_skills": ["PyTorch", "OpenCV", "Kubernetes"], "missing_skills": ["C++", "Rust"], "reason": "CV role"}
    v = audit.verify(raw, text, "PyTorch OpenCV Python C++")
    assert v["years"] == 3 and v["spons"] == "no" and "start" not in v and v["unverified"] == ["start"]
    assert v["matched"] == ["PyTorch", "OpenCV"] and "missing" not in v                  # not in text / on resume
    raw2 = dict(raw, years_required=5, years_quote="3+ years of experience with C++ in production")
    assert "years" not in audit.verify(raw2, text, "")                                   # number must be in the quote
    assert audit.quote_ok("we are unable to sponsor visas  for this position.", audit._norm(text))
    assert audit.parse_json('<think>hmm</think>```json\n{"fit": 5}\n```') == {"fit": 5}


def test_audit_end_to_end():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data" / "dashboard" / "details").mkdir(parents=True)
    desc = "Machine learning engineer, new grad. We sponsor visas: visa sponsorship is available. " * 5
    jobs = {"gh:1": {"uid": "gh:1", "status": "open", "d": "d1", "has_desc": True, "company": "Acme", "title": "ML Engineer",
                     "url": "https://x/1", "locations": ["SF"], "posted_at": "2026-09-28T00:00:00+00:00"},
            "gh:2": {"uid": "gh:2", "status": "open", "company": "NoDesc", "title": "SWE", "url": "https://x/2"}}
    (tmp / "data" / "jobs.json").write_text(json.dumps(jobs))
    (tmp / "data" / "dashboard" / "details" / "d1.json").write_text(json.dumps({"description": desc}))

    class FakeLLM:
        def __init__(self, *a, **k):
            self.base = "http://x"

        def connect(self):
            return "qwen3.8-27b"

        def ask(self, system, user):
            assert "RESUME \"SDE\"" in system and "ML Engineer" in user
            return {"fit": 5, "resume": "ML/AI", "level": "entry", "sponsorship": "yes",
                    "sponsorship_quote": "visa sponsorship is available", "reason": "new-grad ML role"}
    with mock.patch.object(audit, "DATA", tmp / "data"), mock.patch.object(audit, "OUT", tmp / "data" / "ai_review.json"), \
            mock.patch.object(audit, "LocalLLM", FakeLLM):
        assert audit.main(["--no-digest"]) == 0
        out = json.loads((tmp / "data" / "ai_review.json").read_text())
        assert out["reviews"]["gh:1"]["fit"] == 5 and out["reviews"]["gh:1"]["spons"] == "yes"
        assert "gh:2" not in out["reviews"]
        assert audit.main(["--no-digest"]) == 0                    # already reviewed → nothing to do
        out = json.loads((tmp / "data" / "ai_review.json").read_text())
        assert out["reviews"]["gh:1"]["pv"] == audit.PROMPT_VERSION
        out["reviews"]["gh:1"]["pv"] = 1                           # made with an older prompt → redone
        (tmp / "data" / "ai_review.json").write_text(json.dumps(out))
        assert audit.main(["--no-digest"]) == 0
        assert json.loads((tmp / "data" / "ai_review.json").read_text())["reviews"]["gh:1"]["pv"] == audit.PROMPT_VERSION
        assert audit.main(["--disagreements"]) == 0
        # the posting text changed → the review is redone; unchanged → kept
        jobs["gh:1"]["dh"] = "aaaaaaaaaaaa"
        (tmp / "data" / "jobs.json").write_text(json.dumps(jobs))
        stamp = json.loads((tmp / "data" / "ai_review.json").read_text())["reviews"]["gh:1"]["at"]
        with mock.patch.object(audit, "datetime") as dt:
            from datetime import datetime as real, timezone as tz
            dt.now.return_value = real(2031, 1, 1, tzinfo=tz.utc)
            dt.fromisoformat = real.fromisoformat
            assert audit.main(["--no-digest"]) == 0
        rev = json.loads((tmp / "data" / "ai_review.json").read_text())["reviews"]["gh:1"]
        assert rev["dh"] == "aaaaaaaaaaaa" and rev["at"] != stamp
        # resumes changed → a different review key → redone
        assert audit.review_key("m", {"resumes": {"SDE": "a"}}, {}) != audit.review_key("m", {"resumes": {"SDE": "b"}}, {})


def test_local_llm_http_and_fallback():
    """Talks to an OpenAI-compatible server; falls back from json_schema to json_object if refused."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._send(200, {"data": [{"id": "qwen3.8-27b"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((body.get("response_format") or {}).get("type"))
            assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["model"] == "qwen3.8-27b"
            if seen[-1] == "json_schema":
                return self._send(400, {"error": "structured outputs not supported"})
            self._send(200, {"choices": [{"message": {"content": '```json\n{"fit": 4, "reason": "ok"}\n```'}}]})
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        llm = audit.LocalLLM(f"http://127.0.0.1:{srv.server_port}/v1", "auto", "key")
        assert llm.connect() == "qwen3.8-27b"
        assert llm.ask("sys", "user") == {"fit": 4, "reason": "ok"}
        assert llm.ask("sys", "user") == {"fit": 4, "reason": "ok"}
        assert seen == ["json_schema", "json_object", "json_object"]      # remembers the working mode
    finally:
        srv.shutdown()


def test_export_includes_hidden_with_reason():
    tmp = Path(tempfile.mkdtemp())
    st = Store(tmp / "jobs.json")
    st.add({"uid": "a", "url": "https://x/a", "fp": "1", "board": "b", "status": "open", "first_seen": "2026-09-28",
            "hidden": True, "hidden_reason": "asks for 5+ years", "company": "C", "title": "T"})
    st.add({"uid": "b", "url": "https://x/b", "fp": "2", "board": "b", "status": "open", "first_seen": "2026-09-28",
            "company": "C", "title": "T2", "staffing": True})
    with mock.patch.object(export, "OUT", tmp / "dash"), mock.patch.object(export, "DETAILS", tmp / "dash" / "details"), \
            mock.patch.object(export, "TRACKING", tmp / "tracking.json"):
        export.export_dashboard(st, 1, {}, {"stale_days": 120})
    d = json.loads((tmp / "dash" / "jobs.json").read_text())
    rows = {r["uid"]: r for r in d["jobs"]}
    assert rows["a"]["hidden"] and rows["a"]["hidden_reason"] == "asks for 5+ years" and rows["b"]["staffing"]
    assert d["open"] == 1 and d["stale_days"] == 120


def test_clearance_pattern():
    from tracker import run
    tmp = Path(tempfile.mkdtemp())
    st = Store(tmp / "jobs.json")
    for i, sp in enumerate(["citizen"] * 4 + ["sponsors", "unknown", "unknown"]):
        st.add({"uid": f"x{i}", "url": f"https://x/{i}", "fp": str(i), "board": "b", "status": "open",
                "first_seen": "2026-09-28", "company": "Defense Co", "title": "SWE", "sponsorship": sp})
    st.add({"uid": "y", "url": "https://y", "fp": "y", "board": "b", "status": "open", "first_seen": "2026-09-28",
            "company": "Normal Co", "title": "SWE", "sponsorship": "unknown"})
    run.mark_clearance_pattern(st, CFG)
    assert st.jobs["x5"].get("clearance_likely") and st.jobs["x6"].get("clearance_likely")
    assert not st.jobs["x0"].get("clearance_likely") and not st.jobs["y"].get("clearance_likely")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ✓ {name}")
    print("improvement tests passed ✔")
