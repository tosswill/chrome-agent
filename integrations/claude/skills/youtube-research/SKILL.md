---
name: youtube-research
description: Research YouTube by reading real YouTube pages (search results, video metadata, transcripts, comments, channels) in the user's signed-in Chrome (a shared chrome-agent browser), capture frames that show features or data, and report findings with skepticism toward creator endorsements. Use whenever the user asks for YouTube research, asks what YouTubers/reviewers/videos say or show about something, or wants YouTube included among other sources (e.g. "check YouTube too", "include video reviews", "what are reviewers saying"). Also use for product feature, comparison, alternatives, interest-level or pre-release research that lists YouTube as a source. Read-only; never likes, subscribes, comments, or downloads.
---

# YouTube Research

Read normal YouTube pages in a real Chrome signed in to the user's (spare) YouTube account. Contrast many videos to ground what you report, capture frames that show what matters, and treat creator opinions with skepticism. Other agents may be researching at the same time. The helper gives each agent its own window, paces everyone's page loads together, and acts like a person would (real clicks, mouse-wheel scrolling, the player's own mute button and progress bar).

```bash
Y=~/.claude/skills/youtube-research/scripts/youtube_tab.py
```

## Hard rules

1. **Use `$Y` only.** Not WebFetch, other browser tools, yt-dlp, or new or headless chrome-agent launches. Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never like, dislike, subscribe, comment, save to playlists, report, or change settings.
3. **Act naturally.** The helper does this for you: audio is muted with the player's mute button, jumps are progress-bar clicks, ads are waited out or skipped with a click. Don't script the player or page yourself (`eval` is for reading).
4. **No downloading, no internal APIs** (the helper refuses `youtubei`, `timedtext` and similar).
5. **If a result contains `stop`, stop and tell the user why.** This covers bot checks, CAPTCHAs, "unusual traffic", consent or age gates, and signed-out sessions. Never retry another way.
6. **Budget:** 40 page loads for a YouTube-focused task, 15 when YouTube is one source among several. Each result reports `loads`. `transcript`, `frame`, `comments` and `shot` work on the loaded video and don't count, but keep them purposeful. Ask before exceeding the budget.
7. **Always `$Y close` your window** when done, even after errors. Never stop the browser itself.

## Commands

```bash
$Y open --wait                                   # -> {"target": "<ID>"}
$Y search <ID> '"XREAL One Pro" review'          # --sort relevance|views  --upload hour|today|week|month|year  --shorts
$Y video <ID> '<watch url>'                      # metadata, chapters, description, disclosure signals (1 page load)
$Y transcript <ID> --context 1 --grep battery "sent me"   # matching segments with timestamps
$Y frame <ID> 6:17                               # jump + capture the player -> {path, actual, ad_seen, ad_in_frame, link, warning?}
$Y comments <ID> --max 20                        # one batch of top comments
$Y shot <ID>                                     # screenshot of the visible page
$Y channel <ID> @handle                          # channel header + recent uploads (1 page load)
$Y close <ID>
```

Quote URLs and queries. YouTube no longer honors sorting by date, so use `--upload` for recency.

## Workflow: a guide, not a quota

Quantity, quality, and spread of videos vary a lot by subject: a hot product may have hundreds of reviews, a pre-release one three hands-ons and a leak. Cast a wide net with searches, read enough videos to contrast viewpoints, and stop when new videos stop changing the picture.

### 1. Plan

Write a plan and rough page-load estimate. Choose the angles that fit:

- **Reviews:** `"<product>" review`.
- **Recency and bleeding edge:** the same query with `--upload week` or `month`. For new or unreleased products, also `"<product>" hands on`, `first look`, `leak`, and `<event> <product>` (CES, launch events).
- **Skeptical and long-term:** `"<product>" after 6 months`, `problems`, `why I returned`, `unsponsored` (a useful signal, but also a marketing hook).
- **Comparisons and alternatives:** `"<product>" vs`, `"<product>" alternatives`, `best <category> <year>`.
- **Official:** the brand's own channel or launch video, for claimed features and specs.
- **Setup friction:** `setup`, `how to`, `fix`. Tutorials reveal pain points reviews skip.

### 2. Triage from search results

Search results already show title, channel, views, age, and length. Choose a spread: big and small channels, launch-week and later uploads, official and independent, and review, comparison, long-term, and tutorial angles. Record views and age for every result (opened or not) for the interest read. Many big-channel reviews on the same day usually means an embargo lift with brand-supplied units.

### 3. Read videos

Always start with `video`: disclosure signals, chapters, description, metadata. Then combine as needed:

| Pattern | When | Steps |
| --- | --- | --- |
| **Skim** | Most videos | chapters and description → `transcript --grep` for your questions |
| **Find and frame** | A feature, stat table, benchmark chart, comparison shot, or defect is worth showing | `transcript --grep` → `frame` at that timestamp → check the image |
| **Chapter jump** | Chapters name the part you need ("Battery", "Cons") | `frame` a few seconds after the chapter start, plus `transcript --grep` near it |
| **Comparison grid** | A "vs" video shows products side by side | frame the side-by-side moments and pair them with the transcript lines |
| **Claim check** | The brand claims a feature or spec | frame the official claim → find an independent demo that confirms or contradicts it → frame that |
| **Owner pulse** | You need real-world experience | `comments`: recurring problems, "fixed in update", owners contradicting the review |
| **Creator context** | A glowing review from an unfamiliar channel | `channel`: how much of their recent output covers this brand |

Cite claims with timestamped links (`frame` returns one; otherwise `…watch?v=<id>&t=<seconds>s`).

### 4. Frames and screenshots

- **Capture** stat tables, benchmark charts, spec slides, side-by-side comparisons, a feature in use, visible defects, UI, and telling search pages.
- **Look at every image with Read before using it.** It must show the expected content, not an ad, sponsor segment, b-roll, talking head, or transition. `frame` waits out or skips ads and reports `ad_seen` and `ad_in_frame`, but the image is the check. If it's wrong, try a few seconds later; if an ad keeps appearing, retry once, then move on.
- **If `warning` says the player is small**, tell the user the research window is too small (a tiling window manager may have shrunk it).
- **Save** into the report's folder with `--out <dir>/YYYY-MM-DD_<topic>_<channel>_<mmss>.png`, and caption each image with video, channel, and timestamp. A handful of telling images beats dozens.

### 5. Close

`$Y close <ID>`.

## Weighing sources

**Creators are marketing-adjacent until shown otherwise.** Lean on videos for features, demos, interest level, comparisons, and alternatives, more than for verdicts.

- **Report every disclosure signal** `video` finds (paid-promotion label, sponsor phrases, affiliate links), plus review units mentioned in transcripts (`--grep "sent me" "sent us" "provided" "sponsor" "embargo"`).
- **Continued access depends on staying positive.** Many prior videos on one brand is a pattern. "Unsponsored" or "honest" in a title is a claim, not proof. Pinned and hearted comments are chosen by the creator. Claims every reviewer repeats may come from the brand's reviewer guide.

| Source | Use it for | Watch out for |
| --- | --- | --- |
| **Official brand videos** | Exactly what is claimed: features, specs, release timing | Overly positive, best-case conditions. Verify against independent demos |
| **Large channels** | Polished feature tours, comparisons, early access | Brand dependence, embargo timing, affiliate income |
| **Small, low-production channels with a real person** | Unscripted long-term use, returns, quirks, setup pain | Single-unit anecdotes. Often the most candid |
| **AI slideshows, b-roll compilations, spec readers** | Little beyond the spec sheet | Often wrong or recycled. Skip or mark low-value |
| **Shorts** | A minor buzz signal, quick demos | Little context. Keep them a small part of the research |
| **Very recent uploads** | Bleeding edge: hands-ons, pre-release, leaks, firmware changes | Unconfirmed. Label as preliminary |

**Quality and reach:** read views against subscribers (a 4K-sub channel with 40K views signals unusual interest; 441K on a 1.6M channel is ordinary), and likes and comments against views. Production value is not credibility. Agreement across independent sources beats any single review.

**Interest level (signal, not statistics):** volume and velocity of uploads over the past week, month, and year; views relative to channel size; and the result mix (many "vs" or "alternatives" videos means comparison shopping, "problems" or "returned" videos are a warning, hands-ons and leaks mean anticipation). Always state the dates and sample.

## Output

**YouTube-focused task:**

1. **Key findings:** features shown, recurring praise and complaints, alternatives raised, each with timestamped links. Frames inline where they add something.
2. **Interest read:** what volume, velocity, views, and engagement suggest, with the sample.
3. **Sources table:** video (linked), channel and subscribers, type (official, large, small, Short), published date, views, disclosure (paid label, affiliate, review unit, none found), one-line takeaway.
4. **Caveats:** sponsorship or affiliate dependence, embargo clustering, preliminary information, dates spanned.
5. **Sample size:** videos read, transcripts used, frames captured, page loads used.

**YouTube among other sources:** fold the findings in, attributed as YouTube creator or commenter claims with their disclosure status. Use video evidence mainly for features, demos, and comparisons, and say where it agrees or conflicts with owner reports (e.g. Reddit) and vendor information.

If saving to disk, name the report `YYYY-MM-DD_youtube-<topic>.md`, with frames alongside it.

## Stop and ask the user when

- A result contains `stop`, or `open` fails (browser can't be restored, or no slot after waiting).
- The plan needs more page loads than the budget.
- The task would require writing to YouTube or downloading media.
