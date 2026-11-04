---
name: reddit-research
description: Research Reddit discussions by reading real Reddit pages in the user's signed-in Chrome through the chrome-agent connector's reddit_* tools, then report what users say with linked sources and a read on brand promotion. Use whenever the user asks for Reddit research, asks what Reddit/redditors/subreddits say about something, or wants Reddit included among other sources (e.g. "check Reddit too", "include Reddit and forums", "what are users saying online"). Also use for owner experience, complaints, comparisons, alternatives or landscape ("what should I buy", "best X") research that lists Reddit as a source. Read-only; never posts, votes, or messages.
---

# Reddit Research

Read normal Reddit pages, one at a time, in a real Chrome signed in to the user's Reddit account, and report what users say, with sources. The **chrome-agent** connector's `reddit_*` tools give you your own window in a shared browser, pace traffic across all agents, and handle loading, waiting, and block detection.

If the `reddit_*` tools aren't available, say so and stop. Don't fall back to web search or fetching reddit.com.

## Hard rules

1. **Use the `reddit_*` tools only.** Not web fetch, other browser tools, or the generic chrome-agent `run`/`screenshot` tools on the Reddit browser.
2. **Read-only.** Never post, comment, vote, join, message, or change settings.
3. **Never solve CAPTCHAs or type credentials.**
4. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several, about 20 for a landscape question. Each result reports `page.loads`. Ask before exceeding the budget.
6. **Always `reddit_close` your window** when done, even after errors.

## Tools

- `reddit_open(wait_seconds?)` → `{"target": ID}`. Pass the ID to every call. If all windows are busy, retry with `wait_seconds: 45` a few times, then tell the user.
- `reddit_search(target, query, subreddit?, sort?, time?)` → `{page, posts: [{id, sub, url, title}]}`. Wrap product names in double quotes for an exact match.
- `reddit_thread(target, url, max_comments?)`: opens the thread by clicking it in the current results when it's there, then reads down it like a person. Returns the post (author and badges, score, upvote ratio, created date, flair, body, the community's moderators), the top comments plus the original poster's replies (each with `link`, `created`, `score`, `badges`, `ai_declared`), and `promo_bias`.
- `reddit_tally(target, names)`: threads, independent users (brand accounts excluded) and upvotes behind each product name, from what this window has seen. No page load.
- `reddit_shot(target)`: an image of the visible page content, without the top bar (the user's name) or the left navigation.
- `reddit_close(target)`, `reddit_list()`.

`search` and `thread` cost one page load each; the others cost nothing.

## Landscape (when warranted)

Use a **full pass** when no products are named or the question is about a category. Use a **light pass** (2–3 loads) to check named products for a missing major alternative.

1. **Search decision threads, use case first:** "<category> for <task>", "which <category> should I buy", "best <category>", "<product> vs", "<product> alternative". Search site-wide and in the category's main subreddits, not only brand subreddits.
2. **Read 3–5 well-discussed recommendation threads.**
3. **`reddit_tally`** the candidates. Upvoted recommendations from several unrelated users carry the weight.
4. **Pick the top 4–6,** listing the ones left out, with reasons.
5. **Find the 3–5 dividing lines** owners argue about, separate from table stakes and marketing-only differences.
6. **Position each product.** If the field is really two categories, rank within each.

## Workflow

1. **Claim a window** (`reddit_open`).
2. **Plan** and count page loads:
   - Per product: a site-wide search (relevance, year), a recency search (new, month), and an in-subreddit issues search (`problem OR issue OR return OR bug`).
   - Then 2–3 threads per product, with a spread of dates and communities.
3. **Search, then open threads from the results** with `reddit_thread`.
4. **If a thread is removed or returns `null`,** note it and move on. Don't retry it.
5. **Close** (`reddit_close`).

## Weighing what you find

- **Check dates.** Always run the recency search.
- **Weigh engagement.** A 2-comment post is an anecdote; a 172-comment AMA carries more weight.
- **Label claims as user-reported.**
- **`promo_bias`** (0.0–1.0, with linked evidence). Signals:
  - a brand account posted it
  - brand accounts commenting
  - a brand-moderated or brand-owned community (e.g. XREAL staff moderating r/Xreal)
  - referral or affiliate links and codes
  - sponsor or review-unit mentions
  - "I bought it myself" claims, which lower it

  It's page-only (no account age or karma). Brand involvement is context, not proof of bad faith, but include an independent community. **Report the score and reasons.**
- **AI-declared or bot comments** don't count as owner experience.

## Output

**Reddit-focused task:**

1. **Key findings:** each linked to the exact comment or post.
2. **Sources table:** thread (linked), subreddit, date, score, comment count, promo bias (score and reasons), takeaway.
3. **Caveats:** dates spanned, brand involvement, anything unverified.
4. **Sample size:** threads read, page loads used. It's signal, not statistics.

**After a landscape pass**, add a landscape table (products against the dividing lines), the dividing lines as "you gain X, you give up Y", segments with "if you need A, look at B", and what was left out and why.

**Reddit among other sources:** fold the findings in as Reddit user reports with comment links and dates, note brand involvement, and say where Reddit agrees or conflicts with other sources. Owners on Reddit are often the best check on sponsored reviews.
