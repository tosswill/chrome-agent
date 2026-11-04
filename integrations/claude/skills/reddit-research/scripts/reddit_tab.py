#!/usr/bin/env python3
"""Polite, read-only Reddit research in a shared, signed-in chrome-agent browser.

Several agents (Claude Code sessions, Claude Desktop via MCP) can research at
once. Each claims its own window in one long-lived browser instance, so they
share the Reddit login but never each other's tabs. Every page load goes
through one cross-process pacing lock, so combined traffic stays at a human
pace however many agents are working.

CLI (each command prints JSON):

    reddit_tab.py open [--wait [SECONDS]]           claim a window -> {"target": ID}
    reddit_tab.py search ID QUERY [--sub S] [--sort relevance|new|top|comments] [--time hour|day|week|month|year|all]
    reddit_tab.py search ID --url URL               any Reddit search/listing URL
    reddit_tab.py thread ID URL [--max N]           post + top comments (sorted by top)
    reddit_tab.py nav ID URL                        paced load, page state only
    reddit_tab.py eval ID JS|@file.js               run JS in your tab (escape hatch)
    reddit_tab.py close ID                          release the window
    reddit_tab.py list | gc                         show / clean up claims

The same functions back the Reddit tools of the chrome-agent MCP server, so the
CLI and the MCP tools behave identically.

Environment:
    REDDIT_TAB_INSTANCE   chrome-agent instance holding the signed-in session (default: reddit-01)
    REDDIT_TAB_MAX        concurrent windows (3)
    REDDIT_TAB_GAP        seconds between page loads, across all agents (4)
    REDDIT_TAB_IDLE       seconds before an idle claim is reclaimed (1800)
    REDDIT_TAB_MAX_LOADS  hard cap on page loads per claimed window (40)
    REDDIT_TAB_STATE      lock/claim directory (/tmp/chrome-agent-reddit)
Stdlib only; needs `chrome-agent` on PATH.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl

HERE = Path(__file__).resolve().parent
INSTANCE = os.environ.get("REDDIT_TAB_INSTANCE", "reddit-01")
MAX_CLAIMS = int(os.environ.get("REDDIT_TAB_MAX", "3"))
GAP = float(os.environ.get("REDDIT_TAB_GAP", "4"))
IDLE = float(os.environ.get("REDDIT_TAB_IDLE", "1800"))
MAX_LOADS = int(os.environ.get("REDDIT_TAB_MAX_LOADS", "40"))
STATE = Path(os.environ.get("REDDIT_TAB_STATE", "/tmp/chrome-agent-reddit"))
CLAIMS = STATE / "claims"
BIN = shutil.which("chrome-agent") or str(Path.home() / ".local/bin/chrome-agent")

READY_JS = (
    "document.readyState === 'complete' && "
    "!!document.querySelector(\"shreddit-post, shreddit-comment, a[href*='/comments/']\")"
)
SORTS = ("relevance", "new", "top", "comments", "hot")
TIMES = ("hour", "day", "week", "month", "year", "all")


class Fail(Exception):
    """An error the agent should read and act on (usually: stop and ask the user)."""


def _js(name: str) -> str:
    return (HERE / name).read_text()


# -------------------------------------------------------------------------
# chrome-agent plumbing
# -------------------------------------------------------------------------

def _ca(*args: str, timeout: float = 60) -> str:
    p = subprocess.run([BIN, *args], capture_output=True, text=True,
                       timeout=timeout, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise Fail((p.stderr or p.stdout).strip())
    return p.stdout


def evaluate(target: str, expr: str, timeout: float = 30):
    out = _ca(INSTANCE, "--target-id", target, "Runtime.evaluate",
              json.dumps({"expression": expr, "returnByValue": True, "awaitPromise": True}),
              timeout=timeout)
    res = json.loads(out)
    if "exceptionDetails" in res:
        raise Fail(json.dumps(res["exceptionDetails"])[:500])
    return res.get("result", {}).get("value")


def _status() -> dict | None:
    try:
        data = json.loads(_ca("status", INSTANCE))
    except Fail:
        return None
    inst = data[0] if isinstance(data, list) else data
    return inst if inst.get("alive") else None


def _targets(restore: bool = False) -> list[dict]:
    inst = _status()
    if inst is None and restore:
        try:
            _ca("restore", INSTANCE, "--any-port", timeout=120)
        except Fail as e:
            raise Fail(f"{INSTANCE} is not running and could not be restored ({e}). "
                       "Stop and ask the user.")
        inst = _status()
    if inst is None:
        raise Fail(f"{INSTANCE} is not running. Stop and ask the user "
                   f"(it can be restored with: chrome-agent restore {INSTANCE} --any-port).")
    return inst.get("targets", [])


class _locked:
    """Exclusive cross-process lock on a file under STATE."""

    def __init__(self, name: str):
        STATE.mkdir(parents=True, exist_ok=True)
        self.f = open(STATE / name, "a+")

    def __enter__(self):
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self.f

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# -------------------------------------------------------------------------
# Claims
# -------------------------------------------------------------------------

def _claims() -> dict[str, dict]:
    CLAIMS.mkdir(parents=True, exist_ok=True)
    out = {}
    for f in CLAIMS.glob("*.json"):
        try:
            out[f.stem] = json.loads(f.read_text())
        except (OSError, ValueError):
            pass
    return out


def _write_claim(full_id: str, claim: dict) -> None:
    CLAIMS.mkdir(parents=True, exist_ok=True)
    (CLAIMS / f"{full_id}.json").write_text(json.dumps(claim))


def _resolve(target: str) -> str:
    hits = [k for k in _claims() if k.upper().startswith(target.upper())]
    if len(hits) != 1:
        raise Fail(f"no window of yours matches {target!r}. Claim one with open "
                   "(or run list to see active claims).")
    return hits[0]


def _gc_locked(live_ids: set[str]) -> list[str]:
    """Drop claims whose tab is gone; close and drop claims idle past IDLE."""
    dropped, now = [], time.time()
    for full_id, c in _claims().items():
        gone = full_id not in live_ids
        idle = now - c.get("last_used", 0) > IDLE
        if gone or idle:
            if idle and not gone:
                try:
                    _ca("stop", INSTANCE, "--target-id", full_id)
                except Fail:
                    pass
            (CLAIMS / f"{full_id}.json").unlink(missing_ok=True)
            dropped.append(full_id[:8])
    return dropped


def _count_load(full_id: str) -> int:
    claims = _claims()
    c = claims.get(full_id)
    if c is None:
        raise Fail(f"window {full_id[:8]} is no longer claimed (closed or reclaimed after idling). "
                   "Claim a new one with open.")
    if c.get("loads", 0) >= MAX_LOADS:
        raise Fail(f"page-load cap reached ({MAX_LOADS}) for this window. "
                   "Stop and ask the user before loading more.")
    c["loads"] = c.get("loads", 0) + 1
    c["last_used"] = time.time()
    _write_claim(full_id, c)
    return c["loads"]


def _pace() -> None:
    """Block until GAP seconds have passed since the last load by any agent."""
    with _locked("pace.lock") as f:
        f.seek(0)
        try:
            last = float(f.read().strip() or 0)
        except ValueError:
            last = 0.0
        wait = last + GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))
        f.flush()


def _wait_ready(target: str, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if evaluate(target, READY_JS, timeout=10):
                time.sleep(2.5)  # let Reddit's web components finish rendering
                return
        except Fail:
            pass
        time.sleep(1)


# -------------------------------------------------------------------------
# Public operations (used by the CLI and the MCP server)
# -------------------------------------------------------------------------

def open_window(wait_seconds: float = 0) -> dict:
    """Claim a new window in the shared browser. Restores the instance from its
    latest snapshot if it is not running. With wait_seconds, waits for a free slot."""
    deadline = time.time() + wait_seconds
    while True:
        with _locked("claims.lock"):
            tabs = _targets(restore=True)
            _gc_locked({t["full_id"] for t in tabs})
            busy = len(_claims())
            if busy < MAX_CLAIMS:
                if not tabs:
                    raise Fail(f"{INSTANCE} has no open tab to issue commands through.")
                res = json.loads(_ca(INSTANCE, "--target-id", tabs[0]["full_id"], "Target.createTarget",
                                     json.dumps({"url": "about:blank", "newWindow": True, "background": True})))
                now = time.time()
                _write_claim(res["targetId"], {"created": now, "last_used": now, "loads": 0})
                return {"target": res["targetId"], "slots_in_use": busy + 1, "max_slots": MAX_CLAIMS}
        if time.time() >= deadline:
            raise Fail(f"all {MAX_CLAIMS} research windows are in use. Wait and retry "
                       "(open --wait), or ask the user.")
        time.sleep(min(15, max(1, deadline - time.time())))


def nav(target: str, url: str) -> dict:
    """Paced, counted page load. Returns {url, title, loggedIn, blocked, loads, stop?}."""
    full_id = _resolve(target)
    if not urlparse(url).netloc.endswith("reddit.com"):
        raise Fail("only reddit.com pages may be loaded with this tool.")
    if any(seg in urlparse(url).path for seg in (".json", "/api/")):
        raise Fail("Reddit API/JSON endpoints are off-limits; load the normal page instead.")
    loads = _count_load(full_id)
    _pace()
    _ca(INSTANCE, "--target-id", full_id, "Page.navigate", json.dumps({"url": url}))
    _wait_ready(full_id)
    state = evaluate(full_id, _js("page_state.js")) or {}
    state["loads"] = loads
    if state.get("blocked"):
        state["stop"] = "Reddit is showing a block or CAPTCHA page. Stop and ask the user; do not retry."
    elif not state.get("loggedIn"):
        state["stop"] = ("Reddit is not signed in. Ask the user to log in manually in the browser window, "
                         f"then save it: chrome-agent save {INSTANCE} --overwrite")
    return state


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
    page = nav(target, url)
    if "stop" in page:
        return {"page": page, "posts": []}
    posts = evaluate(_resolve(target), _js("search_results.js")) or []
    return {"page": page, "posts": posts}


def thread(target: str, url: str, max_comments: int = 15) -> dict:
    """Load a thread sorted by top and return the post plus its top comments
    (top-level, plus the OP's own replies so AMA answers come through)."""
    if not url.startswith("http"):
        url = "https://www.reddit.com/" + url.lstrip("/")
    parts = urlparse(url)
    q = dict(parse_qsl(parts.query))
    q.setdefault("sort", "top")
    url = urlunparse(parts._replace(query=urlencode(q)))
    page = nav(target, url)
    if "stop" in page:
        return {"page": page, "thread": None}
    data = evaluate(_resolve(target), _js("thread.js"))
    if data is None:
        return {"page": page, "thread": None, "note": "no post found on this page (removed, or not a thread URL)"}
    top = [c for c in data["comments"] if c["depth"] == 0][:max_comments]
    keep = {id(c) for c in top}
    # OP replies that come right after a kept top-level comment.
    out, include = [], False
    for c in data["comments"]:
        if c["depth"] == 0:
            include = id(c) in keep
            if include:
                out.append(c)
        elif include:
            out.append(c)
    data["comments"] = out
    return {"page": page, "thread": data}


def run_js(target: str, js: str):
    """Evaluate JS in your own window (escape hatch for pages the helpers don't cover)."""
    full_id = _resolve(target)
    if js.startswith("@"):
        js = Path(js[1:]).expanduser().read_text()
    return evaluate(full_id, js)


def close(target: str) -> dict:
    full_id = _resolve(target)
    with _locked("claims.lock"):
        try:
            _ca("stop", INSTANCE, "--target-id", full_id)
        except Fail:
            pass
        (CLAIMS / f"{full_id}.json").unlink(missing_ok=True)
    return {"closed": full_id}


def list_claims() -> list[dict]:
    now = time.time()
    return [{"target": k, "age_s": int(now - c["created"]), "idle_s": int(now - c["last_used"]),
             "loads": c.get("loads", 0)} for k, c in _claims().items()]


def gc() -> dict:
    with _locked("claims.lock"):
        return {"released": _gc_locked({t["full_id"] for t in _targets()})}


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def _opt(args: list[str], name: str, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            val = args[i + 1]
            del args[i:i + 2]
            return val
        del args[i]
        return True
    return default


def _cli(argv: list[str]):
    cmd, args = argv[0], argv[1:]
    if cmd == "open":
        w = _opt(args, "--wait", 0)
        return open_window(600 if w is True else float(w))
    if cmd == "search":
        url = _opt(args, "--url")
        sub, sort, t = _opt(args, "--sub"), _opt(args, "--sort", "relevance"), _opt(args, "--time", "year")
        if not args or (len(args) < 2 and not url):
            raise Fail("usage: search ID QUERY [--sub S] [--sort S] [--time T] | search ID --url URL")
        return search(args[0], " ".join(args[1:]) or None, url=url, subreddit=sub, sort=sort, time_filter=t)
    if cmd == "thread":
        n = int(_opt(args, "--max", 15))
        if len(args) != 2:
            raise Fail("usage: thread ID URL [--max N]")
        return thread(args[0], args[1], n)
    if cmd == "nav" and len(args) == 2:
        return nav(*args)
    if cmd == "eval" and len(args) == 2:
        return run_js(*args)
    if cmd == "close" and len(args) == 1:
        return close(args[0])
    if cmd == "list":
        return list_claims()
    if cmd == "gc":
        return gc()
    raise Fail(__doc__.strip())


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        print(json.dumps(_cli(sys.argv[1:]), indent=2, ensure_ascii=False))
        return 0
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
