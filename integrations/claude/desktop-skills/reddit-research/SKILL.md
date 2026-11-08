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
3. **Never solve CAPTCHAs or type credentials.** Never run `chrome-agent save`, restart or stop the browser yourself, even when a `stop` message mentions it: that is for the user. A sign-in `stop` only fires when the page shows a log-in button and no session cookie is present.
4. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
   - **Need the user (don't retry):** the screen is locked or asleep, the research window is hidden, a `stop` (sign-in, CAPTCHA, block or bot check), or `reddit_open` failing to restore the browser.
   - **May retry once, after waiting about 30 s:** all windows busy (`wait_seconds`), a page that didn't finish loading, or a timeout. If the retry fails the same way, stop and tell the user.
   - **Not an error, move on:** `thread: null` with a `note`, an empty search (its `note` says how to simplify), a removed thread.
5. **Budget:** 25 page loads for a Reddit-focused task, 10 when Reddit is one source among several, 12–15 for the middle case (a light pass plus complaints, or named products plus a landscape check), about 20 for a landscape question. A user-given cap wins. Each result reports `page.loads`. Ask before exceeding the budget.
6. **Always `reddit_close` your window** when done, even after errors.

## Tools

- `reddit_open(wait_seconds?)` → `{"target": ID}`. Pass the ID to every call. If all windows are busy, retry with `wait_seconds: 45` a few times, then tell the user.
- `reddit_search(target, query, subreddit?, sort?, time?)` → `{page, posts}`. **Write queries the way a person would type them:** brand plus model, quoted when the model name is a common word (`"EcoFlow Delta 3"`, not `"Delta 3"`; `"Bambu P2S"`), plus one or two plain words (`"EcoFlow Delta 3" problems`, `"EcoFlow Delta 3" died`). No boolean `OR` strings. If a search returns nothing, simplify it (fewer words, drop the quotes) rather than adding terms. Keep issues searches to the product's subreddit plus the category subs.
  - With no `sort`/`time` (Reddit's defaults: relevance, all time) the query is typed into Reddit's search box like a person once the window is on Reddit (a site-wide search from inside a subreddit first removes the box's "r/<sub>" chip); filters (e.g. `time: "year"` for recent-only) or a different subreddit load the search URL. `page.via` says which. With `subreddit`, results from other subreddits mean the search wasn't scoped: a typed search is then reloaded by URL, and `page.note` says if any remain. An empty search comes back with a `page.note`.
  - **Recency searches** (`sort: "new"`) match loosely: quote brand and model (`"Bissell Big Green"`) and prefer `subreddit` with the category's main subreddits over site-wide.
  - Each post has `id`, `url`, `title`, `sub`, `author`, `score`, `comments`, `created` and `match`. `match: "comment"` means a comment matched: `title` is the real post title and the comment is in `snippet`. Use `comments`, `score` and `created` to pick threads before spending a load.
- `reddit_thread(target, url, max_comments?, replies?, expand?, brands?)`: opens the thread by clicking it in the results (going back to this window's last results first when needed, which is free), then reads down it like a person. `page.opened` says how (`clicked`, `clicked (after back)`, `url (click missed)`, `url`, ...). Call it for several threads from the same search.
  - Returns the post (author, `author_flair`, `author_badges`, score, upvote ratio, created date, `flair`, body, links, moderators with `moderators_truncated` and a `moderators_note` when none were shown, `thread_status` when it is `locked` or `archived`), the top comments plus the original poster's replies (`replies: N` adds up to N replies from anyone per comment), and `promo_bias`.
  - Each comment has `link`, `created`, `score`, `is_op`, `flair` (user flair, often the gear they own), `badges` (Reddit achievements like "Top 1% Commenter", not about the product), `ai_declared`, `text`.
  - `counts` explains a short list: deleted/removed and empty comments are skipped without using slots (`skipped_deleted`, `skipped_automod`), `collapsed_chains`/`expanded_chains` (Reddit hides replies under collapsed deleted comments; `expand: N` opens up to N), and `not_scrolled_into_view`. Only comments scrolled into view are returned. With `--expand`, up to 3 replies under each opened chain are returned (`revealed: true`, `revealed_replies`). `collapsed_not_reached` counts chains below where the read stopped (raise `--max` to reach them), and `collapsed_too_deep` counts chains deeper than depth 2, which aren't opened. Repeats of the same text by the same user are dropped (`skipped_duplicates`). Promo signals only use comments that were read.
  - `thread: null` with a `note`: the note quotes the page if it says the post was removed; otherwise it isn't evidence of removal.
- `reddit_back(target)`: browser back (the mouse's back button). Rarely needed; `reddit_thread` does it.
- `reddit_tally(target, names, alias?, exclude_op?, near?)`: names that are everyday words ("pass", "Keeper", "op") get a `warning`; `near="pass=gpg,gopass,password-store"` counts a mention only with one of those words close by; 1–3 letter names written lowercase match lowercase only (`op` isn't "OP"). `repeat_promoters` lists users recommending a name in 3+ threads (one voice, not several owners). In news threads (a license change, a breach) mentions are news: lean on `recommend_upvotes`. Otherwise: per product name, from what this window has seen (no page load). Whole-name, longest-match counting ("Core One" doesn't count "Core One+", "MK4" doesn't count "MK4S"), with `alias` like `"Core One=C1,Core 1; MK4S=MK 4S"`. Built-in misspellings (Bamboo/Bambu, Eccoflow/EcoFlow, blietti/Bluetti, Bissel/Bissell, ...). Returns, per `term`: `users`, `independent_users` (brand accounts excluded), `threads`, `search_only_threads` (seen only in search titles or snippets, kept apart), `polarity` (read on the clause around each mention), `raw_upvotes` vs `recommend_upvotes` and `complain_upvotes`, linked `examples`, and `implied` (recommendation-style replies that don't name the product, in threads whose title names only it; kept apart). The OP counts once across cross-posts and an OP's question isn't an endorsement (`exclude_op` drops the OP entirely). Read the examples before quoting a count.
- `reddit_shot(target, at?, comment?, selector?, full?)`: an image of the page content without the top bar or left navigation. Give a target (`at: "post"`, `comment: <id or permalink>`, or a CSS `selector`) so it scrolls there first and captures just that; without one it captures the current view and warns when no post or comment is in it. Usernames in the image are fine. If `ads_in_view` is not empty, caption the ad as an ad or retake.
- `reddit_close(target)`, `reddit_list()`.

A crosspost's search result may show the original post's score and comment count (`reddit_thread` reports the real ones). If `page.note` says the quoted phrase wasn't kept, skim titles before opening anything.

`reddit_search` and `reddit_thread` cost one page load each; the others cost nothing (`reddit_back` counts one only if the page had to be fetched again, and says so).

## Landscape (when warranted)

Use a **full pass** when no products are named or the question is about a category. Use a **light pass** (2–3 loads) to check named products for a missing major alternative.

1. **Search decision threads, use case first:** "best <category> for <task>", "which <category> for <task>", "which <category> should I buy", "<product> vs", "<product> alternative". A bare "<product> for <task>" tends to return "things to make with it" posts. Search site-wide and in the category's main subreddits, not only brand subreddits.
2. **Read 3–5 well-discussed recommendation threads** (check `comments` in the results), several from the same search.
3. **`reddit_tally`** the candidates. Upvoted recommendations from several unrelated users carry the weight.
4. **Pick the top 4–6,** listing the ones left out, with reasons.
5. **Find the 3–5 dividing lines** owners argue about, separate from table stakes and marketing-only differences.
6. **Position each product.** If the field is really two categories, rank within each.

## Workflow

1. **Claim a window** (`reddit_open`).
2. **Plan** and count page loads:
   - Per product: a site-wide search, a recency search (`sort: "new"`, `time: "month"`), and an in-subreddit issues search (`"<brand> <model>" problems`, or `died`/`returned`).
   - Then 2–3 threads per product, with a spread of dates and communities.
   - Splitting a budget: about a third searches, the rest threads. For 12–15 loads: about 4 searches and 8–10 threads; for 10: 3 searches and 7 threads.
3. **Search, then open several threads from the results** with `reddit_thread`; it goes back to the results by itself.
4. **If a thread is removed or returns `null`,** note it and move on. Don't retry it.
5. **Close** (`reddit_close`).

## Weighing what you find

- **Check dates.** Always run the recency search.
- **Weigh engagement.** A 2-comment post is an anecdote; a 172-comment AMA carries more weight.
- **Label claims as user-reported.**
- **`promo_bias`** (0.0–1.0, with linked evidence). Signals:
  - a brand account posted it, or Reddit labels it a brand-affiliate post
  - brand accounts commenting (`<brand>support`-style names, a brand's staff tag in its own subreddit such as `Sam_BW` or `1P_Jordan`, "<Brand> team/staff/Community Manager" flair, a matching company email); `possible_brand_accounts` are weaker matches, not counted
  - a brand-moderated community (e.g. XREAL staff moderating r/Xreal, or "EcoFlow | Ryan" / "CS_Team_EU" flair in the brand's subreddit; bots, mod tools and flair that merely mentions a brand never count) or a subreddit named after the brand (r/dji, never a category sub like r/podcasting or r/CarpetCleaning)
  - referral or affiliate links and codes, including partner short links (ebay.io, a.co, amzn.eu, bit.ly)
  - an affiliate or deals community (the sidebar says it earns commissions or allows self-promotion)
  - content marketing: one outside site linked repeatedly or a listicle-shaped post, an OP edit pointing to a "helpful article", "this roundup/breakdown" comments with no link left, ad-copy comments, "I'm a bit biased" plus a store, a product's own author or builder pitching it ("I'm the author of X", "I'm building this into Y"; no link needed; bots never count)
  - sponsor or review-unit mentions
  - content marketing: an OP edit pointing to an article, repeated outside links, partner short links, unlinked guide plugs ("the Newborn Sound Machine Guide"), ad-copy comments, and `coordinated_links` (3+ different users linking the same uncommon site, a sign of a seeded thread)
  - "I bought it myself" claims, which lower it

  `brand_tokens` shows which names were treated as brands (add more with `brands`). It's page-only (no account age or karma); `confidence: "low"` means the community sidebar wasn't rendered, so moderators couldn't be checked. Brand involvement is context, not proof of bad faith, but include an independent community. **Report the score and reasons.**
- **AI-declared or bot comments** don't count as owner experience.
- **Screenshots:** use `reddit_shot` sparingly (a highly upvoted answer, a comparison table), always with a target, and look at each image before using it. Caption it with the thread or comment link.

## Output

**Reddit-focused task:**

1. **Key findings:** each linked to the exact comment or post.
2. **Sources table:** thread (linked), subreddit, date, score, comment count (and how many were read), promo bias (score and reasons), takeaway.
3. **Caveats:** dates spanned, brand involvement, anything unverified.
4. **Sample size:** threads read, page loads used. It's signal, not statistics.

**After a landscape pass**, add a landscape table (products against the dividing lines), the dividing lines as "you gain X, you give up Y", segments with "if you need A, look at B", and what was left out and why.

**Reddit among other sources:** fold the findings in as Reddit user reports with comment links and dates, note brand involvement, and say where Reddit agrees or conflicts with other sources. Owners on Reddit are often the best check on sponsored reviews.
