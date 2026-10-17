"""Web search and page fetching."""

from __future__ import annotations

import html
import re
import urllib.parse

from ..common import truncate
from ..http_utils import html_to_text, http_json, http_open, http_text
from ..registry import S, tool


def _ddg_search(query: str, n: int) -> list[dict]:
    body = urllib.parse.urlencode({"q": query}).encode()
    with http_open("https://html.duckduckgo.com/html/", body, timeout=15,
                   headers={"Content-Type": "application/x-www-form-urlencoded",
                            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/126.0 Safari/537.36"}) as r:
        page = r.read().decode("utf-8", "replace")
    results = []
    for block in re.split(r'(?=<div[^>]+class="[^"]*\bresult\b)', page)[1:]:
        if "result--ad" in block[:400]:
            continue
        a = re.search(r'<a\b[^>]*class="result__a"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        href_m = re.search(r'href="([^"]+)"', a.group(0))
        href = html.unescape(href_m.group(1)) if href_m else ""
        if "uddg=" in href:
            href = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0])
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div|td)>', block, re.S)
        clean = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()  # noqa: E731
        results.append({"title": clean(a.group(1)), "url": href, "snippet": clean(sn.group(1) if sn else "")})
        if len(results) >= n:
            break
    return results


def _wiki_search(query: str, n: int) -> list[dict]:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n})
    d = http_json(url, timeout=15) or {}
    return [{"title": r["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(r["title"].replace(" ", "_")),
             "snippet": html.unescape(re.sub(r"<[^>]+>", "", r.get("snippet", "")))}
            for r in d.get("query", {}).get("search", [])]


@tool("web_search", "Search the web. Returns titles, URLs and snippets. Follow up with fetch_url to read pages.",
      {"query": S("Search query"), "max_results": S("Number of results (default 6)", "integer")}, ["query"])
def t_web_search(ag, query: str, max_results: int = 6):
    n = max(1, min(int(max_results or 6), 12))
    results, errors = [], []
    sx = ag.app.cfg.get("search.searxng_url")
    if sx:
        try:
            d = http_json(sx.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": query, "format": "json"}), timeout=15)
            results = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                       for r in (d or {}).get("results", [])[:n]]
        except Exception as e:
            errors.append(f"searxng: {e}")
    if not results:
        try:
            results = _ddg_search(query, n)
        except Exception as e:
            errors.append(f"duckduckgo: {e}")
    if not results:
        try:
            results = _wiki_search(query, n)
        except Exception as e:
            errors.append(f"wikipedia: {e}")
    if not results:
        return "No results. " + "; ".join(errors)
    lines = [f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet'][:300]}" for i, r in enumerate(results, 1)]
    return "<<<UNTRUSTED WEB CONTENT>>>\n" + "\n".join(lines) + "\n<<<END UNTRUSTED>>>"


@tool("fetch_url", "Download a web page and return its readable text.",
      {"url": S("http(s) URL"), "max_chars": S("Maximum characters to return (default 8000)", "integer")}, ["url"])
def t_fetch_url(ag, url: str, max_chars: int = 8000):
    ag.app.security.url(url)
    text, ctype = http_text(url, timeout=20)
    if "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, body = html_to_text(text)
    else:
        title, body = "", text
    n = max(500, min(int(max_chars or 8000), 40000))
    return (f"<<<UNTRUSTED WEB CONTENT from {url}>>>\nTitle: {title}\n\n{truncate(body, n)}\n<<<END UNTRUSTED>>>")
