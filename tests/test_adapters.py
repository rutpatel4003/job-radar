"""Adapter parsing tests with canned API payloads (no network)."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.config import load_config  # noqa: E402
from tracker.sources import aggregators, ashby, greenhouse, lever, smartrecruiters, workday  # noqa: E402
from tracker.ids import uid_from_url, ats_from_url  # noqa: E402

CFG = load_config()


def test_greenhouse():
    payload = {"jobs": [{"id": 123, "title": "ML Engineer", "absolute_url": "https://x.com/careers?gh_jid=123",
                         "location": {"name": "Mountain View, CA"}, "first_published": "2026-09-20T00:00:00Z"}]}
    with mock.patch.object(greenhouse, "get_json", return_value=payload):
        jobs = greenhouse.fetch({"name": "Waymo", "ats": "greenhouse", "token": "waymo"}, CFG)
    assert jobs[0].uid == "gh:123" == uid_from_url(jobs[0].url)
    with mock.patch.object(greenhouse, "get_json", return_value={"content": "&lt;p&gt;We sponsor.&lt;/p&gt;", "offices": [{"location": "Austin, TX"}]}):
        d = jobs[0].fetch_detail()
    assert "We sponsor." in d["description"] and d["locations"] == ["Austin, TX"]


def test_lever_ashby_sr():
    lv = [{"id": "AB12CD34-0000-0000-0000-000000000000", "text": "Perception Engineer", "hostedUrl": "https://jobs.lever.co/zoox/ab12cd34-0000-0000-0000-000000000000",
           "categories": {"location": "Foster City, CA", "commitment": "Full-time"}, "createdAt": 1790000000000,
           "descriptionPlain": "Build perception.", "lists": [{"text": "Requirements", "content": "<li>3+ years of experience</li>"}]}]
    with mock.patch.object(lever, "get_json", return_value=lv):
        j = lever.fetch({"name": "Zoox", "ats": "lever", "token": "zoox"}, CFG)[0]
    assert j.uid == uid_from_url(j.url) and "3+ years" in j.description
    ab = {"jobs": [{"id": "0B1C2D3E-0000-0000-0000-000000000000", "title": "Research Engineer", "location": "San Francisco",
                    "secondaryLocations": [{"location": "New York"}], "jobUrl": "https://jobs.ashbyhq.com/openai/0b1c2d3e-0000-0000-0000-000000000000",
                    "descriptionPlain": "x", "isListed": True, "address": {"postalAddress": {"addressCountry": "United States"}}}]}
    with mock.patch.object(ashby, "get_json", return_value=ab):
        j = ashby.fetch({"name": "OpenAI", "ats": "ashby", "token": "openai"}, CFG)[0]
    assert j.uid == uid_from_url(j.url) and j.locations == ["San Francisco", "New York"] and j.country == "United States"
    sr = {"totalFound": 1, "content": [{"id": "744000151555600", "name": "Software Engineer", "location": {"city": "Austin", "region": "TX", "country": "us"}}]}
    with mock.patch.object(smartrecruiters, "get_json", return_value=sr):
        j = smartrecruiters.fetch({"name": "Visa", "ats": "smartrecruiters", "token": "Visa"}, CFG)[0]
    assert j.uid == uid_from_url(j.url) == "sr:744000151555600"


def test_workday():
    c = {"name": "NVIDIA", "ats": "workday", "host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"}
    page = {"jobPostings": [{"title": "Deep Learning Engineer - New College Grad 2027", "externalPath": "/job/US-CA-Santa-Clara/Deep-Learning-Engineer_JR2001234",
                             "locationsText": "US, CA, Santa Clara", "postedOn": "Posted 2 Days Ago"}]}
    with mock.patch.object(workday, "post_json", return_value=page):
        jobs = workday.fetch(c, CFG)
    assert len(jobs) == 1 and jobs[0].uid == "wd:nvidia:jr2001234" == uid_from_url(jobs[0].url)
    assert ats_from_url(jobs[0].url)["site"] == "NVIDIAExternalCareerSite"


def test_hn():
    stories = {"hits": [{"title": "Ask HN: Who is hiring? (September 2026)", "objectID": "1"}]}
    item = {"children": [{"id": 42, "created_at": "2026-09-01T00:00:00Z",
                          "text": "Acme AI (YC W24) | Senior Backend Engineer, New Grad ML Engineer | San Francisco, CA | ONSITE | VISA<p>We sponsor visas."}]}
    with mock.patch.object(aggregators, "get_json", side_effect=[stories, item]):
        jobs = aggregators._hn()
    assert jobs[0].company == "Acme AI" and "New Grad ML Engineer" in jobs[0].title


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("adapter tests passed ✔")
