#!/usr/bin/env python3
"""Polite, read-only YouTube research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
login but never each other's tabs; all page loads share one pacing lock.
Every action is one a person would take: real mouse clicks and wheel scrolling,
the player's own mute button and progress bar. Scripts only read the page.

CLI (each command prints JSON):

    youtube_tab.py open [--wait [SECONDS]]                 claim a window -> {"target": ID}
    youtube_tab.py search ID QUERY [--sort relevance|views] [--upload hour|today|week|month|year] [--shorts]
    youtube_tab.py video ID URL                            metadata, disclosure signals, chapters, description
    youtube_tab.py transcript ID [--grep TERM ...] [--context N]   segments (all, or matching terms +/- N)
    youtube_tab.py comments ID [--max N]                   top comments of the loaded video (default 20)
    youtube_tab.py frame ID AT [--out PATH]                jump to AT (e.g. 6:17), capture the player as PNG
    youtube_tab.py shot ID [--out PATH]                    screenshot of the visible page
    youtube_tab.py channel ID URL|@handle                  channel header + recent uploads
    youtube_tab.py nav ID URL | eval ID JS|@file.js        escape hatches (paced load / run JS)
    youtube_tab.py close ID | list | gc                    release window / show / clean up claims

transcript, comments, frame and shot work on the video already loaded by `video`
and do not count as page loads. The same functions back the youtube_* MCP tools.

Environment (YOUTUBE_TAB_*): INSTANCE (youtube-research-01), MAX (3 windows),
GAP (6 s between loads, all agents), IDLE (1800 s), MAX_LOADS (60 per window),
STATE (/tmp/chrome-agent-youtube), SHOTS (STATE/shots). Needs `chrome-agent` on PATH.
"""
import base64
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from research_core import Fail, Site, common_cli, opt, run_cli  # noqa: E402

SORTS = {"relevance": None, "views": 3}
UPLOADS = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
MIN_PLAYER_WIDTH = 640  # CSS px; smaller frames are hard to read


def _js(name: str) -> str:
    return (HERE / name).read_text()


def _call(name: str, opts: dict) -> str:
    """A JS file that evaluates to a function, applied to opts."""
    return f"({_js(name).strip()})({json.dumps(opts)})"


def _stop_reason(state: dict, site: Site) -> str | None:
    if state.get("blocked"):
        return ("YouTube is showing a bot check, CAPTCHA or 'unusual traffic' page. "
                "Stop and ask the user; do not retry.")
    if state.get("gate"):
        return "YouTube is showing a consent or age gate. Stop and ask the user."
    if not state.get("loggedIn"):
        return ("YouTube is not signed in. Ask the user to sign in manually in the browser window, "
                f"then save it: chrome-agent save {site.instance} --overwrite")
    return None


SITE = Site(
    prefix="YOUTUBE_TAB",
    default_instance="youtube-research-01",
    hosts=("youtube.com",),
    forbidden=("/youtubei/", "/api/timedtext", "timedtext?", "/get_video_info", "googlevideo.com"),
    ready_js=("document.readyState === 'complete' && !!document.querySelector('ytd-app') && "
              "!!document.querySelector('ytd-video-renderer, ytd-watch-metadata #title h1, "
              "ytd-rich-item-renderer, ytd-item-section-renderer, #avatar-btn, a[href*=\"ServiceLogin\"]')"),
    page_state_js=_js("page_state.js"),
    stop_reason=_stop_reason,
    default_gap=6.0,
    default_max_loads=60,
)
INSTANCE = SITE.instance
SHOTS = Path(os.environ.get("YOUTUBE_TAB_SHOTS", SITE.state / "shots"))


# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------

def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def search_url(query: str, sort: str = "relevance", upload: str | None = None) -> str:
    if sort not in SORTS:
        raise Fail(f"sort must be one of {tuple(SORTS)} (YouTube no longer honors sorting by date; "
                   "use --upload to restrict to recent videos)")
    if upload is not None and upload not in UPLOADS:
        raise Fail(f"upload must be one of {tuple(UPLOADS)}")
    # The `sp` parameter is a base64 protobuf: field 1 = sort, field 2 = filters
    # {field 1 = upload date, field 2 = type (1 = video)}.
    filters = (b"\x08" + _varint(UPLOADS[upload]) if upload else b"") + b"\x10\x01"
    sp = (b"\x08" + _varint(SORTS[sort]) if SORTS[sort] else b"") + b"\x12" + _varint(len(filters)) + filters
    return ("https://www.youtube.com/results?"
            + urlencode({"search_query": query, "sp": base64.b64encode(sp).decode()}, quote_via=quote))


def _seconds(at) -> float:
    if isinstance(at, (int, float)):
        return float(at)
    at = str(at).strip()
    if re.fullmatch(r"\d+(\.\d+)?", at):
        return float(at)
    if not re.fullmatch(r"\d{1,2}(:\d{2}){1,2}", at):
        raise Fail(f"timestamp must look like 6:17, 1:02:03 or 377, not {at!r}")
    s = 0
    for part in at.split(":"):
        s = s * 60 + int(part)
    return float(s)


def _mmss(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _video_id(full_id: str) -> str | None:
    url = SITE.evaluate(full_id, "location.href") or ""
    return parse_qs(urlparse(url).query).get("v", [None])[0]


def _require_watch(full_id: str) -> str:
    vid = _video_id(full_id)
    if not vid:
        raise Fail("no video is loaded in this window; load one with `video` first.")
    return vid


_PLAYER_STATE_JS = """(() => { const p = document.querySelector('#movie_player'), v = document.querySelector('video');
  const ad = !!p && (p.classList.contains('ad-showing') || p.classList.contains('ad-interrupting'));
  const sk = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
  let skip = null;
  if (sk) { const s = getComputedStyle(sk), r = sk.getBoundingClientRect();
    if (s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0) skip = [r.x + r.width / 2, r.y + r.height / 2]; }
  const pr = p ? p.getBoundingClientRect() : null;
  return { ad, skip, t: v ? v.currentTime : null, duration: v ? v.duration : null, muted: v ? v.muted : null,
           player: pr ? {x: pr.x, y: pr.y, width: pr.width, height: pr.height} : null }; })()"""


def _state(full_id: str) -> dict:
    return SITE.evaluate(full_id, _PLAYER_STATE_JS) or {}


def _hover_player(full_id: str, st: dict) -> None:
    """Move the pointer over the player so its controls show."""
    pl = st.get("player")
    if pl:
        SITE.move(full_id, pl["x"] + pl["width"] * 0.55, pl["y"] + pl["height"] * 0.5)
        time.sleep(0.4)


def _ensure_muted(full_id: str) -> bool:
    """If the video has sound, click the player's mute button (YouTube remembers it)."""
    st = _state(full_id)
    if st.get("muted") is None or st.get("muted"):
        return bool(st.get("muted"))
    _hover_player(full_id, st)
    r = SITE.rect(full_id, ".ytp-mute-button")
    if r:
        SITE.click(full_id, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
        time.sleep(0.4)
    return bool(_state(full_id).get("muted"))


def _clear_ads(full_id: str, timeout: float = 30) -> dict:
    """Wait out any ad, clicking Skip when YouTube offers it, then make sure we're muted."""
    seen = skipped = False
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _state(full_id)
        if not st.get("ad"):
            break
        seen = True
        if st.get("skip"):
            SITE.move(full_id, st["skip"][0] - 4, st["skip"][1] - 3)
            time.sleep(0.2)
            SITE.click(full_id, *st["skip"])
            skipped = True
            time.sleep(1.5)
        else:
            time.sleep(1)
    still = bool(_state(full_id).get("ad"))
    if not still:
        _ensure_muted(full_id)
    return {"ad_seen": seen, "ad_skipped": skipped, "ad_still_playing": still}


def _seek_by_click(full_id: str, sec: float) -> float | None:
    """Jump to `sec` by clicking the progress bar where a person would. Returns the new time."""
    st = _state(full_id)
    dur = st.get("duration")
    if not dur or st.get("ad"):
        return None
    _hover_player(full_id, st)
    bar = SITE.rect(full_id, ".ytp-progress-bar")
    if not bar:
        return None
    for _ in range(2):
        x = bar["x"] + bar["width"] * min(max(sec / dur, 0), 0.999)
        y = bar["y"] + bar["height"] / 2
        SITE.move(full_id, x, y)
        time.sleep(0.25)
        SITE.click(full_id, x, y)
        time.sleep(1.2)
        t = _state(full_id).get("t") or 0
        if abs(t - sec) <= max(3, 2 * dur / bar["width"]):
            return t
        sec_err = t - sec
        sec = sec - sec_err * 0.5  # nudge once toward the target
    return _state(full_id).get("t")


def _open_transcript(full_id: str) -> bool:
    seg = "transcript-segment-view-model, ytd-transcript-segment-renderer"
    if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(seg)})"):
        return True
    btn = "ytd-video-description-transcript-section-renderer button"
    if not SITE.rect(full_id, btn):
        SITE.click_on(full_id, "#description-inline-expander #expand, tp-yt-paper-button#expand")
        time.sleep(0.8)
    if not SITE.click_on(full_id, btn):
        return False
    return SITE.wait_for(full_id, f"!!document.querySelector({json.dumps(seg)})", timeout=15)


# -------------------------------------------------------------------------
# Operations (used by the CLI and the MCP server)
# -------------------------------------------------------------------------

def open_window(wait_seconds: float = 0) -> dict:
    return SITE.open_window(wait_seconds)


def search(target: str, query: str, sort: str = "relevance", upload: str | None = None,
           shorts: bool = False) -> dict:
    page = SITE.nav(target, search_url(query, sort, upload))
    if "stop" in page:
        return {"page": page, "results": []}
    results = SITE.evaluate(SITE.resolve(target), _call("search_results.js", {"shorts": shorts})) or []
    return {"page": page, "results": results}


def video(target: str, url: str) -> dict:
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"
    page = SITE.nav(target, url, ready_js=(
        "document.readyState === 'complete' && !!window.ytInitialPlayerResponse && "
        "!!document.querySelector('#movie_player') && !!document.querySelector('ytd-watch-metadata #title h1')"))
    if "stop" in page:
        return {"page": page, "video": None}
    full_id = SITE.resolve(target)
    data = SITE.evaluate(full_id, _js("video.js"))
    st = _state(full_id)
    muted = _ensure_muted(full_id) if not st.get("ad") else st.get("muted")
    return {"page": page, "video": data, "ad_playing": bool(st.get("ad")), "muted": muted}


def transcript(target: str, grep: list[str] | None = None, context: int = 0) -> dict:
    full_id = SITE.resolve(target)
    _require_watch(full_id)
    SITE.touch(full_id)
    if not _open_transcript(full_id):
        return {"available": False, "segments": [], "note": "this video has no transcript button"}
    res = SITE.evaluate(full_id, _js("transcript.js")) or {"available": False, "segments": []}
    SITE.scroll_to_top(full_id)
    segs = res["segments"]
    if grep and segs:
        pat = re.compile("|".join(re.escape(g) for g in grep), re.I)
        keep: set[int] = set()
        for i, s in enumerate(segs):
            if pat.search(s["text"]):
                keep.update(range(max(0, i - context), min(len(segs), i + context + 1)))
        res = {**res, "matched": sum(1 for s in segs if pat.search(s["text"])), "total_segments": len(segs),
               "segments": [segs[i] for i in sorted(keep)]}
    return res


def comments(target: str, max_comments: int = 20) -> dict:
    full_id = SITE.resolve(target)
    _require_watch(full_id)
    SITE.touch(full_id)
    if SITE.scroll_into_view(full_id, "ytd-comments#comments") is None:
        return {"available": False, "comments": []}
    SITE.wait_for(full_id, "!!document.querySelector('ytd-comment-thread-renderer')", timeout=15)
    res = SITE.evaluate(full_id, _call("comments.js", {"max": max_comments}))
    SITE.scroll_to_top(full_id)
    return res


def frame(target: str, at, out: str | None = None) -> dict:
    """Jump to `at`, wait for the frame (and for any ad the jump triggers), capture the player."""
    full_id = SITE.resolve(target)
    vid = _require_watch(full_id)
    SITE.touch(full_id)
    sec = _seconds(at)
    SITE.scroll_to_top(full_id)
    before = _clear_ads(full_id)
    _seek_by_click(full_id, sec)
    after = _clear_ads(full_id)
    t = _state(full_id).get("t") or 0
    if abs(t - sec) > 4 and not after["ad_still_playing"]:
        # An ad (or buffering) moved playback away from the target; click once more.
        _seek_by_click(full_id, sec)
        again = _clear_ads(full_id)
        after = {"ad_seen": after["ad_seen"] or again["ad_seen"], "ad_skipped": after["ad_skipped"] or again["ad_skipped"], "ad_still_playing": again["ad_still_playing"]}
    # Move the pointer off the player so its controls fade out.
    SITE.move(full_id, 5, 5)
    time.sleep(1.8)
    final = _state(full_id)
    rect = final.get("player") or SITE.rect(full_id, "#movie_player")
    path = Path(out) if out else SHOTS / f"{date.today().isoformat()}_{vid}_{_mmss(sec).replace(':', '')}.png"
    SITE.screenshot(full_id, path, clip=rect)
    result = {
        "path": str(path.expanduser()),
        "requested": _mmss(sec), "actual": _mmss(final.get("t") or 0),
        "ad_seen": before["ad_seen"] or after["ad_seen"],
        "ad_skipped": before["ad_skipped"] or after["ad_skipped"],
        "ad_in_frame": bool(final.get("ad")),
        "player_css_px": [round(rect["width"]), round(rect["height"])],
        "link": f"https://www.youtube.com/watch?v={vid}&t={int(sec)}s",
        "check": "Look at the image before using it: it must show the expected content, not an ad, "
                 "sponsor segment, b-roll or a transition.",
    }
    if result["ad_in_frame"]:
        result["warning"] = "An ad was still playing when the frame was captured; this image shows the ad."
    elif rect["width"] < MIN_PLAYER_WIDTH:
        result["warning"] = (f"The player is only {round(rect['width'])} px wide, so details may be hard to read. "
                             "Tell the user the research window is small (a window manager may be tiling it).")
    return result


def shot(target: str, out: str | None = None) -> dict:
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    vid = _video_id(full_id) or "page"
    path = Path(out) if out else SHOTS / f"{date.today().isoformat()}_{vid}_{int(time.time())}.png"
    SITE.screenshot(full_id, path)
    ads = _state(full_id)
    res = {"path": str(path.expanduser()), "url": SITE.evaluate(full_id, "location.href"),
           "check": "Look at the image before using it."}
    if ads.get("ad"):
        res["warning"] = "An ad is playing in the player on this page."
    return res


def channel(target: str, url: str) -> dict:
    if url.startswith("@"):
        url = f"https://www.youtube.com/{url}"
    url = url.rstrip("/")
    if not re.search(r"/(videos|shorts|streams)$", url):
        url += "/videos"
    page = SITE.nav(target, url)
    if "stop" in page:
        return {"page": page, "channel": None}
    return {"page": page, "channel": SITE.evaluate(SITE.resolve(target), _js("channel.js"))}


def close(target: str) -> dict:
    return SITE.close(target)


def list_claims() -> list[dict]:
    return SITE.list_claims()


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def _dispatch(cmd: str, args: list[str]):
    if cmd == "search":
        sort, upload, shorts = opt(args, "--sort", "relevance"), opt(args, "--upload"), bool(opt(args, "--shorts", False))
        if len(args) < 2:
            raise Fail("usage: search ID QUERY [--sort relevance|views] [--upload hour|today|week|month|year] [--shorts]")
        return search(args[0], " ".join(args[1:]), sort, upload, shorts)
    if cmd == "video" and len(args) == 2:
        return video(*args)
    if cmd == "transcript":
        ctx = int(opt(args, "--context", 0))
        terms: list[str] = []
        if "--grep" in args:
            i = args.index("--grep")
            terms, args = args[i + 1:], args[:i]
        if len(args) != 1:
            raise Fail("usage: transcript ID [--context N] [--grep TERM ...]")
        return transcript(args[0], terms or None, ctx)
    if cmd == "comments":
        n = int(opt(args, "--max", 20))
        if len(args) != 1:
            raise Fail("usage: comments ID [--max N]")
        return comments(args[0], n)
    if cmd == "frame":
        out = opt(args, "--out")
        if len(args) != 2:
            raise Fail("usage: frame ID AT [--out PATH]")
        return frame(args[0], args[1], out)
    if cmd == "shot":
        out = opt(args, "--out")
        if len(args) != 1:
            raise Fail("usage: shot ID [--out PATH]")
        return shot(args[0], out)
    if cmd == "channel" and len(args) == 2:
        return channel(*args)
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
