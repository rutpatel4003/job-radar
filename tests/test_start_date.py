"""Start-date classification edge cases (graduating May 2027, can start June 2027)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tracker.filters import classify_start  # noqa: E402

CASES = [
    ("Software Engineer, New Grad (2027 Start)", "", "fits"),
    ("Machine Learning Engineer, Summer 2027 start", "", "fits"),
    ("New Grad 2026/2027 - ML Engineer", "", "fits"),
    ("Software Engineer - 2027", "", "fits"),
    ("Software Engineer, New Grad 2026", "", "too_early"),
    ("Software Engineer - Winter 2027 Start", "", "too_early"),
    ("Research Engineer (PhD) 2026", "", "too_early"),
    ("ML Engineer", "We're hiring candidates graduating between December 2026 and August 2027.", "fits"),
    ("ML Engineer", "Expected graduation date of Dec 2026 - Jun 2027.", "fits"),
    ("ML Engineer", "Class of 2027 graduates welcome. Must be available to start immediately.", "fits"),
    ("SWE I", "Bachelor's degree obtained in 2024 or later.", "fits"),
    ("SWE", "Start date: July 2027. We build robots.", "fits"),
    ("SWE", "Our 2027 new grad cohort starts in August 2027.", "fits"),
    ("ML Engineer", "Candidates must have graduated between December 2025 and August 2026.", "too_early"),
    ("SWE I", "Bachelor's degree completed by June 2026.", "too_early"),
    ("SWE", "Recent graduates (class of 2025 or 2026).", "too_early"),
    ("SWE", "This role has a start date in January 2027.", "too_early"),
    ("SWE", "Must be available to start immediately.", "too_early"),
    ("SWE", "Founded in 2016, we raised a Series B in 2026. Apply today!", "unknown"),
    ("SWE", "Please apply immediately, roles fill fast.", "unknown"),
    ("Senior Staff 2026 roadmap", "", "unknown"),
    ("Software Engineer", "", "unknown"),
    ("Software Engineer - Early Career — Immediate Start", "", "too_early"),
    ("Software Engineer - Immediate Start (New Grad 2027)", "", "fits"),
]


def test_start_dates():
    for title, text, want in CASES:
        got, _ = classify_start(title, text, (2027, 5), (2027, 6))
        assert got == want, f"{title!r} / {text!r}: expected {want}, got {got}"


if __name__ == "__main__":
    test_start_dates()
    print("start-date tests passed ✔")
