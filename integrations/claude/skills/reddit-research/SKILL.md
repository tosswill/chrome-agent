---
name: reddit-research
description: Research Reddit discussions by reading real Reddit pages in the user's signed-in Chrome (a shared chrome-agent browser), then report what users say with linked sources. Use whenever the user asks for Reddit research, asks what Reddit/redditors/subreddits say about something, or wants Reddit included among other sources (e.g. "check Reddit too", "include Reddit and forums", "what are users saying online"). Also use when broader product, sentiment, or complaint research explicitly lists Reddit as a source. Read-only; never posts, votes, or messages.
---

# Reddit Research

Read normal Reddit pages, one at a time, in a real Chrome signed in to the user's Reddit account, and report what users say, with sources. Other agents may be researching at the same time. The helper gives each agent its own window, paces everyone's traffic together, and handles loading, waiting, and block detection, so you only issue commands.

```bash
R=~/.claude/skills/reddit-research/scripts/reddit_tab.py
```

## Hard rules

1. **Use `$R` only.** Not WebFetch, the built-in browser, Claude in Chrome, or a new or headless chrome-agent launch. Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never post, comment, vote, join, message, or change settings.
3. **Never solve CAPTCHAs or type credentials.**
4. **If a result contains `stop`, stop and tell the user why.** This covers block pages, CAPTCHAs, and signed-out sessions. Never retry another way, and never use Reddit's JSON/API endpoints (the helper refuses them).
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several. Each result reports `loads`. Ask before exceeding the budget.
6. **Always `$R close` your window** when done, even after errors. Never stop the browser itself.

## Workflow

### 1. Claim a window

```bash
$R open --wait            # -> {"target": "<ID>", ...}; waits up to 10 min for a free slot
```

It restores the browser from its snapshot if it isn't running. Any error from `open` (restore failed, no free slot after waiting): stop and tell the user.

### 2. Plan

Before loading anything, write a short plan and count its page loads against the budget:

- Per product or topic: **one site-wide search** (relevance, past year), **one recency search** (new, past month), and, if it has its own subreddit, **one in-subreddit issues search** (`problem OR issue OR return OR bug`).
- Then **2–3 threads per product**, favoring high engagement and a spread of dates.
- When Reddit is one source among several, trim this to the searches and threads that matter most.

### 3. Search

```bash
$R search <ID> '"Even G2"'                                       # site-wide, relevance, past year
$R search <ID> '"Even G2"' --sort new --time month               # recency
$R search <ID> 'problem OR issue OR return' --sub EvenRealities  # in-subreddit issues
```

Each returns `{page: {loggedIn, blocked, loads, stop?}, posts: [{id, sub, url, title}]}`. Wrap product names in double quotes for an exact match.

### 4. Read threads

```bash
$R thread <ID> '<post url>'      # sorted by top; --max N top-level comments (default 15)
```

Returns the post (title, author, score, comments_total, created, url, body) and its top comments, plus the original poster's replies under them, so AMA answers come through.

### 5. Release

```bash
$R close <ID>
```

**Escape hatch:** for a page the helpers don't cover, `$R nav <ID> <reddit url>` loads it (paced, counted, block-checked), and `$R eval <ID> '<js>'` reads it. `$R list` shows active windows across all agents.

## Weighing what you find

- **Check dates.** Relevance results mix old and new, and early complaints may be fixed by later updates. Always run the recency search.
- **Weigh engagement.** A 2-comment post is an anecdote; a 172-comment AMA or a 65-upvote writeup carries more weight.
- **Label claims as user-reported.** Bugs, billing changes, and similar claims are unverified until the vendor or the user confirms them.
- **Note affiliations.** Flag disclosed ties (e.g., an AMA host who builds apps for the device) and vendor accounts in threads.
- **Flag sponsored-only coverage.** Early reviews of brand-new products may all be paid.

## Output

**Reddit-focused task:**

1. **Key findings:** themes and recurring complaints or praise, each linked to supporting threads.
2. **Sources table:** thread title (linked), subreddit, date, score, comment count, one-line takeaway.
3. **Caveats:** dates spanned, affiliations or sponsorship, anything unverified.
4. **Sample size:** threads read and page loads used. It's signal, not statistics.

**Reddit among other sources:** fold the findings into the overall answer and attribute them as Reddit user reports (with thread links and dates). Note the Reddit sample size in one line. Where Reddit agrees with or contradicts other sources, say so.

If saving to disk, name the file `YYYY-MM-DD_reddit-<topic>.md`.
