#!/usr/bin/env python3
"""Polite, read-only YouTube research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
login but never each other's tabs; all page loads share one pacing lock.
Every action is one a person would take: real mouse clicks, typing and wheel scrolling,
the player's own mute, theater, play/pause buttons and progress bar.
Scripts only read the page.

CLI (each command prints JSON):

    youtube_tab.py open [--wait [SECONDS]]                 claim a window -> {"target": ID}
    youtube_tab.py search ID QUERY [--sort relevance|views] [--upload hour|today|week|month|year] [--shorts]
                       typed into the search box when a YouTube page is open and no filter is set
                       (`page.via`: "typed"; else "url"). Results carry likely_ai_listicle /
                       amazon_influencer flags when they look like it.
                       (`open` and `list` cost no page load; search, video, channel and nav cost one each)
    youtube_tab.py video ID URL                            metadata, disclosure signals, chapters, description.
                       Clicked from the current results (or after going back to them) when the video is
                       listed there (`page.opened`: "clicked" | "clicked (after back)" | "url" | "url (...)").
    youtube_tab.py transcript ID [--from T] [--to T] [--context N] [--grep TERM ...] [--out-dir DIR]
                       segments ({at, sec, text, link}; `sec` is the start in seconds), deduplicated, with
                       total_segments. The FULL transcript is always saved as JSON + .md (`saved`) in
                       --out-dir, else ~/.local/share/chrome-agent-research/youtube-transcripts/.
                       --from/--to limit the output to a time window; --grep: case-insensitive
                       plain-text substrings, any of them (not regex).
    youtube_tab.py comments ID [--max N]                   top comments of the loaded video (default 20)
    youtube_tab.py frame ID AT [AT ...] [--burst SECONDS] [--full] [--out-dir DIR | --out PATH]
                       theater mode, pause, jump to each AT (e.g. 6:17), capture the player
                       (within about +/-2.5 s). --burst N then lets it play N s (max 15), capturing
                       a candidate every second with the controls hidden. During an ad nothing is
                       paused or seeked; it is waited out (Skip is clicked when offered) and a frame
                       that can't be taken in time reports ad_timeout (no image).
    youtube_tab.py frame ID --scan FROM-TO [--samples N] [--keep K] [--out-dir DIR]
                       sample N points (default 4) in the range with short bursts and rank the
                       frames by graphic-ness (charts, tables, slides, UI); keeps the best K (3).
    youtube_tab.py shot ID [--selector CSS] [--full] [--out-dir DIR | --out PATH]   visible page
                       content (or one element), with ads_in_view
    youtube_tab.py channel ID URL|@handle                  channel header + recent uploads
    youtube_tab.py tally ID NAME [NAME ...] [--alias "Name=Alt,Alt2; Other=..."] [--no-transcripts]
                       channels and independent channels mentioning each name among everything this
                       window has seen (search results merged with loaded videos, their descriptions,
                       links and read transcripts), with snippets and links. No page load.
    youtube_tab.py nav ID URL | eval ID JS|@file.js        escape hatches (paced load / run JS)
    youtube_tab.py close ID | list | gc                    release window / show / clean up claims

transcript, comments and frame work on the video loaded by `video`; shot works on any page.
None of them count as page loads. `video` accepts watch, youtu.be and shorts links. The same functions back the youtube_* MCP tools.

Environment (YOUTUBE_TAB_*): INSTANCE (youtube-research-01), MAX (3 windows),
GAP (6 s between loads, all agents), IDLE (1800 s), MAX_LOADS (45 per window: a hard
stop just above the skill's 40-load budget), AD_TIMEOUT (60 s),
STATE (/tmp/chrome-agent-youtube), SHOTS (STATE/shots),
TRANSCRIPTS (~/.local/share/chrome-agent-research/youtube-transcripts). Needs `chrome-agent` on PATH.
"""
import base64
import json
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
sys.path.insert(0, str(HERE))
from research_core import Fail, Site, common_cli, intersect, opt, pause, run_cli  # noqa: E402
import disclosure as D  # noqa: E402
import tally as T  # noqa: E402
import youtube_signals as S  # noqa: E402

SORTS = {"relevance": None, "views": 3}
UPLOADS = {"hour": 1, "today": 2, "week": 3, "month": 4, "year": 5}
MIN_PLAYER_WIDTH = S.MIN_PLAYER_WIDTH  # CSS px; smaller frames are hard to read
AD_TIMEOUT = float(os.environ.get("YOUTUBE_TAB_AD_TIMEOUT", "60"))
TRANSCRIPTS = Path(os.environ.get("YOUTUBE_TAB_TRANSCRIPTS", str(S.DEFAULT_TRANSCRIPTS))).expanduser()


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


# The page content without the top bar (account avatar) and the left guide (subscriptions).
_CONTENT_RECT_JS = """(() => { const vw = innerWidth, vh = innerHeight;
  const top = (document.querySelector('#masthead-container')?.getBoundingClientRect().bottom) || 0;
  const pm = document.querySelector('ytd-page-manager, #page-manager');
  const left = pm ? Math.max(0, pm.getBoundingClientRect().left) : 0;
  return {x: left, y: top, width: vw - left, height: vh - top}; })()"""

# Ad units on YouTube pages (search "Sponsored" units, home/watch masthead and companion ads).
AD_SELECTORS = (
    "ytd-ad-slot-renderer", "ytd-in-feed-ad-layout-renderer", "ytd-promoted-sparkles-web-renderer",
    "ytd-promoted-video-renderer", "ytd-display-ad-renderer", "ytd-search-pyv-renderer", "#masthead-ad",
    "ytd-banner-promo-renderer", "ytd-statement-banner-renderer", "ytd-companion-slot-renderer",
    "ytd-action-companion-ad-renderer", "ytd-player-legacy-desktop-watch-ads-renderer", "ytd-engagement-panel-section-list-renderer[target-id='engagement-panel-ads']",
)

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
    default_max_loads=45,
    content_js=_CONTENT_RECT_JS,
    ad_selectors=AD_SELECTORS,
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
    # The `sp` parameter is a base64 protobuf: field 1 = sort, field 2 = filters {field 1 = upload date}.
    # No type filter: casual searchers don't set one, and Shorts are separated when parsing results.
    sp = b""
    if SORTS[sort]:
        sp += b"\x08" + _varint(SORTS[sort])
    if upload:
        filters = b"\x08" + _varint(UPLOADS[upload])
        sp += b"\x12" + _varint(len(filters)) + filters
    params = {"search_query": query}
    if sp:
        params["sp"] = base64.b64encode(sp).decode()
    return "https://www.youtube.com/results?" + urlencode(params, quote_via=quote)


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


def _stamp(sec: float) -> str:
    sec = int(sec)
    h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h{m:02d}m{s_:02d}s" if h else f"{m:02d}m{s_:02d}s"


def _now() -> str:
    return time.strftime("%H:%M:%S")


def video_id_of(url: str) -> str | None:
    """The 11-character id in a watch, youtu.be or shorts link (or a bare id)."""
    if re.fullmatch(r"[\w-]{11}", url or ""):
        return url
    m = re.search(r"(?:youtu\.be/|/shorts/|/live/|[?&]v=)([\w-]{11})", url or "")
    return m.group(1) if m else None


def _handle_of(url: str | None) -> str | None:
    m = re.search(r"/(@[\w.-]+)", url or "")
    return m.group(1) if m else None


# -------------------------------------------------------------------------
# What this window has seen (for `tally`), stored next to the claim
# -------------------------------------------------------------------------

def _seen_path(full_id: str) -> Path:
    return SITE.state / "seen" / f"{full_id}.json"


def _seen_load(full_id: str) -> dict:
    try:
        data = json.loads(_seen_path(full_id).read_text())
    except (OSError, ValueError):
        data = {}
    legacy = (SITE.claims().get(full_id) or {}).get("seen") or []
    items = data.get("items") or []
    if legacy and not items:
        items = [{**x, "id": video_id_of(x.get("url") or "") or x.get("url")} for x in legacy]
    return {"items": items, "channels": data.get("channels") or {}}


def _seen_save(full_id: str, data: dict) -> None:
    p = _seen_path(full_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False))


def _seen_add(full_id: str, items: list[dict], channels: dict | None = None) -> None:
    """Merge records into this window's seen list (a loaded video replaces its search record)."""
    if SITE.claims().get(full_id) is None:
        return
    data = _seen_load(full_id)
    data["items"] = T.upsert(data["items"], [i for i in items if i.get("id") or i.get("url")], cap=1500)
    for name, flags in (channels or {}).items():
        if name:
            data["channels"].setdefault(name, {}).update(flags)
    _seen_save(full_id, data)


def _video_record(data: dict, sb: dict, segments: list[dict] | None = None) -> dict:
    """A tally record for a loaded video (richer than its search record, so it wins the merge)."""
    rec = {"id": data.get("video_id"), "url": data.get("url"), "via": "video", "title": data.get("title") or "",
           "channel": data.get("channel"), "text": data.get("description") or "",
           "links": list(data.get("description_links") or []), "age": data.get("published"),
           "views": data.get("views"), "sponsor_score": sb.get("score"),
           "transcript_scanned": bool(sb.get("transcript_scanned"))}
    if sb.get("brand_owned"):
        rec["brand_owned"] = sb["brand_owned"]
    if sb.get("by_brand"):
        rec["by_brand"] = sb["by_brand"]
        rec["by_brand_shared"] = sb.get("shared_score")
    for k in ("likely_ai_listicle", "amazon_influencer"):
        if data.get(k):
            rec[k] = True
    if segments is not None:
        rec["segments"] = [{"text": s.get("text"), "link": s.get("link")} for s in segments]
    return rec


def _remember(full_id: str, data: dict) -> None:
    c = SITE.claims().get(full_id)
    if c is not None:
        c["video"] = data
        SITE._write_claim(full_id, c)


def _recall(full_id: str, vid: str) -> dict | None:
    v = (SITE.claims().get(full_id) or {}).get("video")
    return v if v and v.get("video_id") == vid else None


def _claim_set(full_id: str, **kv) -> None:
    c = SITE.claims().get(full_id)
    if c is not None:
        c.update(kv)
        SITE._write_claim(full_id, c)


def _video_id(full_id: str) -> str | None:
    url = SITE.evaluate(full_id, "location.href") or ""
    return parse_qs(urlparse(url).query).get("v", [None])[0]


def _require_watch(full_id: str) -> str:
    vid = _video_id(full_id)
    if not vid:
        raise Fail("no video is loaded in this window; load one with `video` first.")
    return vid


# -------------------------------------------------------------------------
# The player: one shared state reader (Y15) and organic controls
# -------------------------------------------------------------------------

_PLAYER_STATE_JS = """(() => { const p = document.querySelector('#movie_player'), v = document.querySelector('video');
  const ad = !!p && (p.classList.contains('ad-showing') || p.classList.contains('ad-interrupting'));
  const sk = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button');
  let skip = null;
  if (sk) { const s = getComputedStyle(sk), r = sk.getBoundingClientRect();
    if (s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0) skip = [r.x + r.width / 2, r.y + r.height / 2]; }
  const pr = p ? p.getBoundingClientRect() : null;
  return { ad, skip, t: v ? v.currentTime : null, duration: v ? v.duration : null, muted: v ? v.muted : null, paused: v ? v.paused : null,
           autohide: !!p && p.classList.contains('ytp-autohide'),
           theater: !!document.querySelector('ytd-watch-flexy[theater]'), vw: innerWidth, vh: innerHeight,
           player: pr ? {x: pr.x, y: pr.y, width: pr.width, height: pr.height} : null }; })()"""


def _state(full_id: str) -> dict:
    """The one reader of player and ad state, used by every command (Y15)."""
    st = SITE.evaluate(full_id, _PLAYER_STATE_JS) or {}
    st["checked_at"] = _now()
    return st


class _AdLog:
    """What a command saw of ads, and when (Y15)."""

    def __init__(self):
        self.events: list[dict] = []

    def add(self, event: str, **kw) -> None:
        if not self.events or self.events[-1]["event"] != event:
            self.events.append({"at": _now(), "event": event, **kw})


def _press(full_id: str, x: float, y: float) -> None:
    SITE.cdp(full_id, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    time.sleep(random.uniform(0.06, 0.14))
    SITE.cdp(full_id, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


def _click_control(full_id: str, selector: str, during_ad: bool = False, at: tuple[float, float] | None = None) -> bool:
    """Move to a player control along a human path and click it. Unless `during_ad`, the ad state is
    re-read right before the press, and nothing is pressed while an ad shows (Y2)."""
    r = SITE.rect(full_id, selector)
    if not r:
        return False
    x = at[0] if at else r["x"] + r["width"] * random.uniform(0.35, 0.65)
    y = at[1] if at else r["y"] + r["height"] * random.uniform(0.35, 0.65)
    SITE.move(full_id, x, y)
    pause(random.uniform(0.12, 0.3), fuzz=0)
    if not during_ad and _state(full_id).get("ad"):
        return False
    _press(full_id, x, y)
    return True


def _key(full_id: str, key: str, code: str, vk: int, text: str | None = None, modifiers: int = 0,
         commands: list[str] | None = None) -> None:
    down = {"type": "keyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": modifiers}
    if text:
        down.update(text=text, unmodifiedText=text)
    if commands:
        down["commands"] = commands
    SITE.cdp(full_id, "Input.dispatchKeyEvent", down)
    time.sleep(random.uniform(0.03, 0.09))
    SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code,
                                                  "windowsVirtualKeyCode": vk, "modifiers": modifiers})


def _ensure_muted(full_id: str) -> bool:
    """If the video has sound, click the player's mute button (YouTube remembers it). This is done
    during ads too: muting is a normal action; only play/pause and seeking are avoided during ads."""
    st = _state(full_id)
    if st.get("muted") is None or st.get("muted"):
        return bool(st.get("muted"))
    if _click_control(full_id, ".ytp-mute-button"):
        pause(0.4)
    return bool(_state(full_id).get("muted"))


def _ensure_quiet(full_id: str) -> dict:
    """Undo anything a stray key could have done to the user's screen and speakers: leave fullscreen
    (Escape, as a person would) and make sure the video is muted."""
    fixed = {}
    try:
        if SITE.evaluate(full_id, "!!document.fullscreenElement"):
            _key(full_id, "Escape", "Escape", 27)
            pause(0.6)
            fixed["left_fullscreen"] = not SITE.evaluate(full_id, "!!document.fullscreenElement")
        if SITE.evaluate(full_id, "!!document.querySelector('video')"):
            fixed["muted"] = _ensure_muted(full_id)
    except Fail:
        pass
    return fixed


def _clear_ads(full_id: str, timeout: float | None = None, log: _AdLog | None = None) -> dict:
    """Wait out any ad without touching the player (no pause, no seek, no k). Skip is clicked after a
    human-like delay when offered; a paused ad is resumed with one click on the play button. Then make
    sure we're muted."""
    log = log or _AdLog()
    timeout = AD_TIMEOUT if timeout is None else timeout
    seen = skipped = resumed = False
    paused_since = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = _state(full_id)
        if not st.get("ad"):
            break
        if not seen:
            log.add("ad_showing")
        seen = True
        if st.get("skip"):
            pause(random.uniform(1, 4), fuzz=0)  # people don't hit Skip the instant it appears
            st = _state(full_id)
            if not st.get("ad") or not st.get("skip"):
                continue
            SITE.move(full_id, st["skip"][0] - 4, st["skip"][1] - 3)
            pause(0.2)
            SITE.click(full_id, *st["skip"])
            skipped = True
            log.add("skip_clicked")
            pause(1.5)
        elif st.get("paused"):
            paused_since = paused_since or time.time()
            if not resumed and time.time() - paused_since > 1.5:
                # An ad left paused never ends: resume it once, the way a person would.
                _click_control(full_id, ".ytp-play-button", during_ad=True)
                resumed = True
                log.add("paused_ad_resumed")
            pause(1)
        else:
            paused_since = None
            pause(1)
    still = bool(_state(full_id).get("ad"))
    if still:
        log.add("ad_timeout")
    else:
        if seen:
            log.add("ad_ended")
        _ensure_muted(full_id)
    return {"ad_seen": seen, "ad_skipped": skipped, "ad_still_playing": still, "ad_resumed": resumed}


_BUFFERING_JS = """(() => { const p = document.querySelector('#movie_player'), v = document.querySelector('video');
  return (!!p && p.classList.contains('buffering-mode')) || (!!v && v.readyState < 2); })()"""


def _wait_buffered(full_id: str, timeout: float = 10) -> bool:
    """Wait until the frame is decoded (no spinner). Returns True if still buffering."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not SITE.evaluate(full_id, _BUFFERING_JS):
            return False
        pause(0.4)
    return bool(SITE.evaluate(full_id, _BUFFERING_JS))


def _ensure_theater(full_id: str) -> bool:
    """Switch to theater mode with the player's own button (YouTube remembers it) for a larger frame."""
    is_theater = "!!document.querySelector('ytd-watch-flexy[theater]')"
    if SITE.evaluate(full_id, is_theater):
        return True
    if not _click_control(full_id, ".ytp-size-button"):
        return False
    SITE.wait_for(full_id, is_theater, timeout=5, settle=0.6)
    return bool(SITE.evaluate(full_id, is_theater))


def _ensure_paused(full_id: str) -> bool:
    """Pause with the player's play/pause button, as a person would before studying a frame (not during ads)."""
    st = _state(full_id)
    if st.get("ad") or st.get("paused") is None or st.get("paused"):
        return bool(st.get("paused"))
    if _click_control(full_id, ".ytp-play-button"):
        pause(0.4)
    return bool(_state(full_id).get("paused"))


def _resume_briefly(full_id: str) -> None:
    """Play for a couple of seconds between captures, as a person would before the next jump."""
    st = _state(full_id)
    if st.get("paused") and not st.get("ad"):
        if _click_control(full_id, ".ytp-play-button"):
            pause(random.uniform(1.5, 3), fuzz=0)


def _seek_by_click(full_id: str, sec: float) -> float | None:
    """Jump to `sec` by clicking the progress bar where a person would. Returns the new time.
    Never during an ad."""
    st = _state(full_id)
    dur = st.get("duration")
    if not dur or st.get("ad"):
        return None
    bar = SITE.rect(full_id, ".ytp-progress-bar")
    if not bar:
        return None
    aim, t = sec, None
    for _ in range(2):
        x = bar["x"] + bar["width"] * min(max(aim / dur, 0), 0.999)
        y = bar["y"] + bar["height"] / 2
        if not _click_control(full_id, ".ytp-progress-bar", at=(x, y)):
            return None  # an ad started
        pause(0.7)
        t = _state(full_id).get("t") or 0
        if abs(t - sec) <= max(1.0, 1.5 * dur / bar["width"]):
            return t
        if t == aim or abs((aim - (t - sec)) - aim) < 0.5:
            break
        aim = aim - (t - sec)  # chapter gaps make the bar slightly non-linear; correct once
    # Clicks can snap to a nearby chapter start. Finish with the arrow keys (5 s per press),
    # which is how a person fine-tunes a position. The ad state is re-checked before each press.
    presses = round((sec - (t or 0)) / 5)
    key = "ArrowRight" if presses > 0 else "ArrowLeft"
    for _ in range(min(abs(presses), 12)):
        if _state(full_id).get("ad"):
            break
        _key(full_id, key, key, 39 if key == "ArrowRight" else 37)
        pause(random.uniform(0.15, 0.35), fuzz=0)
    pause(0.4)
    return _state(full_id).get("t")


def _pointer_off_player(full_id: str, st: dict) -> None:
    """Move the pointer off the player (below it, or onto the top bar) so the controls fade."""
    pl, vh, vw = st.get("player"), st.get("vh") or 800, st.get("vw") or 1200
    if not pl:
        return
    below = pl["y"] + pl["height"]
    if below + 60 < vh:
        x, y = pl["x"] + pl["width"] * random.uniform(0.2, 0.6), below + random.uniform(30, min(140, vh - below - 20))
    else:
        x, y = vw * random.uniform(0.3, 0.45), random.uniform(18, 36)
    SITE.move(full_id, x, y)


def _wait_controls_hidden(full_id: str, timeout: float = 3.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _state(full_id).get("autohide"):
            return True
        pause(0.3)
    return bool(_state(full_id).get("autohide"))


_SEG = "transcript-segment-view-model, ytd-transcript-segment-renderer"
_TRANSCRIPT_ERR_JS = ("Math.max(0, ...performance.getEntriesByType('resource')"
                      ".filter(e => e.name.includes('/get_transcript')).map(e => e.responseStatus || 0))")


def _open_transcript(full_id: str) -> tuple[bool, str | None]:
    """Open the transcript with real clicks. Returns (ok, reason-if-not)."""
    if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_SEG)})"):
        return True, None
    btn = "ytd-video-description-transcript-section-renderer button"
    if not SITE.rect(full_id, btn):
        SITE.click_on(full_id, "#description-inline-expander #expand, tp-yt-paper-button#expand",
                      look=random.uniform(0.8, 2.0))
        # Skim the description before looking for the button (with an occasional idle pointer drift).
        SITE.read_pause(full_id, random.uniform(2.5, 6.0), area=SITE.content_area(full_id))
    if not SITE.click_on(full_id, btn, look=random.uniform(1.0, 2.5)):
        return False, "no_transcript: this video has no 'Show transcript' button."
    deadline = time.time() + 15
    while time.time() < deadline:
        if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_SEG)})"):
            pause(0.8)  # let the rest of the list render
            return True, None
        status = SITE.evaluate(full_id, _TRANSCRIPT_ERR_JS) or 0
        if status >= 400:
            return False, (f"youtube_error: YouTube's transcript service returned HTTP {status} for this video, "
                           "so the panel never loads (a person would see the same endless spinner). "
                           "Use chapters, the description, comments and frames instead.")
        pause(1)
    return False, "timeout: the transcript panel did not load within 15 s."


# -------------------------------------------------------------------------
# Typing and clicking instead of URL loads (B3)
# -------------------------------------------------------------------------

_PUNCT = {" ": ("Space", 32, False), "-": ("Minus", 189, False), "_": ("Minus", 189, True), ".": ("Period", 190, False),
          ",": ("Comma", 188, False), "/": ("Slash", 191, False), "?": ("Slash", 191, True), "'": ("Quote", 222, False),
          '"': ("Quote", 222, True), ":": ("Semicolon", 186, True), ";": ("Semicolon", 186, False),
          "+": ("Equal", 187, True), "=": ("Equal", 187, False), "!": ("Digit1", 49, True), "#": ("Digit3", 51, True),
          "$": ("Digit4", 52, True), "%": ("Digit5", 53, True), "&": ("Digit7", 55, True), "(": ("Digit9", 57, True),
          ")": ("Digit0", 48, True)}


def _type_text(full_id: str, text: str, focus_js: str | None = None) -> bool:
    """Type `text` key by key with uneven, human timing (short gaps, longer after spaces, rare pauses).
    With `focus_js`, check before every key that the intended field still has focus, and stop at
    once if not: a stray key on a watch page is a player shortcut (f = fullscreen, m = mute, k = play,
    c = captions, t = theater, i = miniplayer, digits = seek). Returns False if it stopped early."""
    for ch in text:
        if focus_js and not SITE.evaluate(full_id, focus_js):
            return False
        if ch.isascii() and ch.isalpha():
            code, vk, shift = f"Key{ch.upper()}", ord(ch.upper()), ch.isupper()
        elif ch.isascii() and ch.isdigit():
            code, vk, shift = f"Digit{ch}", ord(ch), False
        elif ch in _PUNCT:
            code, vk, shift = _PUNCT[ch]
        else:
            SITE.cdp(full_id, "Input.insertText", {"text": ch})  # e.g. accented letters (an IME-like input)
            continue
        _key(full_id, ch, code, vk, text=ch, modifiers=8 if shift else 0)
        gap = min(random.lognormvariate(-2.6, 0.6), 0.6)
        if ch == " ":
            gap += random.uniform(0.05, 0.25)
        if random.random() < 0.04:
            gap += random.uniform(0.4, 1.2)  # a moment's hesitation
        time.sleep(gap)
    return True


_SEARCH_BOX = "input[name='search_query'], textarea[name='search_query']"


def _typed_search(full_id: str, query: str) -> tuple[dict | None, str]:
    """Search the way a person does: click the search box, replace its text, type, press Enter.
    Needs a YouTube page in the window. Returns (page state, via) or (None, fallback reason)."""
    host = SITE.evaluate(full_id, "location.hostname") or ""
    if not host.endswith("youtube.com"):
        return None, "url"
    r = SITE.rect(full_id, _SEARCH_BOX)
    if not r:
        return None, "url (no search box)"
    prev_first = SITE.evaluate(full_id, "document.querySelector('ytd-search ytd-video-renderer a#video-title')?.href || null")
    SITE.click(full_id, r["x"] + r["width"] * random.uniform(0.2, 0.6), r["y"] + r["height"] * random.uniform(0.35, 0.65))
    pause(random.uniform(0.3, 0.7), fuzz=0)
    box_value = f"document.querySelector({json.dumps(_SEARCH_BOX)})?.value ?? null"
    focus_js = f"!!document.activeElement && document.activeElement.matches({json.dumps(_SEARCH_BOX)})"
    if not SITE.evaluate(full_id, focus_js):
        return None, "url (search box did not take focus)"  # never type into the page/player
    if SITE.evaluate(full_id, box_value):
        mac = sys.platform == "darwin"
        _key(full_id, "a", "KeyA", 65, modifiers=4 if mac else 2, commands=["selectAll"])
        pause(random.uniform(0.15, 0.4), fuzz=0)
    if not _type_text(full_id, query, focus_js=focus_js):
        _ensure_quiet(full_id)
        return None, "url (search box lost focus while typing)"
    pause(random.uniform(0.3, 0.9), fuzz=0)
    if (SITE.evaluate(full_id, box_value) or "").strip() != query.strip():
        return None, "url (typing failed)"
    loads = SITE._count_load(full_id)
    SITE._pace()
    _key(full_id, "Enter", "Enter", 13, text="\r")
    q = json.dumps(query.strip())
    url_ok = (f"(() => {{ const u = new URL(location.href); return u.pathname === '/results' && "
              f"(u.searchParams.get('search_query') || '').trim() === {q}; }})()")
    fresh = (f"{url_ok} && !!document.querySelector('ytd-search ytd-video-renderer') && "
             f"(document.querySelector('ytd-search ytd-video-renderer a#video-title')?.href || null) !== {json.dumps(prev_first)}")
    if not SITE.wait_for(full_id, fresh, timeout=12, settle=SITE.settle):
        if not SITE.wait_for(full_id, url_ok, timeout=3, settle=1.0):
            return None, "url (search did not submit)"
    state = SITE.evaluate(full_id, SITE.page_state_js) or {}
    state["loads"] = loads
    reason = _stop_reason(state, SITE)
    if reason:
        state["stop"] = reason
    return state, "typed"


def _link_selector(vid: str) -> str:
    v = f'[href*="v={vid}"]'
    return ", ".join([f"ytd-video-renderer a#video-title{v}", f"ytd-video-renderer a#thumbnail{v}",
                      f"ytd-compact-video-renderer a{v}", f"yt-lockup-view-model a{v}", f"ytd-rich-item-renderer a#video-title-link{v}"])


def _visible_link_js(vid: str) -> str:
    """Whether `vid` is listed on the visible page (YouTube keeps earlier pages in the DOM, hidden)."""
    return f"[...document.querySelectorAll({json.dumps(_link_selector(vid))})].some(e => e.getClientRects().length > 0)"


def _watch_ready_js(vid: str) -> str:
    v = json.dumps(vid)
    return (f"(() => {{ const f = document.querySelector('ytd-watch-flexy'); let pr = null;"
            f" try {{ pr = document.querySelector('#movie_player')?.getPlayerResponse?.(); }} catch (e) {{}}"
            f" return new URLSearchParams(location.search).get('v') === {v} && !!f && f.getAttribute('video-id') === {v}"
            f" && pr?.videoDetails?.videoId === {v} && !!document.querySelector('ytd-watch-metadata #title h1')?.textContent.trim(); }})()")


def _back_to_results(full_id: str, vid: str) -> bool:
    """If the previous history entry is the last search and it lists `vid`, go back to it (the browser's
    own back navigation; YouTube redraws the results from its cache)."""
    ls = (SITE.claims().get(full_id) or {}).get("last_search") or {}
    if vid not in (ls.get("ids") or []):
        return False
    hist = SITE.cdp(full_id, "Page.getNavigationHistory")
    i, entries = hist.get("currentIndex", 0), hist.get("entries") or []
    if i < 1 or entries[i - 1].get("url") != ls.get("url"):
        return False
    pause(random.uniform(0.4, 1.0), fuzz=0)
    SITE.cdp(full_id, "Page.navigateToHistoryEntry", {"entryId": entries[i - 1]["id"]})
    return SITE.wait_for(full_id, f"location.href === {json.dumps(ls['url'])} && {_visible_link_js(vid)}",
                         timeout=10, settle=1.0)


def _open_by_click(full_id: str, vid: str) -> tuple[dict | None, str]:
    """Open a video by clicking it in the current results (or after going back to them).
    Returns (page state, opened) or (None, fallback reason)."""
    sel = _link_selector(vid)
    how = "clicked"
    if not SITE.evaluate(full_id, _visible_link_js(vid)):
        try:
            if not _back_to_results(full_id, vid):
                return None, "url"
        except Fail:
            return None, "url"
        how = "clicked (after back)"
    loads = SITE._count_load(full_id)
    SITE._pace()
    try:
        if not SITE.click_on(full_id, sel, look=random.uniform(0.6, 1.8)):
            return None, "url (click missed)"
    except Fail as e:
        return None, f"url (click failed: {str(e)[:80]})"
    if not SITE.wait_for(full_id, _watch_ready_js(vid), timeout=15, settle=SITE.settle):
        return None, "url (click missed)"
    state = SITE.evaluate(full_id, SITE.page_state_js) or {}
    state["loads"] = loads
    reason = _stop_reason(state, SITE)
    if reason:
        state["stop"] = reason
    return state, how


# -------------------------------------------------------------------------
# Operations (used by the CLI and the MCP server)
# -------------------------------------------------------------------------

def open_window(wait_seconds: float = 0) -> dict:
    return SITE.open_window(wait_seconds)


def _flag_result(r: dict) -> dict:
    """Y3/Y17 flags on a search result."""
    handle = _handle_of(r.get("channel_url"))
    lst, why = S.likely_ai_listicle(r.get("title") or "", r.get("channel"), handle)
    if lst:
        r["likely_ai_listicle"], r["listicle_reasons"] = True, why
    amz, why = S.amazon_influencer(r.get("title") or "")
    if amz:
        r["amazon_influencer"], r["amazon_reasons"] = True, why
    return r


def search(target: str, query: str, sort: str = "relevance", upload: str | None = None,
           shorts: bool = False, typed: bool = True) -> dict:
    url = search_url(query, sort, upload)  # validates sort/upload
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    page, via = None, "url"
    if SORTS[sort] or upload:
        via = "url (filters)"  # filters are set through the URL, not the filter menu
    elif typed:
        page, via = _typed_search(full_id, query)
    if page is None:
        page = SITE.nav(target, url)
    page["via"] = via
    if "stop" in page:
        return {"page": page, "results": []}
    results = [_flag_result(r) for r in (SITE.evaluate(full_id, _call("search_results.js", {"shorts": shorts})) or [])]
    here = SITE.evaluate(full_id, "location.href") or url
    _claim_set(full_id, last_search={"url": here, "ids": [video_id_of(r["url"]) for r in results if video_id_of(r["url"])]})
    recs = []
    for r in results:
        rec = {"id": video_id_of(r["url"]) or r["url"], "url": r["url"], "via": "search", "title": r.get("title", ""),
               "channel": r.get("channel"), "age": r.get("age"), "views": r.get("views")}
        for k in ("likely_ai_listicle", "amazon_influencer"):
            if r.get(k):
                rec[k] = True
        recs.append(rec)
    _seen_add(full_id, recs, {r["channel"]: {"listicle": True} for r in results
                              if r.get("likely_ai_listicle") and r.get("channel")})
    return {"page": page, "results": results}


def sponsor_bias(data: dict, transcript_hits: list[dict] | None = None) -> dict:
    """0.0-1.0 likelihood that a video is sponsored or sponsor-influenced, with its evidence.
    Y5: the paid-promotion label weighs 0.2 instead of 0.6 when the only named sponsor is a third party.
    Y6: with two or more brands, `by_brand` scores each brand separately (`roundup` with three or more)."""
    d = data.get("disclosure") or {}
    page: list[dict] = []
    if d.get("paid_promotion_label"):
        page.append({"signal": "paid_promotion_label", "weight": 0.6, "source": "player", "text": d.get("paid_promotion_text")})
    subject = " ".join([data.get("title") or ""] + list(data.get("keywords") or [])[:8])
    ch, own = data.get("channel"), d.get("own_domains") or ()
    page += D.scan_text(data.get("title", ""), "title", subject=subject, channel=ch, own_domains=own)
    page += D.scan_text(data.get("description", ""), "description", subject=subject, channel=ch, own_domains=own)
    page += D.link_hits(d)
    if d.get("products_earning_commission"):
        page.append({"signal": "shopping_commission", "weight": 0.15, "source": "product shelf",
                     "text": f"{d['products_earning_commission']} tagged product(s) marked 'Earns commission'"})
    # Brand ownership is judged from the channel name/handle against the title's brand only (not keywords,
    # not the creator's own shop links).
    own_links = set(d.get("self_promotion_links") or [])
    page = D.brand_adjust(page, subject, ch, title=data.get("title") or "", handle=data.get("handle"),
                          brand_domains=[u for u in data.get("description_links") or () if u not in own_links])
    extra = D.brand_adjust(list(transcript_hits or []), subject, None)
    every = page + extra
    third = [h for h in every if h["signal"] == "third_party_sponsor"]
    brand_reads = [h for h in every if h["signal"] == "sponsor_read" and h.get("sponsor")]
    for i, h in enumerate(page):
        if h["signal"] == "paid_promotion_label" and third and not brand_reads:
            names = sorted({x.get("sponsor") for x in third if x.get("sponsor")})
            page[i] = {**h, "weight": 0.2, "third_party_sponsor": names,
                       "text": f"{h.get('text') or 'Includes paid promotion'} (the named sponsor is a third party: "
                               f"{', '.join(names) or 'not the reviewed brand'})"}
    res = D.score(page, extra)
    brands = S.detect_brands(data.get("title") or "", data.get("description") or "",
                             data.get("description_links") or (), own)
    if len(brands) >= 2:
        pb = S.per_brand(page + extra, brands, data.get("description") or "", d)
        res["brands"], res["by_brand"], res["shared_score"] = brands, pb["by_brand"], pb["shared_score"]
        if len(brands) >= 3:
            res["roundup"] = True
            res["roundup_note"] = ("Multi-brand video: read `by_brand` (each brand's own sponsor score); the overall "
                                   "score mixes every brand's signals. `shared_score` covers signals tied to no brand.")
    res["transcript_scanned"] = transcript_hits is not None
    if transcript_hits is None:
        res["confidence"] = "low"
        res["confidence_note"] = ("Page only: the transcript hasn't been scanned (or couldn't be), so spoken "
                                  "sponsor reads and review-unit mentions are unknown. A low score here is not a clean bill.")
    else:
        res["confidence"] = "medium"
    return res


_VIDEO_READY_JS = ("document.readyState === 'complete' && !!window.ytInitialPlayerResponse && "
                   "!!document.querySelector('#movie_player') && !!document.querySelector('ytd-watch-metadata #title h1')")


def video(target: str, url: str) -> dict:
    vid = video_id_of(url)
    if vid and not re.search(r"/shorts/", url):
        url = f"https://www.youtube.com/watch?v={vid}"
    elif not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    page, opened = (None, "url")
    if vid and "/shorts/" not in url:
        page, opened = _open_by_click(full_id, vid)
    if page is None:
        page = SITE.nav(target, url, ready_js=_VIDEO_READY_JS)
    page["opened"] = opened
    if "stop" in page:
        return {"page": page, "video": None}
    data = SITE.evaluate(full_id, _js("video.js"))
    if data and data.get("stale"):
        pause(2)
        data = SITE.evaluate(full_id, _js("video.js"))
        if data and data.get("stale"):
            data = None
    if data:
        data["disclosure"] = D.merge_link_classes(data.get("disclosure"), D.classify_links(
            data.get("description_links") or [], channel=data.get("channel"), description=data.get("description") or "",
            handle=data.get("handle")))
        lst, why = S.likely_ai_listicle(data.get("title") or "", data.get("channel"), data.get("handle"))
        if lst:
            data["likely_ai_listicle"], data["listicle_reasons"] = True, why
        amz, why = S.amazon_influencer(data.get("title") or "", data.get("description") or "",
                                       data.get("description_links") or [], data.get("length_s"), data.get("views"))
        if amz:
            data["amazon_influencer"], data["amazon_reasons"] = True, why
        data["sponsor_bias"] = sponsor_bias(data)
        _seen_add(full_id, [_video_record(data, data["sponsor_bias"])],
                  {data.get("channel"): {"listicle": True}} if lst else None)
        _remember(full_id, {k: data.get(k) for k in (
            "video_id", "title", "keywords", "channel", "handle", "url", "published", "captions", "length_s", "views",
            "description", "disclosure", "description_links", "likely_ai_listicle", "amazon_influencer")})
    st = _state(full_id)
    muted = _ensure_muted(full_id) if not st.get("ad") else st.get("muted")
    # Let it play (muted) for a few seconds, as a person settling in would, before doing anything else.
    SITE.read_pause(full_id, random.uniform(3, 7))
    st = _state(full_id)  # re-read: an ad may have started or ended meanwhile (Y15)
    return {"page": page, "video": data, "ad_playing": bool(st.get("ad")),
            "ad_state": {"ad": bool(st.get("ad")), "checked_at": st.get("checked_at"),
                         "note": "Ad state when `video` returned; `frame` reads it again before every capture."},
            "muted": muted}


def transcript(target: str, grep: list[str] | None = None, context: int = 0,
               start: str | None = None, end: str | None = None, out_dir: str | None = None) -> dict:
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    SITE.touch(full_id)
    vid = _require_watch(full_id)
    ok, reason = _open_transcript(full_id)
    if not ok:
        SITE.scroll_to_top(full_id)
        res = {"available": False, "segments": [], "total_segments": 0, "reason": reason}
        page = _recall(full_id, vid)
        if page:
            res["sponsor_bias"] = sponsor_bias(page)  # page-only, flagged low confidence
        return res
    raw = SITE.evaluate(full_id, _js("transcript.js")) or {"available": False, "segments": []}
    SITE.scroll_to_top(full_id)
    segs, removed = S.dedupe_segments(raw.get("segments") or [])
    for seg in segs:
        if seg.get("sec") is not None:
            seg["link"] = f"https://www.youtube.com/watch?v={vid}&t={seg['sec']}s"
    page = _recall(full_id, vid)
    # Disclosure mentions anywhere in the transcript (independent of --grep), and the updated bias.
    subject = " ".join([(page or {}).get("title") or ""] + list((page or {}).get("keywords") or [])[:8])
    mentions = D.dedupe_hits(D.scan_segments(segs, subject, channel=(page or {}).get("channel"),
                                             own_domains=((page or {}).get("disclosure") or {}).get("own_domains") or ()))
    res = {"available": bool(segs), "total_segments": len(segs), "rendered_segments": raw.get("rendered_segments"),
           "duplicates_removed": removed,
           "language": raw.get("language") or (((page or {}).get("captions") or [None])[0]),
           "disclosure_mentions": mentions}
    sb = None
    if page:
        sb = res["sponsor_bias"] = sponsor_bias(page, mentions)
    # Save the full transcript (whatever --grep/--from/--to return), so it can be reviewed later (Y12).
    if segs:
        meta = {"video_id": vid, "title": (page or {}).get("title"), "channel": (page or {}).get("channel"),
                "url": f"https://www.youtube.com/watch?v={vid}", "published": (page or {}).get("published"),
                "language": res["language"], "sponsor_bias": sb}
        try:
            res["saved"] = S.save_transcript(out_dir or TRANSCRIPTS, meta, segs)
        except OSError as e:
            res["saved"] = {"error": f"could not save the transcript: {e}"}
        if page and sb:
            _seen_add(full_id, [_video_record({**page, "video_id": vid}, sb, segs)])
    res["segments"] = segs
    if start or end:
        lo = _seconds(start) if start else 0
        hi = _seconds(end) if end else float("inf")
        segs = [sg for sg in segs if sg.get("sec") is not None and lo <= sg["sec"] <= hi]
        res = {**res, "window": [_mmss(lo), _mmss(hi) if hi != float("inf") else "end"],
               "window_segments": len(segs), "segments": segs}
    if grep and segs:
        terms = [g.lower() for g in grep]
        hit_idx = {i: [t for t in terms if t in sg["text"].lower()] for i, sg in enumerate(segs)}
        hit_idx = {i: m for i, m in hit_idx.items() if m}
        keep: set[int] = set()
        for i in hit_idx:
            keep.update(range(max(0, i - context), min(len(segs), i + context + 1)))
        out = []
        for i in sorted(keep):
            sg = dict(segs[i])
            if i in hit_idx:
                sg["matched"] = hit_idx[i]
            else:
                sg["context"] = True
            out.append(sg)
        res = {**res, "matched_segments": len(hit_idx), "returned_segments": len(out), "segments": out}
    return res


def comments(target: str, max_comments: int = 20) -> dict:
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    vid = _require_watch(full_id)
    SITE.touch(full_id)
    if SITE.scroll_into_view(full_id, "ytd-comments#comments") is None:
        return {"available": False, "comments": []}
    SITE.wait_for(full_id, "!!document.querySelector('ytd-comment-thread-renderer')", timeout=15)
    SITE.read_pause(full_id, random.uniform(1.0, 2.5))
    res = SITE.evaluate(full_id, _call("comments.js", {"max": max_comments})) or {"available": False, "comments": []}
    SITE.scroll_to_top(full_id)
    page = _recall(full_id, vid) or {}
    brands = S.detect_brands(page.get("title") or "", page.get("description") or "") or \
        sorted(D.brand_tokens(page.get("title") or ""))[:4]
    hint = S.comment_promo_hints(res.get("comments") or [], brands)
    if hint:
        res["comment_promo_hint"] = hint
    return res


IMG_WIDTH = int(os.environ.get("YOUTUBE_TAB_IMG_WIDTH", "1600"))


def _img_path(out: str | None, out_dir: str | None, stem: str, full: bool, used: set | None = None) -> Path:
    if out:
        return Path(out).expanduser()
    return S.unique_path(Path(out_dir or SHOTS).expanduser() / f"{stem}.{'png' if full else 'jpg'}", used)


def _graphic(full_id: str, clip: dict) -> dict | None:
    """Graphic-ness of what is in `clip` right now, from a small PNG (Y16)."""
    try:
        scale = min(1.0, 320 / max(1, clip["width"]))
        data = SITE.cdp(full_id, "Page.captureScreenshot", {"format": "png", "clip": {
            "x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"], "scale": scale}})["data"]
        return S.graphic_score(base64.b64decode(data))
    except (Fail, ValueError, KeyError):
        return None


def _player_clip(full_id: str, st: dict) -> dict:
    """The player's rect in page coordinates (screenshot clips are page-relative)."""
    pl = st.get("player") or SITE.rect(full_id, "#movie_player")
    sx, sy = SITE.evaluate(full_id, "[scrollX, scrollY]") or [0, 0]
    return {"x": pl["x"] + sx, "y": pl["y"] + sy, "width": pl["width"], "height": pl["height"]}


def _burst(full_id: str, vid: str, seconds: float, out_dir: str | None, full: bool, used: set,
           log: _AdLog, score: bool = False) -> list[dict]:
    """Let the video play (muted) for about `seconds`, capturing one candidate per second on a steady
    timer. The pointer is already off the player; playback starts with the keyboard (k), never during an
    ad; frames with the controls showing or an ad are dropped (and replaced when time allows)."""
    st = _state(full_id)
    if st.get("ad"):
        log.add("ad_showing")
        return []
    _key(full_id, "k", "KeyK", 75, text="k")
    pause(2.6)  # the key press shows the controls briefly; let them fade before capturing
    want, step = max(1, int(round(seconds))), 1.0
    out, dropped = [], 0
    t0 = time.time()
    deadline = t0 + seconds + 4
    i = 0
    while len(out) < want and time.time() < deadline:
        wait = t0 + i * step - time.time()
        if wait > 0:
            time.sleep(wait)
        i += 1
        st = _state(full_id)
        if st.get("ad"):
            log.add("ad_showing")
            dropped += 1
            continue
        if not st.get("autohide"):
            dropped += 1
            continue
        t = st.get("t") or 0
        path = _img_path(None, out_dir, f"{date.today().isoformat()}_{vid}_at{_stamp(t)}", full, used)
        clip = _player_clip(full_id, st)
        SITE.screenshot(full_id, path, clip=clip, max_width=None if full else IMG_WIDTH)
        c = {"path": str(path), "at": _mmss(t), "sec": int(t), "link": f"https://www.youtube.com/watch?v={vid}&t={int(t)}s"}
        if score:
            c["graphic"] = _graphic(full_id, clip)
        out.append(c)
    if dropped and out:
        out[-1].setdefault("note", f"{dropped} candidate(s) dropped (controls visible or ad)")
    return out


def _ad_timeout_result(sec: float, vid: str, ads: dict, log: _AdLog) -> dict:
    return {"path": None, "requested": _mmss(sec), "ad_timeout": True, "ad_seen": True,
            "ad_skipped": ads.get("ad_skipped", False), "ad_log": log.events,
            "link": f"https://www.youtube.com/watch?v={vid}&t={int(sec)}s",
            "warning": (f"An ad was still showing after {int(AD_TIMEOUT)} s, so no frame was captured (the player is "
                        "never paused or seeked during an ad). Skip this frame and note it; don't reload and retry "
                        "more than once.")}


def _capture(full_id: str, vid: str, sec: float, out: str | None, out_dir: str | None,
             burst: float = 0, full: bool = False, used: set | None = None, score: bool = False) -> dict:
    used = used if used is not None else set()
    log = _AdLog()
    t_start = time.time()
    ads = {"ad_seen": False, "ad_skipped": False, "ad_still_playing": False}

    def clear():
        r = _clear_ads(full_id, log=log)
        ads["ad_seen"] |= r["ad_seen"]
        ads["ad_skipped"] |= r["ad_skipped"]
        ads["ad_still_playing"] = r["ad_still_playing"]
        return not r["ad_still_playing"]

    if not clear():
        return _ad_timeout_result(sec, vid, ads, log)
    theater = _ensure_theater(full_id)
    final = None
    for attempt in range(3):
        _ensure_paused(full_id)
        st = _state(full_id)
        if abs((st.get("t") or 0) - sec) > 2.6:
            _seek_by_click(full_id, sec)
        if not clear():  # a jump can trigger a mid-roll ad, which resumes playback afterwards
            return _ad_timeout_result(sec, vid, ads, log)
        _ensure_paused(full_id)
        if abs((_state(full_id).get("t") or 0) - sec) > 2.6 and attempt < 2:
            continue
        buffering = _wait_buffered(full_id)
        # Move the pointer off the player so its controls fade out (the video stays paused).
        _pointer_off_player(full_id, _state(full_id))
        _wait_controls_hidden(full_id)
        final = _state(full_id)  # re-checked right before the capture (Y2)
        if not final.get("ad"):
            break
        if not clear():
            return _ad_timeout_result(sec, vid, ads, log)
        final = None
    if final is None:
        final = _state(full_id)
    actual = final.get("t") or 0
    clip = _player_clip(full_id, final)
    path = _img_path(out, out_dir, f"{date.today().isoformat()}_{vid}_at{_stamp(actual)}", full, used)
    SITE.screenshot(full_id, path, clip=clip, max_width=None if full else IMG_WIDTH)
    width = clip["width"]
    c = SITE.claims().get(full_id) or {}
    largest = c.get("max_player_w")
    if not largest or width > largest:
        _claim_set(full_id, max_player_w=width)
    result = {
        "path": str(path),
        "requested": _mmss(sec), "actual": _mmss(actual), "sec": int(actual), "landing_error_s": round(actual - sec, 1),
        "link": f"https://www.youtube.com/watch?v={vid}&t={int(actual)}s",
        "ad_seen": ads["ad_seen"], "ad_skipped": ads["ad_skipped"], "ad_timeout": False,
        "ad_in_frame": bool(final.get("ad")), "ad_checked_at": final.get("checked_at"),
        "buffering": buffering, "paused": bool(final.get("paused")), "theater": theater,
        "controls_visible": not final.get("autohide"),
        "player_css_px": [round(clip["width"]), round(clip["height"])],
        "check": "Look at the image before using it: it must show the expected content, not an ad, "
                 "sponsor segment, b-roll, a buffering spinner or a transition.",
    }
    if log.events:
        result["ad_log"] = log.events
    if score:
        result["graphic"] = _graphic(full_id, clip)
    warnings = []
    if result["ad_in_frame"]:
        warnings.append("An ad was showing when the frame was captured; this image shows the ad.")
    if buffering:
        warnings.append("The video was still buffering; the image may show a spinner or a blurry frame.")
    if abs(result["landing_error_s"]) > 3.5:
        warnings.append(f"Landed {result['landing_error_s']:+} s from the requested time; "
                        "cite `actual`/`link`, or retry.")
    pw = S.player_warning(width, theater, largest)
    if pw:
        warnings.append(pw)
    if warnings:
        result["warning"] = " ".join(warnings)
    if burst > 0 and not result["ad_in_frame"]:
        result["candidates"] = _burst(full_id, vid, burst, out_dir, full, used, log, score=score)
        result["check"] = ("`path` is the paused frame at the landing time; `candidates` were captured while it "
                           "played on. Look at them, keep the best, and delete the rest.")
    result["elapsed_s"] = round(time.time() - t_start, 1)
    return result


def _scan_points(lo: float, hi: float, samples: int) -> list[float]:
    span = hi - lo
    if span <= 20:
        return [lo]
    n = max(1, min(samples, 6))
    return [lo + span * (i + 0.5) / n for i in range(n)]


def scan(target: str, rng: str, samples: int = 4, keep: int = 3, out_dir: str | None = None,
         full: bool = False) -> dict:
    """Y16: sample a time range with a few short bursts and rank the frames by graphic-ness (charts,
    tables, slides, UI score high; talking heads low). Keeps the best `keep` images, deletes the rest."""
    m = re.fullmatch(r"\s*([\d:.]+)\s*-\s*([\d:.]+)\s*", rng or "")
    if not m:
        raise Fail("--scan takes a range like 12:00-19:00")
    lo, hi = _seconds(m.group(1)), _seconds(m.group(2))
    if hi <= lo:
        raise Fail("--scan range must end after it starts")
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    vid = _require_watch(full_id)
    SITE.touch(full_id)
    SITE.scroll_to_top(full_id)
    points = _scan_points(lo, hi, samples)
    burst = min(15.0, hi - lo) if len(points) == 1 else 3.0
    used: set = set()
    shots, notes = [], []
    for p in points:
        r = _capture(full_id, vid, p, None, out_dir, burst=burst, full=full, used=used, score=True)
        if r.get("ad_timeout"):
            notes.append(f"{_mmss(p)}: ad_timeout")
            continue
        if not r.get("ad_in_frame"):
            shots.append({k: r.get(k) for k in ("path", "actual", "sec", "link", "graphic")})
        shots += [{"path": c["path"], "actual": c["at"], "sec": c["sec"], "link": c["link"], "graphic": c.get("graphic")}
                  for c in r.get("candidates", [])]
    ranked = sorted(shots, key=lambda s: -((s.get("graphic") or {}).get("score") or 0))
    kept, dropped = ranked[:max(1, keep)], ranked[max(1, keep):]
    for s in dropped:
        try:
            Path(s["path"]).unlink()
        except OSError:
            pass
    return {"range": [_mmss(lo), _mmss(hi)], "points": [_mmss(p) for p in points], "frames": kept,
            "sampled": len(shots), "deleted": len(dropped), **({"notes": notes} if notes else {}),
            "check": ("Frames are ranked by `graphic.score` (edge density, flat colors, little skin tone): a hint, "
                      "not a judgment. Look at each kept image before using it.")}


def frame(target: str, at, out: str | None = None, out_dir: str | None = None,
          burst: float = 0, full: bool = False):
    """Jump to `at` (one timestamp, or a list), wait out ads and buffering, capture the player.
    Returns one result, or a list for several timestamps."""
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    vid = _require_watch(full_id)
    SITE.touch(full_id)
    times = at if isinstance(at, (list, tuple)) else [at]
    if out and len(times) > 1:
        raise Fail("--out names one file; use --out-dir for several timestamps.")
    SITE.scroll_to_top(full_id)
    used: set = set()
    results = []
    for i, t in enumerate(times):
        results.append(_capture(full_id, vid, _seconds(t), out, out_dir, min(burst, 15), full, used))
        if i < len(times) - 1 and not burst:
            _resume_briefly(full_id)
    return results if isinstance(at, (list, tuple)) else results[0]


def shot(target: str, out: str | None = None, out_dir: str | None = None, full: bool = False,
         selector: str | None = None) -> dict:
    """Screenshot of the page content visible in the window (any page), or of one element
    (`selector`, wheel-scrolled into view first). The top bar and the left guide are left out.
    `ads_in_view` lists ad units in the image (search "Sponsored" units, masthead, a video ad)."""
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    vid = _video_id(full_id)
    url = SITE.evaluate(full_id, "location.href") or ""
    slug = vid or (re.search(r"/(@[\w.-]+|results)", url) or [None, "page"])[1].lstrip("@") or "page"
    path = _img_path(out, out_dir, f"{date.today().isoformat()}_{slug}_shot-{time.strftime('%H%M%S')}", full)
    res = SITE.capture(full_id, path, selector=selector, max_width=None if full else IMG_WIDTH)
    st = _state(full_id)
    if st.get("ad") and st.get("player"):
        view = {**res["clip"]}
        sx, sy = SITE.evaluate(full_id, "[scrollX, scrollY]") or [0, 0]
        view.update(x=view["x"] - sx, y=view["y"] - sy)
        if intersect(st["player"], view):
            res["ads_in_view"].append({"selector": "#movie_player.ad-showing", "label": "video ad playing in the player",
                                       "rect": st["player"]})
    res["ads_in_view"] = S.outermost_ads(res["ads_in_view"])
    res.update(url=url, check="Look at the image before using it.")
    if res["ads_in_view"]:
        res["warning"] = ("The image includes ads (`ads_in_view`). Caption them as ads, or scroll and retake.")
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
    full_id = SITE.resolve(target)
    SITE.ensure_visible(full_id)  # fail fast in a hidden window
    _ensure_quiet(full_id)
    ch = SITE.evaluate(full_id, _js("channel.js"))
    if ch:
        lst, why = S.likely_ai_listicle("", ch.get("name"), ch.get("handle"), ch.get("videos"))
        if lst:
            ch["likely_ai_listicle"], ch["listicle_reasons"] = True, why
            _seen_add(full_id, [], {ch.get("name"): {"listicle": True}})
    return {"page": page, "channel": ch}


def tally(target: str, terms: list[str], aliases: str | dict | None = None, include_transcripts: bool = True) -> dict:
    """For each candidate name: channels and independent channels mentioning it among everything this
    window has seen (search results merged with loaded videos: titles, descriptions, links and, when read,
    transcripts). Whole-name matching (longest name wins), aliases and built-in caption garbles, a snippet
    and link per example. A channel is not independent for a name when a video of it mentioning the name
    scores >= 0.6 sponsor bias (in a multi-brand video: that brand's score), or it is strongly brand-owned,
    or a likely AI listicle / Amazon-influencer channel. No page loads."""
    full_id = SITE.resolve(target)
    seen = _seen_load(full_id)
    recs = seen["items"]
    flags = {ch: {"listicle": True} for ch, f in seen["channels"].items() if f.get("listicle")}
    rows = []
    for term in terms:
        def indep(r, term=term):
            return T.default_independence({**r, "sponsor_score": S.score_for_term(r, term)}, 0.6)
        out = T.tally(recs, [term], aliases=aliases, group="channel", group_label="channels", separate_search=False,
                      include_transcripts=include_transcripts, independence=indep, group_flags=flags)
        for row in out["tally"]:
            row["videos"] = row.pop("threads")
            for k in ("raw_upvotes", "recommend_upvotes", "complain_upvotes"):
                row.pop(k, None)
            row["by_channel"] = row.pop("by_group")
            rows.append(row)
    rows.sort(key=lambda r: (-r["independent_channels"], -r["items"]))
    merged = T.merge_records(recs)
    return {"seen_items": len(merged), "loaded_videos": sum(1 for r in merged if r.get("via") == "video"),
            "transcripts_included": sum(1 for r in merged if r.get("segments")) if include_transcripts else 0,
            "tally": rows,
            "note": ("Counts come only from what this window has seen (search results, loaded videos and read "
                     "transcripts); search more to widen the base. In multi-brand videos, put the brand in the name "
                     "(\"Bambu P2S\") or add an alias so the right brand's sponsor score applies.")}


def close(target: str) -> dict:
    full_id = SITE.resolve(target)
    res = SITE.close(target)
    _seen_path(full_id).unlink(missing_ok=True)
    return res


def list_claims() -> list[dict]:
    return SITE.list_claims()


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def _dispatch(cmd: str, args: list[str]):
    if cmd == "search":
        sort, upload, shorts = opt(args, "--sort", "relevance"), opt(args, "--upload"), bool(opt(args, "--shorts", False))
        typed = not opt(args, "--url", False)
        if len(args) < 2:
            raise Fail("usage: search ID QUERY [--sort relevance|views] [--upload hour|today|week|month|year] [--shorts]")
        return search(args[0], " ".join(args[1:]), sort, upload, shorts, typed)
    if cmd == "video" and len(args) == 2:
        return video(*args)
    if cmd == "tally":
        aliases = []
        while "--alias" in args:
            aliases.append(str(opt(args, "--alias")))
        no_tr = bool(opt(args, "--no-transcripts", False))
        if len(args) < 2:
            raise Fail('usage: tally ID NAME [NAME ...] [--alias "Name=Alt,Alt2; Other=..."] [--no-transcripts]')
        return tally(args[0], args[1:], "; ".join(aliases) or None, not no_tr)
    if cmd == "transcript":
        ctx = int(opt(args, "--context", 0))
        frm, to, out_dir = opt(args, "--from"), opt(args, "--to"), opt(args, "--out-dir")
        terms: list[str] = []
        if "--grep" in args:
            i = args.index("--grep")
            terms, args = args[i + 1:], args[:i]
        if len(args) != 1:
            raise Fail("usage: transcript ID [--from T] [--to T] [--context N] [--out-dir DIR] [--grep TERM ...]")
        return transcript(args[0], terms or None, ctx, frm, to, out_dir)
    if cmd == "comments":
        n = int(opt(args, "--max", 20))
        if len(args) != 1:
            raise Fail("usage: comments ID [--max N]")
        return comments(args[0], n)
    if cmd == "frame":
        out, out_dir = opt(args, "--out"), opt(args, "--out-dir")
        burst, full = float(opt(args, "--burst", 0)), bool(opt(args, "--full", False))
        rng = opt(args, "--scan")
        samples, keep = int(opt(args, "--samples", 4)), int(opt(args, "--keep", 3))
        if rng:
            if len(args) != 1:
                raise Fail("usage: frame ID --scan FROM-TO [--samples N] [--keep K] [--out-dir DIR]")
            return scan(args[0], str(rng), samples, keep, out_dir, full)
        if len(args) < 2:
            raise Fail("usage: frame ID AT [AT ...] [--burst SECONDS] [--full] [--out-dir DIR | --out PATH]")
        return frame(args[0], args[1] if len(args) == 2 else args[1:], out, out_dir, burst, full)
    if cmd == "shot":
        out, out_dir, full = opt(args, "--out"), opt(args, "--out-dir"), bool(opt(args, "--full", False))
        selector = opt(args, "--selector")
        if len(args) != 1:
            raise Fail("usage: shot ID [--selector CSS] [--full] [--out-dir DIR | --out PATH]")
        return shot(args[0], out, out_dir, full, selector)
    if cmd == "channel" and len(args) == 2:
        return channel(*args)
    if cmd == "close" and len(args) == 1:
        return close(args[0])
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
