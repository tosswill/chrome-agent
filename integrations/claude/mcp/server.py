# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""MCP server exposing chrome-agent (and polite Reddit/YouTube research) to Claude Desktop.

General tools wrap the chrome-agent CLI. The reddit_* tools call the same
module the Claude Code reddit-research skill uses (skills/reddit-research/
scripts/reddit_tab.py), so both share one browser, one pacing lock and one
set of window claims.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "reddit-research" / "scripts"))
sys.path.insert(0, str(HERE.parent / "skills" / "youtube-research" / "scripts"))
import reddit_tab  # noqa: E402
import youtube_tab  # noqa: E402

BIN = shutil.which("chrome-agent") or str(Path.home() / ".local/bin/chrome-agent")
EVENT_DIR = Path(tempfile.gettempdir()) / "chrome-agent-mcp"
EVENT_DIR.mkdir(exist_ok=True)
_attach: dict[str, subprocess.Popen] = {}

mcp = FastMCP(
    "chrome-agent",
    instructions=(
        "Controls Chrome via the chrome-agent CLI (raw Chrome DevTools Protocol). "
        "Call `guide` once before general use. Typical flow: run(['launch']) -> "
        "run([inst, 'Page.navigate', '{\"url\": \"...\"}']) -> screenshot(inst) -> run(['stop', inst]). "
        "Use attach_start/attach_read/attach_stop for event streams.\n\n"
        "For Reddit research use ONLY the reddit_* tools (never run/screenshot on the Reddit "
        "instance without your own target): reddit_open -> reddit_search / reddit_thread -> "
        "reddit_close. They are read-only and paced. If a result contains `stop`, stop and "
        f"tell the user why. Never stop the `{reddit_tab.INSTANCE}` browser; always reddit_close your window.\n\n"
        "For YouTube research use ONLY the youtube_* tools: youtube_open -> youtube_search / youtube_video "
        "-> youtube_transcript / youtube_frame / youtube_comments / youtube_channel -> youtube_close. "
        "Look at every image youtube_frame or youtube_shot returns before using it (it may show an ad). "
        f"Never stop the `{youtube_tab.INSTANCE}` browser; always youtube_close your window."
    ),
)


def _run(args: list[str], timeout: float) -> str:
    p = subprocess.run([BIN, *args], capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL,
                       cwd=os.environ.get("CHROME_AGENT_CWD", str(Path.home())))
    out = p.stdout.strip()
    if p.returncode != 0 or p.stderr.strip():
        out += f"\n[exit {p.returncode}] {p.stderr.strip()}"
    return out[:100_000]


def _reddit(fn, *args, **kwargs) -> str:
    try:
        return json.dumps(fn(*args, **kwargs), ensure_ascii=False)
    except reddit_tab.Fail as e:  # the same Fail class backs both sites
        return json.dumps({"error": str(e)})


def _with_image(fn, *args, **kwargs):
    """Run a capture and return its JSON plus the image itself, so the model can check it."""
    try:
        res = fn(*args, **kwargs)
    except youtube_tab.Fail as e:
        return json.dumps({"error": str(e)})
    return [json.dumps(res, ensure_ascii=False), Image(path=res["path"])]


# -------------------------------------------------------------------------
# General chrome-agent tools
# -------------------------------------------------------------------------

@mcp.tool()
def guide() -> str:
    """Return the chrome-agent agent guide (AGENTS.md). Read before first general use."""
    return Path(_run(["guide", "--path"], 15).splitlines()[0]).read_text()


@mcp.tool()
def run(args: list[str], timeout: float = 60) -> str:
    """Run chrome-agent with the given argv (no shell). Examples:
    ['launch'], ['launch','--headless'], ['status'],
    ['myproj-01','Runtime.evaluate','{"expression":"document.title","returnByValue":true}'],
    ['help','myproj-01','Page.navigate'], ['stop','myproj-01']."""
    if args and args[0] == "attach":
        return "Use attach_start for attach (it is long-running)."
    return _run(args, timeout)


@mcp.tool()
def screenshot(instance: str, full_page: bool = False, target: str | None = None) -> Image:
    """Capture a PNG screenshot of the instance's (or a given tab's) current page."""
    params = {"format": "png", "captureBeyondViewport": full_page}
    args = [instance, "Page.captureScreenshot", json.dumps(params)]
    if target:
        args += ["--target-id", target]
    out = _run(args, 60)
    try:
        data = json.loads(out)["data"]
    except Exception:
        raise RuntimeError(out)
    return Image(data=base64.b64decode(data), format="png")


@mcp.tool()
def attach_start(instance: str, events: list[str], target: str | None = None) -> str:
    """Start a background attach session subscribed to events (e.g. ['Page.loadEventFired',
    'Runtime.consoleAPICalled']). Returns a session id; read with attach_read."""
    sid = f"{instance}-{int(time.time() * 1000)}"
    log = EVENT_DIR / f"{sid}.jsonl"
    args = [BIN, "attach", instance, *[e if e.startswith("+") else f"+{e}" for e in events]]
    if target:
        args += ["--target", target]
    _attach[sid] = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=open(log, "w"),
                                    stderr=subprocess.STDOUT, start_new_session=True)
    return json.dumps({"session": sid, "log": str(log)})


@mcp.tool()
def attach_read(session: str, last: int = 50) -> str:
    """Return the last N event lines from an attach session."""
    log = EVENT_DIR / f"{session}.jsonl"
    lines = log.read_text().splitlines() if log.exists() else []
    p = _attach.get(session)
    state = "running" if p and p.poll() is None else "exited"
    return f"[{state}, {len(lines)} lines]\n" + "\n".join(lines[-last:])


@mcp.tool()
def attach_stop(session: str) -> str:
    """Stop an attach session."""
    p = _attach.pop(session, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
    return "stopped"


# -------------------------------------------------------------------------
# Reddit research tools (shared implementation with the Claude Code skill)
# -------------------------------------------------------------------------

@mcp.tool()
def reddit_open(wait_seconds: float = 0) -> str:
    """Claim your own window in the shared, signed-in Reddit browser. Returns {"target": ID};
    pass that ID to every other reddit_* call. Restores the browser if it is not running.
    If all windows are busy, returns an error; retry with wait_seconds (max ~50) or later."""
    return _reddit(reddit_tab.open_window, min(wait_seconds, 50))


@mcp.tool()
def reddit_search(target: str, query: str, subreddit: str | None = None,
                  sort: str = "relevance", time: str = "year") -> str:
    """Search Reddit posts in your window. query uses Reddit syntax (wrap exact phrases in
    double quotes; OR works). subreddit restricts to one community (e.g. 'Xreal').
    sort: relevance|new|top|comments. time: hour|day|week|month|year|all.
    Returns {page: {loggedIn, blocked, loads, stop?}, posts: [{id, sub, url, title}]}.
    If page.stop is present, stop researching and tell the user."""
    return _reddit(reddit_tab.search, target, query, subreddit=subreddit, sort=sort, time_filter=time)


@mcp.tool()
def reddit_thread(target: str, url: str, max_comments: int = 15) -> str:
    """Read a Reddit thread (sorted by top) in your window. Returns the post (title, author,
    score, comments_total, created, url, body) and its top max_comments top-level comments,
    plus the original poster's replies under them (AMA answers). If page.stop is present,
    stop researching and tell the user."""
    return _reddit(reddit_tab.thread, target, url, max_comments)


@mcp.tool()
def reddit_close(target: str) -> str:
    """Close your Reddit research window and free the slot. Always call when done, even after errors."""
    return _reddit(reddit_tab.close, target)


@mcp.tool()
def reddit_list() -> str:
    """List active Reddit research windows (all agents): target, age, idle time, page loads."""
    return _reddit(reddit_tab.list_claims)


# -------------------------------------------------------------------------
# YouTube research tools (shared implementation with the Claude Code skill)
# -------------------------------------------------------------------------

@mcp.tool()
def youtube_open(wait_seconds: float = 0) -> str:
    """Claim your own window in the shared, signed-in YouTube browser. Returns {"target": ID};
    pass it to every other youtube_* call. If all windows are busy, retry with wait_seconds (max ~50)."""
    return _reddit(youtube_tab.open_window, min(wait_seconds, 50))


@mcp.tool()
def youtube_search(target: str, query: str, sort: str = "relevance", upload: str | None = None,
                   shorts: bool = False) -> str:
    """Search YouTube videos. sort: relevance|views (YouTube no longer honors date sorting; use upload).
    upload: hour|today|week|month|year restricts to recent uploads. shorts: include Shorts (keep minor).
    Returns {page: {loggedIn, blocked, loads, stop?}, results: [{type, title, url, channel, verified,
    views, age, length}]}. If page.stop is present, stop and tell the user."""
    return _reddit(youtube_tab.search, target, query, sort, upload, shorts)


@mcp.tool()
def youtube_video(target: str, url: str) -> str:
    """Load a video (one page load) and return its metadata: channel, subscribers, views, likes,
    published, length, chapters, captions, full description, description links, and disclosure signals
    (paid-promotion label, sponsor phrases, affiliate links). Audio is muted with the player's own button.
    transcript/frame/comments then work on this video without further page loads."""
    return _reddit(youtube_tab.video, target, url)


@mcp.tool()
def youtube_transcript(target: str, grep: list[str] | None = None, context: int = 0) -> str:
    """Transcript of the loaded video as [{at, sec, text}]. With grep, only segments matching any term
    (case-insensitive), plus `context` neighboring segments each side. Use it to find moments to frame."""
    return _reddit(youtube_tab.transcript, target, grep, context)


@mcp.tool()
def youtube_frame(target: str, at: str):
    """Jump to timestamp `at` (e.g. "6:17") in the loaded video by clicking the progress bar, wait out or
    skip any ad, and capture the player. Returns JSON (path, requested/actual time, ad_seen, ad_in_frame,
    timestamped link, warning?) and the image. LOOK AT THE IMAGE: it must show the expected content, not
    an ad, sponsor segment, b-roll or transition. If wrong, try a few seconds later, or move on."""
    return _with_image(youtube_tab.frame, target, at)


@mcp.tool()
def youtube_shot(target: str):
    """Screenshot of the visible page (e.g. search results, description). Returns JSON and the image."""
    return _with_image(youtube_tab.shot, target)


@mcp.tool()
def youtube_comments(target: str, max_comments: int = 20) -> str:
    """Top comments of the loaded video (one batch): author, likes, age, pinned, creator_heart, replies, text."""
    return _reddit(youtube_tab.comments, target, max_comments)


@mcp.tool()
def youtube_channel(target: str, url: str) -> str:
    """A channel's header (subscribers, video count) and recent uploads (one page load). url may be a
    channel URL or @handle. Use it to judge how much of a channel covers one brand."""
    return _reddit(youtube_tab.channel, target, url)


@mcp.tool()
def youtube_close(target: str) -> str:
    """Close your YouTube research window and free the slot. Always call when done, even after errors."""
    return _reddit(youtube_tab.close, target)


if __name__ == "__main__":
    mcp.run()
