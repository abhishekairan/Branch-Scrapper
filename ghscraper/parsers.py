"""Pure HTML -> dict parsers for GitHub search and repository pages.

Strategy (from analysing live pages): GitHub's React pages ship their data in
<script type="application/json" data-target="react-app.embeddedData">. That JSON
is far more stable than the hashed CSS-module class names (e.g. Button__AyoG5z),
so it is the primary source. Values only present in rendered markup are read via
ARIA labels, data-component attributes and href suffixes - never hashed classes.
"""
from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

GITHUB = "https://github.com"

_EMBEDDED_RE = re.compile(
    r'<script type="application/json" data-target="react-app\.embeddedData">(.*?)</script>',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


# ----------------------------------------------------------------- helpers
def embedded_payload(page_html: str) -> dict[str, Any]:
    m = _EMBEDDED_RE.search(page_html)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("payload", {}) or {}
    except json.JSONDecodeError:
        return {}


def strip_highlight(text: str | None) -> str:
    """Search JSON wraps matches in <em> and HTML-escapes the rest."""
    if not text:
        return ""
    return html_lib.unescape(_TAG_RE.sub("", text)).strip()


def parse_count(text: str | None) -> int | None:
    """'6,495' -> 6495, '1.1k' -> 1100, '2.3m' -> 2300000."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km]?)", t)
    if not m:
        return None
    n = float(m.group(1))
    n *= {"k": 1_000, "m": 1_000_000}.get(m.group(2), 1)
    return int(round(n))


# ----------------------------------------------------------------- search
def parse_search_page(page_html: str, expected_page: int | None = None) -> dict[str, Any]:
    """Return {"results": [...], "page", "page_count", "total", "source"}.

    After client-side (SPA) navigation the embedded JSON can belong to the
    previous page, so it is only trusted when its page number matches; otherwise
    the rendered result list is parsed instead.
    """
    route = embedded_payload(page_html).get("blackbirdSearchRoute") or {}
    if route.get("type") == "repositories" and route.get("results") is not None and (
        expected_page is None or route.get("page") == expected_page
    ):
        results = [_search_result_from_json(r) for r in route["results"]]
        page, page_count = route.get("page") or 1, route.get("page_count") or 0
        return {
            "results": results,
            "page": page,
            "page_count": page_count,
            "total": route.get("result_count"),
            "source": "json",
            "has_next": page < page_count,
        }
    return _parse_search_dom(page_html)


def _search_result_from_json(r: dict[str, Any]) -> dict[str, Any]:
    repo = (r.get("repo") or {}).get("repository") or {}
    full_name = strip_highlight(r.get("hl_name")) or f"{repo.get('owner_login')}/{repo.get('name')}"
    return {
        "full_name": full_name,
        "url": f"{GITHUB}/{full_name}",
        "owner": repo.get("owner_login") or full_name.split("/")[0],
        "name": repo.get("name") or full_name.split("/")[-1],
        "description": strip_highlight(r.get("hl_trunc_description")),
        "language": r.get("language") or "",
        "stars": r.get("followers"),
        "topics": [strip_highlight(t) for t in (r.get("topics") or [])],
        "archived": bool(r.get("archived")),
        "is_mirror": bool(r.get("mirror")),
        "sponsorable": bool(r.get("sponsorable")),
        "owned_by_org": bool(r.get("owned_by_organization")),
        "help_wanted_issues": r.get("help_wanted_issues_count"),
        "good_first_issues": r.get("good_first_issue_issues_count"),
        "updated_at": repo.get("updated_at") or "",
    }


def _parse_search_dom(page_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(page_html, "lxml")
    out: list[dict[str, Any]] = []
    rl = soup.select_one('[data-testid="results-list"]')
    for row in (rl.find_all(recursive=False) if rl else []):
        link = row.select_one("h3 a[href]")
        if not link:
            continue
        full_name = link["href"].strip("/")
        h3 = link.find_parent("h3")
        desc_el = h3.find_next_sibling("div") if h3 else None
        lang = row.select_one('span[aria-label$=" language"]')
        stars = row.select_one('a[href$="/stargazers"][aria-label]')
        updated = row.find(lambda t: t.name == "span" and t.get_text(strip=True).startswith("Updated"))
        upd_title = updated.select_one("[title]") if updated else None
        out.append({
            "full_name": full_name,
            "url": f"{GITHUB}/{full_name}",
            "owner": full_name.split("/")[0],
            "name": full_name.split("/")[-1],
            "description": desc_el.get_text(" ", strip=True) if desc_el else "",
            "language": lang.get_text(strip=True) if lang else "",
            "stars": parse_count(stars["aria-label"]) if stars else None,
            "topics": [],
            "archived": "archive" in row.get_text(" ", strip=True).lower()[:300],
            "updated_at": upd_title["title"] if upd_title else "",
        })
    page = page_count = None
    nxt = soup.select_one('a[rel="next"][href]')
    if nxt:
        qs = parse_qs(urlparse(nxt["href"]).query)
        page = int(qs.get("p", ["2"])[0]) - 1
    return {"results": out, "page": page, "page_count": page_count,
            "total": None, "source": "dom", "has_next": nxt is not None}


# ----------------------------------------------------------------- repo page
def parse_repo_page(page_html: str, full_name: str) -> dict[str, Any]:
    payload = embedded_payload(page_html)
    soup = BeautifulSoup(page_html, "lxml")
    base = f"/{full_name}"

    about = payload.get("sidebarAbout") or {}
    layout_repo = (payload.get("codeViewLayoutRoute") or {}).get("repo") or {}
    route = payload.get("codeViewRepoRoute") or {}
    tree = route.get("tree") or {}
    overview = route.get("overview") or {}
    about_repo = about.get("repo") or {}
    releases = (about.get("sections") or {}).get("releases") or {}
    license_ = about_repo.get("license") or {}

    data: dict[str, Any] = {
        "full_name": full_name,
        "url": f"{GITHUB}/{full_name}",
        "description": about.get("description") or "",
        "website": about.get("website") or "",
        "topics": [t.get("name") for t in (about.get("topics") or []) if t.get("name")],
        "stars": about.get("stargazerCount"),
        "forks": about.get("forksCount"),
        "watchers": about.get("watcherCount"),
        "license": license_.get("spdxId") or license_.get("name") or "",
        "releases": releases.get("releaseCount"),
        "tags": releases.get("tagCount"),
        "default_branch": layout_repo.get("defaultBranch") or (route.get("refInfo") or {}).get("name") or "",
        "created_at": layout_repo.get("createdAt") or "",
        "is_fork": layout_repo.get("isFork", about_repo.get("isFork")),
        "is_template": about_repo.get("isTemplate"),
        "archived": layout_repo.get("isArchived", about_repo.get("isArchived")),
        "owner_avatar": layout_repo.get("ownerAvatar") or about_repo.get("ownerAvatarUrl") or "",
        "commits": parse_count(overview.get("commitCount")),
        "root_files": [i.get("name") for i in (tree.get("items") or []) if i.get("name")],
    }

    # --- values only present in rendered markup ---------------------------
    data["branches"] = _link_count(soup, f'a[href="{base}/branches"]')
    if data["tags"] is None:
        data["tags"] = _link_count(soup, f'a[href="{base}/tags"]')
    if data["commits"] is None:
        data["commits"] = _link_count(soup, f'a[href^="{base}/commits/"]')

    data["contributors"] = _heading_counter(soup, f"{base}/graphs/contributors")
    if data["releases"] is None:
        data["releases"] = _heading_counter(soup, f"{base}/releases")

    data["open_issues"] = _tab_counter(soup, f"{base}/issues")
    data["open_pull_requests"] = _tab_counter(soup, f"{base}/pulls")

    data["languages"] = {}
    for bar in soup.select('span[role="progressbar"][aria-label]'):
        name, _, pct = bar["aria-label"].rpartition(":")
        if name and pct.strip().endswith("%"):
            data["languages"][name.strip()] = float(pct.strip().rstrip("%"))
    data["language"] = next(iter(data["languages"]), "")

    rt = soup.select_one("relative-time[datetime]")
    data["last_commit_at"] = rt["datetime"] if rt else ""

    # Fallbacks if the JSON shape ever changes.
    if not data["description"]:
        og = soup.select_one('meta[property="og:description"]')
        data["description"] = og["content"] if og else ""
    if data["stars"] is None:
        el = soup.select_one(f'a[href="{base}/stargazers"]')
        data["stars"] = parse_count(el.get_text()) if el else None
    return data


def _link_count(soup: BeautifulSoup, selector: str) -> int | None:
    for a in soup.select(selector):
        txt = a.get_text(" ", strip=True)
        if txt and re.match(r"^[\d,.]+[km]?\b", txt.lower()):
            return parse_count(txt)
    return None


def _heading_counter(soup: BeautifulSoup, href: str) -> int | None:
    for a in soup.select(f'h2 a[href="{href}"]'):
        h2 = a.find_parent("h2")
        c = h2.select_one('[data-component="CounterLabel"]') if h2 else None
        if c:
            return parse_count(c.get_text())
    return None


def _tab_counter(soup: BeautifulSoup, href: str) -> int | None:
    """Repo tab count. GitHub hides the counter at 0; a missing tab means disabled (None)."""
    tabs = soup.select(f'a[href="{href}"]')
    for a in tabs:
        c = a.select_one('[data-component="CounterLabel"]')
        if c:
            return parse_count(c.get_text())
    return 0 if tabs else None


# ----------------------------------------------------------------- blocks
_BLOCK_MARKERS = (
    "whoa there!",
    "too many requests",
    "you have triggered an abuse detection mechanism",
    "secondary rate limit",
    "exceeded a secondary rate limit",
)


def detect_block(page_html: str, title: str = "") -> str | None:
    """Return a reason string if GitHub served a rate-limit / abuse page.

    Real data pages always carry the embedded React payload, so body markers
    only count when it is missing (a README mentioning "too many requests"
    must not trigger a false positive).
    """
    t = title.lower()
    if "unicorn" in t or "rate limit" in t or "too many requests" in t:
        return title
    if _EMBEDDED_RE.search(page_html):
        return None
    body = page_html.lower()
    for marker in _BLOCK_MARKERS:
        if marker in body:
            return marker
    return None
