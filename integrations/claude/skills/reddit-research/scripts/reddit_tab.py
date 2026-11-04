#!/usr/bin/env python3
"""Polite, read-only Reddit research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
Reddit login but never each other's tabs; all page loads share one pacing lock.

CLI (each command prints JSON):

    reddit_tab.py open [--wait [SECONDS]]           claim a window -> {"target": ID}
    reddit_tab.py search ID QUERY [--sub S] [--sort relevance|new|top|comments] [--time hour|day|week|month|year|all]
    reddit_tab.py search ID --url URL               any Reddit search/listing URL
    reddit_tab.py thread ID URL [--max N]           post + top comments (sorted by top)
    reddit_tab.py nav ID URL                        paced load, page state only
    reddit_tab.py eval ID JS|@file.js               run JS in your tab (escape hatch)
    reddit_tab.py close ID                          release the window
    reddit_tab.py list | gc                         show / clean up claims

The same functions back the reddit_* tools of the chrome-agent MCP server.

Environment (REDDIT_TAB_*): INSTANCE (reddit-research-01), MAX (3 windows),
GAP (4 s between loads, all agents), IDLE (1800 s), MAX_LOADS (40 per window),
STATE (/tmp/chrome-agent-reddit). Needs `chrome-agent` on PATH.
"""
import sys
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from research_core import Fail, Site, common_cli, opt, run_cli  # noqa: E402

SORTS = ("relevance", "new", "top", "comments", "hot")
TIMES = ("hour", "day", "week", "month", "year", "all")


def _js(name: str) -> str:
    return (HERE / name).read_text()


def _stop_reason(state: dict, site: Site) -> str | None:
    if state.get("blocked"):
        return "Reddit is showing a block or CAPTCHA page. Stop and ask the user; do not retry."
    if not state.get("loggedIn"):
        return ("Reddit is not signed in. Ask the user to log in manually in the browser window, "
                f"then save it: chrome-agent save {site.instance} --overwrite")
    return None


SITE = Site(
    prefix="REDDIT_TAB",
    default_instance="reddit-research-01",
    hosts=("reddit.com",),
    forbidden=(".json", "/api/"),
    ready_js=("document.readyState === 'complete' && "
              "!!document.querySelector(\"shreddit-post, shreddit-comment, a[href*='/comments/']\")"),
    page_state_js=_js("page_state.js"),
    stop_reason=_stop_reason,
    default_gap=4.0,
)
INSTANCE = SITE.instance


# -------------------------------------------------------------------------
# Operations (used by the CLI and the MCP server)
# -------------------------------------------------------------------------

def open_window(wait_seconds: float = 0) -> dict:
    return SITE.open_window(wait_seconds)


def nav(target: str, url: str) -> dict:
    return SITE.nav(target, url)


def search_url(query: str, subreddit: str | None = None, sort: str = "relevance",
               time_filter: str = "year") -> str:
    if sort not in SORTS:
        raise Fail(f"sort must be one of {SORTS}")
    if time_filter not in TIMES:
        raise Fail(f"time must be one of {TIMES}")
    base = f"https://www.reddit.com/r/{quote(subreddit.removeprefix('r/'))}/search/" if subreddit \
        else "https://www.reddit.com/search/"
    return base + "?" + urlencode({"q": query, "type": "posts", "sort": sort, "t": time_filter},
                                  quote_via=quote)


def search(target: str, query: str | None = None, *, url: str | None = None,
           subreddit: str | None = None, sort: str = "relevance", time_filter: str = "year") -> dict:
    """Run a Reddit search (or load a listing URL) and return the unique posts on it."""
    url = url or search_url(query or "", subreddit, sort, time_filter)
    page = SITE.nav(target, url)
    if "stop" in page:
        return {"page": page, "posts": []}
    posts = SITE.evaluate(SITE.resolve(target), _js("search_results.js")) or []
    return {"page": page, "posts": posts}


def thread(target: str, url: str, max_comments: int = 15) -> dict:
    """Load a thread sorted by top: the post plus its top comments (top-level, plus
    the OP's own replies under them so AMA answers come through)."""
    if not url.startswith("http"):
        url = "https://www.reddit.com/" + url.lstrip("/")
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query))
    q.setdefault("sort", "top")
    url = urlunparse(parts._replace(query=urlencode(q)))
    page = SITE.nav(target, url)
    if "stop" in page:
        return {"page": page, "thread": None}
    data = SITE.evaluate(SITE.resolve(target), _js("thread.js"))
    if data is None:
        return {"page": page, "thread": None, "note": "no post found on this page (removed, or not a thread URL)"}
    out, kept, include = [], 0, False
    for c in data["comments"]:
        if c["depth"] == 0:
            include = kept < max_comments
            kept += include
        if include:
            out.append(c)
    data["comments"] = out
    return {"page": page, "thread": data}


def run_js(target: str, js: str):
    return SITE.run_js(target, js)


def close(target: str) -> dict:
    return SITE.close(target)


def list_claims() -> list[dict]:
    return SITE.list_claims()


def gc() -> dict:
    return SITE.gc()


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def _dispatch(cmd: str, args: list[str]):
    if cmd == "search":
        url = opt(args, "--url")
        sub, sort, t = opt(args, "--sub"), opt(args, "--sort", "relevance"), opt(args, "--time", "year")
        if not args or (len(args) < 2 and not url):
            raise Fail("usage: search ID QUERY [--sub S] [--sort S] [--time T] | search ID --url URL")
        return search(args[0], " ".join(args[1:]) or None, url=url, subreddit=sub, sort=sort, time_filter=t)
    if cmd == "thread":
        n = int(opt(args, "--max", 15))
        if len(args) != 2:
            raise Fail("usage: thread ID URL [--max N]")
        return thread(args[0], args[1], n)
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
