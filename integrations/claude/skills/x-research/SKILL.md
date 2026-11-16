---
name: x-research
description: Research X / Twitter (x.com) by reading real posts, threads, replies and profiles in the user's signed-in Chrome (a shared chrome-agent browser), then report what people say with linked sources and a read on promotion and reach. Use whenever the user asks for X or Twitter research, asks what people on X/Twitter are saying about something, or wants X included among other sources (e.g. "check Twitter too", "include X", "what's the reaction online"). Also use for launch reactions, breaking news, developer/creator sentiment, complaints, or who the voices on a topic are. Read-only; never posts, likes, reposts, follows, or messages.
---

# X (Twitter) Research

```bash
H=~/.claude/skills/x-research/scripts/x_tab.py
```

## Hard rules

1. **Use `$H` only.** Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never post, reply, like, repost, bookmark, follow, vote in a poll, message, open Grok, or change the account.
3. **If a result contains `stop`, stop and tell the user why.** Never solve CAPTCHAs; never retry another way. X locks
   accounts that look automated, so a wall (login flow, `/account/access`, rate limit) ends the run.
4. **Budget:** 15 page loads unless the user says otherwise. Each result reports `loads`. Scrolling within a page
   doesn't count, but keep `--max` moderate (≤ 60).
5. **Always `$H close` your window** when done, even after errors.

## Commands

```bash
$H open --wait                                   # -> {"target": "<ID>"}
$H search <ID> "sony a7c ii" [--latest] [--max 40]   # typed into the search box (1 load), then scrolls
$H post <ID> '<post url>' [--max 40]             # the post, thread above it, and replies (1 load)
$H profile <ID> @handle [--max 20]               # bio, followers, joined, verified + recent posts (1 load)
$H shot <ID> [--out PATH]
$H close <ID>
$H log [<ID>] [--since 2h] [--last N] [--raw]    # action log (no page load)
```

- **`search`** takes X's search operators in the query: `"exact phrase"`, `from:handle`, `to:handle`, `lang:en`,
  `since:2026-10-01 until:2026-11-01`, `min_faves:20`, `min_replies:5`, `-filter:replies`, `filter:links`,
  `-is:retweet`, `OR`. Top (default) ranks by engagement; `--latest` is newest first. Each post: `url`, `name`,
  `handle`, `verified`, `date`, `text`, `lang`, `replies`, `reposts`, `likes`, `bookmarks`, `views`, `media`,
  `quoted`, `reposted_by`, `promoted`, `truncated` (long post: open it with `post` for the full text),
  `community_note`. `summary` gives post/author counts, verified authors, promoted, and the date range.
- **`post`** clicks the post in the current page when it's listed there (pick from a search rather than pasting URLs).
  Returns `thread_above`, `post`, `replies` (X's ranking) and `hidden_reply_sections` (low-quality/probable-spam
  replies X folds away; not read).
- **`profile`** is for judging a voice: `followers`, `following`, `joined`, `verified` / `verified_kind`
  (blue = paid Premium, gold = organization, grey = government), `affiliation`, `bio`, and recent posts with
  `own_posts` vs `reposts`.

## Using what you find

- **Verified ≠ credible.** Blue checks are paid (Premium) and Premium replies are ranked higher. Don't treat a
  check mark or reply position as authority; use followers, history (`profile`), and what the account does.
- **Promotion:** flag `promoted` posts, brand and affiliate accounts, creators posting launch-day praise with
  links (`media.card`), and posts with a `community_note`. Note when the loudest posts come from the brand itself.
- **Reach vs sentiment:** high `views` with few replies is broadcast, not discussion. Report counts with the date.
- **Sampling:** Top search is engagement-biased; for "what are people saying", run both Top and `--latest`, and
  narrow with `since:`/`lang:`. Say how many posts you read.
- **Absence claims** ("nobody on X mentions X", "no complaints") need a second search with different wording
  (and `--latest`) before they go in a report.
- Link every quoted post by `url`.

## Action log

Every command is logged per window in `~/.local/state/chrome-agent-research/logs/x/` (`$H log [<ID>] [--since 2h]`);
a `stop` saves a screenshot (`stop_shot`) and the rule that matched (`blocked_by`). When a run hits a `stop` or
behaves oddly, say so in the report and include the log's path.
