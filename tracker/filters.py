"""Decide whether a job is relevant, and pull useful flags out of its description."""
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
    r"without\s+(the\s+)?(need\s+for\s+|requiring\s+)?(current\s+or\s+future\s+|future\s+)?(employer\s+|visa\s+|immigration\s+)*sponsorship",
    r"sponsorship\s+(is|will)\s+not\s+(be\s+)?(available|provided|offered|possible)",
    r"not\s+(be\s+)?(eligible|available)\s+for\s+(visa\s+|immigration\s+)?sponsorship",
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
    r"\bpublic\s+trust\s+(clearance|position|background)",
    r"\b(secret|top\s+secret)\s+(security\s+)?clearance",
]
SPONSORS = [
    r"(visa|h-?1b|immigration)\s+sponsorship\s+(is\s+)?(available|provided|offered)",
    r"(will|can|may|able to|happy to|do|does)\s+(provide\s+|offer\s+)?sponsor(ship)?\s+(visas?|h-?1b|work\s+authorization|for\s+(qualified|eligible|this))",
    r"(we|company)\s+(will\s+|can\s+)?(offer|provide|support)s?\s+(visa\s+|immigration\s+)sponsorship",
    r"sponsorship\s+(is\s+)?available",
    r"open\s+to\s+sponsor",
]
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


def analyze_description(text):
    """Pull decision-relevant facts out of a job description.

    Returns {"sponsorship": no_sponsor|citizen|sponsors|unknown, "sponsorship_evidence": str|None,
             "min_years": int|None, "years_evidence": str|None, "phd": bool, "salary": str|None}
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

    years, yev = [], None
    for m in _YEARS.finditer(t):
        if _AGE.match(t, m.end()):
            continue                      # "must be 18 years of age or older" is not an experience requirement
        window = t[m.start(): m.end() + 80]
        before = t[max(0, m.start() - 30): m.start()]
        wide_before = t[max(0, m.start() - 90): m.start()]
        if _DEGREE_ALT.search(window) or _DEGREE_FIRST.search(wide_before):
            continue                      # "3+ years or a Master's" / "PhD, or MS with 2 years" → degree path open
        if "experience" in window.lower() or "experience" in before.lower():
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if 0 < n <= 20:
                if not years or n < min(years):
                    yev = _evidence(t, m)
                years.append(n)
    sal = _SALARY.search(t)
    return {"sponsorship": spons, "sponsorship_evidence": evidence,
            "min_years": min(years) if years else None, "years_evidence": yev,
            "phd": bool(_PHD_REQ.search(t)),
            "salary": " ".join(sal.group(0).split()) if sal else None}


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


def years_verdict(min_years, tags, exp):
    """Return (years to show as a ⏳ flag or None, reason to hide or None).

    A title that says new grad / early career / Engineer I is flagged, never hidden, for a years
    requirement: those postings often list "2+ years" that internships count toward."""
    flag = min_years if (min_years or 0) >= exp.get("flag_min_years", 2) else None
    hide = None
    if min_years and min_years >= exp.get("hide_min_years", 3):
        if not ("newgrad" in (tags or []) and exp.get("never_hide_newgrad_titles", True)):
            hide = f"asks for {min_years}+ years"
    return flag, hide


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
