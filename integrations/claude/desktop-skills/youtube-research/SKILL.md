---
name: youtube-research
description: Research YouTube by reading real YouTube pages (search results, video metadata, transcripts, comments, channels) in the user's signed-in Chrome through the chrome-agent connector's youtube_* tools, capture frames that show features or data, and report findings with skepticism toward creator endorsements. Use whenever the user asks for YouTube research, asks what YouTubers/reviewers/videos say or show about something, or wants YouTube included among other sources (e.g. "check YouTube too", "include video reviews", "what are reviewers saying"). Also use for product feature, comparison, alternatives, interest-level or pre-release research that lists YouTube as a source. Read-only; never likes, subscribes, comments, or downloads.
---

# YouTube Research

Read normal YouTube pages in a real Chrome signed in to the user's (spare) YouTube account, through the **chrome-agent** connector's `youtube_*` tools. Contrast many videos to ground what you report, capture frames that show what matters, and treat creator opinions with skepticism. The tools give you your own window, pace page loads across all agents, and act like a person would (real clicks, the player's own mute, theater, play/pause and progress bar; frames are taken paused, within about a second of the requested time).

If the `youtube_*` tools aren't available, say so and stop. Don't fall back to web search or fetching youtube.com.

## Hard rules

1. **Use the `youtube_*` tools only.** Not web fetch, other browser tools, or the generic chrome-agent `run`/`screenshot` tools on the YouTube browser.
2. **Read-only.** Never like, dislike, subscribe, comment, save to playlists, report, or change settings.
3. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
4. **Budget:** 40 page loads for a YouTube-focused task, 15 when YouTube is one source among several (`page.loads` in each result). `youtube_transcript`, `youtube_frame`, `youtube_comments` and `youtube_shot` work on the loaded video and don't count, but keep them purposeful. Ask before exceeding the budget.
5. **Always `youtube_close` your window** when done, even after errors.

## Tools

- `youtube_open(wait_seconds?)` → `{"target": ID}`. Pass the ID to every call.
- `youtube_search(target, query, sort?, upload?, shorts?)`. `sort`: relevance or views. `upload`: hour, today, week, month, or year (use this for recency; date sorting isn't honored). `shorts`: include Shorts (keep them minor).
- `youtube_video(target, url)`: metadata, chapters, description, disclosure signals (1 page load).
- `youtube_transcript(target, grep?, context?)`: segments, each with a timestamped `link`. `grep` matches case-insensitive plain-text substrings (any term; not regex). Transcripts are speech-to-text, so write numbers both ways and prefer words to symbols. If unavailable, `reason` says why: `youtube_error` means YouTube's own transcript service failed, so don't retry; use chapters, the description, comments, and frames.
- `youtube_frame(target, at)`: jump to e.g. `"6:17"` (or a list of timestamps) and capture the player. Returns JSON (actual time, `landing_error_s`, `link`, ad and buffering flags) plus the images.
- `youtube_comments(target, max_comments?)`, `youtube_channel(target, url_or_@handle)` (1 page load), `youtube_shot(target)`.
- `youtube_close(target)`.

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
   - Use a handful of telling frames, each captioned with video, channel, and timestamp.
5. **Close** with `youtube_close`.

## Weighing sources

**Creators are marketing-adjacent until shown otherwise.** Lean on videos for features, demos, interest level, comparisons, and alternatives, more than for verdicts.

- **`sponsor_bias`** (0.0–1.0, from `youtube_video`, with evidence) is a page-only estimate.
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

**YouTube among other sources:** fold the findings in as creator or commenter claims with disclosure status, and say where they agree or conflict with owner reports and vendor information.
