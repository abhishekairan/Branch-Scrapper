"""GitHub repository-search filters: registry, input normalisation, query building.

Qualifiers and syntax follow GitHub's "Searching for repositories" docs and were
verified live against github.com/search. Flag names mirror `gh search repos`.

Important: GitHub does NOT report bad qualifier values - `stars:abc` or
`created:notadate` silently return 0 results - so every value is validated here
before a run starts.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# kinds: list (one qualifier per value), match (in:a,b), range (numbers),
#        date, choice (value -> literal qualifier), flag (bool), raw
@dataclass(frozen=True)
class FilterSpec:
    key: str
    label: str
    kind: str
    qualifier: str = ""
    help: str = ""
    group: str = ""
    choices: tuple[tuple[str, str], ...] = ()   # (stored value, qualifier) for kind=choice
    negate: bool = False


FILTERS: tuple[FilterSpec, ...] = (
    FilterSpec("match", "Match in", "match", "in", "Fields the keywords must appear in", "Text"),
    FilterSpec("owner", "Owner", "list", "user", "Users/orgs; several are OR'd", "Scope"),
    FilterSpec("language", "Language", "list", "language", "e.g. python, typescript", "Scope"),
    FilterSpec("exclude_language", "Exclude language", "list", "language", "", "Scope", negate=True),
    FilterSpec("topic", "Topic", "list", "topic", "Repo must have ALL of these topics", "Scope"),
    FilterSpec("exclude_topic", "Exclude topic", "list", "topic", "", "Scope", negate=True),
    FilterSpec("license", "License", "list", "license", "e.g. mit, apache-2.0, gpl-3.0", "Scope"),
    FilterSpec("stars", "Stars", "range", "stars", "", "Numbers"),
    FilterSpec("forks", "Forks", "range", "forks", "", "Numbers"),
    FilterSpec("followers", "Followers", "range", "followers", "Users following the repo", "Numbers"),
    FilterSpec("size", "Size (KB)", "range", "size", "Repository size in kilobytes", "Numbers"),
    FilterSpec("number_topics", "Number of topics", "range", "topics", "", "Numbers"),
    FilterSpec("good_first_issues", "Good-first issues", "range", "good-first-issues", "", "Numbers"),
    FilterSpec("help_wanted_issues", "Help-wanted issues", "range", "help-wanted-issues", "", "Numbers"),
    FilterSpec("created", "Created", "date", "created", "Repository creation date", "Dates"),
    FilterSpec("pushed", "Last pushed", "date", "pushed", "Last push (what GitHub calls 'updated')", "Dates"),
    FilterSpec("archived", "Archived", "choice", "", "", "Flags",
               (("false", "archived:false"), ("true", "archived:true"))),
    FilterSpec("include_forks", "Forks in results", "choice", "",
               "GitHub excludes forks unless asked", "Flags",
               (("true", "fork:true"), ("only", "fork:only"))),
    FilterSpec("template", "Template repos", "choice", "", "", "Flags",
               (("true", "template:true"), ("false", "template:false"))),
    FilterSpec("mirror", "Mirrors", "choice", "", "", "Flags",
               (("true", "mirror:true"), ("false", "mirror:false"))),
    FilterSpec("sponsorable", "Owner is sponsorable", "flag", "is:sponsorable", "", "Flags"),
    FilterSpec("funding_file", "Has FUNDING.yml", "flag", "has:funding-file", "", "Flags"),
    FilterSpec("qualifiers", "Raw qualifiers", "raw", "", "Anything else, typed as GitHub syntax", "Other"),
)
SPECS = {s.key: s for s in FILTERS}

MATCH_FIELDS = ("name", "description", "topics", "readme")
LICENSE_HINTS = ("mit", "apache-2.0", "gpl-3.0", "gpl-2.0", "agpl-3.0", "lgpl-3.0", "bsd-3-clause",
                 "bsd-2-clause", "mpl-2.0", "isc", "unlicense", "cc0-1.0")

# Every qualifier GitHub accepts in repository search (for typo checks on inline qualifiers).
KNOWN_QUALIFIERS = {
    "in", "repo", "user", "org", "size", "followers", "forks", "stars", "topics", "topic",
    "good-first-issues", "help-wanted-issues", "created", "pushed", "language", "license",
    "is", "mirror", "template", "archived", "has", "fork", "deployable", "deployed",
}
_QUALIFIER_RE = re.compile(r'(?:^|\s)-?([A-Za-z][\w-]*):(?=\S)')


# ------------------------------------------------------------------ numbers
_NUM = r"\d+(?:\.\d+)?[km]?"


def _num(tok: str) -> int:
    t = tok.lower()
    mult = {"k": 1_000, "m": 1_000_000}.get(t[-1:], 1)
    return int(round(float(t.rstrip("km")) * mult))


def normalize_range(text: str) -> str:
    """'100+' / '>=100' / '10..50' / '10-50' / '<5' / '1.5k' -> GitHub range syntax."""
    t = re.sub(r"\s+", "", str(text).lower())
    if m := re.fullmatch(rf"({_NUM})\+", t):
        return f">={_num(m[1])}"
    if m := re.fullmatch(rf"(>=|<=|>|<)?({_NUM})", t):
        return f"{m[1] or ''}{_num(m[2])}"
    if m := re.fullmatch(rf"({_NUM}|\*)(?:\.\.|-)({_NUM}|\*)", t):
        lo, hi = m[1], m[2]
        if lo == hi == "*":
            raise ValueError("A range needs at least one number")
        lo_s = "*" if lo == "*" else str(_num(lo))
        hi_s = "*" if hi == "*" else str(_num(hi))
        if lo != "*" and hi != "*" and _num(lo) > _num(hi):
            raise ValueError(f"Range start {lo_s} is above its end {hi_s}")
        return f"{lo_s}..{hi_s}"
    raise ValueError(f"'{text}' isn't a number range. Use 100, 100+, >=100, <50 or 10..50")


# ------------------------------------------------------------------ dates
_ISO = r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?"
_REL = r"\d+[dwmy]"
_DATE_TOKEN = rf"(?:{_ISO}|{_REL}|today|yesterday)"
_DATE_WORDS = {  # word form -> operator (word forms avoid quoting < > on Windows shells)
    "before": "<", "after": ">", "since": ">=", "from": ">=", "until": "<=", "on": "",
    "within": ">=", "last": ">=", "in the last": ">=", "within the last": ">=",
}


def _check_date_token(tok: str) -> str:
    tok = tok.lower() if not re.fullmatch(_ISO, tok) else tok
    if re.fullmatch(_ISO, tok):
        try:
            date.fromisoformat(tok[:10])
        except ValueError:
            raise ValueError(f"'{tok}' isn't a real calendar date") from None
        return tok
    if re.fullmatch(_REL, tok) or tok in ("today", "yesterday"):
        return tok
    raise ValueError(f"'{tok}' isn't a date. Use YYYY-MM-DD or relative: 30d, 2w, 6m, 1y")


def normalize_date(text: str) -> str:
    """Canonical date expression; relative tokens (30d) stay relative and resolve at run time.

    Accepts: 2024-01-01, <2024-01-01, >=30d, 2024-01-01..2024-06-30, *..2024-01-01,
             'before 2024-01-01', 'after 6m', 'since 2025-01-01', 'within 30d',
             'between 2024-01-01 and 2024-06-30'.
    """
    t = str(text).strip()
    low = t.lower()
    if m := re.fullmatch(r"between\s+(\S+)\s+and\s+(\S+)", low):
        t = f"{m[1]}..{m[2]}"
    else:
        for word in sorted(_DATE_WORDS, key=len, reverse=True):
            if low.startswith(word + " "):
                t = _DATE_WORDS[word] + t[len(word):].strip()
                break
    t = t.replace(" ", "")
    if m := re.fullmatch(r"(>=|<=|>|<)?(.+)", t):
        op, rest = m[1] or "", m[2]
        if ".." not in rest:
            return op + _check_date_token(rest)
        if op:
            raise ValueError("Use either an operator or a range (a..b), not both")
        lo, hi = rest.split("..", 1)
        lo = "*" if lo == "*" else _check_date_token(lo)
        hi = "*" if hi == "*" else _check_date_token(hi)
        if lo == hi == "*":
            raise ValueError("A date range needs at least one date")
        return f"{lo}..{hi}"
    raise ValueError(f"'{text}' isn't a date filter")


def _shift_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 - months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def resolve_date_token(tok: str, today: date | None = None) -> str:
    today = today or date.today()
    if tok == "today":
        return today.isoformat()
    if tok == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if m := re.fullmatch(r"(\d+)([dwmy])", tok):
        n, unit = int(m[1]), m[2]
        if unit == "d":
            return (today - timedelta(days=n)).isoformat()
        if unit == "w":
            return (today - timedelta(weeks=n)).isoformat()
        return _shift_months(today, n * (12 if unit == "y" else 1)).isoformat()
    return tok


def resolve_date(expr: str, today: date | None = None) -> str:
    if ".." in expr:
        lo, hi = expr.split("..", 1)
        return f"{resolve_date_token(lo, today)}..{resolve_date_token(hi, today)}"
    m = re.fullmatch(r"(>=|<=|>|<)?(.+)", expr)
    return (m[1] or "") + resolve_date_token(m[2], today)


# ------------------------------------------------------------------ values
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


def _split_list(value: Any) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out: list[str] = []
    for i in items:
        i = str(i).strip()
        if i and i not in out:
            out.append(i)
    return out


def normalize(key: str, value: Any) -> Any:
    """Validate and canonicalise a filter value. Returns None to mean 'clear'."""
    spec = SPECS[key]
    if value is None or value == "" or value == []:
        return None
    if spec.kind == "list":
        items = _split_list(value)
        if spec.qualifier == "topic":
            # GitHub topics: lowercase letters, digits, hyphens, max 50 chars.
            items = [re.sub(r"[\s_]+", "-", i.lower()) for i in items]
            for i in items:
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", i):
                    raise ValueError(f"'{i}' isn't a valid topic (lowercase letters, digits, hyphens)")
        if spec.qualifier == "user":
            for i in items:
                if not _OWNER_RE.fullmatch(i):
                    raise ValueError(f"'{i}' isn't a valid GitHub user/org name")
        if spec.qualifier == "license":
            items = [i.lower() for i in items]
        return items or None
    if spec.kind == "match":
        items = [i.lower() for i in _split_list(value)]
        bad = [i for i in items if i not in MATCH_FIELDS]
        if bad:
            raise ValueError(f"Unknown field(s) {bad}; choose from {', '.join(MATCH_FIELDS)}")
        return items or None
    if spec.kind == "range":
        return normalize_range(value)
    if spec.kind == "date":
        return normalize_date(value)
    if spec.kind == "choice":
        v = str(value).strip().lower()
        if v in ("any", "none", ""):
            return None
        allowed = [c[0] for c in spec.choices]
        if v not in allowed:
            raise ValueError(f"{spec.label}: use one of {', '.join(allowed)}")
        return v
    if spec.kind == "flag":
        if isinstance(value, bool):
            return True if value else None
        v = str(value).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", "any"):
            return None
        raise ValueError(f"{spec.label}: use true or false")
    if spec.kind == "raw":
        return str(value).strip() or None
    raise AssertionError(spec.kind)


def clean_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Normalise a whole filters dict (e.g. loaded from config.json); raises on bad values."""
    out: dict[str, Any] = {}
    for k, v in (filters or {}).items():
        if k not in SPECS:
            raise ValueError(f"Unknown filter '{k}'. Known: {', '.join(SPECS)}")
        nv = normalize(k, v)
        if nv is not None:
            out[k] = nv
    return out


# ------------------------------------------------------------------ output
def _quote(v: str) -> str:
    return f'"{v}"' if " " in v else v


def to_qualifiers(filters: dict[str, Any], today: date | None = None) -> list[str]:
    quals: list[str] = []
    for spec in FILTERS:  # registry order = stable, readable query strings
        v = filters.get(spec.key)
        if v in (None, "", [], False):
            continue
        neg = "-" if spec.negate else ""
        if spec.kind == "list":
            quals += [f"{neg}{spec.qualifier}:{_quote(i)}" for i in v]
        elif spec.kind == "match":
            quals.append("in:" + ",".join(v))
        elif spec.kind == "range":
            quals.append(f"{spec.qualifier}:{v}")
        elif spec.kind == "date":
            quals.append(f"{spec.qualifier}:{resolve_date(v, today)}")
        elif spec.kind == "choice":
            quals.append(dict(spec.choices)[v])
        elif spec.kind == "flag":
            quals.append(spec.qualifier)
        elif spec.kind == "raw":
            quals.append(v)
    return quals


def build_query(keywords: str, filters: dict[str, Any], today: date | None = None) -> str:
    return " ".join([keywords.strip(), *to_qualifiers(filters, today)]).strip()


_OPS = {">=": "≥ ", "<=": "≤ ", ">": "> ", "<": "< "}


def describe(key: str, value: Any, today: date | None = None) -> str:
    """Human-readable value, e.g. 'after 30d (2026-09-23)'."""
    spec = SPECS[key]
    if value in (None, "", [], False):
        return ""
    if spec.kind in ("list", "match"):
        return ", ".join(value)
    if spec.kind == "range":
        if ".." in value:
            lo, hi = value.split("..")
            return f"≥ {lo}" if hi == "*" else f"≤ {hi}" if lo == "*" else f"{lo} – {hi}"
        m = re.fullmatch(r"(>=|<=|>|<)?(\d+)", value)
        return _OPS.get(m[1], "= ") + m[2]
    if spec.kind == "date":
        def tok(t: str) -> str:
            r = resolve_date_token(t, today)
            return f"{t} ago ({r})" if r != t and t not in ("today", "yesterday") else t
        if ".." in value:
            lo, hi = value.split("..")
            if hi == "*":
                return f"on/after {tok(lo)}"
            if lo == "*":
                return f"on/before {tok(hi)}"
            return f"{tok(lo)} → {tok(hi)}"
        m = re.fullmatch(r"(>=|<=|>|<)?(.+)", value)
        word = {">": "after", "<": "before", ">=": "on/after", "<=": "on/before"}.get(m[1], "on")
        return f"{word} {tok(m[2])}"
    if spec.kind == "choice":
        return {"true": "include", "only": "only forks", "false": "exclude"}.get(value, value) \
            if key == "include_forks" else value
    if spec.kind == "flag":
        return "required"
    return str(value)


# ------------------------------------------------------------------ query checks
def check_query(query: str) -> list[str]:
    """Warnings for inline qualifiers GitHub won't recognise (typos like 'topc:')."""
    warnings = []
    for m in _QUALIFIER_RE.finditer(query):
        name = m[1].lower()
        if name in KNOWN_QUALIFIERS or name.startswith("props."):
            continue
        # Only near-misses are worth flagging; 'http:' or '10:30' are just text.
        guess = difflib.get_close_matches(name, KNOWN_QUALIFIERS, n=1, cutoff=0.75)
        if guess:
            warnings.append(f"'{name}:' isn't a GitHub repo-search qualifier and will be searched as "
                            f"text - did you mean '{guess[0]}:'?")
    return warnings


def split_queries(text: str) -> list[str]:
    """Split on ';' / newlines always, and on ',' unless it sits inside quotes or a
    qualifier value such as in:name,description. Drops blanks and duplicates."""
    parts, buf, in_quote = [], [], False
    for i, ch in enumerate(text):
        if ch == '"':
            in_quote = not in_quote
        if not in_quote and (ch in ";\n" or ch == ","):
            if ch == ",":
                token = "".join(buf).rsplit(" ", 1)[-1]
                nxt = text[i + 1] if i + 1 < len(text) else " "
                if ":" in token and not nxt.isspace():
                    buf.append(ch)  # comma-separated qualifier value
                    continue
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    out: list[str] = []
    for p in parts:
        p = " ".join(p.split())
        if p and p not in out:
            out.append(p)
    return out
