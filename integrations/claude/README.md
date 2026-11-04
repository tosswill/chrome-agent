# Claude integrations

Tools for using chrome-agent from Claude Code and Claude Desktop, plus a polite, read-only Reddit research workflow that both can share.

```
integrations/claude/
├── mcp/server.py                         MCP server for Claude Desktop (chrome-agent + reddit_* tools)
├── skills/reddit-research/               Claude Code skill
│   ├── SKILL.md
│   └── scripts/
│       ├── reddit_tab.py                 shared implementation (CLI + imported by the MCP server)
│       ├── page_state.js                 login / block detection
│       ├── search_results.js             unique posts on a search or listing page
│       └── thread.js                     post + top comments (+ OP replies)
└── desktop-skills/reddit-research/       Claude Desktop skill (same workflow, MCP tool names)
    └── SKILL.md
```

## How the Reddit workflow works

One long-lived, headed chrome-agent instance holds a signed-in Reddit session. Each agent (any mix of Claude Code sessions and Claude Desktop) claims **its own window** in that browser, so agents share the login but never each other's tabs. Every page load goes through a **cross-process pacing lock** (4 s between loads, across all agents). Each window has a hard page-load cap, and only normal `reddit.com` pages can be loaded; JSON/API endpoints are refused. Every load reports `loggedIn` and `blocked`, and the agent stops on either problem.

The CLI (`reddit_tab.py`) and the MCP `reddit_*` tools call the same functions, so they behave identically.

## Setup

Prerequisites: `uv tool install chrome-agent`, and Google Chrome.

1. **Create the Reddit browser** from a dedicated folder and sign in by hand:

   ```bash
   mkdir -p ~/.local/share/chrome-agent-profiles/reddit-research
   cd ~/.local/share/chrome-agent-profiles/reddit-research && chrome-agent launch   # -> reddit-research-01
   # sign in to reddit.com in that window, then:
   chrome-agent save reddit-research-01   # snapshot so it can be restored after a reboot
   ```

   chrome-agent names an instance after the folder it was launched from and only avoids names that are *running*, not saved snapshots. A dedicated folder keeps the name distinctive, so an unrelated `chrome-agent launch` can't create an empty instance with the same name while the research browser is stopped (a later `save` would make that empty profile the newest snapshot).

2. **Point the tools at it.** Set `REDDIT_TAB_INSTANCE` to the instance name (default `reddit-research-01`).

3. **Claude Code:** link the skill and set the variable:

   ```bash
   ln -s "$PWD/integrations/claude/skills/reddit-research" ~/.claude/skills/reddit-research
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

   Then upload the skill: zip `desktop-skills/reddit-research/` (the zip must contain the `reddit-research/` folder) and add it under **Settings → Capabilities → Skills**.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `REDDIT_TAB_INSTANCE` | `reddit-research-01` | chrome-agent instance holding the signed-in session |
| `REDDIT_TAB_MAX` | `3` | concurrent research windows |
| `REDDIT_TAB_GAP` | `4` | seconds between page loads, across all agents |
| `REDDIT_TAB_IDLE` | `1800` | seconds before an idle window is reclaimed |
| `REDDIT_TAB_MAX_LOADS` | `40` | hard page-load cap per window |
| `REDDIT_TAB_STATE` | `/tmp/chrome-agent-reddit` | lock and claim files |

## Notes

- `save`/`restore` on macOS needs the `/proc` fix on the `macos-snapshot-proc` branch.
- The two `SKILL.md` files share their workflow, weighing, and output sections. Change both together.
- Read-only by design: the tools never post, vote, or type credentials, and they stop on any CAPTCHA, block page, or signed-out session.
