---
name: reddit-research
description: Research Reddit discussions by reading real Reddit pages in the user's signed-in Chrome through the chrome-agent connector's reddit_* tools, then report what users say with linked sources. Use whenever the user asks for Reddit research, asks what Reddit/redditors/subreddits say about something, or wants Reddit included among other sources (e.g. "check Reddit too", "include Reddit and forums", "what are users saying online"). Also use when broader product, sentiment, or complaint research explicitly lists Reddit as a source. Read-only; never posts, votes, or messages.
---

# Reddit Research

Read normal Reddit pages, one at a time, in a real Chrome signed in to the user's Reddit account, and report what users say, with sources. The **chrome-agent** connector's `reddit_*` tools give you your own window in a shared browser, pace traffic across all agents, and handle loading, waiting, and block detection.

If the `reddit_*` tools aren't available, say so and stop. Don't fall back to web search or fetching reddit.com.

## Hard rules

1. **Use the `reddit_*` tools only.** Not web fetch, other browser tools, or the generic chrome-agent `run`/`screenshot` tools on the Reddit browser.
2. **Read-only.** Never post, comment, vote, join, message, or change settings.
3. **Never solve CAPTCHAs or type credentials.**
4. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several. Each result reports `page.loads`. Ask before exceeding the budget.
6. **Always `reddit_close` your window** when done, even after errors.

## Workflow

1. **Claim a window:** `reddit_open()` returns `{"target": ID}`. Pass the ID to every other call. If all windows are busy, retry with `wait_seconds: 45` a few times, then tell the user.
2. **Plan** before loading anything, and count its page loads against the budget:
   - Per product or topic: **one site-wide search** (`sort: relevance`, `time: year`), **one recency search** (`sort: new`, `time: month`), and, if it has its own subreddit, **one in-subreddit issues search** (`query: "problem OR issue OR return OR bug"`, `subreddit: <name>`).
   - Then **2–3 threads per product**, favoring high engagement and a spread of dates.
   - When Reddit is one source among several, trim this to what matters most.
3. **Search:** `reddit_search(target, query, subreddit?, sort?, time?)` returns `{page, posts: [{id, sub, url, title}]}`. Wrap product names in double quotes for an exact match, e.g. `"\"Even G2\""`.
4. **Read threads:** `reddit_thread(target, url, max_comments?)` returns the post (title, author, score, comments_total, created, url, body) and its top comments, plus the original poster's replies under them, so AMA answers come through.
5. **Release:** `reddit_close(target)`.

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
