---
name: youtube-research
description: Research YouTube by reading real YouTube pages (search results, video metadata, transcripts, comments, channels) in the user's signed-in Chrome (a shared chrome-agent browser), capture frames that show features or data, and report findings with skepticism toward creator endorsements. Use whenever the user asks for YouTube research, asks what YouTubers/reviewers/videos say or show about something, or wants YouTube included among other sources (e.g. "check YouTube too", "include video reviews", "what are reviewers saying"). Also use for product feature, comparison, alternatives, landscape ("what are the best/top X"), interest-level or pre-release research that lists YouTube as a source. Read-only; never likes, subscribes, comments, or downloads.
---

# YouTube Research

Read normal YouTube pages in a real Chrome signed in to the user's (spare) YouTube account. Contrast many videos to ground what you report, capture frames that show what matters, and treat creator opinions with skepticism. Other agents may be researching at the same time. The helper gives each agent its own window, paces everyone's page loads together, and acts like a person would (typed searches, clicked results, real clicks, mouse-wheel scrolling, the player's own mute button and progress bar).

```bash
Y=~/.claude/skills/youtube-research/scripts/youtube_tab.py
```

## Hard rules

1. **Use `$Y` only.** Not WebFetch, other browser tools, yt-dlp, or new or headless chrome-agent launches. Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never like, dislike, subscribe, comment, save to playlists, report, or change settings.
3. **Act naturally.** The helper does this for you:
   - Each video plays muted for a few seconds after loading.
   - Audio is muted with the player's mute button, and theater mode is turned on with its button for larger frames.
   - Before a jump, playback is paused with the play/pause button.
   - Jumps are progress-bar clicks, finished with the arrow keys. This lands within about ±2 s even when YouTube snaps a click to a chapter start.
   - Every click follows a human-like mouse path (curved, uneven speed, sometimes a small overshoot) after a moment to find the target. YouTube refuses some requests, transcripts in particular, when the pointer jumps straight to buttons.
   - Between frames, the video plays for a moment.
   - Searches are typed into YouTube's search box when a YouTube page is open and no `--sort`/`--upload` filter is set (`page.via`: `typed`); the first search in a new window, and filtered searches, load the results URL (`url`, `url (filters)`).
   - `video` clicks the video in the current results or related list when it is there, or goes back to the last search and clicks it (`page.opened`: `clicked`, `clicked (after back)`); otherwise it loads the URL (`url`, or `url (click missed)` etc. when a click didn't work). So open videos from your latest search when you can.
   - **Ads are never paused, seeked or keyed.** The helper waits them out, clicks Skip after a human-like delay, and resumes an ad that was left paused with one click. Don't script the player or page yourself (`eval` is for reading).
4. **No downloading, no internal APIs** (the helper refuses `youtubei`, `timedtext` and similar).
5. **If a result contains `stop`, stop and tell the user why.** This covers bot checks, CAPTCHAs, "unusual traffic", consent or age gates, and signed-out sessions. Never retry another way.
6. **Budget:** 40 page loads for a YouTube-focused task, 15 when YouTube is one source among several, about 12–15 for the middle case (a light pass plus complaints, or named products plus a landscape check), and about 20 for a category or landscape question within a broader task. Split it between searches and videos: a landscape usually needs about 6 searches and 10–12 videos. Each result reports `loads`. `transcript`, `frame`, `comments` and `shot` work on the loaded video and don't count, but keep them purposeful. Ask before exceeding the budget.
7. **Always `$Y close` your window** when done, even after errors. Never stop the browser itself.

## Commands

```bash
$Y open --wait                                   # -> {"target": "<ID>"}
$Y search <ID> '"XREAL One Pro" review'          # --sort relevance|views  --upload hour|today|week|month|year  --shorts
$Y video <ID> '<watch url>'                      # metadata, chapters, description, sponsor_bias (1 page load)
$Y transcript <ID> --out-dir <report dir> --context 1 --grep battery "sent me"   # segments {at, sec, text, link}; full transcript saved
$Y frame <ID> 2:43 6:17 --out-dir <report dir>   # theater, pause, jump, capture each -> {path, actual, sec, landing_error_s, link, ad_seen, ad_timeout, ad_log, warning?}
$Y frame <ID> 6:17 --burst 5 --out-dir <dir>     # land, then let it play 5 s, one candidate per second (controls hidden) -> candidates[]
$Y frame <ID> --scan 12:00-19:00 --out-dir <dir> # sample the range, keep the 3 frames most likely to show a chart/table/slide/UI
$Y comments <ID> --max 20                        # one batch of top comments (+ comment_promo_hint)
$Y shot <ID> --out-dir <report dir>              # visible page content (top bar and left guide left out) + ads_in_view
$Y shot <ID> --selector '#description' --out-dir <dir>   # one element, scrolled into view first
$Y channel <ID> @handle                          # channel header + recent uploads (1 page load)
$Y tally <ID> "Bambu P2S" "Qidi Q2" --alias "Bambu P2S=P2S; Qidi Q2=Q2,Cheety Q2"   # channels / independent channels per name (no load)
$Y transcript <ID> --from 4:00 --to 6:30         # only a time window (e.g. one chapter)
$Y close <ID>
$Y list                                          # active research windows across all agents
$Y gc                                            # release windows left behind by crashed agents
$Y nav <ID> '<youtube url>'                      # escape hatch: paced load of any youtube.com page
$Y eval <ID> '<js>'                              # escape hatch: read something the commands don't return
```

Images are ~1600 px JPEGs (`--full` for full-resolution PNG). `open`, `list`, `transcript`, `frame`, `comments` and `shot` cost no page load; `search`, `video`, `channel` and `nav` cost one each. Errors are JSON (`{"error": "..."}`) with exit code 1. `video` accepts watch, `youtu.be` and Shorts links. Search results never carry `t=`; on a signed-in account that would be a watch-history resume point, not a citation.

Quote URLs and queries. YouTube no longer honors sorting by date, so use `--upload` for recency.

- **Segments** are `{at, sec, text, link}`: `at` is "4:11", **`sec` is the start in seconds** (251; there is no separate `seconds` field), and `link` opens that moment. Duplicates are removed (YouTube can render a transcript twice); `total_segments` is always the full, deduplicated count.
- **Saved transcripts:** every successful `transcript` call saves the **full** transcript (whatever `--grep`/`--from`/`--to` return), at no extra page load, as `YYYY-MM-DD_<video-id>_transcript.json` (title, channel, URL, published, fetched-at, language, sponsor_bias at fetch time, segments) and a readable `.md` (`[mm:ss](link) text` lines). Pass `--out-dir <report dir>`; without it they go to `~/.local/share/chrome-agent-research/youtube-transcripts/`. `saved` returns both paths (`reused: true` when that video's transcript was already there). **List the saved transcripts in the report's sources** so they can be reviewed later.
- **`--grep`** matches case-insensitive plain-text substrings (any term; not regex). With `--grep`, the output adds counts: `matched_segments` (how many segments contain a term; each such segment is tagged `matched: [terms]`) and `returned_segments` (including `context: true` neighbors).
- **Auto-captions garble numbers:** "$3.99" may really be $399, and "1,0" may be 1,000. Grep both forms (`$799` and `799`, `1000` and `thousand`), and verify prices and specs against a frame, the description, or another source before citing them.
- **Transcript unavailable:** `reason` says why. The helper opens transcripts the way a person does (skims the description, finds the button, mouse path and all), which is what lets YouTube serve them; if it still fails, `youtube_error` means YouTube refused it.
  1. **Don't retry**, and don't try other ways to get captions.
  2. **Note it** for that video ("transcript: unavailable, youtube_error").
  3. **Carry on:** use chapters, the description, comments, and frames at chapter starts for that video. If its spoken content is essential, pick another video on the same angle.
  4. **Report** the count of unavailable transcripts in the sample size, and mention in caveats when that limits a finding (e.g. sponsor reads couldn't be checked).

## Landscape: establish the field first (when warranted)

Use a **full landscape pass** when the user names no products, asks what the options are, or the question is about a category. Use a **light pass** (2–3 page loads) when products are given, to check that no major alternative is missing; mention any you find under "Also consider".

1. **Find roundups and comparisons.** **Start with the use case**, phrased as a buying question: `"best <product> for <task>"` or `"which <product> for <task>"` (`"best 3D printer for functional parts"`, `"which AR glasses for coding"`). A bare `"<product> for <task>"` tends to return how-to videos ("things to print"). Then `"best <category> <year>"`, `"<category> buyer's guide"`, `"<category> comparison"` or `"tier list"`, `"<product> vs"`, `"<product> alternatives"`.
   - Use `--upload year`. `--sort views` can pull in off-topic viral videos, so use it once at most.
   - **Skip AI listicle channels:** results, videos and channels carry `likely_ai_listicle: true` (with `listicle_reasons`) for future-year channel names or titles (`@TheForemostPicksof2027`), generic "Picks / Reviews Club / Top5Lab / Scout" names, "Top N" titles and very high upload counts. Also watch for slideshow or stock b-roll thumbnails and synthetic narration. Don't count their mentions; `tally` excludes them.
   - **Skip Amazon-influencer clips:** `amazon_influencer: true` marks Amazon storefront links, "#amazonfinds"-style tags, and very short "reviews" with Amazon links and little reach, often of unbranded kits (e.g. many MAYBESTA clips). Use-case searches for cheap gear are full of them.
2. **Read 2–4 roundup or comparison videos.** Their chapters often list the products; `transcript --grep "best" "pick" "alternative" "instead" "vs"` shows the verdicts.
3. **Tally candidates** with `$Y tally <ID> <name> <name> ...`. Among everything this window has seen, it counts the channels and the *independent* channels mentioning each name, with snippets and links (`examples`).
   - **Merged records:** a loaded video replaces its search result, so its description, description links and (when read) transcript count. `--no-transcripts` leaves transcripts out.
   - **Whole names, longest wins:** "Core One" doesn't count "Core One+", "MK4" doesn't count "MK4S", "Lark M2" doesn't count "Lark M2S". Add spellings with `--alias "Core One=C1,Core 1; Lark M2=M2 kit"`; common caption garbles ("Bamboo"/"Bumble Lab" = Bambu, "Cheety" = Qidi, "Eligu" = Elegoo, "Pruza" = Prusa) are built in.
   - **Independence:** a channel isn't independent for a name when one of its videos mentioning that name scores 0.6 or more `sponsor_bias` (the latest score, so read transcripts first), when it is strongly brand-owned, or when it is a likely AI listicle or Amazon-influencer channel. `excluded` gives the reasons. In multi-brand videos the brand's own `by_brand` score applies, so put the brand in the name (`"Bambu P2S"`) or alias the bare model to it.
   - Description affiliate links are often the cleanest product list. A product appearing only in its own sponsored or referral-code videos isn't evidence of standing.
4. **Pick the top handful** (usually 4–6) and say why. List notable products you left out, with the reason (niche, discontinued, unreleased, little coverage).
5. **Find the dividing lines:** the 3–5 features or design choices where products genuinely split and buyers must give something up (e.g. tethered vs standalone, display vs camera-only, field of view vs weight, open SDK vs closed ecosystem, price tier).
   - Find them in what comparison videos argue about, what owners complain about, and where reviewers say "if you need X, get Y".
   - Separate these from **table stakes** (everyone has them) and from **marketing differences** that don't change real use. Say which is which.
6. **Position each product** against the dividing lines, then continue with the normal workflow for the products that matter to the question.

**When the field is really two or more categories** (e.g. virtual-monitor glasses vs heads-up display glasses), say so first. Treat the top split as the primary dividing line, and choose the top products within each category rather than ranking across them.

Budget: a full landscape pass usually takes 8–12 page loads on its own (about 4–6 searches and 4–6 videos), so allow about 20 for a landscape question, or tell the user the landscape will be thinner.

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

**Every claim and every frame gets a timestamped link** so the user can open YouTube at that moment and watch. Use the `link` from transcript segments and frames (it uses the time actually landed on), or `https://www.youtube.com/watch?v=<id>&t=<seconds>s`.

### 4. Frames and screenshots

- **Capture** stat tables, benchmark charts, spec slides, side-by-side comparisons, a feature in use, visible defects, UI, and telling search pages.
- **Label what an image really shows.** Separate real through-the-lens or screen captures from editing composites and mock-ups (overlays added in editing, renders, marketing animations). Most "what you see" shots in reviews are composites; say so in the caption.
- **Look at every image with Read before using it.** It must show the expected content, not an ad, sponsor segment, b-roll, talking head, or transition. `frame` waits out or skips ads and reports `ad_seen`, `ad_in_frame` and an `ad_log` (what it saw, and when), but the image is the check. If it's wrong, try a few seconds later.
- **Ads:** the helper never pauses, seeks or keys during an ad, and re-checks the ad state right before every capture. If the ad doesn't end in time (`YOUTUBE_TAB_AD_TIMEOUT`, 60 s), the frame reports **`ad_timeout: true`** with no image. Skip that frame and note it in the sample size; don't reload and retry more than once. `video` reports `ad_state` with the time it was checked; an ad seen there may be over by the time you call `frame`.
- **If `warning` says the player is small** (under 1280 px wide in theater mode, or much smaller than earlier), tell the user the research window is too small (a tiling window manager may have shrunk it). Don't resize it.
- **Finding a graphic:** talking heads dominate most reviews. `frame --scan 12:00-19:00` samples a few points in the range with short bursts and keeps the 3 frames most likely to show a chart, table, slide or UI (`graphic.score`: edge density, flat colors, little skin tone; a hint, not a judgment). Use it when chapters or the transcript point to a section with a chart but not to the second.
- **Save** into the report's folder with `--out-dir <dir>`. Files are ~1600 px JPEGs named `YYYY-MM-DD_<videoid>_at03m49s.jpg` from the time actually landed on (`-2`, `-3` is added rather than overwriting; `--full` for PNG); rename if you like. Caption each image with video, channel, and the timestamped link. A handful of telling images beats dozens.
- **Several moments in one video:** pass several timestamps to one `frame` call.
- **Screenshots of pages** (`shot`): `ads_in_view` lists ads in the image (search "Sponsored" units, masthead ads, a video ad in the player). Caption them as ads, or scroll and retake. `--selector <css>` captures one element (e.g. `#description`). The signed-in account's name or avatar appearing in a shot is fine.
- **Busy or moving moments** (a demo, a chart that animates in, a slide that changes): use `--burst 5`. The video lands, plays on muted, and a candidate is captured every second with the controls hidden. Look at the candidates, keep the best, and delete the rest. This beats jumping repeatedly, which looks odd. Frames land within about ±2.5 s, since YouTube can snap a click to a chapter start, so a burst also covers that.

### 5. Close

`$Y close <ID>`.

## Weighing sources

**Creators are marketing-adjacent until shown otherwise.** Lean on videos for features, demos, interest level, comparisons, and alternatives, more than for verdicts.

- **`sponsor_bias`** (0.0–1.0) estimates how likely a video is sponsored or sponsor-influenced, and lists the evidence (each with its source and, for the transcript, a link).
  - `video` scores the page: the paid-promotion label, YouTube Shopping products marked "Earns commission", campaign-tracking links, affiliate links (networks and brand `/affiliates/` paths) and "(affiliate link)" labels, and sponsor, review-unit or "use my code" wording in the title and description. Its `confidence` is **low** until the transcript is scanned, so a low page-only score is not a clean bill.
  - `transcript` rescans the **whole** transcript (whatever you grep) and returns an updated `sponsor_bias` plus `disclosure_mentions`:
    - sponsor reads ("thanks to X for sponsoring")
    - review units, early access, embargoes ("they sent me")
    - "use my link" or "use code X at checkout"
    - "I bought it with my own money"
    - "they didn't see the video before it went live"
  - Raising signals: sponsor reads, campaign links, review units, affiliate links and disclosures, link or code pitches. Lowering signals: bought it myself, claims of not being sponsored, editorial-independence claims. Each counts once.
  - **Whose sponsor?** A sponsor read names the sponsor (`sponsor`). If it isn't the brand being reviewed (e.g. an accessory maker sponsoring a Meta review, a music library's discount code), it's recorded as `third_party_sponsor` with a lower weight: it shows commercial ties, not bias toward the product. YouTube's "Includes paid promotion" label then weighs 0.2 instead of 0.6 (`third_party_sponsor` on the label's evidence). Negated lines ("they did not sponsor this") count as denials.
  - **Roundups:** when a video names two or more brands, `by_brand` scores each brand on its own (its sponsor reads, codes, giveaways and the affiliate links on its description lines), and `roundup: true` marks three or more. **Read the reviewed brand's own score there**, not the overall score, which mixes every brand's signals. `shared_score` covers signals tied to no brand.
  - **Brand-aware:** a channel that is the brand itself scores 1.0 (`brand_owned_channel`, `brand_owned: "strong"`: the channel matches the brand in the title and its handle matches a brand domain). A name match alone is `brand_owned: "possible"` (0.3); check it with `channel`. A creator's own shop or site links are `self_promotion`, never brand ownership. Affiliate links and codes tied to the reviewed brand (`brand_affiliate_links`, `brand_code_or_link`) weigh more than generic ones. "I bought it myself" lowers the score only when it is about the reviewed product, which matters in multi-product roundups.
  - **Adjust the result yourself:** higher for channels mostly covering one brand (`channel`); lower only for credible evidence of independent purchase.
  - `video` puts `sponsor_bias` under `video`; `transcript` returns the updated one at the top level.
  - **Report the final score and its main reasons** in the sources table.
- **Comments:** `comment_promo_hint` (low confidence) flags clusters of short, generic, slangy pro-brand praise ("flows so much better fr", "ngl ... so smooth") that may be seeded; each such comment carries `promo_hint`. Don't count those comments as owner experience.
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
3. **Sources table:** video (linked), channel and subscribers, type (official, large, small, Short), published date, views, sponsor bias (final score and the main reasons), one-line takeaway.
4. **Caveats:** sponsorship or affiliate dependence, embargo clustering, preliminary information, dates spanned.
5. **Sample size:** videos read, transcripts used (and how many were unavailable), frames captured (and any `ad_timeout`), page loads used (searches vs videos).
6. **Saved transcripts:** the `saved` JSON/`.md` paths, listed with the sources.

**When a landscape pass was done**, add before the key findings:

- **Landscape table:** the top products against the dividing lines (one row per product, one column per dividing line, plus price and availability), with the source links behind each cell.
- **Dividing lines:** each real trade-off in a sentence ("you gain X, you give up Y"), plus a note of the table stakes and the marketing-only differences.
- **Segments and how to choose:** group products by the trade-offs they make, then a short "if you need A, look at B" guide.
- **Considered and left out:** the products you excluded, and why.

**YouTube among other sources:** fold the findings in, attributed as YouTube creator or commenter claims with their disclosure status. Use video evidence mainly for features, demos, and comparisons, and say where it agrees or conflicts with owner reports (e.g. Reddit) and vendor information.

If saving to disk, name the report `YYYY-MM-DD_youtube-<topic>.md`, with frames alongside it.

## Stop and ask the user when

- A result contains `stop`, or `open` fails (browser can't be restored, or no slot after waiting).
- The plan needs more page loads than the budget.
- The task would require writing to YouTube or downloading media.
