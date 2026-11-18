#!/usr/bin/env python3
"""Polite, read-only X (Twitter) research in a shared, signed-in chrome-agent browser (x).

CLI (each command prints JSON):

    x_tab.py open [--wait [SECONDS]]      claim a window -> {"target": ID} (`stop` when signed out)
    x_tab.py search ID QUERY [--latest] [--max N]   typed into the search box (X search operators work:
                       since:2026-01-01 min_faves:20 lang:en -filter:replies from:user); Top tab by default,
                       --latest for newest first; scrolls until N posts (default 40) (1 load)
    x_tab.py post ID URL [--max N]        a post with its thread and replies (scrolls until N replies, default 40);
                       clicked from the current results when listed there (1 load)
    x_tab.py profile ID HANDLE [--max N]  profile header (bio, followers, joined, verified) and recent posts (1 load)
    x_tab.py nav ID URL                   paced page load on x.com
    x_tab.py shot ID [--out PATH]         screenshot of the window
    x_tab.py eval ID JS   read something off the page (disabled unless RESEARCH_ALLOW_EVAL=1)
    x_tab.py close ID | list | gc         release window / show / clean up claims

Environment (X_TAB_*): INSTANCE (x), MAX (3 windows), GAP (5 s between loads),
MAX_LOADS (40), STATE (/tmp/chrome-agent-x).
"""
import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from research_core import BLOCK_CHECK_JS, Fail, Site, common_cli, opt, pause, run_cli, timed  # noqa: E402

# Where the tab is, whether X shows the signed-in account, and whether a wall is up. X's own walls: the
# login flow, a locked/limited account page, rate limiting and "Something went wrong" error pages.
PAGE_STATE_JS = r"""(() => {
  const acct = document.querySelector("[data-testid=SideNav_AccountSwitcher_Button]");
  const signIn = document.querySelector("[data-testid=loginButton], a[href='/login'], a[href='/i/flow/login']");
  const loggedIn = acct ? true : (signIn ? false : null);
  const base = """ + BLOCK_CHECK_JS + r""";
  const body = (document.body?.innerText || "").slice(0, 3000);
  let wall = null;
  if (/^\/(account\/access|i\/flow\/(login|signup))/.test(location.pathname)) wall = "X sent the window to " + location.pathname;
  else if (/rate limit exceeded/i.test(body)) wall = "X rate limit";
  else if (/your account is (locked|suspended|temporarily restricted)/i.test(body)) wall = "account restricted";
  if (wall && !base.blocked) Object.assign(base, {blocked: true, blocked_by: wall});
  const error = /something went wrong\. try reloading/i.test(body) && !document.querySelector("article[data-testid=tweet]");
  return Object.assign({url: location.href, title: document.title, loggedIn, error}, base);
})()"""


def _cookie_session(site: Site) -> bool | None:
    """Signed in? X's auth_token cookie (httpOnly; read over CDP, no page load)."""
    try:
        tabs = site.targets()
        if not tabs:
            return None
        res = site.cdp(tabs[0]["id"], "Network.getCookies", {"urls": ["https://x.com/"]})
    except (Fail, KeyError, ValueError):
        return None
    now = time.time()
    return any(c.get("name") == "auth_token" and (c.get("expires", -1) <= 0 or c["expires"] > now)
               for c in res.get("cookies", []))


def _stop_reason(state: dict, site: Site) -> str | None:
    if state.get("blocked"):
        return (f"X (Twitter) is showing a wall ({state.get('blocked_by') or 'bot check'}). Stop and ask the user; "
                "do not retry.")
    if state.get("loggedIn") is False and _cookie_session(site) is False:
        return ("X (Twitter) is not signed in. Ask the user to sign in in the browser window, then save it: "
                f"chrome-agent save {site.instance} --overwrite")
    return None


SITE = Site(
    prefix="X_TAB",
    default_instance="x",
    hosts=("x.com",),
    forbidden=("/api/", "/graphql", ".json", "/i/flow/", "/compose", "/settings", "/messages", "/i/grok"),
    ready_js="document.readyState === 'complete' && !!document.body",
    page_state_js=PAGE_STATE_JS,
    stop_reason=_stop_reason,
    default_gap=5.0,
)
SITE.session_check = _cookie_session
SITE.min_width = 1200
INSTANCE = SITE.instance

_POSTS_READY = "document.querySelectorAll('article[data-testid=tweet]').length > 0"
_EMPTY = "/no results for|try searching for something else|hasn.t posted|this account doesn.t exist|this post is unavailable|hmm\\.\\.\\.this page doesn.t exist/i"
_SEARCH_BOX = "input[data-testid=SearchBox_Search_Input]"


def _js(name: str) -> str:
    return (HERE / "js" / name).read_text()


def _status_id(url: str) -> str:
    m = re.search(r"/status(?:es)?/(\d+)", url or "")
    if not m:
        raise Fail(f"not an X post URL: {url!r} (expected https://x.com/<user>/status/<id>)")
    return m.group(1)


def _handle(h: str) -> str:
    h = (h or "").strip()
    m = re.match(r"(?:https?://(?:www\.)?(?:x|twitter)\.com/)?@?(\w{1,15})/?$", h)
    if not m:
        raise Fail(f"not an X handle: {h!r}")
    return m.group(1)


_HYPHENATED = re.compile(r'(?<![\w:"-])([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![\w"])')


def _quote_hyphenated(query: str) -> tuple[str, list[str]]:
    """X reads an unquoted hyphen as NOT ("MS-01" searches MS without 01). Quote hyphenated words outside
    quotes and operators (since:2026-01-01 and -filter:replies are left alone)."""
    parts, quoted = query.split('"'), []
    for i in range(0, len(parts), 2):  # even parts are outside quotes
        def q(m):
            quoted.append(m.group(1))
            return f'"{m.group(1)}"'
        parts[i] = _HYPHENATED.sub(q, parts[i])
    return '"'.join(parts), quoted


_GENERIC = set("""mini homelab home server servers proxmox review reviews best with from that this what price prices
issue issues problem problems reliability power idle died dead failed failing crash crashing memory ddr4 ddr5 cluster
setup build worth vs lang since until filter""".split())


def _query_terms(query: str) -> list[str]:
    """Distinctive words in the query (brands, models) for spotting accounts named after them."""
    words = re.findall(r'(?<![\w:-])[A-Za-z][A-Za-z0-9]{3,}', re.sub(r'\S+:\S+', ' ', query))
    return sorted({w.lower() for w in words} - _GENERIC)


def _flag_name_matches(posts: list[dict], terms: list[str]) -> None:
    for p in posts:
        hay = ((p.get("handle") or "") + " " + (p.get("name") or "")).lower()
        hit = next((t for t in terms if t in hay), None)
        if hit:
            p["name_matches_query"] = hit


def _merge(seen: dict, posts: list[dict]) -> int:
    new = 0
    for p in posts:
        key = p.get("url") or ("promoted:" + (p.get("handle") or "") + (p.get("text") or "")[:60])
        if key not in seen:
            seen[key] = p
            new += 1
    return new


def _wait_posts(full_id: str, timeout: float = 15) -> str:
    """Wait for posts (or X's own empty / error message). Returns 'posts', 'empty' or 'timeout'."""
    ok = SITE.wait_for(full_id, f"{_POSTS_READY} || {_EMPTY}.test(document.body.innerText) || "
                                "/something went wrong/i.test(document.body.innerText)", timeout=timeout, settle=1.0)
    if not ok:
        return "timeout"
    return "posts" if SITE.evaluate(full_id, _POSTS_READY) else "empty"


def _collect(full_id: str, limit: int, stop_js: str | None = None) -> tuple[list[dict], int, str]:
    """Read the rendered posts, wheel down like a reader, and read again until `limit` posts, two scrolls with
    nothing new, or `stop_js` is true (e.g. the replies ran out). X unloads posts that scroll away, so posts
    are merged by URL. Returns (posts in page order, scrolls, why it stopped: "limit", "no_more" (two scrolls
    brought nothing new: the results ran out or X stopped loading), "end_marker" or "scroll_cap")."""
    seen: dict = {}
    stale = scrolls = 0
    ended = None
    _merge(seen, SITE.evaluate(full_id, _js("posts.js")) or [])
    while len(seen) < limit and stale < 2 and scrolls < 40:
        SITE.read_pause(full_id, random.uniform(1.0, 2.2))
        for _ in range(random.randint(1, 3)):
            SITE.wheel(full_id, random.uniform(350, 700))
            pause(random.uniform(0.25, 0.6))
        scrolls += 1
        with timed("fetch_wait"):
            pause(random.uniform(1.0, 1.8))  # let X fetch the next page of posts
        if random.random() < 0.12:
            SITE.wheel(full_id, -random.uniform(120, 300))  # glance back up
            pause(0.6)
        stale = 0 if _merge(seen, SITE.evaluate(full_id, _js("posts.js")) or []) else stale + 1
        if stop_js and SITE.evaluate(full_id, stop_js):
            ended = "end_marker"
            break
    if ended is None:
        ended = "limit" if len(seen) >= limit else ("no_more" if stale >= 2 else "scroll_cap")
    return list(seen.values())[:limit], scrolls, ended


def _spa_load(full_id: str, before: str, act) -> dict:
    """A load inside X's single-page app (typed search, clicked post): counted and paced like a page load."""
    loads = SITE._count_load(full_id)
    with timed("pace_wait"):
        SITE._pace()
    act()
    with timed("page_load"):
        SITE.wait_for(full_id, f"location.href !== {json.dumps(before)}", timeout=20, settle=0.5)
    page = SITE.evaluate(full_id, PAGE_STATE_JS) or {}
    page["loads"] = loads
    reason = _stop_reason(page, SITE)
    if reason:
        page["stop"] = reason
        SITE.record_stop(full_id, page)
    else:
        SITE.log_page(page)
    return page


def _click_tab(full_id: str, label: str) -> bool:
    ok = SITE.evaluate(full_id, f"""(() => {{ document.querySelectorAll('[data-x-tab]').forEach(e => e.removeAttribute('data-x-tab'));
      const t = [...document.querySelectorAll('[role=tab]')].find(e => e.innerText.trim() === {json.dumps(label)});
      if (!t) return false; if (t.getAttribute('aria-selected') === 'true') return 'already';
      t.setAttribute('data-x-tab', '1'); return true; }})()""")
    if ok == "already":
        return True
    if not ok or not SITE.click_on(full_id, "[data-x-tab='1']", look=random.uniform(0.4, 0.9)):
        return False
    pause(random.uniform(1.5, 2.5))
    return True


def search(target: str, query: str, latest: bool = False, limit: int = 40) -> dict:
    """Search like a person: typed into the search box on an x.com page, else by URL. Then the Latest tab when
    asked, and scroll to collect `limit` posts. One load."""
    query, quoted = _quote_hyphenated(query)
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)
    here = SITE.evaluate(full_id, "location.hostname") or ""
    box = here.endswith("x.com") and SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_SEARCH_BOX)})")
    if box:
        before = SITE.evaluate(full_id, "location.href")

        def typed():
            if not SITE.click_on(full_id, _SEARCH_BOX, look=0.5):
                raise Fail("the search box isn't visible")
            pause(0.4)
            SITE.evaluate(full_id, f"(() => {{ const i = document.querySelector({json.dumps(_SEARCH_BOX)}); if (i && i.value) i.select(); }})()")
            SITE.type_text(full_id, query)
            pause(random.uniform(0.6, 1.1))
            SITE.key(full_id, "Enter")

        page = _spa_load(full_id, before, typed)
        page["via"] = "typed"
    else:
        page = SITE.nav(target, f"https://x.com/search?q={quote(query)}&src=typed_query" + ("&f=live" if latest else ""))
        page["via"] = "url"
    if "stop" in page:
        return {"page": page, "posts": []}
    # A search typed on a results page keeps that page's tab (Latest stays Latest): set the tab explicitly.
    want = "Latest" if latest else "Top"
    if not _click_tab(full_id, want):
        page["warning"] = f"couldn't open the {want} tab"
    page["url"] = SITE.evaluate(full_id, "location.href")
    state = _wait_posts(full_id)
    if state != "posts":
        return {"page": page, "posts": [], "count": 0,
                "note": "no posts" if state == "empty" else "posts didn't load (screenshot it and tell the user)"}
    SITE.read_pause(full_id, random.uniform(1.5, 3))
    posts, scrolls, ended = _collect(full_id, limit)
    tab = SITE.evaluate(full_id, "[...document.querySelectorAll('[role=tab]')].find(t => t.getAttribute('aria-selected') === 'true')?.innerText.trim().toLowerCase()") or None
    _flag_name_matches(posts, _query_terms(query))
    _remember(full_id, posts)
    res = {"page": page, "tab": tab, "query": query, "posts": posts, "count": len(posts), "scrolls": scrolls,
           "ended": ended, "summary": _summary(posts),
           "note": ("Top ranks by engagement (and favors Premium accounts); Latest is newest first. `promoted` posts "
                    "are ads. `name_matches_query` marks accounts named after a search term (often the brand). "
                    "`ended`: no_more means X showed nothing further after two scrolls.")}
    if quoted:
        res["quoted_terms"] = quoted
    if tab and tab != want.lower():
        res["warning"] = f"results are on the {tab} tab, not {want}"
    return res


def _summary(posts: list[dict]) -> dict:
    dates = sorted(p["date"] for p in posts if p.get("date"))
    return {"posts": len(posts), "authors": len({p.get("handle") for p in posts if p.get("handle")}),
            "verified_authors": len({p.get("handle") for p in posts if p.get("verified")}),
            "promoted": sum(1 for p in posts if p.get("promoted")),
            "with_community_note": sum(1 for p in posts if p.get("community_note")),
            "name_matches_query": len({p.get("handle") for p in posts if p.get("name_matches_query")}),
            "grok": sum(1 for p in posts if p.get("grok")),
            "oldest": dates[0] if dates else None, "newest": dates[-1] if dates else None}


def _remember(full_id: str, posts: list[dict]) -> None:
    c = SITE.claims().get(full_id)
    if c is None:
        return
    c["last_posts"] = [p["url"] for p in posts if p.get("url")][:200]
    SITE._write_claim(full_id, c)


def post(target: str, url: str, limit: int = 40) -> dict:
    """A post page: the post (with the thread above it) and its replies. Clicked from the current page when
    the post is visible there; else loaded by URL. One load."""
    sid = _status_id(url)
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)
    here = SITE.evaluate(full_id, "location.href") or ""
    if f"/status/{sid}" in here and not re.search(r"/status/\d+/\w", here):
        page = {"url": here, "opened": "already open", "loads": None}
    else:
        sel = f'article[data-testid=tweet] a[href$="/status/{sid}"]:has(time)'
        page = None
        if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(sel)})"):
            SITE.scroll_into_view(full_id, sel)
            page = _spa_load(full_id, here, lambda: SITE.click_on(full_id, sel, look=random.uniform(0.5, 1.2)))
            if f"/status/{sid}" not in (page.get("url") or "") and "stop" not in page:
                SITE.uncount_load(full_id)
                page = None
            elif page:
                page["opened"] = "clicked"
        if page is None:
            page = SITE.nav(target, f"https://x.com/i/status/{sid}")
            page["opened"] = "url"
    if "stop" in page:
        return {"page": page, "post": None, "replies": []}
    if _wait_posts(full_id) != "posts":
        return {"page": page, "post": None, "replies": [], "note": "the post didn't load (deleted, protected or an error)"}
    SITE.read_pause(full_id, random.uniform(2, 3.5))
    end_js = ("[...document.querySelectorAll('button, [role=button]')].some(b => /^show (probable spam|additional replies"
              "|more replies)/i.test(b.innerText.trim()))")
    posts, scrolls, ended = _collect(full_id, limit + 10, stop_js=end_js)
    main = next((p for p in posts if (p.get("url") or "").endswith(f"/status/{sid}")), None)
    idx = posts.index(main) if main else -1
    hidden = SITE.evaluate(full_id, "[...document.querySelectorAll('button, [role=button]')].map(b => b.innerText.trim())"
                           ".filter(t => /^show (probable spam|additional replies|more replies)/i.test(t))") or []
    SITE.scroll_to_top(full_id)
    thread_below, replies = _split_thread(main, posts[idx + 1:] if idx >= 0 else posts)
    replies = replies[:limit]
    me = (main or {}).get("handle")
    return {"page": page, "thread_above": posts[:idx] if idx > 0 else [], "post": main, "thread_below": thread_below,
            "replies": replies, "replies_read": len(replies), "self_replies": sum(1 for r in replies if me and r.get("handle") == me),
            "scrolls": scrolls, "ended": ended, "hidden_reply_sections": hidden, "summary": _summary(replies),
            "note": ("Replies are ranked by X (Premium accounts are boosted); `reply_count` on the post is the total. "
                     "Low-quality or 'probable spam' replies sit behind `hidden_reply_sections` and aren't read.")}


def _split_thread(main: dict | None, after: list[dict]) -> tuple[list[dict], list[dict]]:
    """The author's own continuation right under the post (a thread) vs replies from others (and later
    author replies, counted as `self_replies`)."""
    me = (main or {}).get("handle")
    i = 0
    while me and i < len(after) and after[i].get("handle") == me:
        i += 1
    return after[:i], after[i:]


def profile(target: str, handle: str, limit: int = 20) -> dict:
    """A profile: header (bio, followers, join date, verification) and its recent posts. One load."""
    h = _handle(handle)
    full_id = SITE.resolve(target)
    page = SITE.nav(target, f"https://x.com/{h}")
    if "stop" in page:
        return {"page": page, "profile": None, "posts": []}
    SITE.wait_for(full_id, "!!document.querySelector('[data-testid=UserName]') || " + _EMPTY + ".test(document.body.innerText)",
                  timeout=15, settle=1.0)
    info = SITE.evaluate(full_id, _js("profile.js"))
    if not info:
        return {"page": page, "profile": None, "posts": [], "note": "no such account, or it's suspended"}
    SITE.read_pause(full_id, random.uniform(1.5, 3))
    posts = []
    if _wait_posts(full_id, timeout=10) == "posts":
        posts, _, _ = _collect(full_id, limit)
    SITE.scroll_to_top(full_id)
    own = [p for p in posts if (p.get("handle") or "").lower() == f"@{h}".lower() and not p.get("reposted_by")]
    return {"page": page, "profile": info, "posts": posts,
            "summary": {**_summary(posts), "own_posts": len(own), "reposts": sum(1 for p in posts if p.get("reposted_by")),
                        "pinned": sum(1 for p in posts if (p.get("reposted_by") or "").lower() == "pinned")},
            "note": "A pinned post comes first regardless of date (`reposted_by`: Pinned)."}


def shot(target: str, out: str | None = None) -> dict:
    full_id = SITE.resolve(target)
    path = Path(out or SITE.state / "shots" / f"{int(time.time())}.jpg")
    SITE.screenshot(full_id, path, max_width=1600)
    return {"path": str(path), "note": "Look at the image before using it."}


def _dispatch(cmd: str, args: list[str]):
    if cmd == "search":
        latest = "--latest" in args
        args = [a for a in args if a != "--latest"]
        mx = int(opt(args, "--max", 40))
        if len(args) < 2:
            raise Fail("usage: search ID QUERY [--latest] [--max N]")
        return search(args[0], " ".join(args[1:]), latest, mx)
    if cmd == "post":
        mx = int(opt(args, "--max", 40))
        if len(args) != 2:
            raise Fail("usage: post ID POST_URL [--max N]")
        return post(args[0], args[1], mx)
    if cmd == "profile":
        mx = int(opt(args, "--max", 20))
        if len(args) != 2:
            raise Fail("usage: profile ID HANDLE [--max N]")
        return profile(args[0], args[1], mx)
    if cmd == "shot" and args:
        out = opt(args, "--out")
        return shot(args[0], out if isinstance(out, str) else None)
    res = common_cli(SITE, cmd, args)
    if res is NotImplemented:
        raise Fail(f"unknown command: {cmd}")
    return res


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
