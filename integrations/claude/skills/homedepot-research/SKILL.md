---
name: homedepot-research
description: Research Home Depot (homedepot.com) by reading real pages in the user's signed-in Chrome (a shared chrome-agent browser). Read-only. Use when the user asks what Home Depot lists, prices or rates for a product or category, or wants Home Depot included among other sources. Signed out (the site requires a phone number to create an account).
---

# Home Depot Research

```bash
H=~/.claude/skills/homedepot-research/scripts/homedepot_tab.py
```

## Hard rules

1. **Use `$H` only.** Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never buy, add to cart, review, ask a question, change the account or the store.
3. **If a result contains `stop`, stop and tell the user why.** Never solve CAPTCHAs; never retry another way.
4. **Budget:** 15 page loads unless the user says otherwise. Each result reports `loads`.
5. **Always `$H close` your window** when done, even after errors.

## Commands

```bash
$H open --wait                          # -> {"target": "<ID>"}
$H search <ID> "cordless drill"         # typed into the search box when on homedepot.com, else by URL (1 load)
$H nav <ID> '<homedepot.com url>'       # a product or category page (1 load)
$H shot <ID> [--out PATH]               # look at it before using it
$H close <ID>
```

`search` returns product cards: `id`, `url`, `brand`, `title`, `price`, `was_price`, `rating`, `reviews`,
`sponsored` (an ad) and `badges` ("Best Seller", "Exclusive", "Top Rated"). Home Depot often redirects a
search to a category page (`page.url`). Ratings include reviews syndicated from the brand's own site.
Readers for product pages, reviews and Q&A aren't built yet.
