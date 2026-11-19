"""Wall detection for the research helpers: the shared check (research_core.block_check_js) as Reddit and
YouTube now use it, run in Node against fake pages, plus the stop-confirmation note. No browser."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "reddit-research" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "youtube-research" / "scripts"))
import research_core as C  # noqa: E402
import reddit_tab as R  # noqa: E402
import youtube_tab as Y  # noqa: E402

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")

# A minimal DOM: title, body text, URL path, iframes, and the selectors the page-state scripts look for.
FAKE_DOM = r"""
const page = JSON.parse(process.argv[1]);
globalThis.location = {href: "https://www.example.com" + page.path, pathname: page.path, hostname: "www.example.com"};
const frames = (page.frames || []).map(f => ({src: f.src, getBoundingClientRect: () => ({width: f.w, height: f.h}), _style: {visibility: "visible", display: "block", opacity: "1"}}));
globalThis.document = {
  title: page.title,
  body: {innerText: page.text},
  querySelector: sel => (page.selectors || []).some(s => sel.includes(s)) ? {} : null,
  querySelectorAll: sel => sel === "iframe" ? frames : [],
};
globalThis.getComputedStyle = el => el._style || {visibility: "visible", display: "block", opacity: "1"};
globalThis.window = {};
console.log(JSON.stringify(eval(process.argv[2])));
"""


def run_js(js: str, *, title="Search - Reddit", text="", path="/search/", frames=(), selectors=()) -> dict:
    page = {"title": title, "text": text, "path": path, "frames": list(frames), "selectors": list(selectors)}
    out = subprocess.run([NODE, "-e", FAKE_DOM, json.dumps(page), js], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# The 2026-11-18 false stop: a normal, long results page with a post that mentions "unusual activity".
NORMAL_RESULTS = ("Home Popular News Explore " * 40 + "Posts Grok Bot game changing 4 votes 2 comments "
                  "Account Suspended Without Explanation my account was suddenly suspended for alleged unusual "
                  "activity. 29 votes 16 comments " + "People also search for Grok Bot reviews " * 80)


def test_reddit_post_text_no_longer_stops_a_run():
    st = run_js(R.PAGE_STATE_JS, text=NORMAL_RESULTS, selectors=["#expand-user-drawer-button"])
    assert st["blocked"] is False and st["loggedIn"] is True and st["url"].endswith("/search/")
    assert R._stop_reason(st, R.SITE) is None


def test_reddit_wall_phrase_on_a_short_page_still_stops():
    st = run_js(R.PAGE_STATE_JS, title="Blocked", text="whoa there, pardner! Your request has been blocked "
                "by network security.", path="/")
    assert st["blocked"] is True and "whoa there" in st["blocked_by"]
    assert "orchestrator" in R._stop_reason(st, R.SITE)


def test_shared_phrase_on_a_long_page_is_reported_not_blocked():
    st = run_js(R.PAGE_STATE_JS, text="we noticed unusual traffic " + "x" * 5000)
    assert st["blocked"] is False and st["phrase_ignored"] == "unusual traffic"


def test_visible_challenge_frame_always_stops_but_hidden_anchor_does_not():
    shown = run_js(R.PAGE_STATE_JS, text=NORMAL_RESULTS,
                   frames=[{"src": "https://www.google.com/recaptcha/api2/bframe?k=x", "w": 400, "h": 580}])
    assert shown["blocked"] is True and shown["blocked_by"].startswith("challenge frame")
    hidden = run_js(R.PAGE_STATE_JS, text=NORMAL_RESULTS,
                    frames=[{"src": "https://www.google.com/recaptcha/api2/anchor?k=x", "w": 0, "h": 0}])
    assert hidden["blocked"] is False


def test_youtube_comment_text_vs_bot_wall():
    long_watch = "Sign in to confirm you're not a bot is what I keep seeing lol " + "comment " * 800
    st = run_js(Y.PAGE_STATE_JS, title="Some video - YouTube", text=long_watch, path="/watch",
                selectors=["#avatar-btn"])
    assert st["blocked"] is False and st["loggedIn"] is True and st["gate"] is False
    wall = run_js(Y.PAGE_STATE_JS, title="YouTube", text="Sign in to confirm you're not a bot", path="/watch")
    assert wall["blocked"] is True and "not a bot" in wall["blocked_by"]


def test_shared_check_without_extras_is_unchanged():
    assert C.BLOCK_CHECK_JS == C.block_check_js()
    assert "__EXTRA__" not in C.BLOCK_CHECK_JS and "whoa there" not in C.BLOCK_CHECK_JS
    assert "|whoa there)" in C.block_check_js("whoa there") or "|whoa there/" in C.block_check_js("whoa there")


def test_stop_shot_is_copied_into_the_report_and_confirmation_requested(tmp_path, monkeypatch):
    shot = tmp_path / "logs" / "2026-11-18_193450_AAFC978F.jpg"
    shot.parent.mkdir()
    shot.write_bytes(b"jpg")
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "report" / "raw"))
    copy = C._copy_stop_shot(str(shot), "reddit")
    assert copy == str(tmp_path / "report" / "raw" / "reddit" / "shots" / shot.name) and Path(copy).read_bytes() == b"jpg"
    monkeypatch.delenv("RESEARCH_RAW_DIR")
    assert C._copy_stop_shot(str(shot), "reddit") is None


def test_record_stop_adds_confirm_for_blocks_only(tmp_path, monkeypatch):
    calls = {}
    site = R.SITE
    monkeypatch.setattr(C, "_log_on", lambda: True)
    monkeypatch.setattr(C, "LOG_DIR", tmp_path)

    def fake_shot(full_id, path, max_width=1600):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"jpg")
        return path
    monkeypatch.setattr(type(site), "screenshot", lambda self, *a, **k: fake_shot(*a, **k))
    monkeypatch.setattr(type(site), "hold", lambda self, *a, **k: calls.setdefault("hold", {"target": a[0]}))
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "raw"))

    C._LOG.stop_logged = False
    blocked = {"blocked": True, "blocked_by": "phrase 'whoa there' on a wall page (short page)", "stop": "x"}
    site.record_stop("AAFC978F21AB", blocked)
    assert blocked["confirm"]["suspected_block"] is True
    assert blocked["confirm"]["stop_shot"].startswith(str(tmp_path / "raw" / "reddit" / "shots"))
    assert "orchestrator" in blocked["confirm"]["next"]

    C._LOG.stop_logged = False
    signed_out = {"blocked": False, "loggedIn": False, "stop": "signed out"}
    site.record_stop("AAFC978F21AB", signed_out)
    assert "confirm" not in signed_out
