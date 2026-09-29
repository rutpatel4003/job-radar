"""Decide whether a job is relevant, and pull useful flags out of its description."""
import hashlib
import html
import re
from functools import lru_cache

from .config import compile_terms

# ── Titles ──────────────────────────────────────────────────────────────────


# "Software Engineer - Emerging Talent" → role part "software engineer", team/program part "emerging talent"
_TITLE_SPLIT = re.compile(r"\s+[-–—|:]\s+|,\s*|\s*\(|\s+/\s+")
# "Member of Technical Staff" is an entry-level title at AI labs, not a Staff-level role
_MTS = re.compile(r"\b(member of (the )?technical staff|technical staff member)\b")
# Titles that are only "Engineer" + a strong new-grad marker ("Engineer, New Grad 2027", "New Grad Engineer")
_STRONG_NG = re.compile(r"\b(new (college )?grad(uate)?s?|university (grad(uate)?|hire)|college grad(uate)?|early[- ]career|"
                        r"entry[- ]level|rotational|residency|resident|20\d\d)\b")
_GENERIC_LEFT = re.compile(r"^(engineer(ing|s)?|software|developer|program|programme|technical|i|1|the|and|&|of|for|in|"
                           r"start|hire|us|usa|united|states|role|position|track)$")


class TitleFilter:
    def __init__(self, cfg):
        self.cats = {name: compile_terms(terms) for name, terms in cfg["title_keywords"].items()}
        self.role = compile_terms(cfg.get("role_words"))
        self.exclude = compile_terms(cfg.get("title_exclude"))
        self.exclude_anywhere = compile_terms(cfg.get("title_exclude_anywhere"))
        lvl = cfg.get("level", {})
        self.level_exclude = compile_terms(lvl.get("exclude"))
        self.level_role_part = compile_terms(lvl.get("exclude_in_role_part"))
        self.newgrad = compile_terms(lvl.get("newgrad_markers"))
        self.level_ii = compile_terms(lvl.get("flag_as_level_ii"))
        self.exclude_level_ii = bool(lvl.get("exclude_level_ii"))

    @staticmethod
    def _norm(title):
        t = html.unescape(title or "").lower()
        return re.sub(r"\s+", " ", t).strip()

    def _cats(self, t):
        return [name for name, pats in self.cats.items() if any(p.search(t) for p in pats)]

    def categories(self, title):
        return self._cats(self._norm(title))

    def _generic_newgrad(self, t):
        """'Engineer, New Grad 2027' / 'New Grad Engineer' / 'Engineering Resident' → counts as SDE."""
        if not _STRONG_NG.search(t) or not re.search(r"\b(engineer(ing)?|developer)\b", t):
            return False
        rest = _STRONG_NG.sub(" ", t)
        rest = [w for w in re.split(r"[^a-z0-9&]+", rest) if w]
        return all(_GENERIC_LEFT.match(w) for w in rest)

    def evaluate(self, title):
        """Return (keep, categories, tags). tags ⊂ {"newgrad", "level2"}.

        Exclusion words ("talent", "customer", "support", "lead"…) are checked only against the role part of
        the title when the role part is itself a real engineering title, so team names after a dash or comma
        ("ML Engineer Graduate - Lead Ads", "SWE - Emerging Talent") no longer drop the job. Seniority words
        (senior, staff, principal, intern…) and employment-type words (contract, part-time) apply to the
        whole title.
        """
        t = self._norm(title)
        cats = self._cats(t)
        if not cats and self._generic_newgrad(t):
            cats = ["SDE"]
        if not cats:
            return False, [], []
        if self.role and not any(p.search(t) for p in self.role):
            return False, cats, []
        head = _TITLE_SPLIT.split(t, 1)[0].strip()
        head_ok = head != t and bool(self._cats(head)) and any(p.search(head) for p in self.role)
        scope = head if head_ok else t
        if any(p.search(scope) for p in self.exclude) or any(p.search(t) for p in self.exclude_anywhere):
            return False, cats, []
        t_lvl = _MTS.sub(" ", t)
        if any(p.search(t_lvl) for p in self.level_exclude) or \
                any(p.search(_MTS.sub(" ", scope)) for p in self.level_role_part):
            return False, cats, []
        tags = []
        if any(p.search(t) for p in self.newgrad):
            tags.append("newgrad")
        if any(p.search(t) for p in self.level_ii):
            if self.exclude_level_ii and "newgrad" not in tags:
                return False, cats, []
            tags.append("level2")
        return True, cats, tags


# ── Locations ───────────────────────────────────────────────────────────────

STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
          "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
STATE_NAMES = ["alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
               "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
               "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
               "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
               "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
               "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island",
               "south carolina", "south dakota", "tennessee", "texas", "utah", "vermont",
               "virginia", "washington", "west virginia", "wisconsin", "wyoming"]
US_CITIES = ["san francisco", "sf bay", "bay area", "mountain view", "palo alto", "menlo park",
             "sunnyvale", "santa clara", "san jose", "cupertino", "redwood city", "san mateo",
             "foster city", "south san francisco", "oakland", "berkeley", "emeryville",
             "los angeles", "santa monica", "culver city", "irvine", "san diego", "pasadena",
             "seattle", "bellevue", "redmond", "kirkland", "portland", "new york", "nyc",
             "brooklyn", "manhattan", "boston", "cambridge, ma", "somerville", "waltham",
             "austin", "dallas", "houston", "chicago", "denver", "boulder", "atlanta", "miami",
             "pittsburgh", "philadelphia", "washington dc", "washington, d.c", "arlington",
             "reston", "mclean", "herndon", "raleigh", "durham", "ann arbor", "detroit",
             "phoenix", "tempe", "salt lake", "lehi", "minneapolis", "columbus", "nashville"]
NON_US = ["canada", "toronto", "vancouver", "montreal", "ottawa", "waterloo", "united kingdom",
          "uk", "england", "london", "cambridge, uk", "edinburgh", "ireland", "dublin", "germany",
          "berlin", "munich", "france", "paris", "netherlands", "amsterdam", "switzerland",
          "zurich", "india", "bangalore", "bengaluru", "hyderabad", "pune", "chennai", "mumbai",
          "gurgaon", "gurugram", "noida", "delhi", "china", "beijing", "shanghai", "shenzhen",
          "hong kong", "taiwan", "taipei", "japan", "tokyo", "korea", "seoul", "singapore",
          "australia", "sydney", "melbourne", "israel", "tel aviv", "poland", "warsaw", "krakow",
          "spain", "madrid", "barcelona", "portugal", "lisbon", "italy", "milan", "sweden",
          "stockholm", "denmark", "copenhagen", "norway", "finland", "mexico", "brazil",
          "sao paulo", "argentina", "colombia", "costa rica", "philippines", "vietnam",
          "romania", "czech", "prague", "hungary", "serbia", "ukraine", "emea", "apac", "latam",
          "europe", "uae", "dubai", "saudi"]

_US_WORDS = re.compile(r"(?<![a-z])(united states|usa|u\.s\.a?\.?|us|america)(?![a-z])")
_STATE_ABBR = re.compile(r"(?:^|[,\s\-(/|])(" + "|".join(STATES) + r")(?:$|[\s,)/|\-])")
_US_PREFIX = re.compile(r"(?:^|\W)us-[a-z]{2}(?:\W|$)")
_STATE_NAME = re.compile(r"(?<![a-z])(" + "|".join(STATE_NAMES) + r")(?![a-z])")
_CITY = re.compile(r"(?<![a-z])(" + "|".join(re.escape(c) for c in US_CITIES) + r")(?![a-z])")
_NON_US = re.compile(r"(?<![a-z])(" + "|".join(re.escape(c) for c in NON_US) + r")(?![a-z])")
_REMOTE = re.compile(r"remote|anywhere|distributed")


def _one_location(loc):
    raw = (loc or "").strip()
    low = raw.lower()
    if not low:
        return "unknown"
    # Strong US signals first; then foreign names (so "Toronto, CA" or "Berlin, DE" aren't read
    # as California/Delaware); then weaker state-code / state-name signals.
    if _US_WORDS.search(low) or _US_PREFIX.search(low) or _CITY.search(low):
        return "us"
    if _NON_US.search(low):
        return "non_us"
    if _STATE_ABBR.search(raw) or _STATE_NAME.search(low):
        return "us"
    if _REMOTE.search(low):
        return "remote"
    return "unknown"


def location_status(locations, country=None):
    """Return 'us' | 'remote' | 'unknown' | 'non_us' for a job (any US location → 'us')."""
    if country:
        c = country.lower()
        if c in ("us", "usa", "united states", "united states of america", "u.s."):
            return "us"
    parts = []
    for loc in locations or []:
        parts += [p for p in re.split(r"\s*[;|•]\s*|\s+or\s+", loc or "") if p]
    statuses = {_one_location(p) for p in parts} or {"unknown"}
    for s in ("us", "remote", "unknown"):
        if s in statuses:
            if s != "us" and country and country.lower() not in ("", "remote"):
                return "non_us"
            return s
    return "non_us"


# ── Descriptions ────────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")


def html_to_text(s):
    if not s:
        return ""
    s = html.unescape(html.unescape(s))           # Greenhouse double-escapes
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = _TAG.sub(" ", s)
    return re.sub(r"[ \t\r\f\v]+", " ", s)


NO_SPONSOR = [
    r"(unable|not able|cannot|can ?not|can't|won't|will not|does not|do not|doesn't|don't|are not able|is not able)\s+(to\s+)?(currently\s+)?(offer|provide|support|sponsor|consider)[^.]{0,60}(sponsor|visa|h-?1b)",
    r"(unable|not able|cannot|can ?not|will not|won't|does not|do not)\s+(to\s+)?sponsor",
    r"(will\s+not|won't|does\s+not|do\s+not|is\s+not|are\s+not)\s+(be\s+)?(pursu|provid|offer|support|consider)\w*"
    r"\s+[^.]{0,30}?(visa|immigration|h-?1b)?\s*sponsorship",
    r"without\s+(the\s+)?(need\s+for\s+|requiring\s+)?(current\s+or\s+future\s+|future\s+)?(employer\s+|visa\s+|immigration\s+)*sponsorship",
    r"sponsorship\s+(is|will)\s+not\s+(be\s+)?(available|provided|offered|possible)",
    r"not\s+(be\s+)?(eligible|available)\s+for\s+(visa\s+|immigration\s+)?sponsorship",
    r"not\s+(be\s+)?eligible\s+for\s+[\w&.'’ -]{0,40}?(visa|immigration|h-?1b)\s+sponsorship",   # "…for Qualcomm immigration sponsorship"
    r"no\s+(visa\s+|immigration\s+|h-?1b\s+)?sponsorship",
    r"not\s+(currently\s+)?(offer|offering|provide|providing)\s+(visa\s+|immigration\s+)?sponsorship",
    r"(must|should)\s+(be\s+)?(authorized|eligible)\s+to\s+work[^.]{0,80}without[^.]{0,40}sponsor",
]
CITIZEN = [
    r"u\.?s\.?\s+citizen(ship)?\s+(is\s+)?(required|only)",
    r"must\s+be\s+(a\s+)?(u\.?s\.?|united states)\s+citizen",
    r"(requires?|required|must\s+(have|obtain|hold|possess))[^.]{0,60}(security\s+)?clearance",
    r"(active|current)\s+(secret|top secret|ts/sci|ts)\s+clearance",
    r"\bts/sci\b",
    r"eligib(le|ility)\s+(to\s+obtain|for)\s+(a\s+)?(u\.?s\.?\s+)?(government\s+)?security\s+clearance",
    r"\bitar\b",
    r"u\.?s\.?\s+person",
    r"(u\.?s\.?|united states)\s+citizens?\s+(or|and|/)\s+(lawful\s+)?(permanent\s+residents?|green\s*card)",
    r"(only|must\s+be)\s+(a\s+)?(u\.?s\.?\s+)?(citizens?|green\s*card\s+holders?)\s+(or|and)\s+(lawful\s+)?permanent",
    r"\bpolygraph\b|\b(ci|full[- ]scope)\s+poly\b",
    r"\b(doe\s+)?[ql][- ]clearance\b|\bdoe\s+[ql]\b",
    r"verification\s+of\s+(u\.?s\.?\s+)?citizenship",
    r"citizenship[- ]based\s+(legal\s+)?restrictions?",
    r"\b(secret|top\s+secret)\s+(/?\s*sci\s+)?(security\s+)?clearance",
    r"(able|ability|eligible)\s+to\s+obtain\s+(and\s+(hold|maintain)\s+)?(an?\s+)?(active\s+)?(u\.?s\.?\s+)?"
    r"[\w/ ]{0,25}?(security\s+)?clearance",
]
SPONSORS = [
    r"(visa|h-?1b|immigration)\s+sponsorship\s+(is\s+)?(available|provided|offered)",
    r"(will|can|may|able to|happy to|do|does)\s+(provide\s+|offer\s+)?sponsor(ship)?\s+(visas?|h-?1b|work\s+authorization|for\s+(qualified|eligible|this))",
    r"(we|company)\s+(will\s+|can\s+)?(offer|provide|support)s?\s+(visa\s+|immigration\s+)sponsorship",
    r"sponsorship\s+(is\s+)?available",
    r"open\s+to\s+sponsor",
]
# Not a hard "no": shown as a warning to check, never used to hide a job.
#   Public Trust is a suitability/background investigation, not a security clearance (OPM), and export-control
#   wording with "may" often means the employer can apply for an export license.
WARNINGS = [
    ("Public Trust background check", r"\bpublic\s+trust\b"),
    ("export-control status may be required",
     r"export\s+control\w*[^.]{0,200}?\b(may|might|could)\s+(need|require|be\s+required)|"
     r"\b(may|might)\s+(need|be\s+required)\s+to\s+meet\s+certain\s+legal\s+status|"
     r"\b(may|might)\s+require\s+(an\s+)?export\s+licen[cs]e"),
    ("security screening required", r"security\s+screening\s+requirements?"),
    ("some visa types may not be accepted",
     r"may\s+not\s+be\s+able\s+to\s+(employ|hire|consider)[^.]{0,120}(work\s+authorization|visa)"),
]
_WARN = [(label, re.compile(p, re.I)) for label, p in WARNINGS]
_NO = [re.compile(p, re.I) for p in NO_SPONSOR]
_CIT = [re.compile(p, re.I) for p in CITIZEN]
_YES = [re.compile(p, re.I) for p in SPONSORS]
_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|or more|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years|yrs)", re.I)
_DEGREE_ALT = re.compile(r"\bor\b[^.;\n]{0,50}\b(master'?s?|m\.?s\.?|m\.?eng|ph\.?\s?d|graduate|advanced)\b", re.I)
_DEGREE_FIRST = re.compile(r"\b(master'?s?|m\.?s\.?|ph\.?\s?d|graduate degree|advanced degree)\b[^.;\n]{0,60}\b(or|and|with|plus)\s*$", re.I)
_AGE = re.compile(r"\s*(?:of\s+age|old\b|or\s+older|and\s+(?:older|over)|\+?\s*of\s+age)", re.I)
_PHD_REQ = re.compile(r"(ph\.?\s?d\.?)[^.\n]{0,40}(required|is a must)|(require[sd]?|must\s+have|minimum)[^.\n]{0,80}ph\.?\s?d", re.I)


SIMPLIFY_SPONSOR = {
    "Does Not Offer Sponsorship": "no_sponsor",
    "U.S. Citizenship is Required": "citizen",
    "Offers Sponsorship": "sponsors",
}


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_SALARY = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})+(?:\.\d+)?\s?(?:usd)?\s?(?:-|–|—|to)\s?\$?\s?\d{2,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\$\s?\d{2,3}(?:\.\d)?\s?k\s?(?:-|–|—|to)\s?\$?\s?\d{2,3}(?:\.\d)?\s?k"
    r"|\$\s?\d{2,3}(?:\.\d{2})?\s?(?:-|–|—|to)\s?\$?\s?\d{2,3}(?:\.\d{2})?\s?(?:/|per\s)(?:hr|hour)", re.I)


def _evidence(text, match):
    """The sentence around a regex match, trimmed for display."""
    start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
    end_candidates = [i for i in (text.find(".", match.end()), text.find("\n", match.end())) if i != -1]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    sent = " ".join(text[start:end].split())
    return sent[:300]


# ── Sections: required vs preferred qualifications ──
_HEAD_MAX = 90
_REQ_HEAD = re.compile(
    r"^\W*(key\s+(qualifications?|requirements?|skills)\b[^:]{0,30}|"
    r"(basic|minimum|required|must[- ]have|mandatory)\b[^:]{0,30}(qualifications?|requirements?|skills?|experience)?|"
    r"(requirements?|qualifications?|what you('ll| will)? (need|bring)|who you are|what we('re| are) looking for|"
    r"you (have|bring|should have)|about you|your background|skills (and|&) experience))\W*:?\W*$", re.I)
_PREF_HEAD = re.compile(
    r"^\W*(preferred|desired|bonus|nice[- ]to[- ]haves?|additional|ideal(ly)?|pluses?|extra credit|even better|"
    r"good to have|it(’|')?s a plus|it would be (great|nice)|we(’|')?d love)\b[^.]{0,50}$", re.I)
_OTHER_HEAD = re.compile(
    r"^\W*((key|main|primary|core|your|job|role)\s+)?(responsibilit(?:y|ies)|duties|what you(’|')?ll do|what you will do|the role|your role|about (the|us|our)|who we are|"
    r"benefits|perks|compensation|pay|salary|why join|equal opportunity|eeo|overview|the team|our team|"
    r"job description|summary|location|how to apply|life at)\b[^.]{0,50}$", re.I)
_INLINE_PREF = re.compile(r"\b(preferred|nice[- ]to[- ]have|is a plus|are a plus|a plus|bonus( points)?|ideally|"
                          r"desired|advantageous|preference (will be )?given)\b", re.I)


def desc_hash(text):
    """Fingerprint of a description (whitespace / case-insensitive); None when there's no real description."""
    t = " ".join((text or "").strip()[:20000].split()).lower()
    return hashlib.sha1(t.encode()).hexdigest()[:12] if len(t) > 200 else None


def sections(text):
    """[(kind, line)] with kind in required | preferred | other | none, from the posting's own headers."""
    out, kind = [], "none"
    for raw in re.split(r"\n+|\s*•\s*", text or ""):
        line = raw.strip()
        if not line:
            continue
        if len(line) <= _HEAD_MAX:
            if _PREF_HEAD.match(line):
                kind = "preferred"
                continue
            if _REQ_HEAD.match(line):
                kind = "required"
                continue
            if _OTHER_HEAD.match(line) and (line.endswith(":") or len(line) < 40):
                kind = "other"
                continue
        out.append((kind, line))
    return out


# "3+ years", "3-5 years", "2 - 5 or more years", "1.5+ yrs", "eight (8) years"
_YRS = re.compile(r"(?<![\d.])(\d{1,2})\)?(?:\.\d+)?\s*(?:\+|or more|plus)?\s*"
                  r"(?:(?:-|–|—|to)\s*\d{1,2}(?:\.\d+)?\s*(?:\+|or more|plus)?)?\s*(?:years?|yrs?)\b", re.I)
_MS_WORD = re.compile(r"\b(master'?s?|m\.?s\.?(?=\W)|m\.?eng|advanced degree|graduate (degree|research|studies|background|work|coursework))\b", re.I)
_PHD_WORD = re.compile(r"\bph\.?\s?d\b|\bdoctora", re.I)
_OR = re.compile(r"\bor\b", re.I)
# a path for people WITHOUT a degree never applies to an MS student ("OR 3+ years in lieu of a degree")
_LIEU = re.compile(r"in\s+lieu\s+of|in\s+place\s+of|to\s+satisfy\s+the\s+degree|substitut\w*|instead\s+of\s+(a\s+)?degree|without\s+a\s+degree|"
                   r"equivalent\s+(work\s+|practical\s+|combination\s+of\s+)?experience", re.I)
_DEGREE_WORD = re.compile(r"\b(degree|bachelor'?s?|b\.?s\.?(?=\W)|b\.?a\.?(?=\W)|master'?s?|ph\.?\s?d|diploma)\b", re.I)
_NUMWORD = re.compile(r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
                      r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s*\((\d{1,2})\)", re.I)
_OR_LINE = re.compile(r"^\W*or\W*$", re.I)
_STRICT = re.compile(r"non[- ]?internship|professional|industry|full[- ]time|post[- ]?(graduat|grad|degree)|"
                     r"work experience|commercial", re.I)
_INTERN_OK = re.compile(r"internships?\s+(count|included|acceptable|qualify|are considered)|including internships?|"
                        r"(internship|co-?op|academic|research)\s+experience\s+(counts|is acceptable)", re.I)
_BOAST = re.compile(r"\b(we|we've|we’ve|our|us|founded|company has|team has)\b", re.I)
_YOU = re.compile(r"\b(you|your|candidate|applicant|required|requires|minimum|must|at least|need)\b", re.I)


def _sentence_years(sent):
    """Years one sentence requires of someone with an MS; None if it states no experience requirement.

    Alternatives split by "or" take the easiest path open to an MS: the Master's clause if there is one
    ("BS and 4+ years, or MS and 2+ years" → 2; "3+ years or a Master's" → 0), otherwise the smallest
    ("8 years, or 11 years without a degree" → 8). A PhD-only clause doesn't apply to an MS.
    Several numbers in one clause are required together → the largest."""
    mentions = []
    for m in _YRS.finditer(sent):
        if _AGE.match(sent, m.end()):
            continue                                   # "must be 18 years of age"
        ctx = sent[max(0, m.start() - 30): m.end() + 80].lower()
        if "experience" not in ctx:
            continue
        n = int(m.group(1))
        if 0 < n <= 20:
            mentions.append((m.start(), n))
    has_ms = _MS_WORD.search(sent)
    if not mentions and not has_ms:
        return None
    if _BOAST.search(sent) and not _YOU.search(sent):
        return None                                    # "we have 25 years of experience in…"
    cuts = [0] + [m.start() for m in _OR.finditer(sent)] + [len(sent)]
    clauses = []
    for a, b in zip(cuts, cuts[1:]):
        text = sent[a:b]
        if _LIEU.search(text):
            continue                                   # no-degree path
        nums = [n for pos, n in mentions if a <= pos < b]
        clauses.append((nums, bool(_MS_WORD.search(text)), bool(_PHD_WORD.search(text)) and not _MS_WORD.search(text),
                        bool(_DEGREE_WORD.search(text)), re.sub(r"^\W*or\b\W*", "", text.strip(), flags=re.I), a))
    if not any(c[0] for c in clauses):
        return None
    if len(clauses) > 1:
        ms = [max(c[0]) if c[0] else 0 for c in clauses if c[1]]
        if ms:
            return min(ms)
        # "Engineering degree or 4+ years" / "4+ years or a bachelor's degree" → the degree path needs no years
        for i, c in enumerate(clauses):
            if c[0] and not c[3]:
                before = any(d[3] and not d[0] for d in clauses[:i]) and c[4][:1].isdigit()
                after = any(d[3] and not d[0] for d in clauses[i + 1:])
                if before or after:
                    return 0
    vals = [max(c[0]) for c in clauses if c[0] and not c[2]]
    return min(vals) if vals else None


def _line_years(line):
    """Sentences of one line are required together → the largest sentence requirement."""
    line = _NUMWORD.sub(r"\1", line)                  # "three (3) to five (5) years" → "3 to 5 years"
    vals = [v for v in (_sentence_years(x) for x in re.split(r"(?<=[.;])\s+", line)
                        if not (_LIEU.search(x) and not _DEGREE_WORD.search(_LIEU.split(x)[0])))
            if v is not None]
    return max(vals) if vals else None


def experience(text):
    """Required years = the highest requirement among mandatory lines (every basic qualification applies at
    once). Lines joined by a standalone "OR" line are alternatives (Qualcomm-style "Bachelor's + 4 years / OR /
    Master's + 3 years / OR / PhD + 2 years") → the Master's line, else the smallest. Years in Preferred /
    nice-to-have lines are reported separately and never hide a job."""
    groups, pref, link = [], None, False
    for kind, line in sections(text):
        if kind == "other":
            continue
        if _OR_LINE.match(line):
            link = True
            continue
        n = _line_years(line)
        if n is None:
            link = link and not line.strip()
            continue
        if kind == "preferred" or _INLINE_PREF.search(line):
            pref = max(pref or 0, n) or None
            link = False
            continue
        joined = link or bool(re.match(r"^\W*or\b", line, re.I))
        entry = (n, line, bool(_MS_WORD.search(line)), bool(_PHD_WORD.search(line)) and not _MS_WORD.search(line))
        if joined and groups:
            groups[-1].append(entry)
        else:
            groups.append([entry])
        link = False
    req, ev, strict, quotes = None, None, False, []
    for g in groups:
        ms = [e for e in g if e[2]]
        pool = ms if (ms and len(g) > 1) else [e for e in g if not e[3]] or g
        best = min(pool, key=lambda e: e[0])
        if best[0] <= 0:
            continue
        q = " ".join(best[1].split())[:300]
        quotes.append(q)
        s_ = bool(_STRICT.search(best[1])) and not _INTERN_OK.search(best[1])
        if req is None or best[0] > req or (best[0] == req and s_ and not strict):
            req, ev, strict = best[0], q, s_
    return {"min_years": req, "years_evidence": ev, "years_quotes": quotes[:4], "years_strict": strict,
            "preferred_years": pref}


def analyze_description(text):
    """Pull decision-relevant facts out of a job description.

    Returns {"sponsorship": no_sponsor|citizen|sponsors|unknown, "sponsorship_evidence": str|None,
             "sponsorship_warning": str|None, "sponsorship_warning_evidence": str|None,
             "min_years": int|None (required years, see experience()), "years_evidence": str|None,
             "years_quotes": [str], "years_strict": bool, "preferred_years": int|None,
             "phd": bool, "salary": str|None}
    The evidence strings are the exact sentences that triggered each flag, so you can verify them.
    """
    t = re.sub(r"[ \t\u00a0]+", " ", text or "")
    spons, evidence = "unknown", None
    for label, pats in (("citizen", _CIT), ("no_sponsor", _NO), ("sponsors", _YES)):
        for p in pats:
            m = p.search(t)
            if m:
                spons, evidence = label, _evidence(t, m)
                break
        if spons != "unknown":
            break

    warning = warning_ev = None
    if spons not in ("citizen", "no_sponsor"):
        for label, p in _WARN:
            m = p.search(t)
            if m:
                warning, warning_ev = label, _evidence(t, m)
                break
    sal = _SALARY.search(t)
    out = {"sponsorship": spons, "sponsorship_evidence": evidence,
           "sponsorship_warning": warning, "sponsorship_warning_evidence": warning_ev,
           "phd": bool(_PHD_REQ.search(t)),
           "salary": " ".join(sal.group(0).split()) if sal else None}
    out.update(experience(t))
    return out


# ── Resume match ────────────────────────────────────────────────────────────

class ResumeMatcher:
    """Keyword overlap between a job description and each of your resumes.

    Skills live in config.yaml → profile. The score is the share of *recognised* tech terms in the
    description that appear on the resume, so "missing" tells you what the job wants that you
    didn't list.
    """

    def __init__(self, cfg):
        prof = cfg.get("profile") or {}
        key = lambda s: str(s).lstrip("=").lower()
        self.resumes = {name: {key(s) for s in skills} for name, skills in (prof.get("resumes") or {}).items()}
        vocab = {}
        for s in list(prof.get("extra_vocab") or []) + [s for sk in (prof.get("resumes") or {}).values() for s in sk]:
            # "=Go" (case-sensitive) wins over a plain "go" so ambiguous words stay case-sensitive
            if key(s) not in vocab or str(s).startswith("="):
                vocab[key(s)] = (compile_terms([s])[0], str(s).startswith("="))
        self.vocab = vocab

    def score(self, text):
        if not text or not self.resumes:
            return None
        t = text.lower()
        mentioned = {k for k, (p, case) in self.vocab.items() if p.search(text if case else t)}
        if len(mentioned) < 3:
            return None
        best = None
        for name, skills in self.resumes.items():
            have = {k for k in mentioned if k in skills}
            pct = round(100 * len(have) / len(mentioned))
            if best is None or pct > best["match"]:
                missing = sorted(mentioned - set(skills))
                best = {"match": pct, "resume": name, "missing": missing[:6], "matched": sorted(have)[:8]}
        return best


def years_verdict(min_years, tags, exp, strict=False):
    """Return (years to show as a ⏳ flag or None, reason to hide or None).

    A title that says new grad / early career / Engineer I is flagged, not hidden, for a years requirement
    (those often list "2+ years" that internships count toward) — unless the requirement is explicitly
    professional / non-internship experience ("3+ years of non-internship professional experience")."""
    flag = min_years if (min_years or 0) >= exp.get("flag_min_years", 2) else None
    hide = None
    if min_years and min_years >= exp.get("hide_min_years", 3):
        if strict or not ("newgrad" in (tags or []) and exp.get("never_hide_newgrad_titles", True)):
            hide = f"asks for {min_years}+ years" + (" (professional)" if strict else "")
    return flag, hide


def title_years(title):
    """Cohort / start years in a title ("New Grad 2027", "(2026 Start)"): postings for different years are
    never treated as the same job."""
    return tuple(sorted(set(re.findall(r"\b(20[2-3]\d)\b", html.unescape(title or "")))))


# ── Staffing agencies / contract body shops ─────────────────────────────────

_STAFFING_NAME = re.compile(r"\b(staffing|recruit(ing|ment|ers)?|talent solutions|infotech|info tech|manpower|"
                            r"outsourc\w*|it solutions|consultants? group|h1b)\b", re.I)
_STAFFING_TEXT = re.compile(r"\b(c2c|corp[- ]to[- ]corp|w-?2 only|only w-?2|on w-?2|h-?1b transfers?|our client|"
                            r"end client|direct client|implementation partner|contract[- ]to[- ]hire|\bc2h\b|"
                            r"visa (status )?(any|all)|all visas?)\b", re.I)


class Staffing:
    """Tags staffing agencies / C2C body shops. They are kept (some sponsor) but hidden on the dashboard by
    default and not notified, because their listings drown out real employers.
    config.yaml → staffing: {names: [...], boards: [...]}; plus name / description heuristics."""

    def __init__(self, cfg):
        s = cfg.get("staffing") or {}
        self.names = {norm_company(n) for n in s.get("names") or []}
        self.boards = {str(b).lower() for b in s.get("boards") or []}
        self.never = {norm_company(n) for n in s.get("never") or []}

    def check(self, company, board="", text=None):
        nc = norm_company(company)
        if nc in self.never:
            return False
        if nc in self.names or (board or "").lower() in self.boards \
                or (board or "").split(":", 1)[-1].lower() in self.boards:
            return True
        if _STAFFING_NAME.search(company or ""):
            return True
        return bool(text and len(_STAFFING_TEXT.findall(text[:6000])) >= 2)


# ── Fingerprints (repost / cross-site duplicate detection) ──────────────────

_CORP = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs?|holdings)\b\.?")


@lru_cache(maxsize=20000)
def norm_company(name):
    n = (name or "").lower()
    n = _CORP.sub(" ", n)
    return re.sub(r"[^a-z0-9]", "", n)


def norm_title(title):
    t = html.unescape(title or "").lower()
    t = re.sub(r"\b(19|20)\d\d\b", " ", t)                   # "New Grad 2027" ≈ "New Grad 2026"
    t = re.sub(r"\b(req|job|r|jr)[-_ ]?#?\d{3,}\b", " ", t)    # requisition numbers
    t = re.sub(r"[\(\[].*?(start|grad|hire|onsite|hybrid|remote).*?[\)\]]", " ", t)
    t = re.sub(r"[^a-z0-9+#]+", " ", t)
    return " ".join(t.split())


def norm_location(locations):
    locs = []
    for loc in locations or []:
        l = (loc or "").lower()
        l = re.sub(r"\b(united states( of america)?|usa|us)\b", " ", l)
        l = re.sub(r"[^a-z]+", " ", l).strip()
        if l:
            locs.append(" ".join(l.split()))
    return sorted(locs)[0] if locs else ""


def fingerprint(company, title, locations):
    return f"{norm_company(company)}|{norm_title(title)}|{norm_location(locations)}"


# ── Start date / graduation-window check ────────────────────────────────────

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_SEASONS = {"winter": 1, "spring": 3, "summer": 6, "fall": 9, "autumn": 9}
_DATE = re.compile(
    r"\b(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+"
    r"|(winter|spring|summer|fall|autumn)\s+|(q[1-4])\s+)?(?:of\s+)?((?:19|20)\d\d)\b"
    r"|\b(\d{1,2})/((?:20)\d\d)\b", re.I)
_START_CTX = re.compile(r"\b(start(s|ing)?\b|start date|begin(s|ning)?|commenc|join(ing)? (us|our|the team)|"
                        r"available to (work|start)|availability|cohort|onboard)", re.I)
_GRAD_CTX = re.compile(r"\b(graduat\w*|degree|conferred|class of|complet(e|ed|ing|ion)|diploma)\b", re.I)
_IMMEDIATE = re.compile(r"\b(start(ing)?|available|availability|join|begin)\b[^.\n]{0,40}\b(immediately|asap|"
                        r"as soon as possible|right away|within (2|two|30|thirty) (weeks|days))\b", re.I)
_OR_LATER = re.compile(r"\b(or later|or after|and later|onwards|or beyond|and beyond)\b", re.I)
_TITLE_YEAR_OK_CTX = re.compile(r"new grad|graduate|university|college|campus|start|early career|class of|"
                                r"entry|rotational|residency|\(\s*20\d\d\s*\)|[\s,\-–(]20\d\d\)?\s*$", re.I)


def _ym(parse):
    return parse[0] * 12 + (parse[1] or 0)


def _dates(s):
    out = []
    for m in _DATE.finditer(s):
        if m.group(4):
            year = int(m.group(4))
            month = None
            if m.group(1):
                month = _MONTHS[m.group(1).lower()[:3]]
            elif m.group(2):
                month = _SEASONS[m.group(2).lower()]
            elif m.group(3):
                month = (int(m.group(3)[1]) - 1) * 3 + 1
        else:
            month, year = int(m.group(5)), int(m.group(6))
            if not 1 <= month <= 12:
                continue
        if 2020 <= year <= 2032:
            out.append((year, month))
    return out


def _sentences(text):
    for s in re.split(r"(?<=[.!?;])\s+|\n+|•", text or ""):
        s = s.strip()
        if 8 <= len(s) <= 600:
            yield s


def classify_start(title, text, graduation=(2027, 5), earliest=(2027, 6)):
    """Return (status, evidence): status ∈ {"fits", "too_early", "unknown"}.

    Rules, in order (a job that shows any sign of fitting is never marked too early):
      • title years ("New Grad 2027", "(2026 Start)", "Winter 2027")
      • description start sentences ("start date: July 2027", "start immediately")
      • description graduation windows ("graduating Dec 2026 – Aug 2027", "class of 2026")
    A month-less year counts as fitting if it's your earliest-start year (e.g. "2027 start").
    """
    fits, early = [], []

    def judge_start(dates, sentence):
        ok = [d for d in dates if d[0] > earliest[0] or (d[0] == earliest[0] and (d[1] is None or d[1] >= earliest[1]))]
        if ok:
            fits.append(sentence)
        elif dates:
            early.append(sentence)

    t = title or ""
    tdates = _dates(t)
    if tdates and _TITLE_YEAR_OK_CTX.search(t):
        judge_start(tdates, f"Title: “{t}”")

    immediate = f"Title: “{t}”" if re.search(r"\bimmediate(ly)?\s+start|\bstart\s+immediately|\basap\b", t, re.I) else None
    for s in _sentences(text):
        if len(fits) and len(early) > 3:
            break
        dates = _dates(s)
        if _IMMEDIATE.search(s) and not dates:
            immediate = immediate or s
            continue
        if not dates:
            continue
        if _GRAD_CTX.search(s):
            # a graduation window fits if it reaches your graduation month (or is open-ended)
            latest = max(dates, key=lambda d: _ym((d[0], d[1] or 12)))
            if _OR_LATER.search(s) or _ym((latest[0], latest[1] or 12)) >= _ym(graduation):
                if not re.search(r"\b(before|prior to|no later than)\b", s, re.I):
                    fits.append(s)
                    continue
            if max(d[0] for d in dates) <= graduation[0]:
                early.append(s)
        elif _START_CTX.search(s):
            judge_start(dates, s)

    if fits:
        return "fits", " ".join(fits[0].split())[:300]
    if early:
        return "too_early", " ".join(early[0].split())[:300]
    if immediate:
        return "too_early", " ".join(immediate.split())[:300]
    return "unknown", None
