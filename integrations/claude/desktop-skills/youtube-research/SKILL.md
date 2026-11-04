---
name: youtube-research
description: Research YouTube by reading real YouTube pages (search results, video metadata, transcripts, comments, channels) in the user's signed-in Chrome through the chrome-agent connector's youtube_* tools, capture frames that show features or data, and report findings with skepticism toward creator endorsements. Use whenever the user asks for YouTube research, asks what YouTubers/reviewers/videos say or show about something, or wants YouTube included among other sources (e.g. "check YouTube too", "include video reviews", "what are reviewers saying"). Also use for product feature, comparison, alternatives, landscape ("what are the best/top X"), interest-level or pre-release research that lists YouTube as a source. Read-only; never likes, subscribes, comments, or downloads.
---

# YouTube Research

Read normal YouTube pages in a real Chrome signed in to the user's (spare) YouTube account, through the **chrome-agent** connector's `youtube_*` tools. Contrast many videos to ground what you report, capture frames that show what matters, and treat creator opinions with skepticism. The tools give you your own window, pace page loads across all agents, and act like a person would (real clicks, the player's own mute, theater, play/pause and progress bar; frames are taken paused, within about a second of the requested time).

If the `youtube_*` tools aren't available, say so and stop. Don't fall back to web search or fetching youtube.com.

## Hard rules

1. **Use the `youtube_*` tools only.** Not web fetch, other browser tools, or the generic chrome-agent `run`/`screenshot` tools on the YouTube browser.
2. **Read-only.** Never like, dislike, subscribe, comment, save to playlists, report, or change settings.
3. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
4. **Budget:** 40 page loads for a YouTube-focused task, 15 when YouTube is one source among several, about 20 for a landscape question within a broader task (`page.loads` in each result). `youtube_transcript`, `youtube_frame`, `youtube_comments` and `youtube_shot` work on the loaded video and don't count, but keep them purposeful. Ask before exceeding the budget.
5. **Always `youtube_close` your window** when done, even after errors.

## Tools

- `youtube_open(wait_seconds?)` → `{"target": ID}`. Pass the ID to every call.
- `youtube_search(target, query, sort?, upload?, shorts?)`. `sort`: relevance or views. `upload`: hour, today, week, month, or year (use this for recency; date sorting isn't honored). `shorts`: include Shorts (keep them minor).
- `youtube_video(target, url)`: metadata, chapters, description, disclosure signals (1 page load).
- `youtube_transcript(target, grep?, context?)`: segments, each with a timestamped `link`. `grep` matches case-insensitive plain-text substrings (any term; not regex). Transcripts are speech-to-text, so write numbers both ways and prefer words to symbols. If unavailable, `reason` says why. For `youtube_error` (YouTube's own transcript service failed, often for a whole channel):
  - Don't retry.
  - Note "transcript: unavailable" for that video, and carry on with chapters, the description, comments, and frames.
  - Pick another video if its spoken content is essential.
  - Report the count in the sample size and caveats.

  `youtube_transcript` also scans the whole transcript for disclosure mentions (sponsor reads, "they sent me", "use my code", "bought it myself", "they didn't see the video first") and returns an updated `sponsor_bias`.
- `youtube_frame(target, at, burst?)`: jump to e.g. `"6:17"` (or a list of timestamps) and capture the player, within about ±2.5 s. Returns JSON (actual time, `landing_error_s`, `link`, ad and buffering flags) plus the images. With `burst: 5`, the video then plays on muted and a candidate is captured about every second. Use this for demos, animated charts and changing slides; keep the best candidate and ignore the rest.
- `youtube_comments(target, max_comments?)`, `youtube_channel(target, url_or_@handle)` (1 page load), `youtube_shot(target)`.
- `youtube_tally(target, names)`: for each candidate product name, how many channels and independent channels feature it among everything this window has seen. No page load. Use it in the landscape pass.
- `youtube_close(target)`.

## Landscape: establish the field first (when warranted)

Use a **full landscape pass** when no products are named, the user asks what the options are, or the question is about a category. Use a **light pass** (2–3 page loads) when products are given, to check for a missing major alternative, and report it under "Also consider".

1. **Search for roundups and comparisons.** **Start with the use case** ("AR glasses for coding"), then "best <category> <year>", "buyer's guide", "comparison", "tier list", "<product> vs", "<product> alternatives".
   - Use `upload: year`, and `sort: views` once at most, since it can pull in off-topic viral videos.
   - Skip AI listicle channels: future-dated titles, slideshow thumbnails, near-identical "Top 10" uploads.
2. **Read 2–4 roundup or comparison videos:** chapters often list the products, and transcript grep for "best", "pick", "alternative", "instead", "vs" shows the verdicts.
3. **Tally candidates** with `youtube_tally`: independent channels per product name. Description affiliate links are often the cleanest product list. Products only seen in their own sponsored or referral-code videos don't count as standing.
4. **Pick the top 4–6** and list the notable products left out, with reasons.
5. **Find the 3–5 dividing lines:** genuine trade-offs where buyers must give something up (e.g. tethered vs standalone, display vs camera-only, field of view vs weight, open vs closed, price tier). Look at what comparisons argue about and what owners complain about. Separate them from table stakes and marketing-only differences.
6. **Position each product** against them, then continue with the workflow. If the field is really two categories, say so, make that the primary dividing line, and pick top products within each.

A full pass usually takes 8–12 page loads.

## Workflow: a guide, not a quota

Quantity, quality, and spread of videos vary a lot by subject. Cast a wide net with searches, read enough videos to contrast viewpoints, and stop when new videos stop changing the picture.

1. **Plan** (with a rough page-load estimate). Angles:
   - reviews
   - recency (`upload: week` or `month`), plus hands-ons, first looks, leaks, and event coverage for new or unreleased products
   - skeptical and long-term ("after 6 months", "problems", "why I returned", "unsponsored")
   - comparisons and alternatives ("vs", "alternatives", "best … <year>")
   - the official brand channel
   - setup friction ("setup", "how to", "fix")
2. **Triage from search results** (title, channel, views, age, length). Choose a spread of big and small, early and later, official and independent, and different angles. Record views and age for every result. Same-day clusters of big reviews usually mean an embargo with brand-supplied units.
3. **Read videos:** always `youtube_video` first, then combine:
   - **Skim:** chapters and description, then `youtube_transcript` with grep.
   - **Find and frame:** grep the transcript, then `youtube_frame` at that time, then check the image.
   - **Chapter jump:** frame a few seconds after a chapter's start.
   - **Comparison grid:** frame side-by-side moments in "vs" videos.
   - **Claim check:** frame the official claim, then an independent demo of it.
   - **Owner pulse:** `youtube_comments`.
   - **Creator context:** `youtube_channel`, to see how much of the channel covers one brand.

   **Every claim and every frame gets a timestamped link** (the `link` from transcript segments and frames) so the user can open YouTube at that moment.
4. **Frames:**
   - **Look at every returned image before using it.** It must show the expected content, not an ad, sponsor segment, b-roll, or transition. The tool waits out or skips ads and reports `ad_seen` and `ad_in_frame`, but the image is the check. If it's wrong, try a few seconds later; if ads persist, retry once, then move on.
   - If `warning` says the player is small, tell the user the research window is too small.
   - Label real through-the-lens or screen captures separately from editing composites and mock-ups. Most "what you see" shots in reviews are composites.
   - Use a handful of telling frames, each captioned with video, channel, and timestamp.
5. **Close** with `youtube_close`.

## Weighing sources

**Creators are marketing-adjacent until shown otherwise.** Lean on videos for features, demos, interest level, comparisons, and alternatives, more than for verdicts.

- **`sponsor_bias`** (0.0–1.0, with evidence) starts from the page in `youtube_video` and is updated by `youtube_transcript`. Page signals: paid label, "Earns commission" products, campaign and affiliate links, disclosure wording.
  - Its `confidence` is low until the transcript is scanned, so a low page-only score isn't a clean bill.
  - A sponsor read for a brand other than the one reviewed is a `third_party_sponsor`, with lower weight.
  - Brand-owned channels score 1.0, and the reviewed brand's own affiliate links and codes weigh more than generic ones.
  - Auto-captions garble numbers ("$3.99" may be $399), so verify prices and specs against another source.
  - **Adjust it:** raise it for review units, early access or embargo mentions in the transcript (grep "sent me", "provided", "sponsor", "embargo", "early access"), set it to 1.0 for brand-owned channels, and raise it for channels mostly covering one brand. Lower it for credible "bought it myself" evidence.
  - **Report the final score and reasons.**
- Continued access depends on staying positive. "Unsponsored" in a title is a claim, not proof. Pinned and hearted comments are curated. Claims every reviewer repeats may come from the brand's reviewer guide.
- **Official brand videos:** verify what's claimed, but expect best-case positivity.
- **Large channels:** polished, but brand-dependent.
- **Small, low-production channels with real people:** often the most candid.
- **AI slideshows and b-roll compilations:** low value.
- **Shorts:** a minor signal.
- **Very recent uploads:** bleeding edge, but preliminary.
- **Quality and reach:** read views against subscribers and engagement against views. Production value is not credibility. Agreement across independent sources beats any single review.
- **Interest level (signal, not statistics):** upload volume and velocity, views relative to channel size, and the mix of result types. State dates and sample.

## Output

**YouTube-focused task:**

1. **Key findings:** linked with timestamps, with frames inline where useful.
2. **Interest read:** with the sample.
3. **Sources table:** video (linked), channel and subscribers, type, published date, views, sponsor bias (final score and reasons), takeaway.
4. **Caveats.**
5. **Sample size:** videos, transcripts, frames, page loads.

**When a landscape pass was done**, add before the key findings:

- **Landscape table:** the top products against the dividing lines (one row per product, one column per dividing line, plus price and availability), with the source links behind each cell.
- **Dividing lines:** each real trade-off in a sentence ("you gain X, you give up Y"), plus a note of the table stakes and the marketing-only differences.
- **Segments and how to choose:** group products by the trade-offs they make, then a short "if you need A, look at B" guide.
- **Considered and left out:** the products you excluded, and why.

**YouTube among other sources:** fold the findings in as creator or commenter claims with disclosure status, and say where they agree or conflict with owner reports and vendor information.
