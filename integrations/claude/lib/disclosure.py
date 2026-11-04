"""Disclosure signals shared by the research skills (YouTube, Reddit).

Finds sponsorship, review-unit, affiliate and promotion signals in text, and
scores them 0.0-1.0 with the evidence behind the score. Brand-aware: signals
tied to the brand being discussed weigh more than unrelated ones.
"""
import re

# Signals of sponsorship or sponsor influence. Each category counts once (the strongest hit wins),
# whether it was found in the description, the title or the transcript.
SIGNALS = [
    # (category, weight, pattern)
    ("sponsor_read", 0.5, r"\b(this (video|episode) is sponsored by|sponsored by|thanks? (you )?to \S+( \S+)? for sponsoring|today'?s sponsor|in (paid )?partnership with|paid partnership|#ad\b|#sponsored|brought to you by)"),
    ("review_unit", 0.25, r"\b(sent (it |this |them |these )?(to )?(me|us)|provided (by|to me)|loaned|on loan|review (unit|sample)|gifted|early access|under embargo|embargo (lift|ends))"),
    ("use_my_link_or_code", 0.15, r"\b(use my (link|code)|use (the )?code \S+|code \S+ (at checkout|for \d+ ?%)|discount code|promo code|\d+ ?% off (with|using)|(buy|get|order|grab|pick up) (one|yours|it|them|a pair)? ?(through|using|with|from|via) (my|the) (link|affiliate))"),
    ("affiliate_disclosure", 0.1, r"(\baffiliate links?\b|\(affiliate\)|\bi (may )?earn (a )?(small )?commission|commission (from|on) (any|qualifying) purchases|as an amazon associate)"),
    ("bought_it_myself", -0.15, r"\b(i (bought|purchased|paid for) (it|this|them|these|my own|.{0,20}with my own money)|with my own money|bought (it|them|this|these) myself|i bought)"),
    ("claims_not_sponsored", -0.1, r"\b(not (an? )?(sponsored|paid|gifted)|no(body| one) (paid|sponsored)|un-?sponsored|isn'?t (an? )?sponsored|wasn'?t (an? )?(sponsored|paid)|no sponsors?\b)"),
    ("editorial_independence", -0.05, r"\b((they|\w+) (did ?n'?o?t|do ?n'?o?t|never) (see|review|approve|preview|get to see) (the|this|my) (video|review)( before| ahead| in advance)?|(did ?n'?o?t|do ?n'?o?t|never) (have|get) (any )?(input|say|editorial (control|input))|no editorial (control|input|say)|without (their|any) (input|review|approval))"),
]
COMPILED = [(c, w, re.compile(p, re.I)) for c, w, p in SIGNALS]
GUIDE = ("Estimate from the page and transcript. Set 1.0 for brand-owned channels; raise it for channels mostly "
          "covering one brand; report the final score with reasons.")


SPONSOR_NAME = re.compile(r"(?i:sponsored by|thanks? (?:you )?to|brought to you by|in (?:paid )?partnership with|today'?s sponsor(?: is)?)\s+([A-Z][\w&.+-]*(?:\s+[A-Z][\w&.+-]*){0,2})")


def scan_text(text: str, source: str, link: str | None = None, subject: str = "") -> list[dict]:
    """Find disclosure signals. A sponsor read for a brand that isn't the video's subject is
    recorded as a weaker third-party sponsorship."""
    hits = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        for cat, w, pat in COMPILED:
            m = pat.search(sent)
            if not m:
                continue
            # A negated sponsorship line is a claim of independence, not a sponsor read.
            if cat in ("sponsor_read", "review_unit") and re.search(r"\b(not|no|never|un-?)\s*(sponsor|gift|paid|provid|sent)", sent, re.I):
                continue
            hit = {"signal": cat, "weight": w, "source": source, "text": sent.strip()[:220], **({"link": link} if link else {})}
            if cat == "sponsor_read":
                nm = SPONSOR_NAME.search(sent)
                name = nm.group(1).strip().rstrip(".,!;:") if nm else None
                hit["sponsor"] = name
                if name and subject and not any(tok.lower() in subject.lower() for tok in re.findall(r"[A-Za-z0-9]{3,}", name)):
                    hit.update(signal="third_party_sponsor", weight=0.15)
            hits.append(hit)
    return hits


def score(page: list[dict], extra: list[dict]) -> dict:
    """Combine evidence: each category counts once, with its first (strongest-source) hit kept."""
    score, kept, seen = 0.1, [], set()
    for e in page + extra:
        if e["signal"] in seen:
            continue
        seen.add(e["signal"])
        score += e["weight"]
        kept.append(e)
    score = round(min(1.0, max(0.0, score)), 2)
    return {"score": score, "level": "high" if score >= 0.6 else "medium" if score >= 0.3 else "low",
            "evidence": kept, "note": GUIDE}


GENERIC = {"the", "and", "official", "channel", "tech", "review", "reviews", "glasses", "smart", "video",
            "videos", "best", "new", "with", "for", "pro", "max", "plus", "ultra", "air", "one", "app", "apps"}


def brand_tokens(subject: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", subject or "")} - GENERIC


def brand_adjust(hits: list[dict], subject: str, channel: str | None) -> list[dict]:
    """Make signals brand-aware: brand-owned channels, brand affiliate links and codes weigh more;
    "I bought it myself" counts only when it is about the reviewed product."""
    toks = brand_tokens(subject)
    out = []
    for h in hits:
        txt = (h.get("text") or "").lower()
        about_brand = any(re.search(rf"\b{re.escape(t)}", txt) for t in toks)
        if h["signal"] == "affiliate_links" and about_brand:
            h = {**h, "signal": "brand_affiliate_links", "weight": 0.25}
        elif h["signal"] == "use_my_link_or_code" and about_brand:
            h = {**h, "signal": "brand_code_or_link", "weight": 0.2}
        elif h["signal"] == "bought_it_myself" and not about_brand and h.get("source") != "title":
            h = {**h, "signal": "bought_something_myself", "weight": -0.05}
        out.append(h)
    ch = (channel or "").lower().replace("official", "").strip()
    if ch and len(ch) >= 3 and (ch in (subject or "").lower() or any(t == ch.replace(" ", "") for t in toks)):
        out.insert(0, {"signal": "brand_owned_channel", "weight": 1.0, "source": "channel",
                       "text": f"Channel '{channel}' appears to be the brand itself"})
    return out


