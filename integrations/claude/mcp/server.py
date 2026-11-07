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
from research_core import attach_timings, timings_reset  # noqa: E402
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
        "instance without your own target): reddit_open -> reddit_search -> reddit_thread (several "
        "per search; it goes back to the results by itself) -> reddit_tally / reddit_shot -> "
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
    timings_reset()
    try:
        return json.dumps(attach_timings(fn(*args, **kwargs)), ensure_ascii=False)
    except reddit_tab.Fail as e:  # the same Fail class backs both sites
        return json.dumps(attach_timings({"error": str(e)}))


def _frame_images(target, at, burst, out_dir=None, scan=None, samples=4):
    """frame() (or a scan) plus a flat list of every image it produced (landing frames and burst
    candidates). Frames that hit ad_timeout have no image."""
    if scan:
        res = youtube_tab.scan(target, scan, samples=samples, out_dir=out_dir)
        return {"result": res, "images": [f["path"] for f in res["frames"] if f.get("path")]}
    res = youtube_tab.frame(target, at, out_dir=out_dir, burst=min(burst, 15))
    items = res if isinstance(res, list) else [res]
    paths = [p for r in items for p in [r.get("path")] + [c["path"] for c in r.get("candidates", [])]]
    return {"result": res, "images": [p for p in paths if p]}


def _with_image(fn, *args, **kwargs):
    """Run a capture and return its JSON plus the image itself, so the model can check it."""
    timings_reset()
    try:
        res = fn(*args, **kwargs)
    except youtube_tab.Fail as e:
        return json.dumps(attach_timings({"error": str(e)}))
    if isinstance(res, dict) and "result" in res:
        attach_timings(res["result"])
    if isinstance(res, dict) and "images" in res:
        return [json.dumps(res["result"], ensure_ascii=False)] + [Image(path=p) for p in res["images"]]
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
                  sort: str | None = None, time: str | None = None) -> str:
    """Search Reddit posts in your window. Write queries like a person would: brand plus model,
    quoted when the model name is a common word ('"EcoFlow Delta 3"', not '"Delta 3"'), plus one or
    two plain words ('"EcoFlow Delta 3" problems'). No boolean OR strings; if nothing comes back,
    simplify the query rather than adding terms. subreddit restricts to one
    community (e.g. 'Xreal'). sort: relevance|new|top|comments. time: hour|day|week|month|year|all.
    With no sort/time (Reddit's defaults: relevance, all time) the query is typed into Reddit's search
    box like a person when the window is already on a Reddit page; otherwise the search URL is loaded.
    page.via says which ("typed", "url", "url (<why>)"). With subreddit, a typed search whose results
    came from other subreddits is reloaded by URL. For recency checks (sort "new"), quote brand and
    model and prefer subreddit with the category's main subreddits. An empty result, or results still
    outside the subreddit, come back with page.note.
    Returns {page: {loggedIn, blocked, loads, via, note?, stop?}, posts: [{id, sub, url, title, author, score,
    comments, created, match: "post"|"comment", snippet?, snippet_link?}]}. match "comment" means a
    comment in that thread matched; title is still the real post title and snippet is the comment.
    If page.stop is present, stop researching and tell the user."""
    return _reddit(reddit_tab.search, target, query, subreddit=subreddit, sort=sort, time_filter=time)


@mcp.tool()
def reddit_thread(target: str, url: str, max_comments: int = 15, replies: int = 0,
                  expand: int | None = None, brands: list[str] | None = None) -> str:
    """Open a Reddit thread and read down it like a person. It clicks the thread in the current
    results, or goes back to this window's last results first (no page load) and clicks it there;
    otherwise it loads the URL. page.opened says what happened ("clicked", "clicked (after back)",
    "clicked (second try)", "url (click missed)", "url", "already open"). Pass a URL with ?sort=top to
    force top order (URL load).
    Returns the post (author, author_flair, author_badges, score, upvote ratio, created, flair, body,
    links, moderators, moderators_truncated, moderators_note when the sidebar showed none,
    thread_status "locked"/"archived" when it is), up to max_comments top-level comments plus the OP's
    replies under them (with replies=N, also up to N depth-1 replies from anyone per comment). Each
    comment has link, created, score, is_op, flair (user flair, often owned gear), badges (Reddit
    achievements such as "Top 1% Commenter"), ai_declared, text. Deleted and empty comments and empty
    AutoModerator stickies don't use slots; counts {comments_total, comments_rendered,
    comments_returned, skipped_deleted, skipped_automod, collapsed_chains, expanded_chains,
    not_scrolled_into_view} explain a short list. Only comments scrolled into view are returned.
    expand: collapsed comment chains to open with a click (default: sometimes one). brands: product
    brands to check for brand accounts (the last query's brands are used anyway). promo_bias is
    0.0-1.0 with linked evidence (brand accounts and staff flair, brand-moderated or brand-named
    subreddits, affiliate/deals communities, affiliate and partner short links, content-marketing
    patterns). If page.stop is
    present, stop researching and tell the user."""
    return _reddit(reddit_tab.thread, target, url, max_comments, replies=replies, expand=expand, brands=brands)


@mcp.tool()
def reddit_back(target: str) -> str:
    """Go back one page (the mouse's back button, like a person), e.g. from a thread to the search
    results. Usually not a page load (Reddit restores the page in-app); page.restored says so, and
    page.loads counts it if the page had to be fetched again. reddit_thread already does this by
    itself when the thread you ask for was in the last results, so you rarely need it."""
    return _reddit(reddit_tab.back, target)


@mcp.tool()
def reddit_close(target: str) -> str:
    """Close your Reddit research window and free the slot. Always call when done, even after errors."""
    return _reddit(reddit_tab.close, target)


@mcp.tool()
def reddit_tally(target: str, names: list[str], alias: str | None = None, exclude_op: bool = False) -> str:
    """For each product name, among everything this window has seen (no page load): items, threads,
    users and independent_users (brand accounts excluded), polarity counts (recommend / complain /
    question / mention / mixed), raw_upvotes, recommend_upvotes and complain_upvotes, and linked
    example snippets. Names match whole names only, longest first ("Core One" doesn't count "Core
    One+"; "MK4" doesn't count "MK4S"), with common brand misspellings built in. alias groups variants:
    "Core One=C1,Core 1,Core1; MK4S=MK 4S". Search titles and snippets are kept apart
    (search_only_threads), the OP counts once across cross-posts, an OP's question isn't an
    endorsement, and exclude_op drops the OP's own mentions entirely (OPs then count toward no users
    or threads). Each row's name is in `term`; polarity is read on the clause around each mention;
    `implied` lists recommendation-style replies that don't name the product, in threads whose
    title names only it (kept apart from the counts). Use it in a landscape pass."""
    return _reddit(reddit_tab.tally, target, names, aliases=alias, exclude_op=exclude_op)


@mcp.tool()
def reddit_shot(target: str, at: str | None = None, comment: str | None = None, selector: str | None = None,
                full: bool = False):
    """Image of the Reddit page content (top bar and left navigation excluded). Pass a target to
    scroll it into view with the wheel first and capture just it: at="post", comment=<comment id or
    permalink> (that comment without its replies), or a CSS selector (e.g. a table in the post).
    Without a target it captures the current view and warns if no post or comment is in it.
    ads_in_view lists promoted posts or ad slots in the image: caption them as ads, or retake.
    Returns JSON and the image; look at it before using it."""
    return _with_image(reddit_tab.shot, target, full=full, at=at, comment=comment, selector=selector)


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
    """Search YouTube videos (one page load). Without sort/upload the query is typed into the search box
    when a YouTube page is open (page.via "typed"); otherwise it loads the results URL ("url",
    "url (filters)"). sort: relevance|views (YouTube no longer honors date sorting; use upload).
    upload: hour|today|week|month|year restricts to recent uploads. shorts: include Shorts (keep minor).
    Phrase use-case queries as "best <product> for <task>" or "which <product> for <task>".
    Results are read once the list has settled (page.settled; false = possibly partial, repeat once).
    Returns {page: {loggedIn, blocked, loads, via, settled, stop?}, results: [{type, title, url, channel, verified,
    views, age, length, likely_ai_listicle?, amazon_influencer?}]}. Skip flagged results (AI listicles,
    Amazon-influencer clips). If page.stop is present, stop and tell the user."""
    return _reddit(youtube_tab.search, target, query, sort, upload, shorts)


@mcp.tool()
def youtube_video(target: str, url: str) -> str:
    """Open a video (one page load) and return its metadata: channel, subscribers (and collaborators),
    views, likes, published, length_s and duration, chapters of this video only (chapters_source),
    captions, full description, description links, and
    sponsor_bias (paid-promotion label, sponsor phrases, affiliate links; `by_brand` and `roundup` for
    multi-brand videos: read the brand's own score there). The video is clicked in the current search
    results when listed there (page.opened "clicked" / "clicked (after back)"), else loaded by URL.
    ad_state says whether an ad was showing when it returned. Audio is muted with the player's own
    button. transcript/frame/comments then work on this video without further page loads."""
    return _reddit(youtube_tab.video, target, url)


@mcp.tool()
def youtube_transcript(target: str, grep: list[str] | None = None, context: int = 0,
                       start: str | None = None, end: str | None = None, out_dir: str | None = None) -> str:
    """Transcript of the loaded video as [{at, sec, text, link}] (`sec` = start in seconds), deduplicated,
    with total_segments. With grep, only segments matching any term (case-insensitive), plus `context`
    neighboring segments each side. start/end (e.g. "4:00", "6:30") limit it to a time window.
    The FULL transcript is always saved as YYYY-MM-DD_<id>_transcript.json and .md in out_dir (the
    report folder), else ~/.local/share/chrome-agent-research/youtube-transcripts/; `saved` gives the
    paths (an existing file is reused unless this read is fuller). List saved transcripts in the report's
    sources. `coverage` gives first/last/gaps/complete; when the first read falls short the list is
    scrolled and collected. Remaining gaps are silent stretches or a creator caption track that skips sections (coverage.note).
    Also returns disclosure_mentions and an updated sponsor_bias."""
    return _reddit(youtube_tab.transcript, target, grep, context, start, end, out_dir)


@mcp.tool()
def youtube_frame(target: str, at: str | list[str] | None = None, burst: float = 0, out_dir: str | None = None,
                  scan: str | None = None, samples: int = 4):
    """Switch to theater mode, pause (play/pause button), jump to timestamp `at` (e.g. "6:17") by clicking
    the progress bar, and capture the player (within about +/-2.5 s). `at` may be a list (one image each).
    burst=N (max 15) then lets it play N s and captures a candidate every second with the controls
    hidden; pick the best. scan="12:00-19:00" instead samples `samples` points in that range with short
    bursts and returns the frames most likely to show a chart, table, slide or UI (ranked by
    graphic.score; the rest are deleted). Ads: the player is never paused or seeked during an ad; it is
    waited out (Skip clicked when offered). If it doesn't end in time the frame reports ad_timeout and has
    no image: skip it and note it; don't reload and retry more than once. Images go to out_dir (use the
    report folder); names and `link` use the time actually landed on. Returns JSON (path, requested/actual,
    sec, ad_seen, ad_timeout, ad_log, link, warning?) and the images. LOOK AT EACH IMAGE: it must show the
    expected content, not an ad, sponsor segment, b-roll or transition. A `warning` about a small player
    means the research window is small: tell the user."""
    return _with_image(_frame_images, target, at, burst, out_dir, scan, samples)


@mcp.tool()
def youtube_shot(target: str, selector: str | None = None, out_dir: str | None = None):
    """Screenshot of the visible page content (e.g. search results, description), or of one element
    (`selector`, scrolled into view first). The top bar and left guide are left out. `ads_in_view` lists
    ads in the image (Sponsored units, masthead, a video ad): caption them as ads, or scroll and retake.
    Returns JSON and the image."""
    return _with_image(youtube_tab.shot, target, None, out_dir, False, selector)


@mcp.tool()
def youtube_comments(target: str, max_comments: int = 20) -> str:
    """Top comments of the loaded video (one batch): author, likes, age, pinned, creator_heart, replies, text.
    comment_promo_hint (low confidence) flags short, generic, slangy pro-brand praise that may be seeded;
    don't count those comments as owner experience."""
    return _reddit(youtube_tab.comments, target, max_comments)


@mcp.tool()
def youtube_channel(target: str, url: str) -> str:
    """A channel's header (subscribers, video count) and recent uploads (one page load). url may be a
    channel URL or @handle. Use it to judge how much of a channel covers one brand. Flags
    likely_ai_listicle channels, which tally then excludes from independent counts."""
    return _reddit(youtube_tab.channel, target, url)


@mcp.tool()
def youtube_tally(target: str, names: list[str], aliases: str | None = None,
                  include_transcripts: bool = True) -> str:
    """For each candidate product name: channels and independent channels mentioning it among everything
    this window has seen. Search results are merged with loaded videos (title, description, links) and,
    when read, their transcripts. Whole names only (the longest name wins: "Core One" doesn't count
    "Core One+"); aliases like "Core One=C1,Core 1; Lark M2=M2S" plus built-in caption garbles
    ("Bamboo" = Bambu). A channel isn't independent for a name if a video of it mentioning the name scores
    >= 0.6 sponsor bias (in a roundup, that brand's by_brand score), it is strongly brand-owned, or it is
    an AI-listicle / Amazon-influencer channel. Each row has examples with snippets and links.
    No page load. Use it to pick the top products in a landscape pass."""
    return _reddit(youtube_tab.tally, target, names, aliases, include_transcripts)


@mcp.tool()
def youtube_channels(target: str, topic: str, cited: str | None = None, save: bool = False) -> str:
    """Channel map for a topic, from the videos this window has read (no page load). Per channel:
    independence (little brand money behind the coverage), transparency (money disclosed clearly and
    early; null = nothing to disclose) and rigor (measured, controlled testing in the transcript; null =
    no transcript read), each 0-5; a derived authority (rigor + trust + focus, never subscribers alone);
    context (subscribers, focus = share of recent uploads on the topic, trust_citations from `cited`,
    e.g. "Project Farm=4; Vacuum Wars=2" counted from Reddit or other creators); a quadrant; confidence;
    per-video evidence; and any saved registry rating or user pin. `candidates` lists channels seen only
    in search results, to sample next. save=true stores the ratings in the registry. Call before
    youtube_close (closing clears what the window has seen)."""
    return _reddit(youtube_tab.channels, target, topic, cited, save)


@mcp.tool()
def youtube_registry(action: str, channel: str | None = None, verdict: str | None = None,
                     note: str = "", topic: str | None = None, covers: str | None = None,
                     tags: list[str] | None = None) -> str:
    """Saved channel ratings (dated, per topic; older than 180 days = stale), what each channel covers,
    and the user's pins. action: "list" (with topic: channels rated for it or whose covers tags fit it),
    "show" (one channel), "describe" (store `covers`, one plain sentence on what the channel generally
    reviews, plus category `tags`), "observe" (a dated `note` on what a video showed, for `topic`), or "pin"
    with verdict "trusted", "mixed" (sponsored and independent work, clearly disclosed), "distrusted" or
    "clear" and a note.
    Only pin when the user says so. No page load."""
    if action == "describe":
        return _reddit(youtube_tab.registry, "describe", channel, None, covers or "", ", ".join(tags or []))
    if action == "observe":
        return _reddit(youtube_tab.registry, "observe", channel, None, note, topic)
    return _reddit(youtube_tab.registry, action, channel, verdict, note, topic)


@mcp.tool()
def youtube_close(target: str) -> str:
    """Close your YouTube research window and free the slot. Always call when done, even after errors."""
    return _reddit(youtube_tab.close, target)


if __name__ == "__main__":
    mcp.run()
