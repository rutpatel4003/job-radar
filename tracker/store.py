"""The job database: data/jobs.json (one line per job so git diffs stay small and readable).

Dedup rules
  1. Same job id (derived from the apply URL)      → same job, never notified twice.
  2. Same company + title + location, still open   → duplicate listing (another site or a twin
                                                     requisition): merged in as an alias, no ping.
  3. Same company + title + location, but the old
     one had closed                                → new entry marked 🔁 repost, linked to the original.
  4. A job that closed and later comes back         → ↩️ reopened (same entry, pinged once).
"""
import json
from collections import defaultdict
from pathlib import Path

from .ids import canonical_url


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.jobs = {}
        if self.path.exists():
            text = self.path.read_text().strip()
            if text:
                self.jobs = json.loads(text)
        self.dirty = False
        self._index()

    def _index(self):
        self.alias = {}
        self.by_fp = {}
        self.by_url = {}
        self.by_board = defaultdict(list)
        for uid, r in self.jobs.items():
            self._index_one(r)

    def _index_one(self, r):
        uid = r["uid"]
        self.alias[uid] = uid
        for a in r.get("aliases", []):
            self.alias[a] = uid
        self.by_url[canonical_url(r["url"])] = uid
        for u in r.get("alt_urls", []):
            self.by_url.setdefault(canonical_url(u), uid)
        prev = self.by_fp.get(r["fp"])
        # prefer the open / most recent record for a fingerprint
        if prev is None or (self.jobs[prev]["status"] != "open" and r["status"] == "open") \
                or (self.jobs[prev]["status"] == r["status"] and r["first_seen"] > self.jobs[prev]["first_seen"]):
            self.by_fp[r["fp"]] = uid
        self.by_board[r["board"]].append(r)

    def is_empty(self):
        return not self.jobs

    # ── lookups ──
    def find(self, uid, url=None):
        real = self.alias.get(uid)
        if real:
            return self.jobs[real]
        if url:
            real = self.by_url.get(canonical_url(url))
            if real:
                return self.jobs[real]
        return None

    def find_fp(self, fp):
        uid = self.by_fp.get(fp)
        return self.jobs[uid] if uid else None

    # ── mutations ──
    def add(self, rec):
        self.jobs[rec["uid"]] = rec
        self._index_one(rec)
        self.dirty = True

    def add_alias(self, rec, job):
        changed = False
        if job.uid != rec["uid"] and job.uid not in rec.setdefault("aliases", []):
            rec["aliases"].append(job.uid)
            self.alias[job.uid] = rec["uid"]
            changed = True
        if job.url != rec["url"] and job.url not in rec.setdefault("alt_urls", []):
            rec["alt_urls"].append(job.url)
            self.by_url.setdefault(canonical_url(job.url), rec["uid"])
            changed = True
        changed |= self.note_source(rec, job)
        self.dirty |= changed

    def note_source(self, rec, job):
        changed = False
        if job.source not in rec.setdefault("sources", []):
            rec["sources"].append(job.source)
            changed = True
        # community labels (Simplify…) are often wrong: kept separately as "unverified", never as sponsorship
        if job.sponsorship_hint and not rec.get("community_label"):
            rec["community_label"] = job.sponsorship_hint
            changed = True
        self.dirty |= changed
        return changed

    def seen_again(self, rec):
        """Job present in a feed. Returns True if it had been closed (i.e. it reopened)."""
        if rec.get("miss"):
            rec["miss"] = 0
            self.dirty = True
        if rec["status"] == "closed":
            rec["status"] = "open"
            rec.pop("closed_at", None)
            self.dirty = True
            return True
        return False

    def missed(self, rec, now, threshold=2):
        rec["miss"] = rec.get("miss", 0) + 1
        self.dirty = True
        if rec["miss"] >= threshold and rec["status"] == "open":
            self.close(rec, now)

    def close(self, rec, now):
        if rec["status"] != "closed":
            rec["status"] = "closed"
            rec["closed_at"] = now
            self.dirty = True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(k) + ": " + json.dumps(self.jobs[k], sort_keys=True, ensure_ascii=False)
                 for k in sorted(self.jobs)]
        self.path.write_text("{\n" + ",\n".join(lines) + "\n}\n")
        self.dirty = False
