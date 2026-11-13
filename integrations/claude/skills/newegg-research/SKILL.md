---
name: newegg-research
description: Research Newegg (newegg.com) by reading real pages in the user's signed-in Chrome (a shared chrome-agent browser). Read-only. Use when the user asks what Newegg lists, prices or rates for a product or category (PC parts, electronics), or wants Newegg included among other sources: landscape, specs, price, popularity and average rating. Signed in with the research Google account.
---

# Newegg Research

```bash
H=~/.claude/skills/newegg-research/scripts/newegg_tab.py
```

## Hard rules

1. **Use `$H` only.** Never touch another agent's window or send raw CDP commands to the shared browser.
2. **Read-only.** Never buy, add to cart, review, ask a question, change the account or the store.
3. **If a result contains `stop`, stop and tell the user why.** Never solve CAPTCHAs; never retry another way.
4. **Budget:** 15 page loads unless the user says otherwise. Each result reports `loads`.
5. **Always `$H close` your window** when done, even after errors.

## Commands

```bash
$N open --wait                                   # -> {"target": "<ID>"}
$N search <ID> "rtx 5070"                        # typed into the search box on newegg.com, else by URL (1 load)
$N product <ID> '<product url>'                  # header, price, seller, specs, rating summary, first reviews (1 load)
$N reviews <ID> '<product url>' --sort lowest --filter verified [--filter current-item] [--pages 2]
$N qa <ID> '<product url>'                       # the Q & A tab
$N shot <ID> [--out PATH]
$N close <ID>
```

- **`search`** returns cards: `item`, `url`, `title`, `brand`, `price`, `was_price`, `rating`, `reviews`,
  `refurbished`, `ships_from`, `direct_from` (a marketplace seller ships it) and `promo`. `product` clicks a
  card from the current results when it's listed there; `reviews` and `qa` reuse an open product page (no load).
- **`product`** opens the Specs and Reviews tabs like a reader: `title`, `brand`, `model`, `price`,
  `was_price`, `sold_by` (Newegg or a marketplace seller), `rating`, `ratings_count`, `reviews_with_text`,
  `histogram` (star → %), `best_seller_rank`, `questions` (count), `bullets`, `specifications`, the first
  reviews and `review_flags`.
- **`reviews`** (25 a page) uses the tab's own sort menu (`helpful`, `newest`, `highest`, `lowest`,
  `ownership`) and filter boxes (`current-item`, `verified`, `mfr`, `media`, `no-incentive`), then Next.
  Each review: `author`, `verified_owner`, `ownership` (how long they've had it), `stars`, `title`, `date`,
  `related_product` (the variant it was written for), `pros`, `cons`, `overall`, `helpful`,
  `manufacturer_response`. `summary` is the rating summary for the chosen filters.
- **`qa`** returns questions with answers; `ai` marks answers by Newegg's AI assistant (generated from the
  listing), and store-asked prompts ("Why did you choose this?", `asker_badge`: Store) collect owners' reasons.

## Using what you find

Retail sites are for the landscape (what's sold, by whom), specs, price, popularity (rating counts,
best-seller rank) and average rating, not for opinion bias the way YouTube or Reddit are.

- **Average rating accuracy:** Newegg pools reviews across a product's variants (`related_product`). Check
  `reviews --filter current-item`: e.g. an ASUS RTX 5060 Ti 8GB card showed 4.8 from 65 ratings, but only 1
  rating was for that exact card; the rest were for the 16GB editions. Say so when it's the case.
- **Seller:** `sold_by` other than Newegg (or `ships_from` abroad) is a marketplace listing: prices and returns
  differ.
- **Prices** change constantly; give the date.
