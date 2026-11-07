# Claude integrations

Tools for using chrome-agent from Claude Code and Claude Desktop, plus polite, read-only Reddit and YouTube research workflows that both can share.

```
integrations/claude/
├── lib/research_core.py                  shared core: window claims, pacing, page-load caps, organic input
├── mcp/server.py                         MCP server for Claude Desktop (chrome-agent + reddit_* + youtube_* tools)
├── skills/                               Claude Code skills (symlink each into ~/.claude/skills/)
│   ├── reddit-research/
│   │   ├── SKILL.md
│   │   └── scripts/reddit_tab.py, page_state.js, search_results.js, thread.js
│   └── youtube-research/
│       ├── SKILL.md
│       └── scripts/youtube_tab.py, youtube_signals.py (pure heuristics), channel_map.py (channel ratings + registry),
│                   page_state.js, search_results.js, video.js,
│                   transcript.js, comments.js, channel.js
└── desktop-skills/                       Claude Desktop skills (same workflows, MCP tool names)
    ├── reddit-research/SKILL.md
    └── youtube-research/SKILL.md
```

## How the research workflows work

Each site has its own long-lived, headed chrome-agent instance holding a signed-in session. Each agent (any mix of Claude Code sessions and Claude Desktop) claims **its own window** in that browser, so agents share the login but never each other's tabs. Every page load goes through a **cross-process pacing lock** per site (4 s between loads for Reddit, 6 s for YouTube, across all agents). Each window has a hard page-load cap, and only the site's normal pages can be loaded; JSON/API endpoints are refused. Every load reports `loggedIn` and `blocked`, and the agent stops on either problem.

**Actions are organic:** real mouse clicks and wheel scrolling, the YouTube player's own mute, theater and play/pause buttons and progress bar, and ads waited out or skipped with a click. Scripts only read the page. YouTube `frame` captures report whether an ad was seen or is still in frame, and the agent must check each image. On YouTube, searches are typed into the search box and videos are clicked in the results when possible (with a documented URL fallback, reported as `page.via`/`page.opened`), and the player is never paused, seeked or keyed during an ad (a frame that can't wait out an ad reports `ad_timeout`). Every YouTube transcript read is saved in full as JSON and Markdown (`--out-dir`, else `~/.local/share/chrome-agent-research/youtube-transcripts/`).

On Reddit that means: searches are typed into Reddit's search box with human key timing once the window is on Reddit (a search URL is loaded for the first search, for sort/time filters, or when typing isn't possible, and `page.via` says so); threads are opened by clicking the result, retrying a missed click once before loading the URL (`page.opened`); the next thread from the same results is reached with the mouse's back button, which Reddit restores in-app without a page load; and threads are read with wheel scrolls whose dwell time scales with the text that comes into view, with occasional longer pauses, scroll-backs and pointer drift. Only comments scrolled into view are returned.

The CLIs (`reddit_tab.py`, `youtube_tab.py`) and the MCP tools call the same functions, so they behave identically.

Every result, errors included, carries `timings`: `total_s` plus per-step totals and counts (`visibility_check`, `place_windows`, `pace_wait`, `page_load`, and site-specific steps), so slow calls can be traced to waiting, loading or reading.

## Setup

Prerequisites: `uv tool install chrome-agent`, and Google Chrome.

1. **Create the Reddit browser** from a dedicated folder and sign in by hand:

   ```bash
   mkdir -p ~/.local/share/chrome-agent-profiles/reddit-research
   cd ~/.local/share/chrome-agent-profiles/reddit-research && chrome-agent launch --no-window-border   # -> reddit-research-01
   # sign in to reddit.com in that window, then:
   chrome-agent save reddit-research-01   # snapshot so it can be restored after a reboot
   ```

   chrome-agent names an instance after the folder it was launched from and only avoids names that are *running*, not saved snapshots. A dedicated folder keeps the name distinctive, so an unrelated `chrome-agent launch` can't create an empty instance with the same name while the research browser is stopped (a later `save` would make that empty profile the newest snapshot).

   Do the same for YouTube from `~/.local/share/chrome-agent-profiles/youtube-research` (`youtube-research-01`). Use a spare Google account and turn off its watch and search history.

2. **Point the tools at it.** Set `REDDIT_TAB_INSTANCE` to the instance name (default `reddit-research-01`).

3. **Claude Code:** link the skill and set the variable:

   ```bash
   ln -s "$PWD/integrations/claude/skills/reddit-research" ~/.claude/skills/reddit-research
   ln -s "$PWD/integrations/claude/skills/youtube-research" ~/.claude/skills/youtube-research
   ```

   ```jsonc
   // ~/.claude/settings.json
   { "env": { "REDDIT_TAB_INSTANCE": "<instance>" } }
   ```

4. **Claude Desktop:** add the MCP server to `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`), then restart Desktop:

   ```json
   "mcpServers": {
     "chrome-agent": {
       "command": "/path/to/uv",
       "args": ["run", "--script", "/path/to/chrome-agent/integrations/claude/mcp/server.py"],
       "env": {
         "PATH": "<dir containing chrome-agent>:/usr/bin:/bin",
         "REDDIT_TAB_INSTANCE": "reddit-research-01",
         "CHROME_AGENT_CWD": "<home>/.local/share/chrome-agent-profiles/scratch"
       }
     }
   }
   ```

   `CHROME_AGENT_CWD` is where the generic `run(["launch"])` tool launches from, so ad-hoc browsers are named `scratch-NN` and never shadow a research profile.

   Then upload the skills: zip each folder under `desktop-skills/` (each zip must contain its folder) and add them under **Settings → Capabilities → Skills**.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `REDDIT_TAB_INSTANCE` | `reddit-research-01` | chrome-agent instance holding the signed-in session |
| `REDDIT_TAB_MAX` | `3` | concurrent research windows |
| `REDDIT_TAB_GAP` | `4` | seconds between page loads, across all agents |
| `REDDIT_TAB_IDLE` | `1800` | seconds before an idle window is reclaimed |
| `REDDIT_TAB_MAX_LOADS` | `40` | hard page-load cap per window |
| `REDDIT_TAB_STATE` | `/tmp/chrome-agent-reddit` | lock and claim files |
| `REDDIT_TAB_TYPE` | `1` | type searches into Reddit's search box when possible; `0` always loads search URLs |
| `REDDIT_TAB_READ_CAP` | `45` | longest reading dwell per thread, in seconds |
| `REDDIT_TAB_TEST_MISS` | `0` | testing only: make the first N result clicks miss, to exercise the URL fallback |
| `REDDIT_TAB_TEST_BACK` | unset | testing only: `arrow` skips the mouse back button (Reddit's back arrow is used), `toolbar` skips the arrow too (browser back) |

YouTube uses the same settings with the `YOUTUBE_TAB_` prefix. Its defaults are `youtube-research-01`, a 6 s gap, and 45 loads per window, with state in `/tmp/chrome-agent-youtube`, plus `YOUTUBE_TAB_SHOTS` (default `<state>/shots`) for captured frames, `YOUTUBE_TAB_TRANSCRIPTS` (default `~/.local/share/chrome-agent-research/youtube-transcripts`) for saved transcripts when no `--out-dir` is given, `YOUTUBE_TAB_AD_TIMEOUT` (default 60 s) for how long `frame` waits for an ad to end, `swift` (built into macOS) for `screen-text`, the on-device OCR of captured frames, and `YOUTUBE_TAB_CHANNELS` (default `~/.local/share/chrome-agent-research/youtube-channels.json`) for the channel registry: dated, topic-scoped channel ratings from `channels --save`, and the user's `trusted`/`distrusted` pins.

### Keeping research windows visible

Chrome barely draws a hidden window (minimized, on another desktop or workspace, or fully covered), so clicks miss, pages stay blank and scrolling stalls. The helpers check `document.visibilityState` when a window is opened and before each page load, and fail with a clear message instead of stalling.

On macOS, `open` first runs a screen preflight: if the screen is locked or every display is asleep (Chrome then reports every window as hidden), it fails at once with a message asking the user to wake and unlock the computer. The same check names the cause when a window is found hidden later.

Both sites share these settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `RESEARCH_AEROSPACE_WORKSPACE` | unset | With [AeroSpace](https://github.com/nikitabobko/AeroSpace), move this instance's browser windows to that workspace. If the workspace isn't on screen, switch to it and back again, so that a workspace assigned to another monitor stays visible without taking focus. Only the research browser's own windows are moved, never your own Chrome. A good choice is a workspace on a spare monitor (`workspace-to-monitor-force-assignment`). |
| `<PREFIX>_LONG_IDLE_P` / `<PREFIX>_LONG_IDLE` | `0.125` / `20-60` | Chance of an occasional long idle period before a page load, and its length in seconds. Set the chance to `0` to turn it off. |

## Notes

- `save`/`restore` on macOS needs the `/proc` fix on the `macos-snapshot-proc` branch.
- Each site's two `SKILL.md` files (Code and Desktop) share their workflow, weighing, and output sections. Change both together.
- Read-only by design: the tools never post, vote, like, subscribe, comment, download, or type credentials, and they stop on any CAPTCHA, block or bot-check page, consent or age gate, or signed-out session.
- On macOS, closing a research browser's last window leaves Chrome running with no tabs; `open` recovers from that automatically.
- Reddit hides the community sidebar (and with it the moderator list) in narrow windows, which a tiling window manager can produce. `promo_bias` then reports `confidence: "low"` and says brand moderation couldn't be checked.
