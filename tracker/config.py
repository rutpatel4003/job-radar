import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def compile_terms(terms):
    """Plain words/phrases → whole-word regex; entries with regex symbols are used as-is."""
    out = []
    for t in terms or []:
        t = str(t).strip().lower()
        if not t:
            continue
        if re.search(r"[\\\[\]()?*+{}|^$]", t):
            out.append(re.compile(t))
        else:
            out.append(re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])"))
    return out


def load_companies():
    """Union of auto-discovered boards (data/companies.json) and manual ones (companies.yaml)."""
    from .ids import ats_from_url, board_key

    auto = []
    p = ROOT / "data" / "companies.json"
    if p.exists():
        auto = json.loads(p.read_text())
    with open(ROOT / "companies.yaml") as f:
        manual_cfg = yaml.safe_load(f) or {}
    exclude = {e.strip().lower() for e in manual_cfg.get("exclude") or []}

    boards = {}
    for c in auto:
        boards[board_key(c)] = c
    for entry in manual_cfg.get("companies") or []:
        if isinstance(entry, dict):
            c = dict(entry)
            if "url" in c and "ats" not in c:
                found = ats_from_url(c["url"])
                if not found:
                    print(f"[config] could not detect ATS from url for {c.get('name')}: {c['url']}")
                    continue
                c.update(found)
            if "ats" not in c:
                continue  # name-only dicts are handled by discovery
            c.setdefault("origin", "manual")
            boards[board_key(c)] = c
    return [c for k, c in boards.items() if c.get("name", "").lower() not in exclude
            and k.split(":", 1)[-1] not in exclude]


def env(name):
    v = os.environ.get(name, "").strip()
    return v or None
