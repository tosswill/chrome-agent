#!/usr/bin/env python3
"""Read-only Hacker News research through public APIs (no account, no browser).

Search, comment trees and user lookups use the Algolia HN Search API (hn.algolia.com/api/v1: full-text search,
filters, whole comment trees in one call). Live front-page lists and authoritative scores use the official
Firebase API (hacker-news.firebaseio.com/v0). Both are public, unauthenticated, GET only.

CLI (each command prints JSON):

    hn_api.py search QUERY [--sort relevance|date] [--tags story|comment|ask_hn|show_hn|poll|front_page]
                       [--author NAME] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--min-points N]
                       [--min-comments N] [--story ID] [--url-only] [--max N] [--page N]
                       stories or comments matching QUERY. --url-only matches the submitted URL only (every HN
                       thread about a link: `search github.com/owner/repo --url-only`); --story ID keeps comments
                       on one story
    hn_api.py item ID [--max-comments N] [--max-depth N]   a story (or comment) with its comment tree, flattened
                       in thread order with depth, parent and reply counts
    hn_api.py user NAME                          karma, account age, about (and how many items submitted)
    hn_api.py front [top|new|best|ask|show|job] [--max N]   the live list, with each story's score and comments

Environment (HN_RESEARCH_*): GAP (0.4 s between calls), MAX_CALLS (300 per run folder), TIMEOUT (20 s).
Algolia allows about 10,000 requests an hour per IP; search returns at most 1,000 hits per query.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
import research_core as core  # noqa: E402
from research_core import Fail, opt  # noqa: E402

SITE = "hackernews"
ALGOLIA = "https://hn.algolia.com/api/v1"
FIREBASE = "https://hacker-news.firebaseio.com/v0"
HOSTS = ("hn.algolia.com", "hacker-news.firebaseio.com")
GAP = float(os.environ.get("HN_RESEARCH_GAP", "0.4"))
MAX_CALLS = int(os.environ.get("HN_RESEARCH_MAX_CALLS", "300"))
TIMEOUT = float(os.environ.get("HN_RESEARCH_TIMEOUT", "20"))
STATE = Path(os.environ.get("HN_RESEARCH_STATE", "~/.local/state/chrome-agent-research/hackernews")).expanduser()
UA = "research-helper/1.0"  # generic: never the user's name or email
TAGS = {"story", "comment", "ask_hn", "show_hn", "poll", "pollopt", "front_page", "job"}
LISTS = {"top": "topstories", "new": "newstories", "best": "beststories", "ask": "askstories",
         "show": "showstories", "job": "jobstories"}


class Stop(Exception):
    pass


def _count_call() -> None:
    d = core.raw_dir(SITE)
    if not d:
        return
    f = d / ".calls"
    try:
        n = int(f.read_text().strip() or 0)
    except (OSError, ValueError):
        n = 0
    if n >= MAX_CALLS:
        raise Stop(f"API call budget for this run is spent ({MAX_CALLS}; HN_RESEARCH_MAX_CALLS raises it). "
                   "Tell the user rather than continuing.")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(n + 1))


def _pace() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = STATE / "last_call"
    try:
        wait = float(stamp.read_text()) + GAP - time.time()
    except (OSError, ValueError):
        wait = 0
    if wait > 0:
        time.sleep(wait)
    stamp.write_text(str(time.time()))


def http_get(url: str):
    """GET JSON from one of the two HN API hosts. Raises Stop on rate limits, Fail on other errors."""
    host = urllib.parse.urlparse(url).netloc
    if host not in HOSTS or not url.startswith("https://"):
        raise Fail(f"refusing to fetch {url}: only {', '.join(HOSTS)} over https")
    _count_call()
    _pace()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(20_000_000)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Stop("Hacker News API rate limit reached (HTTP 429); wait an hour or tell the user")
        if e.code == 404:
            raise Fail(f"not found: {url}")
        raise Fail(f"HN API error {e.code} for {url}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise Fail(f"HN API unreachable ({e}) for {url}")
    try:
        return json.loads(body)
    except ValueError:
        raise Fail(f"HN API returned non-JSON for {url}")


def clean(text: str | None) -> str:
    """HN's HTML (comment_text, story text, about) as plain text; links kept as their URL."""
    if not text:
        return ""
    t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>.*?</a>', lambda m: html.unescape(m.group(1)), text, flags=re.S)
    t = re.sub(r"<p>", "\n\n", t)
    t = re.sub(r"<pre><code>(.*?)</code></pre>", r"\n\1\n", t, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def _day(s: str, end: bool = False) -> int:
    try:
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Fail(f"dates are YYYY-MM-DD (got {s!r})")
    return int(d.timestamp()) + (86399 if end else 0)


def hn_url(item_id) -> str:
    return f"https://news.ycombinator.com/item?id={item_id}"


def _hit(h: dict) -> dict:
    tags = h.get("_tags") or []
    is_comment = "comment" in tags
    out = {"id": h.get("objectID"), "type": "comment" if is_comment else
           next((t for t in ("ask_hn", "show_hn", "poll", "job") if t in tags), "story"),
           "author": h.get("author"), "created": h.get("created_at"), "hn_url": hn_url(h.get("objectID")),
           "front_page": "front_page" in tags}
    if is_comment:
        out.update(text=clean(h.get("comment_text")), story_id=h.get("story_id"), story_title=h.get("story_title"),
                   parent_id=h.get("parent_id"), story_hn_url=hn_url(h.get("story_id")) if h.get("story_id") else None)
    else:
        out.update(title=h.get("title"), url=h.get("url") or None, points=h.get("points"),
                   comments=h.get("num_comments"), text=clean(h.get("story_text")) or None)
    return out


def search(query: str, sort: str = "relevance", tags: str | None = None, author: str | None = None,
           since: str | None = None, until: str | None = None, min_points: int | None = None,
           min_comments: int | None = None, limit: int = 30, page: int = 0, story: str | None = None,
           url_only: bool = False) -> dict:
    tag_list = []
    for t in (tags or "").split(","):
        t = t.strip()
        if t:
            if t not in TAGS:
                raise Fail(f"--tags takes {', '.join(sorted(TAGS))} (comma = all of them)")
            tag_list.append(t)
    if author:
        if not re.match(r"^[A-Za-z0-9_-]{1,32}$", author):
            raise Fail("--author must be an HN username")
        tag_list.append(f"author_{author}")
    if story:
        if not str(story).isdigit():
            raise Fail("--story takes a story ID (a number)")
        tag_list.append(f"story_{story}")
    nums = []
    if since:
        nums.append(f"created_at_i>={_day(since)}")
    if until:
        nums.append(f"created_at_i<={_day(until, end=True)}")
    if min_points is not None:
        nums.append(f"points>={int(min_points)}")
    if min_comments is not None:
        nums.append(f"num_comments>={int(min_comments)}")
    params = {"query": query, "hitsPerPage": str(limit), "page": str(page)}
    if tag_list:
        params["tags"] = ",".join(tag_list)
    if nums:
        params["numericFilters"] = ",".join(nums)
    if url_only:
        params["restrictSearchableAttributes"] = "url"
    endpoint = "search_by_date" if sort == "date" else "search"
    body = http_get(f"{ALGOLIA}/{endpoint}?{urllib.parse.urlencode(params)}")
    hits = [_hit(h) for h in body.get("hits") or []]
    return {"query": query, "sort": sort, "tags": tag_list, "filters": nums, "url_only": url_only,
            "total": body.get("nbHits"),
            "page_number": body.get("page"), "pages": body.get("nbPages"), "hits": hits,
            "note": "Algolia caps a query at 1,000 hits; narrow with --since/--until or --tags for more.",
            "page": {"url": f"https://hn.algolia.com/?q={urllib.parse.quote(query)}"}}


def item(item_id: str, max_comments: int = 300, max_depth: int = 20) -> dict:
    if not str(item_id).isdigit():
        raise Fail("item ID must be a number (the id= in a news.ycombinator.com link)")
    root = http_get(f"{ALGOLIA}/items/{item_id}")
    flat: list[dict] = []
    total = [0]

    def walk(node: dict, depth: int):
        for ch in node.get("children") or []:
            if ch.get("type") != "comment":
                continue
            total[0] += 1
            if len(flat) < max_comments and depth <= max_depth:
                text = clean(ch.get("text"))
                flat.append({"id": ch.get("id"), "author": ch.get("author"), "created": ch.get("created_at"),
                             "depth": depth, "parent": ch.get("parent_id"), "text": text,
                             "points": ch.get("points"),
                             "replies": sum(1 for c in ch.get("children") or [] if c.get("type") == "comment"),
                             "deleted": not ch.get("author") and not text,
                             "link": hn_url(ch.get("id"))})
            walk(ch, depth + 1)
    walk(root, 1)
    out = {"id": root.get("id"), "type": root.get("type"), "author": root.get("author"),
           "created": root.get("created_at"), "title": root.get("title"), "url": root.get("url") or None,
           "points": root.get("points"), "text": clean(root.get("text")) or None, "story_id": root.get("story_id"),
           "hn_url": hn_url(root.get("id")), "comments_total": total[0], "comments": flat,
           "comments_truncated": total[0] > len(flat),
           "note": "HN doesn't publish comment scores; order is HN's thread order (roughly by rank) as Algolia "
                   "returns it.", "page": {"url": hn_url(root.get("id"))}}
    return out


def user(name: str) -> dict:
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", name):
        raise Fail("expected an HN username")
    u = http_get(f"{FIREBASE}/user/{name}.json")
    if not u:
        raise Fail(f"no HN user {name!r}")
    created = u.get("created")
    return {"username": u.get("id"), "karma": u.get("karma"),
            "created": datetime.fromtimestamp(created, timezone.utc).strftime("%Y-%m-%d") if created else None,
            "account_age_days": int((time.time() - created) / 86400) if created else None,
            "about": clean(u.get("about")) or None, "submitted_count": len(u.get("submitted") or []),
            "page": {"url": f"https://news.ycombinator.com/user?id={name}"}}


def front(which: str = "top", limit: int = 30) -> dict:
    if which not in LISTS:
        raise Fail(f"front takes {', '.join(LISTS)}")
    ids = http_get(f"{FIREBASE}/{LISTS[which]}.json") or []
    stories = []
    for rank, i in enumerate(ids[:limit], 1):
        it = http_get(f"{FIREBASE}/item/{i}.json") or {}
        stories.append({"rank": rank, "id": i, "type": it.get("type"), "title": it.get("title"),
                        "url": it.get("url"), "author": it.get("by"), "points": it.get("score"),
                        "comments": it.get("descendants"),
                        "created": datetime.fromtimestamp(it["time"], timezone.utc).isoformat() if it.get("time") else None,
                        "hn_url": hn_url(i)})
    return {"list": which, "listed": len(ids), "stories": stories,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "page": {"url": "https://news.ycombinator.com/" + ("" if which == "top" else which)}}


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise Fail(f"{name} needs a number")


def _dispatch(cmd: str, args: list[str]):
    args = list(args[1:])  # args[0] is the "api" placeholder (see main)
    try:
        if cmd == "search":
            kw = dict(sort=opt(args, "--sort", "relevance"), tags=opt(args, "--tags"), author=opt(args, "--author"),
                      since=opt(args, "--since"), until=opt(args, "--until"))
            kw.update(story=opt(args, "--story"), url_only=opt(args, "--url-only") is True)
            mp, mc = opt(args, "--min-points"), opt(args, "--min-comments")
            kw.update(min_points=_int(mp, "--min-points") if mp is not None else None,
                      min_comments=_int(mc, "--min-comments") if mc is not None else None,
                      limit=max(1, min(_int(opt(args, "--max", 30), "--max"), 100)),
                      page=_int(opt(args, "--page", 0), "--page"))
            return search(" ".join(args), **kw) if args or kw.get("tags") else NotImplemented
        if cmd == "item" and args:
            mc = _int(opt(args, "--max-comments", 300), "--max-comments")
            md = _int(opt(args, "--max-depth", 20), "--max-depth")
            return item(args[0], max(1, min(mc, 2000)), md)
        if cmd == "user" and args:
            return user(args[0])
        if cmd == "front":
            n = max(1, min(_int(opt(args, "--max", 30), "--max"), 100))
            return front(args[0] if args else "top", n)
    except Stop as e:
        return {"stop": str(e)}
    return NotImplemented


def main(argv: list[str]) -> int:
    core._LOG.site = SITE  # raw results go to raw/hackernews/
    if len(argv) >= 2 and argv[1] not in ("-h", "--help", "help"):
        argv = [argv[0], argv[1], "api", *argv[2:]]  # placeholder where research_core expects a window ID
    return core.run_cli(__doc__, _dispatch, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
