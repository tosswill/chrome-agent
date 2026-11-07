---
name: reddit-research
description: Research Reddit discussions by reading real Reddit pages in the user's signed-in Chrome (a shared chrome-agent browser), then report what users say with linked sources and a read on brand promotion. Use whenever the user asks for Reddit research, asks what Reddit/redditors/subreddits say about something, or wants Reddit included among other sources (e.g. "check Reddit too", "include Reddit and forums", "what are users saying online"). Also use for owner experience, complaints, comparisons, alternatives or landscape ("what should I buy", "best X") research that lists Reddit as a source. Read-only; never posts, votes, or messages.
---

# Reddit Research

Read normal Reddit pages in a real Chrome signed in to the user's Reddit account, and report what users say, with links to the exact posts and comments. Other agents may be researching at the same time. The helper gives each agent its own window, paces everyone's page loads together, and acts like a person would: it types searches into Reddit's search box, clicks into threads from the results with a human-like mouse path, goes back to the results with the mouse's back button, and scrolls through threads with the wheel while "reading" (longer threads get longer reads).

```bash
R=~/.claude/skills/reddit-research/scripts/reddit_tab.py
```

## Hard rules

1. **Use `$R` only.** Not WebFetch, the built-in browser, Claude in Chrome, or a new or headless chrome-agent launch. Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never post, comment, vote, join, message, or change settings.
3. **Never solve CAPTCHAs or type credentials.** Never run `chrome-agent save`, restart or stop the browser yourself, even when a `stop` message mentions it: that is for the user. A sign-in `stop` only fires when the page shows a log-in button and no session cookie is present.
4. **If a result contains `stop`, stop and tell the user why.** This covers block pages, CAPTCHAs, and signed-out sessions. Never retry another way, and never use Reddit's JSON/API endpoints (the helper refuses them). See **Errors** below for which errors the user must fix and which you may retry once.
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several, **12–15 for the middle case** (a light pass plus complaints, or named products plus a landscape check), and about 20 for a category or landscape question. A user-given cap wins. Each result reports `loads`. Ask before exceeding the budget.
6. **Always `$R close` your window** when done, even after errors. Never stop the browser itself.

## Commands

```bash
$R open --wait                                       # -> {"target": "<ID>"}
$R search <ID> '"Even G2"'                           # typed into the search box when the window is on Reddit (Reddit's defaults: relevance, all time)
$R search <ID> '"Even G2"' --sort new --time month   # filters (or --time year) -> loads the search URL
$R search <ID> '"Bambu P2S" problems' --sub BambuLab
$R thread <ID> '<post url>' [--max 15] [--replies [N]] [--expand N] [--brand NAME ...]
$R back <ID>                                         # browser back (rarely needed: thread does it for you)
$R tally <ID> "Core One" MK4S "A1 mini" [--alias "Core One=C1,Core 1; MK4S=MK 4S"] [--exclude-op]
$R shot <ID> --at post | --comment <id or permalink> | --selector '<css>' [--out-dir <report dir>]
$R close <ID>
$R list | gc                                         # active windows across agents / release ones left by crashed agents
$R nav <ID> '<reddit url>' | eval <ID> '<js>'        # escape hatches: paced load / read something the commands don't return
```

- **Costs:** `search`, `thread` and `nav` are one page load each. `open`, `back` (in-app restore), `list`, `tally` and `shot` cost nothing. If `back` ever has to fetch the page again it says so (`restored`) and counts it.
- **Errors** are JSON (`{"error": "..."}`) with exit code 1. Quote URLs and queries, and wrap product names in double quotes for an exact match.
  - **Need the user (stop and tell them, don't retry):** the screen is locked or asleep, the research window is hidden, a `stop` (sign-in, CAPTCHA, block or bot check), or `open` failing to restore the browser.
  - **May retry once, after waiting about 30 s:** all windows busy (use `open --wait`), a page that didn't finish loading, or a timeout. If the retry fails the same way, stop and tell the user.
  - **Not an error, move on:** `thread: null` with a `note`, an empty search (its `note` says how to simplify), a removed thread.
- **Queries:** **Write queries the way a person would type them:** brand plus model, quoted when the model name is a common word (`"EcoFlow Delta 3"`, not `"Delta 3"`; `"Bambu P2S"`), plus one or two plain words (`"EcoFlow Delta 3" problems`, `"EcoFlow Delta 3" died`). No boolean `OR` strings. If a search returns nothing, simplify it (fewer words, drop the quotes) rather than adding terms. Keep issues searches to the product's subreddit plus the category subs.
- **How `search` runs:** the first search in a window loads the search URL (the window starts blank). Later searches with no `--sort`/`--time` are typed into Reddit's search box with human key timing (a site-wide search from inside a subreddit first removes the box's "r/<sub>" chip, the way a person would); `--sort`, `--time` or a `--sub` other than the one you're in load the URL. `page.via` says which (`typed`, `url`, or `url (<why>)`). With `--sub`, results from other subreddits mean the search wasn't scoped: a typed search is then reloaded by URL, and `page.note` says if any remain. An empty search comes back with a `page.note`; simplify, don't add terms. Use `--time year` for recent-only results.
- **Recency searches** (`--sort new`) match loosely, so unquoted words pull in unrelated posts: quote brand and model (`'"Bissell Big Green"' --sort new --time month`) and prefer `--sub` with the category's main subreddits (e.g. `--sub CarpetCleaning`, `--sub VacuumCleaners`) over site-wide.
- **Search results** (`posts`): `id`, `url`, `title`, `sub`, `author`, `score`, `comments`, `created`, and `match`. `match: "comment"` means a comment in that thread matched: `title` is still the real post title, and the matching comment is in `snippet` (`snippet_link`). Use `comments`, `score` and `created` to pick threads before spending a load; skip 1–2 comment posts unless they're the only source.
- **Opening several threads from one search:** call `thread` for each pick. It clicks the result when it's on screen; when you're on a thread opened from the last search and the next pick was in those results, it goes back first (mouse back button, no page load) and clicks it. `page.opened` says what happened: `clicked`, `clicked (after back)`, `clicked (second try)`, `url (click missed)`, `url` or `already open`. A thread URL with `?sort=top` forces top order and loads by URL.
- **If `thread` returns `thread: null`,** read `note`: it quotes the page when the page itself says the post was removed. Otherwise it is not evidence of removal.
- **Thread output:**
  - The post: title, author, `author_flair`, `author_badges`, score, upvote ratio, comment count, created date, subreddit, post `flair`, linked URL, body, `links`, the community's `moderators` (`moderators_truncated: true` when Reddit only shows the first few; the full list is never loaded; `moderators_note` when the sidebar showed none), `thread_status` (`locked` or `archived`, when it is: the discussion is frozen as of its last comment), and `promo_bias`.
  - Up to `--max` top-level comments, plus the original poster's replies under them. `--replies N` adds up to N depth-1 replies from anyone per comment (good answers are often replies). Each comment has `link` (permalink), `created`, `score`, `is_op`, `flair`, `badges`, `ai_declared` and `text`.
  - **`flair` vs `badges`:** `flair` is user flair set in that community and often lists the gear someone owns (e.g. "A1 (3), A1 Mini (1), AMS Lite (3)"), which helps judge owner experience. `badges` is a list of Reddit achievements such as "Top 1% Commenter"; they say nothing about the product.
  - **Counts** explain a short list: `comments_total` (Reddit's count), `comments_rendered` (on the page), `comments_returned`, `skipped_deleted` (deleted/removed or empty; they don't use `--max` slots), `skipped_automod` (empty AutoModerator stickies), `collapsed_chains`, `expanded_chains` and `not_scrolled_into_view`. Reddit doesn't render reply chains under collapsed `[deleted]` comments; `thread` sometimes opens one with a click (`--expand N` to open up to N, `--expand 0` never). Only comments that were scrolled into view are returned (`reading` shows how far and how long it read).

## Landscape: establish the field first (when warranted)

Use a **full landscape pass** when the user names no products, asks what the options are, or the question is about a category. Use a **light pass** (2–3 page loads) when products are given, to check for a missing major alternative; report it under "Also consider".

1. **Search for decision threads, use case first:** `best <category> for <task>`, `which <category> for <task>`, `which <category> should I buy`, `"<product>" vs`, `"<product>" alternative`. A bare `<product> for <task>` tends to return "things to make with it" posts. Run them site-wide and in the category's main subreddits (e.g. r/3Dprinting, r/AR_MR_XR), not only brand subreddits.
2. **Read 3–5 recommendation or comparison threads** with many comments (check `comments` in the results), several from the same search.
3. **Tally** with `$R tally <ID> <name> ... [--alias ...]` (see below). Upvoted recommendations from several unrelated users carry the weight; one enthusiastic user repeated across threads doesn't.
4. **Pick the top 4–6** and list the notable products left out, with reasons.
5. **Find the 3–5 dividing lines:** the trade-offs owners actually argue about ("if you need X, get Y"). Separate them from table stakes and marketing-only differences. Reddit is good at this because owners debate trade-offs directly.
6. **Position each product,** then continue with the workflow. If the field is really two categories, say so, make that the main dividing line, and rank within each.

## Workflow

### 1. Claim a window

`$R open --wait`. It restores the browser from its snapshot if it isn't running. Any error from `open`: stop and tell the user.

### 2. Plan

Write a short plan and count page loads against the budget:

- Per product or topic: **one site-wide search**, **one recency search** (`--sort new --time month`), and, if it has its own subreddit, **one in-subreddit issues search** (`"<brand> <model>" problems`, or `died`/`returned`, with `--sub`).
- Then **2–3 threads per product**, favoring high engagement and a spread of dates and communities.
- When Reddit is one source among several, trim this to what matters most.
- **Splitting a budget:** about a third searches, the rest threads, 2–3 threads per search (going back to the results is free). For 12–15 loads: about 4 searches (decision threads, a second angle or subreddit, a recency search, a complaints search) and 8–10 threads. For 10: 3 searches and 7 threads.

### 3. Search, then open threads from the results

Search first and open threads from the result list, several per search. `thread` clicks the result like a person and goes back to the results by itself for the next pick; loading thread URLs cold is the fallback.

### 4. If a page fails

If a thread is removed or deleted, or `thread` returns `null`, note it (with its `note`) and move on to the next candidate. Don't retry the same URL. On `stop`, end the research and tell the user.

### 5. Close

`$R close <ID>`.

## Weighing what you find

- **Check dates.** Relevance results mix old and new, and early complaints may be fixed by later updates. Always run the recency search.
- **Weigh engagement.** A 2-comment post is an anecdote; a 172-comment AMA or a 65-upvote writeup carries more weight.
- **Label claims as user-reported.** Bugs, billing changes, and similar claims are unverified until the vendor or the user confirms them.
- **`promo_bias`** (0.0–1.0, in `thread` output) estimates how promotional a thread is, and lists the evidence, each item with a link. Signals:
  - a brand account posted it, or Reddit labels it a brand-affiliate post
  - brand accounts commenting in the thread (`brand_accounts`): usernames like `<brand>support`/`<brand>_official`/`team-<brand>`, "<Brand> team/staff/employee" flair, or a company email matching the username. `possible_brand_accounts` are weaker matches, not counted.
  - the community's moderators include brand accounts (`brand_moderated_subreddit`, e.g. XREAL staff moderating r/Xreal, or staff flair like "EcoFlow | Ryan" or "CS_Team_EU" in the brand's own subreddit; utility bots and mod tools like AutoModerator or `*Bot` never count, nor does flair that merely mentions a brand), or the subreddit is named after the brand (`brand_subreddit`, e.g. r/dji; never a category sub like r/podcasting or r/CarpetCleaning)
  - referral or affiliate links and discount codes in the post or comments, including partner short links (`partner_short_links`: ebay.io, a.co, amzn.eu, bit.ly ...)
  - an affiliate or deals community: the sidebar says the community earns commissions ("As an Amazon Associate…") or allows self-promotion (`affiliate_community`)
  - content marketing: the same outside site linked again and again or a listicle-shaped post (`repeated_external_links`), an OP edit pointing to a "helpful article" (`op_edit_points_to_article`), comments pointing to "this roundup/breakdown" with no link left (`dangling_roundup_reference`), a comment written like ad copy (`spec_sheet_pitch`), and "I'm a bit biased" plus a store (`self_declared_bias`)
  - sponsor or review-unit mentions
  - "not sponsored" or "I bought it myself" claims, which lower it

  `brand_tokens` shows which names were treated as brands (from the title, flair and your last search; add more with `--brand`). It's page-only: account age and karma aren't checked. When the community sidebar isn't rendered (a narrow window), moderators can't be checked and `confidence` is `low`. Brand accounts and brand-run subreddits are context, not proof of bad faith. Brand reps often give accurate answers, but criticism may be moderated more heavily there, so include an independent community. **Report the score and its main reasons** in the sources table.
- **AI-declared comments** (`ai_declared`) and obvious bot or spam comments shouldn't count as owner experience.
- **`tally`** counts, per name, among everything this window has seen (no page load):
  - **Whole names, longest match first:** "Core One" doesn't count "Core One+" or "Core One L", "MK4" doesn't count "MK4S", "A1" doesn't count "A1 mini". Common brand misspellings (Bamboo/Bambu, Pruza/Prusa, Eccoflow/EcoFlow, blietti/Bluetti, Bissel/Bissell, ...) are built in. Group variants with `--alias "Core One=C1,Core 1,Core1; MK4S=MK 4S"`.
  - `users` and `independent_users` (brand accounts excluded), `threads`, and `search_only_threads`: names seen only in search titles or snippets, kept apart so a complaints search doesn't inflate a product.
  - The OP counts once across cross-posts, and an OP's question isn't an endorsement (`--exclude-op` drops the OP's mentions entirely: OPs then count toward no users or threads).
  - `polarity` (recommend / complain / question / mention / mixed, heuristic), `raw_upvotes`, `recommend_upvotes` and `complain_upvotes`: a passing mention in a 300-upvote comment adds to `raw_upvotes` only.
  - `examples`: linked snippets around each mention. Read them before quoting a count. Each row's name is in `term`.
  - Polarity is read on the clause around each mention ("Love my Bluetti, but the EcoFlow died" is praise for one and a complaint about the other).
  - `implied`: top-level replies that read as a recommendation without naming the product ("Love mine. No regrets."), in threads whose title names only that product. Kept apart from the counts; quote them as "replies to a <product> thread".

## Screenshots

`shot` saves page content without the top bar or the left navigation (about 1600 px JPEG; `--full` for PNG). Give it a target so it scrolls there with the wheel first and captures just that: `--at post`, `--comment <id or permalink>` (that comment without its replies), or `--selector '<css>'` (e.g. a comparison table in a post). Without a target it captures the current view, which after reading is often the bottom of the page; it warns when no post or comment is in view. Usernames in the image are fine.

Use it sparingly: a highly upvoted answer, a comparison table, a moderator list. Look at every image before using it, and caption it with the thread or comment link. If `ads_in_view` is not empty, a promoted post or ad is in the image: caption it as an ad, or retake with a tighter target.

## Output

**Reddit-focused task:**

1. **Key findings:** themes and recurring complaints or praise, each linked to the supporting comment or post (`link`).
2. **Sources table:** thread title (linked), subreddit, date, score, comment count (and how many were read, from `counts`), promo bias (score and main reasons), one-line takeaway.
3. **Caveats:** dates spanned, brand involvement (brand accounts, brand-moderated communities), anything unverified.
4. **Sample size:** threads read and page loads used. It's signal, not statistics.

**When a landscape pass was done**, add before the key findings:

- **Landscape table:** top products against the dividing lines, with links behind the cells.
- **Dividing lines:** each real trade-off in a sentence ("you gain X, you give up Y"), plus the table stakes and marketing-only differences.
- **Segments and how to choose:** "if you need A, look at B".
- **Considered and left out:** what was excluded, and why.

**Reddit among other sources:** fold the findings into the overall answer, attributed as Reddit user reports with comment links and dates, and note brand involvement where present. Note the Reddit sample size in one line. Where Reddit agrees with or contradicts other sources (e.g. YouTube reviewers), say so; owners on Reddit are often the best check on sponsored reviews.

If saving to disk, name the file `YYYY-MM-DD_reddit-<topic>.md`.
