---
name: reddit-research
description: Research Reddit discussions by reading real Reddit pages in the user's signed-in Chrome (a shared chrome-agent browser), then report what users say with linked sources and a read on brand promotion. Use whenever the user asks for Reddit research, asks what Reddit/redditors/subreddits say about something, or wants Reddit included among other sources (e.g. "check Reddit too", "include Reddit and forums", "what are users saying online"). Also use for owner experience, complaints, comparisons, alternatives or landscape ("what should I buy", "best X") research that lists Reddit as a source. Read-only; never posts, votes, or messages.
---

# Reddit Research

Read normal Reddit pages in a real Chrome signed in to the user's Reddit account, and report what users say, with links to the exact posts and comments. Other agents may be researching at the same time. The helper gives each agent its own window, paces everyone's page loads together, and acts like a person would: it clicks into threads from the results with a human-like mouse path, and scrolls through them with the wheel while "reading".

```bash
R=~/.claude/skills/reddit-research/scripts/reddit_tab.py
```

## Hard rules

1. **Use `$R` only.** Not WebFetch, the built-in browser, Claude in Chrome, or a new or headless chrome-agent launch. Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never post, comment, vote, join, message, or change settings.
3. **Never solve CAPTCHAs or type credentials.**
4. **If a result contains `stop`, stop and tell the user why.** This covers block pages, CAPTCHAs, and signed-out sessions. Never retry another way, and never use Reddit's JSON/API endpoints (the helper refuses them).
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several, and about 20 for a category or landscape question. Each result reports `loads`. Ask before exceeding the budget.
6. **Always `$R close` your window** when done, even after errors. Never stop the browser itself.

## Commands

```bash
$R open --wait                                    # -> {"target": "<ID>"}
$R search <ID> '"Even G2"'                        # site-wide, relevance, past year
$R search <ID> '"Even G2"' --sort new --time month
$R search <ID> 'problem OR issue OR return' --sub EvenRealities
$R thread <ID> '<post url>' [--max 15]            # opens it (clicking it in the results when it's there), reads down, returns post + comments + promo_bias
$R tally <ID> "Even G2" "XREAL One" VITURE        # threads, independent users and upvotes behind each name, from what this window has seen (no load)
$R shot <ID> --out-dir <report dir>               # visible page content (top bar and left nav excluded)
$R close <ID>
$R list | gc                                      # active windows across agents / release ones left by crashed agents
$R nav <ID> '<reddit url>' | eval <ID> '<js>'     # escape hatches: paced load / read something the commands don't return
```

- **Costs:** `search`, `thread` and `nav` are one page load each. `open`, `list`, `tally` and `shot` cost nothing.
- **Errors** are JSON (`{"error": "..."}`) with exit code 1. Quote URLs and queries, and wrap product names in double quotes for an exact match.
- **Opening threads:** `thread` reads comments in Reddit's default order (best) when it clicks in from results. Pass a URL with `?sort=top` to force top order; it then loads by URL.
- **Thread output:**
  - The post: title, author and badges, score, upvote ratio, comment count, created date, subreddit, flair, linked URL, body, and the community's moderators.
  - Up to `--max` top-level comments, plus the original poster's replies under them. Each comment has `link` (permalink), `created`, `score`, `badges`, `ai_declared` and `text`.
  - User flair often lists the gear someone owns (e.g. "One Pro & xbx a01+"), which helps judge owner experience.

## Landscape: establish the field first (when warranted)

Use a **full landscape pass** when the user names no products, asks what the options are, or the question is about a category. Use a **light pass** (2–3 page loads) when products are given, to check for a missing major alternative; report it under "Also consider".

1. **Search for decision threads, use case first:** `"<category> for <task>"`, `"which <category> should I buy"`, `"best <category>"`, `"<product> vs"`, `"<product> alternative"`. Run them site-wide and in the category's main subreddits (e.g. r/3Dprinting, r/AR_MR_XR), not only brand subreddits. Use `--time year`.
2. **Read 3–5 recommendation or comparison threads** with many comments.
3. **Tally** with `$R tally <ID> <name> ...`: threads, independent users (brand accounts excluded) and upvotes behind each name. Upvoted recommendations from several unrelated users carry the weight; one enthusiastic user repeated across threads doesn't.
4. **Pick the top 4–6** and list the notable products left out, with reasons.
5. **Find the 3–5 dividing lines:** the trade-offs owners actually argue about ("if you need X, get Y"). Separate them from table stakes and marketing-only differences. Reddit is good at this because owners debate trade-offs directly.
6. **Position each product,** then continue with the workflow. If the field is really two categories, say so, make that the main dividing line, and rank within each.

## Workflow

### 1. Claim a window

`$R open --wait`. It restores the browser from its snapshot if it isn't running. Any error from `open`: stop and tell the user.

### 2. Plan

Write a short plan and count page loads against the budget:

- Per product or topic: **one site-wide search** (relevance, past year), **one recency search** (new, past month), and, if it has its own subreddit, **one in-subreddit issues search** (`problem OR issue OR return OR bug`).
- Then **2–3 threads per product**, favoring high engagement and a spread of dates and communities.
- When Reddit is one source among several, trim this to what matters most.

### 3. Search, then open threads from the results

Search first and open threads from the result list. `thread` clicks the result like a person; loading thread URLs cold is the fallback.

### 4. If a page fails

If a thread is removed or deleted, or `thread` returns `null`, note it and move on to the next candidate. Don't retry the same URL. On `stop`, end the research and tell the user.

### 5. Close

`$R close <ID>`.

## Weighing what you find

- **Check dates.** Relevance results mix old and new, and early complaints may be fixed by later updates. Always run the recency search.
- **Weigh engagement.** A 2-comment post is an anecdote; a 172-comment AMA or a 65-upvote writeup carries more weight.
- **Label claims as user-reported.** Bugs, billing changes, and similar claims are unverified until the vendor or the user confirms them.
- **`promo_bias`** (0.0–1.0, in `thread` output) estimates how promotional a thread is, and lists the evidence, each item with a link. Signals:
  - a brand account posted it (username or badge)
  - brand accounts commenting in the thread
  - the community's moderators include brand accounts (`brand_moderated_subreddit`, e.g. XREAL staff moderating r/Xreal), or it is the brand's own community
  - referral or affiliate links and discount codes in the post or comments
  - sponsor or review-unit mentions
  - "not sponsored" or "I bought it myself" claims, which lower it

  It's page-only: account age and karma aren't checked. Brand accounts and brand-run subreddits are context, not proof of bad faith. Brand reps often give accurate answers, but criticism may be moderated more heavily there, so include an independent community. **Report the score and its main reasons** in the sources table.
- **AI-declared comments** (`ai_declared`) and obvious bot or spam comments shouldn't count as owner experience.

## Screenshots

`shot` saves the visible page content (about 1600 px JPEG; `--full` for PNG) without the top bar (the user's name and avatar) or the left navigation (their communities). Usernames in the thread itself remain. Use it sparingly, e.g. for a highly upvoted answer, a comparison table in a post, or a moderator list. Look at every image before using it, and caption it with the thread link.

## Output

**Reddit-focused task:**

1. **Key findings:** themes and recurring complaints or praise, each linked to the supporting comment or post (`link`).
2. **Sources table:** thread title (linked), subreddit, date, score, comment count, promo bias (score and main reasons), one-line takeaway.
3. **Caveats:** dates spanned, brand involvement (brand accounts, brand-moderated communities), anything unverified.
4. **Sample size:** threads read and page loads used. It's signal, not statistics.

**When a landscape pass was done**, add before the key findings:

- **Landscape table:** top products against the dividing lines, with links behind the cells.
- **Dividing lines:** each real trade-off in a sentence ("you gain X, you give up Y"), plus the table stakes and marketing-only differences.
- **Segments and how to choose:** "if you need A, look at B".
- **Considered and left out:** what was excluded, and why.

**Reddit among other sources:** fold the findings into the overall answer, attributed as Reddit user reports with comment links and dates, and note brand involvement where present. Note the Reddit sample size in one line. Where Reddit agrees with or contradicts other sources (e.g. YouTube reviewers), say so; owners on Reddit are often the best check on sponsored reviews.

If saving to disk, name the file `YYYY-MM-DD_reddit-<topic>.md`.
