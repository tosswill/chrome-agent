#!/usr/bin/env python3
"""Polite, read-only Reddit research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
Reddit login but never each other's tabs; all page loads share one pacing lock.

CLI (each command prints JSON):

    reddit_tab.py open [--wait [SECONDS]]           claim a window -> {"target": ID}
    reddit_tab.py search ID QUERY [--sub S] [--sort relevance|new|top|comments] [--time hour|day|week|month|year|all]
    reddit_tab.py search ID --url URL               any Reddit search/listing URL
    reddit_tab.py thread ID URL [--max N]           open a thread (by clicking it in the current results
                                                    when it's there), read it, return post + top comments
                                                    with permalinks and a promo_bias score
    reddit_tab.py tally ID NAME [NAME ...]          distinct users and upvotes behind each name, among
                                                    everything this window has seen (no page load)
    reddit_tab.py shot ID [--full] [--out-dir DIR | --out PATH]
                                                    visible page content (top bar and left nav excluded)
    reddit_tab.py nav ID URL                        paced load, page state only
    reddit_tab.py eval ID JS|@file.js               run JS in your tab (escape hatch)
    reddit_tab.py close ID                          release the window
    reddit_tab.py list | gc                         show / clean up claims

The same functions back the reddit_* tools of the chrome-agent MCP server.

Environment (REDDIT_TAB_*): INSTANCE (reddit-research-01), MAX (3 windows),
GAP (4 s between loads, all agents), IDLE (1800 s), MAX_LOADS (40 per window),
STATE (/tmp/chrome-agent-reddit). Needs `chrome-agent` on PATH.
"""
import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from research_core import Fail, Site, common_cli, opt, pause, run_cli  # noqa: E402
import disclosure as D  # noqa: E402

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
    full_id = SITE.resolve(target)
    posts = SITE.evaluate(full_id, _js("search_results.js")) or []
    _seen_add(full_id, [{"url": p["url"], "author": None, "title": p.get("title", ""), "text": "", "via": "search"}
                        for p in posts])
    return {"page": page, "posts": posts}


_SHOTS = SITE.state / "shots"
_BADGE = re.compile(r"\b(mod|moderator|official|staff|employee|team|admin|community manager|rep)\b", re.I)


def _seen_add(full_id: str, items: list[dict]) -> None:
    c = SITE.claims().get(full_id)
    if c is None:
        return
    seen = c.get("seen", [])
    keys = {(x.get("url"), x.get("author")) for x in seen}
    seen += [x for x in items if (x.get("url"), x.get("author")) not in keys]
    c["seen"] = seen[-1500:]
    SITE._write_claim(full_id, c)


def _post_id(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url, re.I)
    return m.group(1) if m else None


def _open_thread(full_id: str, url: str) -> dict:
    """Open a thread the way a person would: click it in the results on screen if it's there
    (paced and counted like any page load); otherwise load the URL."""
    pid = _post_id(url)
    sel = f'a[data-testid="post-title-text"][href*="/comments/{pid}/"], a[data-testid="post-title"][href*="/comments/{pid}/"]'
    if pid and SITE.rect(full_id, sel) is not None or (pid and SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(sel)})")):
        loads = SITE._count_load(full_id)
        SITE._pace()
        if SITE.click_on(full_id, sel, look=random.uniform(0.8, 2.0)):
            SITE.wait_for(full_id, f"!!document.querySelector('shreddit-post[id=\"t3_{pid}\"]') && document.readyState === 'complete'",
                          timeout=15, settle=SITE.settle)
            state = SITE.evaluate(full_id, SITE.page_state_js) or {}
            state.update(loads=loads, opened="clicked from results")
            reason = SITE.stop_reason(state, SITE)
            if reason:
                state["stop"] = reason
            return state
    state = SITE.nav(full_id, url)
    state["opened"] = "loaded by URL"
    return state


def _read_down(full_id: str) -> None:
    """Scroll down through the thread with the mouse wheel, pausing as if reading.
    This also lets Reddit load more comments."""
    for _ in range(random.randint(3, 6)):
        SITE.wheel(full_id, random.uniform(400, 750))
        pause(random.uniform(0.8, 2.4), fuzz=0)


def promo_bias(data: dict) -> dict:
    """0.0-1.0 likelihood that a thread is brand promotion or astroturf, with evidence. Page-only:
    account age and karma aren't checked (that would cost a profile load per user)."""
    sub = (data.get("subreddit") or "").removeprefix("r/")
    subject = " ".join([data.get("title") or "", sub, data.get("flair") or ""])
    toks = D.brand_tokens(subject)
    is_brand = lambda name: bool(name) and any(t in name.lower() for t in toks)
    page = D.scan_text(data.get("title", ""), "title", data.get("url"), subject)
    page += D.scan_text(data.get("body", ""), "post", data.get("url"), subject)
    if is_brand(data.get("author")) or _BADGE.search(data.get("author_badges") or ""):
        page.insert(0, {"signal": "brand_account_post", "weight": 0.6, "source": "post",
                        "text": f"Posted by u/{data.get('author')} ({data.get('author_badges') or 'no badge'})", "link": data.get("url")})
    extra, brand_comments = [], []
    brand_mod_names = {m["name"] for m in data.get("moderators", []) if is_brand(m["name"]) or
                       re.search(r"\b(team|staff|official|employee)\b", m.get("flair") or "", re.I)}
    for c in data.get("comments", []):
        if is_brand(c.get("author")) or c.get("author") in brand_mod_names or _BADGE.search(c.get("badges") or ""):
            brand_comments.append({"author": c["author"], "badges": c.get("badges"), "score": c.get("score"), "link": c.get("link")})
        for h in D.scan_text(c.get("text", ""), f"comment by u/{c.get('author')}", c.get("link"), subject):
            if h["signal"] in ("use_my_link_or_code", "affiliate_disclosure", "sponsor_read", "third_party_sponsor", "review_unit"):
                extra.append(h)
    if brand_comments:
        extra.insert(0, {"signal": "brand_accounts_in_thread", "weight": 0.15, "source": "comments",
                         "text": ", ".join(f"u/{b['author']}" for b in brand_comments[:5]), "link": brand_comments[0]["link"]})
    brand_mods = [m for m in data.get("moderators", []) if is_brand(m["name"]) or
                  re.search(r"\b(team|staff|official|employee)\b", m.get("flair") or "", re.I)]
    if brand_mods:
        extra.append({"signal": "brand_moderated_subreddit", "weight": 0.2, "source": "sidebar",
                      "text": f"r/{sub} moderators include brand accounts: " + ", ".join(
                          f"u/{m['name']}" + (f" ({m['flair']})" if m.get("flair") else "") for m in brand_mods[:5])})
    elif sub and any(t == sub.lower() for t in toks):
        extra.append({"signal": "brand_subreddit", "weight": 0.1, "source": "subreddit",
                      "text": f"r/{sub} is the product's own community; it may be close to the brand"})
    res = D.score(D.brand_adjust(page, subject, None), D.brand_adjust(extra, subject, None))
    res["brand_accounts"] = brand_comments
    res["brand_moderators"] = [m["name"] for m in brand_mods]
    res["ai_declared_comments"] = sum(1 for c in data.get("comments", []) if c.get("ai_declared"))
    res["confidence"] = "medium"
    res["note"] = ("Page-only: account age and karma aren't checked. A brand account or brand subreddit is "
                   "context, not proof of bad faith; weigh the substance of what they say.")
    return res


def thread(target: str, url: str, max_comments: int = 15) -> dict:
    """Open a thread (clicking it in the results when possible), read down it, and return the post
    and its top comments (top-level, plus the OP's own replies so AMA answers come through)."""
    if not url.startswith("http"):
        url = "https://www.reddit.com/" + url.lstrip("/")
    full_id = SITE.resolve(target)
    explicit_sort = "sort=" in url
    page = SITE.nav(full_id, url) if explicit_sort else _open_thread(full_id, url)
    if "stop" in page:
        return {"page": page, "thread": None}
    _read_down(full_id)
    data = SITE.evaluate(full_id, _js("thread.js"))
    if data is None:
        return {"page": page, "thread": None, "note": "no post found on this page (removed, or not a thread URL)"}
    out, kept, include = [], 0, False
    for c in data["comments"]:
        if c["depth"] == 0:
            include = kept < max_comments
            kept += include
        if include:
            out.append(c)
    all_comments = data["comments"]
    data["comments"] = out
    data["comment_order"] = "sort=" + url.split("sort=")[1].split("&")[0] if explicit_sort else "Reddit default (best)"
    data["promo_bias"] = promo_bias({**data, "comments": all_comments})
    _seen_add(full_id, [{"url": data["url"], "author": data.get("author"), "title": data.get("title", ""),
                         "text": data.get("body", ""), "score": data.get("score"), "via": "post"}]
              + [{"url": c.get("link"), "author": c.get("author"), "title": "", "text": c.get("text", ""),
                  "score": c.get("score"), "via": "comment",
                  "brand": any(b["author"] == c.get("author") for b in data["promo_bias"]["brand_accounts"])}
                 for c in all_comments])
    return {"page": page, "thread": data}


def tally(target: str, terms: list[str]) -> dict:
    """For each name: threads and distinct users mentioning it, and the upvotes behind those
    mentions, among everything this window has seen. Brand accounts aren't counted as independent."""
    full_id = SITE.resolve(target)
    seen = (SITE.claims().get(full_id) or {}).get("seen", [])
    out = []
    for term in terms:
        t = term.lower()
        hits = [x for x in seen if re.search(rf"\b{re.escape(t)}", (x.get("title", "") + " " + x.get("text", "")).lower())]
        users = {h.get("author") for h in hits if h.get("author") and not h.get("brand")}
        threads = {(_post_id(h.get("url") or "") or h.get("url")) for h in hits}
        out.append({"term": term, "threads": len(threads), "independent_users": len(users),
                    "upvotes": sum(max(0, h.get("score") or 0) for h in hits if not h.get("brand")),
                    "examples": [{"via": h.get("via"), "author": h.get("author"), "score": h.get("score"),
                                  "url": h.get("url"), "text": (h.get("title") or h.get("text", ""))[:140]}
                                 for h in sorted(hits, key=lambda h: -(h.get("score") or 0))[:5]]})
    out.sort(key=lambda r: (-r["independent_users"], -r["upvotes"]))
    return {"seen_items": len(seen), "tally": out,
            "note": "Counts come only from what this window has seen (search titles, opened posts and their "
                    "comments); read more threads to widen the base. Try name variants as separate terms."}


_CONTENT_RECT_JS = """(() => { const vw = innerWidth, vh = innerHeight;
  const top = (document.querySelector('reddit-header-large, header')?.getBoundingClientRect().bottom) || 0;
  const nav = document.querySelector('#left-sidebar-container, flex-left-nav-container, #left-sidebar');
  const left = nav ? Math.max(0, nav.getBoundingClientRect().right) : 0;
  // Capture clips are in page coordinates, so offset by the scroll position.
  return {x: left + scrollX, y: top + scrollY, width: vw - left, height: vh - top}; })()"""


def shot(target: str, out: str | None = None, out_dir: str | None = None, full: bool = False) -> dict:
    """Screenshot of the page content visible in the window. The top bar (username, avatar) and the
    left navigation (joined communities) are left out."""
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    url = SITE.evaluate(full_id, "location.href") or ""
    slug = _post_id(url) or "page"
    name = f"{date.today().isoformat()}_reddit-{slug}_shot-{time.strftime('%H%M%S')}.{'png' if full else 'jpg'}"
    path = Path(out) if out else Path(out_dir or _SHOTS) / name
    SITE.screenshot(full_id, path, clip=SITE.evaluate(full_id, _CONTENT_RECT_JS), max_width=None if full else 1600)
    return {"path": str(path.expanduser()), "url": url,
            "check": "Look at the image before using it. The top bar and left navigation are excluded, but "
                     "usernames in the thread itself are visible."}


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
    if cmd == "tally":
        if len(args) < 2:
            raise Fail("usage: tally ID NAME [NAME ...]")
        return tally(args[0], args[1:])
    if cmd == "shot":
        out, out_dir, full = opt(args, "--out"), opt(args, "--out-dir"), bool(opt(args, "--full", False))
        if len(args) != 1:
            raise Fail("usage: shot ID [--full] [--out-dir DIR | --out PATH]")
        return shot(args[0], out, out_dir, full)
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
