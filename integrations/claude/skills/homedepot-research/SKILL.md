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
$H open --wait                                  # -> {"target": "<ID>"}
$H search <ID> "cordless drill"                 # typed into the search box on homedepot.com, else by URL (1 load)
$H product <ID> '<product url>'                 # facts, price, specs, rating histogram, AI summary, first reviews (1 load)
$H reviews <ID> '<product url>' --sort lowest --verified [--stars 1] [--page 2] [--pages 2]   # 30 a page (1 load a page)
$H qa <ID> '<product url>' [--page 2]           # questions with answers opened; brand answers flagged (1 load)
$H shot <ID> [--out PATH]                       # look at it before using it
$H close <ID>
```

- **`search`** returns product cards: `id`, `url`, `brand`, `title`, `price`, `was_price`, `rating`, `reviews`,
  and `badges` ("Best Seller", "Exclusive", "Top Rated"). A search often redirects to a
  category page (`page.url`). `product` clicks a card from the current results when it's listed there.
- **`product`** opens the Product Details, Specifications and Customer Reviews sections like a reader and
  returns `name`, `brand`, `model`, `sku`, `price`, `rating`, `review_count`, `recommend_pct`, `histogram`
  (stars → count), `ai_summary` and `review_themes` (Home Depot's own AI summary of the reviews: a summary,
  not evidence), `highlights`, `specifications`, `breadcrumbs`, `questions` (count), the first 10 reviews and
  `review_flags`, plus `reviews_url` / `questions_url`.
- **`reviews`** sorts with the page's own control (`helpful`, `newest`, `oldest`, `highest`, `lowest`,
  `photo`) and filters with its buttons (`--verified`, `--stars N`). Each review has `date`, `stars`, `title`,
  `text`, `author`, `helpful`, and the flags below.
- **`qa`** opens up to `--max` (8) questions' answers. Each answer has `by`, `date`, `badges`, `text`,
  `brand_answer` and `other_variant` (the brand answered about a sibling model: check before quoting).

## Using what you find

Retail sites are for the landscape (what's sold, by whom), specs, price, popularity (review counts, badges)
and average rating, not for opinion bias the way YouTube or Reddit are; promoted placements don't matter here.

- **Average rating accuracy:** `variant` reviews are for a sibling product (another kit, size or color) pooled
  into this rating; when a product has many, say the rating is shared across variants.
- **Q&A:** `brand_answer` answers come from the manufacturer; `other_variant` means it's about a sibling model,
  so check before quoting it as this product's spec.
- **Prices** change constantly and vary by store (the browser's store is set by Home Depot from location);
  give the date.
