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

1. **Create the Reddit browser** and sign in by hand:

   ```bash
   chrome-agent launch            # note the instance name, e.g. reddit-01 (from the current dir)
   # sign in to reddit.com in that window, then:
   chrome-agent save <instance>   # snapshot so it can be restored after a reboot
   ```

2. **Point the tools at it.** Set `REDDIT_TAB_INSTANCE` to the instance name (default `reddit-01`).

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
       "env": { "PATH": "<dir containing chrome-agent>:/usr/bin:/bin", "REDDIT_TAB_INSTANCE": "<instance>" }
     }
   }
   ```

   Then upload the skill: zip `desktop-skills/reddit-research/` (the zip must contain the `reddit-research/` folder) and add it under **Settings → Capabilities → Skills**.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `REDDIT_TAB_INSTANCE` | `reddit-01` | chrome-agent instance holding the signed-in session |
| `REDDIT_TAB_MAX` | `3` | concurrent research windows |
| `REDDIT_TAB_GAP` | `4` | seconds between page loads, across all agents |
| `REDDIT_TAB_IDLE` | `1800` | seconds before an idle window is reclaimed |
| `REDDIT_TAB_MAX_LOADS` | `40` | hard page-load cap per window |
| `REDDIT_TAB_STATE` | `/tmp/chrome-agent-reddit` | lock and claim files |

## Notes

- `save`/`restore` on macOS needs the `/proc` fix on the `macos-snapshot-proc` branch.
- The two `SKILL.md` files share their workflow, weighing, and output sections. Change both together.
- Read-only by design: the tools never post, vote, or type credentials, and they stop on any CAPTCHA, block page, or signed-out session.
