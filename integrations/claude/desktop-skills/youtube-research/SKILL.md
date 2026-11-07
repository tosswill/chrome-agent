---
name: youtube-research
description: Research YouTube by reading real YouTube pages (search results, video metadata, transcripts, comments, channels) in the user's signed-in Chrome through the chrome-agent connector's youtube_* tools, capture frames that show features or data, and report findings with skepticism toward creator endorsements. Use whenever the user asks for YouTube research, asks what YouTubers/reviewers/videos say or show about something, or wants YouTube included among other sources (e.g. "check YouTube too", "include video reviews", "what are reviewers saying"). Also use for product feature, comparison, alternatives, landscape ("what are the best/top X"), interest-level or pre-release research that lists YouTube as a source. Also use to map which YouTube channels or reviewers cover a topic and how far to trust them (independence, disclosure, testing rigor). Read-only; never likes, subscribes, comments, or downloads.
---

# YouTube Research

Read normal YouTube pages in a real Chrome signed in to the user's (spare) YouTube account, through the **chrome-agent** connector's `youtube_*` tools. Contrast many videos to ground what you report, capture frames that show what matters, and treat creator opinions with skepticism. The tools give you your own window, pace page loads across all agents, and act like a person would (typed searches, clicked results, real clicks, the player's own mute, theater, play/pause and progress bar; frames are taken paused, within about ±2.5 s of the requested time; ads are never paused or seeked, only waited out or skipped).

If the `youtube_*` tools aren't available, say so and stop. Don't fall back to web search or fetching youtube.com.

## Hard rules

1. **Use the `youtube_*` tools only.** Not web fetch, other browser tools, or the generic chrome-agent `run`/`screenshot` tools on the YouTube browser.
2. **Read-only.** Never like, dislike, subscribe, comment, save to playlists, report, or change settings.
3. **If a result contains `stop` or `error`, stop and tell the user why.** Never retry another way.
4. **Budget:** 40 page loads for a YouTube-focused task, 15 when YouTube is one source among several, about 12–15 for the middle case (a light pass plus complaints, or named products plus a landscape check), about 20 for a landscape question within a broader task (`page.loads` in each result). Split it between searches and videos: a landscape usually needs about 6 searches and 10–12 videos. `youtube_transcript`, `youtube_frame`, `youtube_comments` and `youtube_shot` work on the loaded video and don't count, but keep them purposeful. Ask before exceeding the budget.
5. **Always `youtube_close` your window** when done, even after errors.

## Tools

- `youtube_open(wait_seconds?)` → `{"target": ID}`. Pass the ID to every call.
- `youtube_search(target, query, sort?, upload?, shorts?)`. `sort`: relevance or views. `upload`: hour, today, week, month, or year (use this for recency; date sorting isn't honored). `shorts`: include Shorts (keep them minor). Without filters the query is typed into the search box (`page.via`: `typed`). Results are read once the list has settled (`page.settled`; `false` means it may be partial, so repeat the search once). They may carry `likely_ai_listicle` (including one channel posting several near-identical short clips) or `amazon_influencer` (two or more signals) flags.
- `youtube_video(target, url)`: metadata, `length_s` and `duration`, chapters of this video only (`chapters_source`: `description`, `player markers` or `chapter panel (unlinked)`), description, `sponsor_bias` (1 page load). It clicks the video in the current results (or after going back to the last search) when it's listed there (`page.opened`), so open videos from your latest search when you can. `ad_state` says whether an ad was showing, and when.
- `youtube_transcript(target, grep?, context?, start?, end?, out_dir?)`: segments `{at, sec, text, link}`; **`sec` is the start in seconds** (there is no `seconds` field). Duplicates are removed and `total_segments` is always the full count. The **full** transcript is always saved as `YYYY-MM-DD_<id>_transcript.json` and `.md` in `out_dir` (pass the report folder), else `~/.local/share/chrome-agent-research/youtube-transcripts/`; `saved` gives the paths (a fuller read replaces an earlier partial copy). **List the saved transcripts in the report's sources.** `coverage` gives `first`, `last`, `gaps` and `complete`; when the first read falls short, the helper scrolls the transcript list and collects the rest. Remaining gaps are silent stretches or a creator caption track that skips sections (`coverage.note` says which); say the transcript is incomplete there and use chapters and frames. `grep` matches case-insensitive plain-text substrings (any term; not regex). Transcripts are speech-to-text, so write numbers both ways and prefer words to symbols. If unavailable, `reason` says why. For `youtube_error` (YouTube's own transcript service failed, often for a whole channel):
  - Don't retry.
  - Note "transcript: unavailable" for that video, and carry on with chapters, the description, comments, and frames.
  - Pick another video if its spoken content is essential.
  - Report the count in the sample size and caveats.

  `youtube_transcript` also scans the whole transcript for disclosure mentions (sponsor reads, "they sent me", "use my code", "bought it myself", "they didn't see the video first") and returns an updated `sponsor_bias`.
- `youtube_frame(target, at, burst?, out_dir?, scan?, samples?)`: jump to e.g. `"6:17"` (or a list of timestamps) and capture the player, within about ±2.5 s. Returns JSON (actual time, `sec`, `landing_error_s`, `link`, `ad_seen`, `ad_timeout`, `ad_log`, buffering) plus the images. With `burst: 5`, the video then plays on muted and a candidate is captured every second with the controls hidden; use it for demos, animated charts and changing slides, and keep the best candidate. With `scan: "12:00-19:00"` it samples the range and keeps the 3 frames most likely to show a chart, table, slide or UI (`graphic.score`, a hint). Pass `out_dir` (the report folder); names never overwrite each other.
- `youtube_comments(target, max_comments?)`, `youtube_channel(target, url_or_@handle)` (1 page load), `youtube_shot(target, selector?, out_dir?)`. `youtube_shot` returns `ads_in_view` (Sponsored units, masthead, a video ad): caption them as ads, or scroll and retake. The account's name or avatar in a shot is fine.
- `youtube_tally(target, names, aliases?, include_transcripts?)`: for each product name, channels and independent channels mentioning it among everything this window has seen, with snippets and links. Loaded videos (description, links, read transcripts) are merged with their search results. Whole names only ("Core One" doesn't count "Core One+"); add spellings as `aliases` ("Core One=C1,Core 1; Lark M2=M2 kit"); caption garbles like "Bamboo" = Bambu are built in. A channel isn't independent for a name when a video of it mentioning the name scores 0.6+ sponsor bias (in a roundup, that brand's `by_brand` score, so include the brand in the name), it's strongly brand-owned, or it's an AI-listicle / Amazon-influencer channel. No page load. Use it in the landscape pass.
- `youtube_channels(target, topic, cited?, save?)`: channel map for the topic from the videos this window read. Gives independence, transparency and rigor (0–5), derived authority, context, quadrant and evidence, plus `candidates` seen only in search. No page load; see *Channel map*.
- `youtube_registry(action, channel?, verdict?, note?, topic?)`: saved channel ratings (`list`, `show`) and the user's pins (`pin` with `trusted`, `distrusted` or `clear`; only when the user says so).
- `youtube_close(target)`.

## Landscape: establish the field first (when warranted)

Use a **full landscape pass** when no products are named, the user asks what the options are, or the question is about a category. Use a **light pass** (2–3 page loads) when products are given, to check for a missing major alternative, and report it under "Also consider".

1. **Search for roundups and comparisons.** **Start with the use case**, phrased as a buying question: "best <product> for <task>" or "which <product> for <task>" (a bare "<product> for <task>" returns how-to videos). Then "best <category> <year>", "buyer's guide", "comparison", "tier list", "<product> vs", "<product> alternatives".
   - **Name products by brand plus model** (`"EcoFlow Delta 3"`, not `"Delta 3"`): bare model names are often common words. Keep queries short and natural, the way a person types them, with no boolean `OR` strings.
   - Use `upload: year`, and `sort: views` once at most, since it can pull in off-topic viral videos.
   - Skip AI listicle channels (`likely_ai_listicle`: future-dated names or titles, generic "Picks/Reviews Club/Top5Lab" names, "Top 10" titles, slideshow thumbnails) and Amazon-influencer clips (`amazon_influencer`, two or more of: storefront links, "#amazonfinds", very short reviews with Amazon links, little reach), and channels posting near-identical short clips. Don't count their mentions.
2. **Read 2–4 roundup or comparison videos:** chapters often list the products, and transcript grep for "best", "pick", "alternative", "instead", "vs" shows the verdicts.
3. **Tally candidates** with `youtube_tally`: independent channels per product name. Description affiliate links are often the cleanest product list. Products only seen in their own sponsored or referral-code videos don't count as standing.
4. **Pick the top 4–6** and list the notable products left out, with reasons.
5. **Find the 3–5 dividing lines:** genuine trade-offs where buyers must give something up (e.g. tethered vs standalone, display vs camera-only, field of view vs weight, open vs closed, price tier). Look at what comparisons argue about and what owners complain about. Separate them from table stakes and marketing-only differences.
6. **Position each product** against them, then continue with the workflow. If the field is really two categories, say so, make that the primary dividing line, and pick top products within each.

A full pass usually takes 8–12 page loads (about 4–6 searches and 4–6 videos).

## Channel map: who covers the topic, and how far to trust them

Run it **after a landscape pass** (reusing the videos already read) or **on its own** when the user asks which channels to trust on a topic. Ratings are **scoped to the topic** (a channel strong on GPUs isn't automatically strong on vacuums).

1. **Pick 6–8 channels** from the landscape searches: the ones that recur, plus the largest. ``youtube_channels(target, topic)`` lists the channels you've only seen in search results under `candidates`.
2. **Check the registry first** (``youtube_registry("list", topic=...)``). A fresh rating for the same topic (`stale: false`, `same_topic: true`) can stand in for sampling that channel. A rating that is stale or for a neighboring topic is a starting point, not the answer. **A user pin (`trusted`/`distrusted`) overrides the heuristics:** report it as the user's verdict.
3. **Sample each channel:** ``youtube_channel(target, @handle)`` (1 load: reach and recent uploads, for `focus`), then 2–3 videos on the topic **with transcripts** (rigor needs them). Prefer their own tests and comparisons over Shorts. Videos read in the landscape pass count. That's about 15–25 loads for 6–8 channels; trim to 4–5 channels when the budget is tight, and say so.
4. **Count trust:** how often Reddit owners or other creators cite each channel, unprompted (for example from a Reddit tally of the channel names, or creators referencing "Project Farm's test"). Pass the counts as `cited` (`"Project Farm=4; Vacuum Wars=2"`).
5. **Map** with ``youtube_channels(target, topic)`` (no page load). **Run it before ``youtube_close``**, which clears what the window has seen. Each channel gets:
   - **independence** (0–5): little brand money behind the coverage. Sponsor reads for the reviewed brand, review units, brand codes or links and brand ownership cost the most; generic store affiliate links and third-party sponsors (a VPN read) cost little.
   - **transparency** (0–5, `null` = nothing to disclose): whether money is disclosed clearly and early (the paid-promotion label, a spoken sponsor read, an affiliate disclosure). Brand links or codes with no disclosure, or a disclosure only late in the video, cost the most.
   - **rigor** (0–5, `null` = no transcript read): measured results with units, controlled side-by-side tests, named tests, instruments, a stated method, long-term follow-ups. Spec-sheet reading lowers it.
   - **authority** (derived): rigor 60%, trust 25%, focus 15%. It's never based on subscribers alone. `context` gives subscribers, focus and trust citations, plus a `quadrant` (trusted tester; rigorous but brand-tied; independent, light testing; affiliate roundup (light testing, most videos earn from affiliate links); promotional/shallow), `confidence` (by sample size and transcripts) and per-video evidence.
6. **Read the evidence before quoting a rating,** and correct it in the report when the heuristic is plainly wrong (e.g. a measured lab test the patterns missed). Then **save** with `save` so later research can reuse it. Never pin a channel yourself; pins are the user's call. Offer to pin when the user states a verdict ("Project Farm is the gold standard").

**Output:** a channel table (channel linked, subscribers, focus, independence, transparency, rigor, authority, quadrant, confidence, the main reasons with video links), plus 2–4 sentences on who to trust for what (e.g. "trust X's measurements; read Y for features, but its reviews are brand-supplied and disclosed"). Weight landscape findings by it: claims from high-rigor, high-independence channels count more. If a chart helps, plot rigor against independence, with transparency as the marker.

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
   - **Look at every returned image before using it.** It must show the expected content, not an ad, sponsor segment, b-roll, or transition. The tool waits out or skips ads and reports `ad_seen`, `ad_in_frame` and `ad_log`, but the image is the check. If it's wrong, try a few seconds later.
   - **Ads:** the player is never paused or seeked during an ad. If an ad doesn't end in time, the frame reports `ad_timeout: true` with no image: skip that frame and note it; don't reload and retry more than once.
   - **Talking heads:** use `scan` over the section where a chart or table should be.
   - If `warning` says the player is small, tell the user the research window is too small (don't try to resize it).
   - Label real through-the-lens or screen captures separately from editing composites and mock-ups. Most "what you see" shots in reviews are composites.
   - Use a handful of telling frames, each captioned with video, channel, and timestamp.
5. **Close** with `youtube_close`.

## Weighing sources

**Creators are marketing-adjacent until shown otherwise.** Lean on videos for features, demos, interest level, comparisons, and alternatives, more than for verdicts.

- **`sponsor_bias`** (0.0–1.0, with evidence) starts from the page in `youtube_video` and is updated by `youtube_transcript`. Page signals: paid label, "Earns commission" products, campaign and affiliate links, disclosure wording.
  - Its `confidence` is low until the transcript is scanned, so a low page-only score isn't a clean bill.
  - A sponsor read (or a music/software discount code) for a brand other than the one reviewed is a `third_party_sponsor`, with lower weight, and the "Includes paid promotion" label then weighs 0.2 instead of 0.6. Denials ("they did not sponsor this") count against sponsorship.
  - **Roundups:** with two or more brands, `by_brand` scores each brand separately (the creator's own gear lists aren't counted as brands; retailer `?ref=` links and brand short links count as affiliate links) and `roundup: true` marks three or more. Read the reviewed brand's own score there, not the overall score; `shared_score` covers signals tied to no brand.
  - Brand-owned channels score 1.0 (`brand_owned: "strong"`); a name match alone is `"possible"` (0.3), so check it with `youtube_channel`. A creator's own shop links are `self_promotion`, not brand ownership. The reviewed brand's own affiliate links and codes weigh more than generic ones.
  - Auto-captions garble numbers ("$3.99" may be $399), so verify prices and specs against another source.
  - **Adjust it:** raise it for review units, early access or embargo mentions in the transcript (grep "sent me", "provided", "sponsor", "embargo", "early access"), set it to 1.0 for brand-owned channels, and raise it for channels mostly covering one brand. Lower it for credible "bought it myself" evidence.
  - **Report the final score and reasons.**
- `comment_promo_hint` (low confidence) flags short, generic, slangy pro-brand comments that may be seeded. Don't count them as owner experience.
- Continued access depends on staying positive. "Unsponsored" in a title is a claim, not proof. Pinned and hearted comments are curated. Claims every reviewer repeats may come from the brand's reviewer guide.
- **Official brand videos:** verify what's claimed, but expect best-case positivity.
- **Large channels:** polished, but brand-dependent.
- **Small, low-production channels with real people:** often the most candid.
- **AI slideshows and b-roll compilations:** low value.
- **Shorts:** a minor signal.
- **Very recent uploads:** bleeding edge, but preliminary.
- **Quality and reach:** read views against subscribers and engagement against views. Production value is not credibility. Agreement across independent sources beats any single review.
- **Interest level (signal, not statistics):** upload volume and velocity, views relative to channel size, and the mix of result types. State dates and sample.

## Errors: who acts

- **Needs the user. Stop, quote the message, don't retry:** any `stop` (bot check, CAPTCHA, consent or age gate, signed out, block); "the screen is locked/asleep"; "the research window is hidden"; `open` can't restore the browser; the page-load cap is reached.
- **Wait, then retry once:** "all N research windows are in use" (`youtube_open` with `wait_seconds: 45`, a few times); one page timing out or not loading (wait about 30 s, retry once, then skip that video); `youtube_search` returning `settled: false` (repeat it once, or simplify the query).
- **Skip, don't retry:** transcript `youtube_error` or `no_transcript`, and `ad_timeout` frames (at most one retry, as above). A missed click falls back to the URL by itself (`page.opened`).

## Output

**YouTube-focused task:**

1. **Key findings:** linked with timestamps, with frames inline where useful.
2. **Interest read:** with the sample.
3. **Sources table:** video (linked), channel and subscribers, type, published date, views, sponsor bias (final score and reasons), takeaway.
4. **Caveats.**
5. **Sample size:** videos, transcripts (and unavailable ones), frames (and any `ad_timeout`), page loads (searches vs videos).
6. **Saved transcripts:** the `saved` paths, listed with the sources.

**When a channel map was made**, add the channel table and the who-to-trust sentences (see *Channel map*) before the sources table.

**When a landscape pass was done**, add before the key findings:

- **Landscape table:** the top products against the dividing lines (one row per product, one column per dividing line, plus price and availability), with the source links behind each cell.
- **Dividing lines:** each real trade-off in a sentence ("you gain X, you give up Y"), plus a note of the table stakes and the marketing-only differences.
- **Segments and how to choose:** group products by the trade-offs they make, then a short "if you need A, look at B" guide.
- **Considered and left out:** the products you excluded, and why.

**YouTube among other sources:** fold the findings in as creator or commenter claims with disclosure status, and say where they agree or conflict with owner reports and vendor information.
