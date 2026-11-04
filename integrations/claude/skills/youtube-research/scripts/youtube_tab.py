#!/usr/bin/env python3
"""Polite, read-only YouTube research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
login but never each other's tabs; all page loads share one pacing lock.
Every action is one a person would take: real mouse clicks and wheel scrolling,
the player's own mute, theater, play/pause buttons and progress bar.
Scripts only read the page.

CLI (each command prints JSON):

    youtube_tab.py open [--wait [SECONDS]]                 claim a window -> {"target": ID}
    youtube_tab.py search ID QUERY [--sort relevance|views] [--upload hour|today|week|month|year] [--shorts]
                       (`open` and `list` cost no page load; search, video, channel and nav cost one each)
    youtube_tab.py video ID URL                            metadata, disclosure signals, chapters, description
    youtube_tab.py transcript ID [--context N] [--grep TERM ...]   segments with timestamped links
                       (--grep: case-insensitive plain-text substrings, any of them; not regex)
    youtube_tab.py comments ID [--max N]                   top comments of the loaded video (default 20)
    youtube_tab.py frame ID AT [AT ...] [--burst SECONDS] [--full] [--out-dir DIR | --out PATH]
                       theater mode, pause, jump to each AT (e.g. 6:17), capture the player
                       (within about +/-2.5 s; clicks can snap to a chapter start). --burst N then
                       lets it play N s (max 15), capturing a candidate about every second.
                       Images are ~1600 px JPEGs; --full saves full-resolution PNGs.
    youtube_tab.py shot ID [--full] [--out-dir DIR | --out PATH]   visible page content (top bar and
                       account sidebar excluded), any page
    youtube_tab.py channel ID URL|@handle                  channel header + recent uploads
    youtube_tab.py nav ID URL | eval ID JS|@file.js        escape hatches (paced load / run JS)
    youtube_tab.py close ID | list | gc                    release window / show / clean up claims

transcript, comments and frame work on the video loaded by `video`; shot works on any page.
None of them count as page loads. `video` accepts watch, youtu.be and shorts links. The same functions back the youtube_* MCP tools.

Environment (YOUTUBE_TAB_*): INSTANCE (youtube-research-01), MAX (3 windows),
GAP (6 s between loads, all agents), IDLE (1800 s), MAX_LOADS (45 per window: a hard
stop just above the skill's 40-load budget),
STATE (/tmp/chrome-agent-youtube), SHOTS (STATE/shots). Needs `chrome-agent` on PATH.
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
from research_core import Fail, Site, common_cli, opt, pause, run_cli  # noqa: E402

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
    default_max_loads=45,
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


def _remember(full_id: str, data: dict) -> None:
    c = SITE.claims().get(full_id)
    if c is not None:
        c["video"] = data
        SITE._write_claim(full_id, c)


def _recall(full_id: str, vid: str) -> dict | None:
    v = (SITE.claims().get(full_id) or {}).get("video")
    return v if v and v.get("video_id") == vid else None


def _stamp(sec: float) -> str:
    sec = int(sec)
    h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h{m:02d}m{s_:02d}s" if h else f"{m:02d}m{s_:02d}s"


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
  return { ad, skip, t: v ? v.currentTime : null, duration: v ? v.duration : null, muted: v ? v.muted : null, paused: v ? v.paused : null,
           player: pr ? {x: pr.x, y: pr.y, width: pr.width, height: pr.height} : null }; })()"""


def _state(full_id: str) -> dict:
    return SITE.evaluate(full_id, _PLAYER_STATE_JS) or {}


def _hover_player(full_id: str, st: dict) -> None:
    """Move the pointer over the player so its controls show."""
    pl = st.get("player")
    if pl:
        SITE.move(full_id, pl["x"] + pl["width"] * 0.55, pl["y"] + pl["height"] * 0.5)
        pause(0.4)


def _ensure_muted(full_id: str) -> bool:
    """If the video has sound, click the player's mute button (YouTube remembers it)."""
    st = _state(full_id)
    if st.get("muted") is None or st.get("muted"):
        return bool(st.get("muted"))
    _hover_player(full_id, st)
    r = SITE.rect(full_id, ".ytp-mute-button")
    if r:
        SITE.click(full_id, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
        pause(0.4)
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
            pause(random.uniform(1, 4), fuzz=0)  # people don't hit Skip the instant it appears
            st = _state(full_id)
            if not st.get("ad") or not st.get("skip"):
                continue
            SITE.move(full_id, st["skip"][0] - 4, st["skip"][1] - 3)
            pause(0.2)
            SITE.click(full_id, *st["skip"])
            skipped = True
            pause(1.5)
        else:
            pause(1)
    still = bool(_state(full_id).get("ad"))
    if not still:
        _ensure_muted(full_id)
    return {"ad_seen": seen, "ad_skipped": skipped, "ad_still_playing": still}


_BUFFERING_JS = """(() => { const p = document.querySelector('#movie_player'), v = document.querySelector('video');
  return (!!p && p.classList.contains('buffering-mode')) || (!!v && v.readyState < 2); })()"""


def _wait_buffered(full_id: str, timeout: float = 10) -> bool:
    """Wait until the frame is decoded (no spinner). Returns True if still buffering."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not SITE.evaluate(full_id, _BUFFERING_JS):
            return False
        pause(0.5)
    return bool(SITE.evaluate(full_id, _BUFFERING_JS))


def _ensure_theater(full_id: str) -> bool:
    """Switch to theater mode with the player's own button (YouTube remembers it) for a larger frame."""
    is_theater = "!!document.querySelector('ytd-watch-flexy[theater]')"
    if SITE.evaluate(full_id, is_theater):
        return True
    st = _state(full_id)
    _hover_player(full_id, st)
    r = SITE.rect(full_id, ".ytp-size-button")
    if not r:
        return False
    SITE.click(full_id, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    SITE.wait_for(full_id, is_theater, timeout=5, settle=1.0)
    return bool(SITE.evaluate(full_id, is_theater))


def _ensure_paused(full_id: str) -> bool:
    """Pause with the player's play/pause button, as a person would before studying a frame."""
    st = _state(full_id)
    if st.get("ad") or st.get("paused") is None or st.get("paused"):
        return bool(st.get("paused"))
    _hover_player(full_id, st)
    r = SITE.rect(full_id, ".ytp-play-button")
    if r:
        SITE.click(full_id, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
        pause(0.5)
    return bool(_state(full_id).get("paused"))


def _resume_briefly(full_id: str) -> None:
    """Play for a couple of seconds between captures, as a person would before the next jump."""
    st = _state(full_id)
    if st.get("paused") and not st.get("ad"):
        _hover_player(full_id, st)
        r = SITE.rect(full_id, ".ytp-play-button")
        if r:
            SITE.click(full_id, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
            pause(random.uniform(2, 4), fuzz=0)


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
    aim, t = sec, None
    for _ in range(2):
        x = bar["x"] + bar["width"] * min(max(aim / dur, 0), 0.999)
        y = bar["y"] + bar["height"] / 2
        SITE.move(full_id, x, y)
        pause(0.25)
        SITE.click(full_id, x, y)
        pause(1.2)
        t = _state(full_id).get("t") or 0
        if abs(t - sec) <= max(1.0, 1.5 * dur / bar["width"]):
            return t
        if t == aim or abs((aim - (t - sec)) - aim) < 0.5:
            break
        aim = aim - (t - sec)  # chapter gaps make the bar slightly non-linear; correct once
    # Clicks can snap to a nearby chapter start. Finish with the arrow keys (5 s per press),
    # which is how a person fine-tunes a position.
    presses = round((sec - (t or 0)) / 5)
    key = "ArrowRight" if presses > 0 else "ArrowLeft"
    for _ in range(min(abs(presses), 24)):
        for typ in ("keyDown", "keyUp"):
            SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": typ, "key": key, "code": key,
                                                        "windowsVirtualKeyCode": 39 if key == "ArrowRight" else 37})
        pause(0.35)
    pause(0.6)
    return _state(full_id).get("t")


_SEG = "transcript-segment-view-model, ytd-transcript-segment-renderer"
_TRANSCRIPT_ERR_JS = ("Math.max(0, ...performance.getEntriesByType('resource')"
                      ".filter(e => e.name.includes('/get_transcript')).map(e => e.responseStatus || 0))")


def _open_transcript(full_id: str) -> tuple[bool, str | None]:
    """Open the transcript with real clicks. Returns (ok, reason-if-not)."""
    if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_SEG)})"):
        return True, None
    btn = "ytd-video-description-transcript-section-renderer button"
    if not SITE.rect(full_id, btn):
        SITE.click_on(full_id, "#description-inline-expander #expand, tp-yt-paper-button#expand")
        pause(0.8)
    if not SITE.click_on(full_id, btn):
        return False, "no_transcript: this video has no 'Show transcript' button."
    deadline = time.time() + 15
    while time.time() < deadline:
        if SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_SEG)})"):
            return True, None
        status = SITE.evaluate(full_id, _TRANSCRIPT_ERR_JS) or 0
        if status >= 400:
            return False, (f"youtube_error: YouTube's transcript service returned HTTP {status} for this video, "
                           "so the panel never loads (a person would see the same endless spinner). "
                           "Use chapters, the description, comments and frames instead.")
        pause(1)
    return False, "timeout: the transcript panel did not load within 15 s."


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


# Signals of sponsorship or sponsor influence. Each category counts once (the strongest hit wins),
# whether it was found in the description, the title or the transcript.
_SIGNALS = [
    # (category, weight, pattern)
    ("sponsor_read", 0.5, r"\b(this (video|episode) is sponsored by|sponsored by|thanks? (you )?to \S+( \S+)? for sponsoring|today'?s sponsor|in (paid )?partnership with|paid partnership|#ad\b|#sponsored|brought to you by)"),
    ("review_unit", 0.25, r"\b(sent (it |this |them |these )?(to )?(me|us)|provided (by|to me)|loaned|on loan|review (unit|sample)|gifted|early access|under embargo|embargo (lift|ends))"),
    ("use_my_link_or_code", 0.15, r"\b(use my (link|code)|use (the )?code \S+|code \S+ (at checkout|for \d+ ?%)|discount code|promo code|\d+ ?% off (with|using)|(buy|get|order|grab|pick up) (one|yours|it|them|a pair)? ?(through|using|with|from|via) (my|the) (link|affiliate))"),
    ("affiliate_disclosure", 0.1, r"(\baffiliate links?\b|\(affiliate\)|\bi (may )?earn (a )?(small )?commission|commission (from|on) (any|qualifying) purchases|as an amazon associate)"),
    ("bought_it_myself", -0.15, r"\b(i (bought|purchased|paid for) (it|this|them|these|my own|.{0,20}with my own money)|with my own money|bought (it|them|this|these) myself|i bought)"),
    ("claims_not_sponsored", -0.1, r"\b(not (sponsored|paid|gifted)|no(body| one) (paid|sponsored)|un-?sponsored|isn'?t sponsored|wasn'?t (sponsored|paid))"),
    ("editorial_independence", -0.05, r"\b((did ?n'?o?t|do ?n'?o?t|never|no) (see|review|approve|preview|get to see|have (any )?(input|say|editorial))( the video| this video| it)?( before| ahead| in advance)?|no editorial control|without (their|any) (input|review|approval))"),
]
_COMPILED = [(c, w, re.compile(p, re.I)) for c, w, p in _SIGNALS]
_GUIDE = ("Estimate from the page and transcript. Set 1.0 for brand-owned channels; raise it for channels mostly "
          "covering one brand; report the final score with reasons.")


_SPONSOR_NAME = re.compile(r"(?i:sponsored by|thanks? (?:you )?to|brought to you by|in (?:paid )?partnership with|today'?s sponsor(?: is)?)\s+([A-Z][\w&.+-]*(?:\s+[A-Z][\w&.+-]*){0,2})")


def _scan_text(text: str, source: str, link: str | None = None, subject: str = "") -> list[dict]:
    """Find disclosure signals. A sponsor read for a brand that isn't the video's subject is
    recorded as a weaker third-party sponsorship."""
    hits = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        for cat, w, pat in _COMPILED:
            m = pat.search(sent)
            if not m:
                continue
            # A negated sponsorship line is a claim of independence, not a sponsor read.
            if cat in ("sponsor_read", "review_unit") and re.search(r"\b(not|no|never|un-?)\s*(sponsor|gift|paid|provid|sent)", sent, re.I):
                continue
            hit = {"signal": cat, "weight": w, "source": source, "text": sent.strip()[:220], **({"link": link} if link else {})}
            if cat == "sponsor_read":
                nm = _SPONSOR_NAME.search(sent)
                name = nm.group(1).strip().rstrip(".,!;:") if nm else None
                hit["sponsor"] = name
                if name and subject and not any(tok.lower() in subject.lower() for tok in re.findall(r"[A-Za-z0-9]{3,}", name)):
                    hit.update(signal="third_party_sponsor", weight=0.15)
            hits.append(hit)
    return hits


def _score(page: list[dict], extra: list[dict]) -> dict:
    """Combine evidence: each category counts once, with its first (strongest-source) hit kept."""
    score, kept, seen = 0.1, [], set()
    for e in page + extra:
        if e["signal"] in seen:
            continue
        seen.add(e["signal"])
        score += e["weight"]
        kept.append(e)
    score = round(min(1.0, max(0.0, score)), 2)
    return {"score": score, "level": "high" if score >= 0.6 else "medium" if score >= 0.3 else "low",
            "evidence": kept, "note": _GUIDE}


def sponsor_bias(data: dict, transcript_hits: list[dict] | None = None) -> dict:
    """0.0-1.0 likelihood that a video is sponsored or sponsor-influenced, with its evidence."""
    d = data.get("disclosure") or {}
    page: list[dict] = []
    if d.get("paid_promotion_label"):
        page.append({"signal": "paid_promotion_label", "weight": 0.6, "source": "player", "text": d.get("paid_promotion_text")})
    subject = " ".join([data.get("title") or ""] + list(data.get("keywords") or [])[:8])
    page += _scan_text(data.get("title", ""), "title", subject=subject)
    page += _scan_text(data.get("description", ""), "description", subject=subject)
    if d.get("affiliate_links"):
        page.append({"signal": "affiliate_links", "weight": 0.15, "source": "description", "text": ", ".join(d["affiliate_links"][:5])})
    if d.get("products_earning_commission"):
        page.append({"signal": "shopping_commission", "weight": 0.15, "source": "product shelf",
                     "text": f"{d['products_earning_commission']} tagged product(s) marked 'Earns commission'"})
    if d.get("campaign_links"):
        page.append({"signal": "campaign_tracking", "weight": 0.2, "source": "description", "text": ", ".join(d["campaign_links"][:3])})
    res = _score(page, transcript_hits or [])
    res["transcript_scanned"] = transcript_hits is not None
    if transcript_hits is None:
        res["confidence"] = "low"
        res["confidence_note"] = ("Page only: the transcript hasn't been scanned (or couldn't be), so spoken "
                                  "sponsor reads and review-unit mentions are unknown. A low score here is not a clean bill.")
    else:
        res["confidence"] = "medium"
    return res


def video(target: str, url: str) -> dict:
    m = re.match(r"https?://(?:www\.)?youtu\.be/([\w-]{11})", url)
    if m:
        url = f"https://www.youtube.com/watch?v={m.group(1)}"
    elif not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={url}"
    page = SITE.nav(target, url, ready_js=(
        "document.readyState === 'complete' && !!window.ytInitialPlayerResponse && "
        "!!document.querySelector('#movie_player') && !!document.querySelector('ytd-watch-metadata #title h1')"))
    if "stop" in page:
        return {"page": page, "video": None}
    full_id = SITE.resolve(target)
    data = SITE.evaluate(full_id, _js("video.js"))
    if data:
        data["sponsor_bias"] = sponsor_bias(data)
        _remember(full_id, {"video_id": data.get("video_id"), "title": data.get("title"), "keywords": data.get("keywords"),
                            "description": data.get("description"), "disclosure": data.get("disclosure")})
    st = _state(full_id)
    muted = _ensure_muted(full_id) if not st.get("ad") else st.get("muted")
    # Let it play (muted) for a few seconds, as a person settling in would, before doing anything else.
    pause(random.uniform(3, 7), fuzz=0)
    return {"page": page, "video": data, "ad_playing": bool(st.get("ad")), "muted": muted}


def transcript(target: str, grep: list[str] | None = None, context: int = 0) -> dict:
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    vid = _require_watch(full_id)
    ok, reason = _open_transcript(full_id)
    if not ok:
        SITE.scroll_to_top(full_id)
        res = {"available": False, "segments": [], "reason": reason}
        page = _recall(full_id, vid)
        if page:
            res["sponsor_bias"] = sponsor_bias(page)  # page-only, flagged low confidence
        return res
    res = SITE.evaluate(full_id, _js("transcript.js")) or {"available": False, "segments": []}
    SITE.scroll_to_top(full_id)
    for seg in res["segments"]:
        if seg.get("sec") is not None:
            seg["link"] = f"https://www.youtube.com/watch?v={vid}&t={seg['sec']}s"
    segs = res["segments"]
    # Disclosure mentions anywhere in the transcript (independent of --grep), and the updated bias.
    page = _recall(full_id, vid)
    subject = " ".join([(page or {}).get("title") or ""] + list((page or {}).get("keywords") or [])[:8])
    mentions = [h for sg in segs for h in _scan_text(sg["text"], f"transcript {sg['at']}", sg.get("link"), subject)]
    res["disclosure_mentions"] = mentions
    if page:
        res["sponsor_bias"] = sponsor_bias(page, mentions)
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
        res = {**res, "matched_segments": len(hit_idx), "returned_segments": len(out),
               "total_segments": len(segs), "segments": out}
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


IMG_WIDTH = int(os.environ.get("YOUTUBE_TAB_IMG_WIDTH", "1600"))


def _img_path(out: str | None, out_dir: str | None, stem: str, full: bool) -> Path:
    if out:
        return Path(out)
    return Path(out_dir or SHOTS) / f"{stem}.{'png' if full else 'jpg'}"


def _burst(full_id: str, vid: str, seconds: float, out_dir: str | None, full: bool, rect: dict) -> list[dict]:
    """Let the video play (muted) for `seconds`, capturing a candidate about every second.
    Started with the keyboard so the pointer stays away and the controls stay hidden."""
    for typ in ("keyDown", "keyUp"):
        SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": typ, "key": "k", "code": "KeyK", "windowsVirtualKeyCode": 75})
    pause(0.8)  # let the play icon fade
    out, end = [], time.time() + seconds
    while time.time() < end:
        st = _state(full_id)
        t = st.get("t") or 0
        if not st.get("ad"):
            path = _img_path(None, out_dir, f"{date.today().isoformat()}_{vid}_at{_stamp(t)}", full)
            if not any(c["path"] == str(path) for c in out):
                SITE.screenshot(full_id, path, clip=st.get("player") or rect, max_width=None if full else IMG_WIDTH)
                out.append({"path": str(path), "at": _mmss(t),
                            "link": f"https://www.youtube.com/watch?v={vid}&t={int(t)}s"})
        pause(1.0)
    return out


def _capture(full_id: str, vid: str, sec: float, out: str | None, out_dir: str | None,
             burst: float = 0, full: bool = False) -> dict:
    before = _clear_ads(full_id)
    theater = _ensure_theater(full_id)
    _ensure_paused(full_id)
    _seek_by_click(full_id, sec)
    after = _clear_ads(full_id)  # a jump can trigger a mid-roll ad, which resumes playback
    for _ in range(2):
        if after["ad_still_playing"]:
            break
        _ensure_paused(full_id)
        t = _state(full_id).get("t") or 0
        if abs(t - sec) <= 2.6:
            break
        _seek_by_click(full_id, sec)
        again = _clear_ads(full_id)
        after = {"ad_seen": after["ad_seen"] or again["ad_seen"],
                 "ad_skipped": after["ad_skipped"] or again["ad_skipped"],
                 "ad_still_playing": again["ad_still_playing"]}
    buffering = _wait_buffered(full_id)
    # Move the pointer off the player so its controls fade out (the video stays paused).
    SITE.move(full_id, 5, 5)
    pause(2.5)
    final = _state(full_id)
    actual = final.get("t") or 0
    rect = final.get("player") or SITE.rect(full_id, "#movie_player")
    path = _img_path(out, out_dir, f"{date.today().isoformat()}_{vid}_at{_stamp(actual)}", full)
    SITE.screenshot(full_id, path, clip=rect, max_width=None if full else IMG_WIDTH)
    result = {
        "path": str(path.expanduser()),
        "requested": _mmss(sec), "actual": _mmss(actual), "landing_error_s": round(actual - sec, 1),
        "link": f"https://www.youtube.com/watch?v={vid}&t={int(actual)}s",
        "ad_seen": before["ad_seen"] or after["ad_seen"],
        "ad_skipped": before["ad_skipped"] or after["ad_skipped"],
        "ad_in_frame": bool(final.get("ad")),
        "buffering": buffering,
        "paused": bool(final.get("paused")),
        "theater": theater,
        "player_css_px": [round(rect["width"]), round(rect["height"])],
        "check": "Look at the image before using it: it must show the expected content, not an ad, "
                 "sponsor segment, b-roll, a buffering spinner or a transition.",
    }
    warnings = []
    if result["ad_in_frame"]:
        warnings.append("An ad was still playing when the frame was captured; this image shows the ad.")
    if buffering:
        warnings.append("The video was still buffering; the image may show a spinner or a blurry frame.")
    if abs(result["landing_error_s"]) > 3.5:
        warnings.append(f"Landed {result['landing_error_s']:+} s from the requested time; "
                        "cite `actual`/`link`, or retry.")
    if rect["width"] < MIN_PLAYER_WIDTH:
        warnings.append(f"The player is only {round(rect['width'])} px wide, so details may be hard to read. "
                        "Tell the user the research window is small (a window manager may be tiling it).")
    if warnings:
        result["warning"] = " ".join(warnings)
    if burst > 0 and not result["ad_in_frame"]:
        result["candidates"] = _burst(full_id, vid, burst, out_dir, full, rect)
        result["check"] = ("`path` is the paused frame at the landing time; `candidates` were captured while it "
                           "played on. Look at them, keep the best, and delete the rest.")
    return result


def frame(target: str, at, out: str | None = None, out_dir: str | None = None,
          burst: float = 0, full: bool = False):
    """Jump to `at` (one timestamp, or a list), wait out ads and buffering, capture the player.
    Returns one result, or a list for several timestamps."""
    full_id = SITE.resolve(target)
    vid = _require_watch(full_id)
    SITE.touch(full_id)
    times = at if isinstance(at, (list, tuple)) else [at]
    if out and len(times) > 1:
        raise Fail("--out names one file; use --out-dir for several timestamps.")
    SITE.scroll_to_top(full_id)
    results = []
    for i, t in enumerate(times):
        results.append(_capture(full_id, vid, _seconds(t), out, out_dir, min(burst, 15), full))
        if i < len(times) - 1 and not burst:
            _resume_briefly(full_id)
    return results if isinstance(at, (list, tuple)) else results[0]


_CONTENT_RECT_JS = """(() => { const vw = innerWidth, vh = innerHeight;
  const top = (document.querySelector('#masthead-container')?.getBoundingClientRect().bottom) || 0;
  const pm = document.querySelector('ytd-page-manager, #page-manager');
  const left = pm ? Math.max(0, pm.getBoundingClientRect().left) : 0;
  return {x: left, y: top, width: vw - left, height: vh - top}; })()"""


def shot(target: str, out: str | None = None, out_dir: str | None = None, full: bool = False) -> dict:
    """Screenshot of the page content visible in the window (any page). The top bar and the
    sidebar, which show the signed-in account and its subscriptions, are left out."""
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    vid = _video_id(full_id)
    url = SITE.evaluate(full_id, "location.href") or ""
    slug = vid or (re.search(r"/(@[\w.-]+|results)", url) or [None, "page"])[1].lstrip("@") or "page"
    path = _img_path(out, out_dir, f"{date.today().isoformat()}_{slug}_shot-{time.strftime('%H%M%S')}", full)
    rect = SITE.evaluate(full_id, _CONTENT_RECT_JS)
    SITE.screenshot(full_id, path, clip=rect, max_width=None if full else IMG_WIDTH)
    st = _state(full_id)
    res = {"path": str(path.expanduser()), "url": url,
           "check": "Look at the image before using it. The account's top bar and sidebar are excluded, "
                    "but a comment box or account avatar inside the page can still appear."}
    if st.get("ad"):
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
        out, out_dir = opt(args, "--out"), opt(args, "--out-dir")
        burst, full = float(opt(args, "--burst", 0)), bool(opt(args, "--full", False))
        if len(args) < 2:
            raise Fail("usage: frame ID AT [AT ...] [--burst SECONDS] [--full] [--out-dir DIR | --out PATH]")
        return frame(args[0], args[1] if len(args) == 2 else args[1:], out, out_dir, burst, full)
    if cmd == "shot":
        out, out_dir, full = opt(args, "--out"), opt(args, "--out-dir"), bool(opt(args, "--full", False))
        if len(args) != 1:
            raise Fail("usage: shot ID [--full] [--out-dir DIR | --out PATH]")
        return shot(args[0], out, out_dir, full)
    if cmd == "channel" and len(args) == 2:
        return channel(*args)
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
