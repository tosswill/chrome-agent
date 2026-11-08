"""Channel map for YouTube research: who covers a topic, and how far to trust them.

Pure functions (no browser), so they can be tested. Each channel gets three scored axes (0-5) from a
sample of its videos the window has already read, plus context:

* independence - how little the coverage depends on the brands reviewed (sponsor reads, review units,
  brand codes/links, brand ownership; generic affiliate links count only a little).
* transparency - when money is involved, is it disclosed clearly and early (paid-promotion label,
  spoken sponsor read, affiliate disclosure) rather than shown only by brand links or codes?
  ``None`` when the sample had nothing to disclose.
* rigor - how much real testing the transcript shows (measured results with units, controlled
  comparisons, instruments, stated method, long-term follow-ups) versus spec-sheet reading.
  Needs transcripts; page-only samples are low confidence.

Context (not scored): reach (subscribers), focus (share of recent uploads on the topic), and trust
(how often Reddit owners or other creators cite the channel, passed in by the agent). ``authority``
is derived from rigor, trust and focus, never from subscribers alone.

The registry (a JSON file) keeps dated, topic-scoped ratings and user pins across sessions.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

STALE_DAYS = 180

# --- independence -------------------------------------------------------------------------------
# Penalty per signal category (each counts once per video). Third-party sponsors (a VPN read) and
# generic store affiliate links barely affect whether the review itself is independent.
_INDEP_PENALTY = {
    "brand_owned_channel": 4.0, "sponsor_read": 2.5, "paid_promotion_label": 2.0, "ad_tag": 2.0, "review_unit": 1.5,
    "brand_code_or_link": 1.0, "brand_affiliate_links": 1.0, "brand_giveaway": 1.0, "campaign_tracking": 0.5,
    "use_my_link_or_code": 0.5, "affiliate_links": 0.3, "shopping_commission": 0.3, "third_party_sponsor": 0.3,
    "giveaway": 0.3, "rented_or_borrowed": 0.0, "self_promotion": 0.1,
    # The creator built, sells or works for a product in the field ("the app I work on", their own
    # Obsidian writing system): not money from a sponsor, but a stake in the verdict.
    "creator_product": 1.5, "gear_affiliate_links": 0.0, "ownership_tie": 1.0,
}
_INDEP_BONUS = {"bought_it_myself": 0.5, "bought_something_myself": 0.3, "claims_not_sponsored": 0.3,
                "editorial_independence": 0.6}

# --- transparency -------------------------------------------------------------------------------
_MONEY = {"sponsor_read", "review_unit", "paid_promotion_label", "ad_tag", "brand_code_or_link", "brand_affiliate_links",
          "affiliate_links", "use_my_link_or_code", "shopping_commission", "brand_giveaway", "campaign_tracking"}
_DISCLOSED_BY = {  # money signal -> signals that count as its disclosure ("#ad" discloses any of them)
    "affiliate_links": {"affiliate_disclosure", "ad_tag"},
    "shopping_commission": {"affiliate_disclosure", "paid_promotion_label", "ad_tag"},
    "use_my_link_or_code": {"affiliate_disclosure", "sponsor_read", "paid_promotion_label", "ad_tag"},
    "brand_code_or_link": {"sponsor_read", "paid_promotion_label", "affiliate_disclosure", "ad_tag"},
    "brand_affiliate_links": {"sponsor_read", "paid_promotion_label", "affiliate_disclosure", "ad_tag"},
    "campaign_tracking": {"sponsor_read", "paid_promotion_label", "ad_tag"},
    "brand_giveaway": {"sponsor_read", "paid_promotion_label", "review_unit", "ad_tag"},
}
_UNDISCLOSED_PENALTY = {"brand_code_or_link": 2.5, "brand_affiliate_links": 2.5, "campaign_tracking": 1.5,
                        "brand_giveaway": 1.5, "use_my_link_or_code": 1.0, "affiliate_links": 1.0,
                        "shopping_commission": 0.5}

# --- rigor --------------------------------------------------------------------------------------
_UNIT = (r"(?:%|percent|degrees?|°|psi|watts?|amps?|volts?|mah|wh|kwh|db|decibels?|lbs?|pounds?|ounces?|oz|"
         r"grams?|kg|ml|milliliters?|liters?|litres?|gallons?|gal|inch(?:es)?|mm|cm|meters?|metres?|feet|ft|seconds?|secs?|"
         r"minutes?|mins?|hours?|rpm|cfm|air ?watts|pa\b|kpa|lux|nits|fps|mph)")
_MEASURE = re.compile(r"\b\d+(?:[.,]\d+)?\s*" + _UNIT, re.I)
_PATTERNS = {
    "controlled_test": re.compile(
        r"\b(?:side[- ]by[- ]side|same (?:amount|stain|carpet|conditions|test|spot)|each (?:machine|one|unit|"
        r"model) (?:got|gets|was)|control(?:led)? (?:test|sample|group)|three (?:passes|runs|trials)|"
        r"(?:test|trial|run) (?:number|#)\s*\d|averaged|on average|repeat(?:ed)? (?:the|this) test|"
        r"to keep (?:it|things) fair|head[- ]to[- ]head)\b", re.I),
    "testing_language": re.compile(r"\b(?:we|i) (?:tested|measured|weighed|timed|compared|ran (?:each|the|a) test)\b"
                                   r"|\b(?:test results?|(?:our|my) results|winner of this test)\b", re.I),
    "study_citation": re.compile(r"\b(?:a|one|this|the|that|another) (?:\d{4} )?(?:study|studies|paper|"
                                 r"clinical trial|meta-analysis)\b|\bresearch(?:ers)? (?:shows?|showed|found|suggests?)\b|"
                                 r"\bpublished in\b|\bpeer[- ]reviewed\b|\bamerican academy of pediatrics\b|"
                                 r"\baccording to (?:the )?(?:research|study|guidelines?)\b|(?-i:\b(?:AAP|NHS|CDC)\b)",
                                 re.I),
    "named_test": re.compile(r"\b(?:stain|suction|dry(?:ing)?|dry time|water recovery|noise|edge|pet hair|"
                             r"endurance|durability|battery|run ?time|drop|heat|load|speed|torque|accuracy|"
                             r"scratch|waterproof|range|brightness|latency) tests?\b", re.I),
    "claim_check": re.compile(r"\b(?:we'?re going to|we'?ll|let'?s|i'?m going to) (?:test|verify|check|see if|find out)"
                              r"(?: that| this| it| if| whether)?\b|\bput (?:it|them|that) to the test\b", re.I),
    "comparative": re.compile(r"\b(?:the (?:most|best|highest|lowest|fastest|slowest|loudest|quietest|strongest|"
                              r"weakest) (?:yet|so far|of (?:the|all))|compared to the|better job than|"
                              r"outperform(?:s|ed)?|in (?:first|second|third|last) place|takes? the lead|"
                              r"the winner)\b", re.I),
    "instrument": re.compile(r"\b(?:scale|thermometer|thermal camera|sound (?:level )?meter|decibel meter|"
                             r"multimeter|watt ?meter|kill a watt|anemometer|manometer|dyno|stopwatch|"
                             r"colorimeter|moisture meter|lux meter)\b", re.I),
    "method": re.compile(r"\b(?:methodology|how (?:we|i) test|test(?:ing)? (?:procedure|protocol|method)|"
                         r"here'?s how (?:the|this) test works|to make (?:it|this) a fair test|"
                         r"(?:tested|measured|recorded) (?:their|the|each|its|every) [\w ]{0,20}?from (?:\d+|one|two|"
                         r"three) (?:meters?|metres?|feet|foot|ft|m)\b|from (?:\d+|one|two|three) (?:meters?|metres?|"
                         r"feet|foot) away|(?:room|ambient|background) (?:baseline|reading|noise reading)|took (?:\d+|two|three|"
                         r"several) readings)", re.I),
    "data_table": re.compile(r"\b(?:(?:you can )?see (?:it )?in|in|see|pause the video[\w ,]{0,20}?(?:in|see)) "
                             r"(?:this|my|our|the results?) (?:table|chart|graph|spreadsheet)\b|"
                             r"\b(?:in|see) the (?:table|chart|graph) (?:below|above|here|on screen)\b", re.I),
    "long_term": re.compile(r"\b(?:\d+|six|several|a few) (?:months?|years?) (?:later|of use|in|of owning)\b"
                            r"|\blong[- ]term (?:test|review|update)\b|\bafter (?:a|one|two|\d+) years?\b", re.I),
    # Software and services are judged by analysis, not measurement: audits and incident history, the
    # security design, a stated set of criteria, features shown working on screen, plan limits checked.
    "audit_or_incident": re.compile(r"\b(?:(?:security|third[- ]party|independent|code) audits?|audited by|"
                                    r"pen(?:etration)?[- ]?test(?:s|ed|ing)?|cve-?\d*|bug bount(?:y|ies)|"
                                    r"(?:data )?breach(?:es|ed)?|vulnerabilit(?:y|ies)|incident|soc ?2|iso ?27001|"
                                    r"compromised|disclosed (?:a|the) (?:bug|flaw|vulnerability))\b", re.I),
    "design_analysis": re.compile(r"\b(?:zero[- ]knowledge|end[- ]to[- ]end encrypt\w*|key derivation|argon2|"
                                  r"pbkdf2|kdf|secret key|master (?:password|key)|threat model\w*|attack surface|"
                                  r"open[- ]source(?:d)? (?:code|client|server)|source code|source[- ]available|"
                                  r"self[- ]host\w*|encrypt\w* (?:locally|on (?:your|the) device)|client[- ]side|"
                                  # apps and editors: storage, format, portability, platform, extensibility
                                  r"plain[- ]text (?:files?|format)|(?:markdown|\.md|local) files?|file format|"
                                  r"proprietary (?:format|database|library)|(?:vendor )?lock[- ]?in|"
                                  r"open[- ]source (?:app|project|alternative|license)|electron|native (?:mac |linux )?app|"
                                  r"(?:sqlite|internal|local) database|works offline|offline[- ]first|"
                                  r"sync(?:s|ing)? (?:via|through|over|across)|export(?:s|ing)? (?:to|as) (?:pdf|html|docx|"
                                  r"markdown|word)|plugin (?:system|ecosystem|api)|(?:an|the|its) api|vim (?:mode|bindings)|"
                                  r"wysiwyg|live preview|split (?:view|pane|preview)|keyboard shortcuts?|cross[- ]platform)\b",
                                  re.I),
    "criteria": re.compile(r"\b(?:(?:our|my|the) criteria|criteria (?:we|i) (?:use|used|look)|what (?:we|i) look(?:ed)? "
                           r"for|(?:we|i) (?:judged|graded|evaluated) (?:them|each|every)|threat model)\b", re.I),
    "demo": re.compile(r"\b(?:let me show you|i'?ll show you|i'?m going to show you|i (?:want|wanna) to show you|"
                       r"as you can see (?:here|on)|(?:i'?ll|let'?s|let me|i'?m (?:going to|gonna)) (?:open|click|tap|type|"
                       r"select|highlight|drag|paste|run|choose|swipe|install|create|set up|log ?in|autofill|import|"
                       r"export|add|toggle|switch (?:over|to))|if (?:i|you) (?:tap|click|press|select|type|hit|drag) "
                       r"(?:that|this|on|the|it|here)|let'?s see (?:if|how|what) (?:this|it|that) (?:works|does|looks)|"
                       r"just like that,? it (?:worked|works)|i (?:just )?(?:clicked|tapped|opened|installed|created|"
                       r"imported|exported|logged in|typed|pasted))\b", re.I),
    "plan_check": re.compile(r"\$\s?\d+(?:\.\d\d)?(?:\s*(?:a|per|/)\s*(?:year|month|yr|mo))|\bfree (?:plan|tier|"
                             r"version) (?:only|limits?|includes?|lets|doesn'?t)\b|\b(?:limited|up) to \d+ (?:devices?|users?|"
                             r"people|members?|passwords?)\b|\b(?:one[- ]time (?:purchase|price|payment|fee)|lifetime "
                             r"licen[cs]e|(?:monthly|yearly|annual) subscription|free (?:and open[- ]source|trial))\b", re.I),
    "spec_sheet": re.compile(r"\b(?:according to the (?:manufacturer|brand|company)|features include|"
                             r"comes with (?:a|an) \d|boasts|the (?:manufacturer|brand) claims|on paper)\b", re.I),
}

_STOP = {"the", "a", "an", "for", "and", "or", "of", "to", "in", "on", "best", "vs", "review", "reviews", "with",
         "home", "machine", "machines"}


def _clamp(x: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return round(max(lo, min(hi, x)), 1)


def _signals(rec: dict) -> list[dict]:
    return list(rec.get("signals") or [])


def video_independence(rec: dict) -> tuple[float, list[str]]:
    sig = {s.get("signal") for s in _signals(rec)}
    score, why = 5.0, []
    if rec.get("brand_owned"):
        sig.add("brand_owned_channel")
    vendors, _ = _vendors(rec)
    if len(vendors) >= 2:
        # Paid links to several of the reviewed vendors ("NordPass-Discount", "roboform-v2"): the ranking
        # earns from the vendors it ranks.
        score -= 1.2
        why.append(f"affiliate links to {len(vendors)} reviewed vendors")
    if "third_party_sponsor" in sig and not sig & {"sponsor_read", "ad_tag", "brand_code_or_link", "brand_affiliate_links"}:
        # YouTube's paid-promotion label for an unrelated sponsor (a budgeting app in a notes-app video)
        # says nothing about the products reviewed.
        sig.discard("paid_promotion_label")
    for s in sorted(sig):
        p = _INDEP_PENALTY.get(s, 0.0)
        if p:
            score -= p
            if p >= 1.0:
                why.append(s)
    for s, b in _INDEP_BONUS.items():
        if s in sig:
            score += b
    if rec.get("likely_ai_listicle") or rec.get("amazon_influencer"):
        score = min(score, 1.5)
        why.append("listicle/amazon-influencer channel")
    return _clamp(score), why


def video_transparency(rec: dict, length_s: float | None = None) -> tuple[float | None, list[str]]:
    """None when the video shows no money to disclose."""
    sigs = _signals(rec)
    names = {s.get("signal") for s in sigs}
    money = names & _MONEY
    if not money:
        return None, []
    score, why = 5.0, []
    for m in sorted(money):
        need = _DISCLOSED_BY.get(m)
        if need and not (names & need):
            score -= _UNDISCLOSED_PENALTY.get(m, 1.0)
            why.append(f"{m} without a disclosure")
    length = length_s or rec.get("length_s")
    for s in sigs:  # a spoken sponsor/review-unit disclosure late in the video is less clear
        if s.get("signal") in ("sponsor_read", "review_unit") and s.get("sec") is not None and length:
            if s["sec"] > 0.5 * length:
                score -= 1.0
                why.append(f"{s['signal']} only at {int(s['sec'] // 60)}:{int(s['sec'] % 60):02d}")
                break
    if "paid_promotion_label" in names:
        score += 0.5
    return _clamp(score), why


_FIRST_PERSON_USE = re.compile(r"\b(?:i|we)(?:'ve| have| had)? (?:used?|using|measured|tested|put|set up|grabbed|"
                               r"took|placed|hooked up|ran|got out)\b|\b(?:my|our) (?:sound|decibel|db) (?:meter|reader)\b",
                               re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")


def rigor_hits(text: str) -> dict[str, int]:
    out = {"measurement": len(_MEASURE.findall(text or ""))}
    for k, rx in _PATTERNS.items():
        if k == "instrument":
            # Only the speaker's own use counts: "I used a sound meter", not "download a sound meter app".
            sents = _SENT.split(text or "")
            out[k] = sum(1 for i, x in enumerate(sents) if rx.search(x) and _FIRST_PERSON_USE.search(
                " ".join(sents[max(0, i - 1):i + 1])))
        else:
            out[k] = len(rx.findall(text or ""))
    return out


def video_rigor(rec: dict) -> tuple[float | None, dict]:
    """Rigor from the transcript (None without one). Densities are per 10 minutes of video."""
    segs = rec.get("segments")
    sd = rec.get("screen_data") or {}
    screen = 0.0
    if sd.get("table_like"):  # a results table or chart read off the screen (OCR of a frame)
        screen = 1.0 + min(0.8, (sd.get("numbers") or 0) * 0.02)
    if not segs:
        return (_clamp(screen), {"hits": {"on_screen_table": 1}, "minutes": None}) if screen else (None, {})
    text = " ".join(s.get("text") or "" for s in segs)
    hits = rigor_hits(text)
    minutes = (rec.get("length_s") or 0) / 60 or max(1.0, len(text.split()) / 150)  # ~150 spoken words/min
    per10 = {k: v * 10 / minutes for k, v in hits.items()}
    measured = hits["measurement"] + hits["named_test"] + hits["controlled_test"] + hits["instrument"]
    # "I tested 6" with nothing measured or shown is a claim: it counts only a little.
    tl_cap = 1.2 if measured >= 3 or hits["demo"] >= 2 else 0.3
    analysis = (min(0.8, hits["audit_or_incident"] * 0.15) + min(0.8, per10["design_analysis"] * 0.1)
                + min(0.4, hits["criteria"] * 0.2) + min(1.0, max(hits["demo"] * 0.1, per10["demo"] * 0.08)) + min(0.3, hits["plan_check"] * 0.1))
    score = (min(2.5, per10["measurement"] * 0.08) + min(1.2, per10["controlled_test"] * 0.4)
             + min(tl_cap, per10["testing_language"] * 0.15) + analysis
             + min(1.0, per10["named_test"] * 0.25)
             + min(1.0, hits["claim_check"] * 0.5) + min(1.0, per10["comparative"] * 0.3)
             + (0.6 if hits["data_table"] else 0) + min(0.6, hits["method"] * 0.3) + (0.6 if hits["instrument"] else 0)
             + (0.4 if hits["long_term"] else 0)
             - min(1.0, per10["spec_sheet"] * 0.3))
    score += screen
    hits["on_screen_table"] = 1 if screen else 0
    if rec.get("likely_ai_listicle"):
        score -= 2
    hands_on = (hits["testing_language"] + hits["named_test"] + hits["claim_check"] + hits["controlled_test"]
                + hits["instrument"] + hits["method"] + hits["on_screen_table"] * 2)
    kind = None
    if analysis >= 1.0 and measured < 3:
        kind = "analysis review (design, audits, demos; nothing measured)"
        score = min(score, 2.9)  # analysis alone stays below "tested" (3+)
    if hits["study_citation"] >= 2 and hands_on <= 1:
        # Numbers quoted from studies or guidelines: an evidence explainer, not product testing.
        score, kind = min(score, 1.5), "evidence explainer (cites research; no hands-on testing)"
    return _clamp(score), {"hits": hits, "minutes": round(minutes, 1), "analysis": round(analysis, 2),
                           **({"kind": kind} if kind else {})}


def topic_terms(topic: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (topic or "").lower()) if w not in _STOP and len(w) > 2]


def focus(recent_titles: list[str], topic: str) -> float | None:
    terms = topic_terms(topic)
    if not recent_titles or not terms:
        return None
    hit = sum(1 for t in recent_titles if any(w in t.lower() for w in terms))
    return round(hit / len(recent_titles), 2)


_TITLE_STOP = _STOP | {"this", "that", "these", "you", "your", "are", "is", "it", "its", "why", "how", "what",
                       "new", "vs", "test", "tested", "testing", "tests", "best", "top", "worst", "really", "actually",
                       "never", "ever", "ever", "all", "every", "one", "can", "will", "should", "get", "does", "did",
                       "not", "don", "dont", "after", "before", "from", "into", "about", "than", "they", "them",
                       "here", "there", "which", "who", "most", "more", "less", "good", "bad", "better", "buy",
                       "2023", "2024", "2025", "2026", "2027", "shorts", "part", "episode", "video", "review",
                       "until", "mins", "min", "only", "just", "now", "still", "also", "even", "much", "many", "way",
                       "ways", "use", "using", "need", "know", "thing", "things", "make", "want", "like", "everything",
                       "nothing", "people", "time", "first", "last", "next", "real", "big", "way", "without", "over",
                       "under", "out", "off", "back", "could", "would", "our", "my", "we", "i", "me", "here's",
                       "what's", "it's", "you're", "don't", "stop", "start", "finally", "today", "years", "year"}


def draft_covers(info: dict, n: int = 8) -> dict:
    """A draft of what a channel covers, from its channel page: the tagline and the words that recur in its
    recent upload titles (singulars merged). The agent should rewrite `summary` into one plain sentence."""
    counts: dict[str, int] = {}
    for t in info.get("recent_titles") or []:
        for w in set(re.findall(r"[a-z][a-z0-9'-]+", t.lower())):
            w = w.strip("'-")
            w = w[:-1] if w.endswith("s") and len(w) > 4 and not w.endswith("ss") else w
            if w not in _TITLE_STOP and len(w) > 2:
                counts[w] = counts.get(w, 0) + 1
    tags = [w for w, c in sorted(counts.items(), key=lambda x: (-x[1], x[0])) if c >= 2][:n]
    return {"tagline": info.get("description"), "recurring_title_words": tags,
            "summary": None, "tags": tags, "source": "draft from channel page"}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _cited(cited: dict[str, int] | None, name: str | None, handle: str | None) -> int | None:
    if not cited:
        return None
    keys = {k.lower().lstrip("@").replace(" ", ""): v for k, v in cited.items()}
    for n in (name, handle):
        if n and n.lower().lstrip("@").replace(" ", "") in keys:
            return keys[n.lower().lstrip("@").replace(" ", "")]
    return 0


def _rank(auth: float | None, conf: str, subs: int | None, ind: float | None = None) -> float:
    """Sort key for the map: authority discounted for a thin sample (one video), tiny reach and money
    ties (independence), so a 72-subscriber channel's one good video, or a coupon-link tier list, doesn't
    top independent channels known from several videos."""
    if auth is None:
        return 0.0
    k = {"high": 1.0, "medium": 0.9, "low": 0.75}[conf]
    if ind is not None:
        k *= 0.7 + 0.06 * ind
    if subs is not None and subs < 1000:
        k *= 0.8
    return round(auth * k, 2)


def authority(rigor: float | None, trust: int | None, fcs: float | None) -> float | None:
    """Derived: rigor (70%) + trust (30%; 3+ independent citations = full), plus up to +0.4 for focus.
    Focus is a small bonus only, so a generalist tester (Project Farm) isn't marked down for covering
    many categories."""
    if rigor is None and not trust:
        return None
    t = min(1.0, (trust or 0) / 3) * 5
    return _clamp(0.7 * (rigor if rigor is not None else 1.5) + 0.3 * t + 0.4 * (fcs or 0))


_RETAIL = re.compile(r"amzn\.|amazon\.|geni\.us|bestbuy|walmart|target\.com|homedepot|lowes|ebay|bhpho|"
                     r"aliexpress|howl\.me|skimresources|sjv\.io|7tiv|avantlink|awin1|linksynergy", re.I)


def _vendors(rec: dict) -> tuple[set[str], int]:
    """(vendors, retailer link count) behind a video's affiliate links: a vendor is a reviewed brand's own
    program ("vpnpro.sale/NordPass-Discount", "1password.partnerlinks.io"), named by a known brand in the URL,
    else by its site name. Retailer links (Amazon, Best Buy) are counted apart."""
    try:
        import youtube_signals as S  # noqa: WPS433 (same folder)
        brands = [(b, re.sub(r"[^a-z0-9]", "", b.lower())) for b in S.KNOWN_BRANDS]
    except Exception:  # pragma: no cover
        brands = []
    vendors, retail = set(), 0
    for x in _signals(rec):
        if x.get("signal") not in ("affiliate_links", "brand_affiliate_links"):
            continue
        for u in re.findall(r"https?://[^\s,]+", str(x.get("text") or "")):
            if _RETAIL.search(u):
                retail += 1
                continue
            flat = re.sub(r"[^a-z0-9]", "", u.lower())
            named = [b for b, a in brands if len(a) >= 4 and a in flat]
            host = re.sub(r"^https?://(?:www\.)?", "", u.lower()).split("/")[0]
            vendors.add(named[0] if named else ".".join(host.split(".")[-2:]))
    return vendors, retail


def _affiliate_weight(rec: dict) -> float:
    """How affiliate-driven one video is: 1.0 for brand codes or commission shelves, affiliate links to
    two or more vendors (a coupon-link tier list), or several retailer links; 0.5 for links to a single
    vendor or "use my link" (an opt-in link to one service is not a coupon farm); 0 otherwise."""
    sig = {x.get("signal"): x for x in _signals(rec)}
    if "brand_code_or_link" in sig:
        return 1.0
    vendors, retail = _vendors(rec)
    if len(vendors) >= 2 or retail >= 2:
        return 1.0
    # A commission shelf alone is often the creator's gear (a notes-app video tagging its camera): half.
    if vendors or retail or sig.keys() & {"affiliate_links", "brand_affiliate_links", "use_my_link_or_code",
                                          "shopping_commission"}:
        return 0.5
    return 0.0


def rate_channel(name: str, recs: list[dict], info: dict | None = None, topic: str = "",
                 cited: dict[str, int] | None = None) -> dict:
    info = info or {}
    per_video, ind, tra, rig, why_i, why_t, notes_kind = [], [], [], [], [], [], []
    for r in recs:
        i, wi = video_independence(r)
        t, wt = video_transparency(r)
        g, gd = video_rigor(r)
        ind.append(i)
        tra.append(t)
        rig.append(g)
        why_i += [f"{w} ({r.get('url')})" for w in wi]
        why_t += [f"{w} ({r.get('url')})" for w in wt]
        if gd.get("kind"):
            notes_kind.append(gd["kind"])
        per_video.append({"url": r.get("url"), "video_id": video_id(r.get("url")), "title": r.get("title"),
                          "published": r.get("age"), "transcript": g is not None,
                          "independence": i, "transparency": t,
                          "rigor": g, **({"rigor_hits": gd["hits"]} if gd else {}),
                          "sponsor_score": r.get("sponsor_score")})
    with_tx = sum(1 for g in rig if g is not None)
    aff_share = round(sum(_affiliate_weight(r) for r in recs) / len(recs), 2) if recs else None
    vendor_share = round(sum(1 for r in recs if len(_vendors(r)[0]) >= 2) / len(recs), 2) if recs else None
    fcs = focus(info.get("recent_titles") or [], topic)
    trust = _cited(cited, name, info.get("handle"))
    axes = {"independence": _mean(ind), "transparency": _mean(tra), "rigor": _mean(rig)}
    conf = "high" if len(recs) >= 3 and with_tx >= 2 else "medium" if len(recs) >= 2 and with_tx >= 1 else "low"
    notes = []
    if axes["transparency"] is None:
        notes.append("transparency: nothing to disclose in the sample")
    if with_tx == 0:
        notes.append("rigor unknown: no transcript read for this channel")
    notes += sorted(set(notes_kind))
    explainer = bool(recs) and sum(1 for k in notes_kind if k.startswith("evidence")) >= max(1, len(recs) / 2)
    analysis_kind = bool(recs) and sum(1 for k in notes_kind if k.startswith("analysis")) >= max(1, len(recs) / 2)
    brand_own = any(r.get("brand_owned") in (True, "strong", "yes") for r in recs)
    if any(r.get("likely_ai_listicle") or r.get("amazon_influencer") for r in recs):
        notes.append("listicle or Amazon-influencer signals")
    return {
        "channel": name, "handle": info.get("handle"), "topic": topic, "date": date.today().isoformat(),
        "axes": axes, "authority": authority(axes["rigor"], trust, fcs),
        "rank_score": _rank(authority(axes["rigor"], trust, fcs), conf, info.get("subscribers"), axes["independence"]),
        "context": {"subscribers": info.get("subscribers"), "focus": fcs, "trust_citations": trust,
                    "affiliate_share": aff_share, "multi_vendor_affiliate_share": vendor_share,
                    "recent_uploads_seen": len(info.get("recent_titles") or [])},
        "sample": {"videos": len(recs), "with_transcript": with_tx}, "confidence": conf,
        "reasons": {"independence": why_i[:6], "transparency": why_t[:6]},
        "notes": notes, "videos": per_video,
        **({"kind": "brand channel"} if brand_own else {"kind": "evidence explainer"} if explainer else
           {"kind": "analysis review"} if analysis_kind else {}),
        **({"covers_draft": draft_covers(info)} if info.get("recent_titles") else {}),
    }


_ROUNDUP = re.compile(r"\b(?:best|top \d+|top (?:five|ten)|\d+ best|tested \d+|every|ranked|tier list|"
                      r"buying guide|which \w+ should)\b", re.I)  # a two-machine "X vs Y" is a head-to-head


def quadrant(r: dict) -> str:
    a = r.get("axes") or {}
    rig, ind = a.get("rigor"), a.get("independence")
    if r.get("kind") == "brand channel":
        return "brand's own channel (vendor material, not a review)"
    if r.get("kind") == "evidence explainer":
        return "evidence explainer (cites research, doesn't test products)"
    why_all = " ".join((r.get("reasons") or {}).get("independence") or [])
    if ind is not None and "creator_product" in why_all and ind < 4:
        return "creator-tied (own product, course or employer in the field)"
    if rig is None or ind is None:
        return "unrated (needs a transcript)" if ind is not None else "unrated"
    hi_r, hi_i = rig >= 3, ind >= 3.5
    if not hi_r and ind < 2.5:
        return "promotional / shallow"
    ctx = r.get("context") or {}
    if (rig < 1.5 and (ctx.get("affiliate_share") or 0) >= 0.8) or \
            (rig < 3 and (ctx.get("multi_vendor_affiliate_share") or 0) >= 0.5):
        titles = [v.get("title") or "" for v in r.get("videos") or []]
        roundup = sum(1 for t in titles if _ROUNDUP.search(t)) >= max(1, len(titles) / 2)
        return "affiliate roundup (light testing)" if roundup else "affiliate-led review (light testing)"
    analysis = r.get("kind") == "analysis review"
    if not hi_r and not hi_i and ind >= 2.5:
        why = " ".join((r.get("reasons") or {}).get("independence") or [])
        if re.search(r"review_unit|brand_giveaway", why):
            return "brand-supplied, light testing"
        return "sponsored analysis" if analysis else "sponsored, light testing"
    if analysis and hi_i and not hi_r:
        return "independent analysis (design, audits, demos)"
    return {(True, True): "trusted tester", (True, False): "rigorous but brand-tied",
            (False, True): "independent, light testing", (False, False): "promotional / shallow"}[(hi_r, hi_i)]


# --- registry -----------------------------------------------------------------------------------
def _key(name: str | None, handle: str | None) -> str:
    # NFKC folds look-alike characters: "Jane - j3․dev" (one-dot leader) and "Jane - j3.dev" are one channel.
    return unicodedata.normalize("NFKC", handle or name or "").casefold().lstrip("@").replace(" ", "")


def _name_key(name: str | None) -> str:
    """A display name for matching only: folded, letters and digits ("Jane - j3.dev" -> "janej3dev")."""
    return re.sub(r"[\W_]+", "", _key(name, None))


def registry_load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("channels", {})
    registry_dedupe(data)  # heals older files with a name-keyed and a handle-keyed row for one channel
    return data


def registry_save(path: Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


def registry_find(data: dict, channel: str, handle: str | None = None) -> tuple[str | None, dict | None]:
    """Find a channel by handle (preferred) or by display name or key. A name match whose row has a
    different handle is a different channel."""
    rows = data["channels"]
    if handle:
        h = _key(None, handle)
        for key, row in rows.items():
            if _key(None, row.get("handle")) == h or (not row.get("handle") and key == h):
                return key, row
    k, nk = _key(channel, None), _name_key(channel)
    for key, row in rows.items():
        if key == k or _key(row.get("name"), None) == k or _key(None, row.get("handle")) == k or (
                nk and _name_key(row.get("name")) == nk):
            if handle and row.get("handle") and _key(None, row["handle"]) != _key(None, handle):
                continue
            return key, row
    return None, None


def _row(data: dict, name: str, handle: str | None = None) -> dict:
    """The channel's row, created if missing; adopts the handle on a row found by name."""
    _, row = registry_find(data, name, handle)
    if row is None:
        row = data["channels"].setdefault(_key(name, handle), {"name": name, "handle": handle, "topics": {}})
    if handle and not row.get("handle"):
        row["handle"] = handle
    return row


def _merge_rows(a: dict, b: dict) -> dict:
    """Fold row b into row a (topics, reviewed videos, observations, covers, pin)."""
    for t, v in (b.get("topics") or {}).items():
        if t not in a.setdefault("topics", {}) or (v.get("date") or "") > (a["topics"][t].get("date") or ""):
            a["topics"][t] = v
    have = {e.get("id"): e for e in a.setdefault("reviewed", [])}
    for e in b.get("reviewed") or []:
        if e.get("id") in have:
            x = have[e["id"]]
            x["topics"] = sorted(set(x.get("topics") or []) | set(e.get("topics") or []))
            x["transcript"] = bool(x.get("transcript") or e.get("transcript"))
            x.setdefault("scores", e.get("scores"))
            x["first_reviewed"] = min(filter(None, [x.get("first_reviewed"), e.get("first_reviewed")]), default=None)
            x["last_reviewed"] = max(filter(None, [x.get("last_reviewed"), e.get("last_reviewed")]), default=None)
        else:
            a["reviewed"].append(e)
    seen = {(o.get("topic"), o.get("text")) for o in a.setdefault("observations", [])}
    a["observations"] += [o for o in b.get("observations") or [] if (o.get("topic"), o.get("text")) not in seen]
    if b.get("covers") and (b["covers"].get("date") or "") >= ((a.get("covers") or {}).get("date") or ""):
        a["covers"] = b["covers"]
    if b.get("pin") and not a.get("pin"):
        a["pin"] = b["pin"]
    a["handle"] = a.get("handle") or b.get("handle")
    a["subscribers"] = a.get("subscribers") or b.get("subscribers")
    return a


def registry_dedupe(data: dict) -> int:
    """Merge rows for the same channel: same handle, or the same display name where one row has no handle."""
    rows = data["channels"]
    merged = 0
    for key in list(rows):
        row = rows.get(key)
        if row is None:
            continue
        for other in list(rows):
            if other == key or other not in rows:
                continue
            o = rows[other]
            same_handle = row.get("handle") and _key(None, row["handle"]) == _key(None, o.get("handle"))
            same_name = bool(_name_key(row.get("name"))) and _name_key(row.get("name")) == _name_key(o.get("name")) and \
                (not row.get("handle") or not o.get("handle"))
            if same_handle or same_name:
                keep, drop = (key, other) if row.get("handle") or not o.get("handle") else (other, key)
                rows[keep] = _merge_rows(rows[keep], rows.pop(drop))
                merged += 1
                if drop == key:
                    break
    return merged


def video_id(url: str | None) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([\w-]{11})", url or "")
    return m.group(1) if m else (url or None)


def registry_add_video(data: dict, channel: str, url: str, topic: str, title: str | None = None,
                       day: str | None = None, transcript: bool | None = None, scores: dict | None = None,
                       source: str | None = None, handle: str | None = None) -> dict:
    """Record a reviewed video on the channel's list (one entry per video; a later review updates it)."""
    row = _row(data, channel, handle)
    vid = video_id(url)
    today = day or date.today().isoformat()
    lst = row.setdefault("reviewed", [])
    entry = next((e for e in lst if e.get("id") == vid), None)
    if entry is None:
        entry = {"id": vid, "url": url, "first_reviewed": today}
        lst.append(entry)
    entry.update({k: v for k, v in {"title": title, "last_reviewed": today, "source": source}.items() if v})
    entry.setdefault("topics", [])
    t = (topic or "general").lower()
    if t not in entry["topics"]:
        entry["topics"].append(t)
    if transcript is not None:
        entry["transcript"] = bool(entry.get("transcript")) or transcript
    if scores:
        entry["scores"] = {k: scores.get(k) for k in ("independence", "transparency", "rigor", "sponsor_score")}
    return entry


def familiarity(row: dict) -> dict:
    """How well we know a channel: videos reviewed (with transcripts), per topic, first/last dates."""
    lst = row.get("reviewed") or []
    topics: dict[str, int] = {}
    for e in lst:
        for t in e.get("topics") or ["general"]:
            topics[t] = topics.get(t, 0) + 1
    n, tx = len(lst), sum(1 for e in lst if e.get("transcript"))
    level = ("none" if n == 0 else "light" if n <= 2 else "moderate" if n <= 5 or tx < 3 else "well known")
    days = sorted(d for e in lst for d in (e.get("first_reviewed"), e.get("last_reviewed")) if d)
    return {"videos": n, "with_transcript": tx, "level": level, "by_topic": topics,
            "first": days[0] if days else None, "last": days[-1] if days else None}


def cumulative_axes(row: dict, topic: str | None = None) -> dict:
    """Mean per-video axes over every scored video reviewed (optionally for one topic), across sessions."""
    t = topic.lower() if topic else None
    es = [e for e in row.get("reviewed") or [] if e.get("scores") and (t is None or t in (e.get("topics") or []))]
    return {"videos": len(es), **{k: _mean([e["scores"].get(k) for e in es])
                                  for k in ("independence", "transparency", "rigor")}}


def registry_put(data: dict, rating: dict) -> None:
    row = _row(data, rating["channel"], rating.get("handle"))
    row["name"] = rating["channel"]
    if rating["context"].get("subscribers"):
        row["subscribers"] = rating["context"]["subscribers"]
    topic = (rating.get("topic") or "general").lower()
    for v in rating["videos"]:
        if v.get("url"):
            registry_add_video(data, row["name"], v["url"], topic, v.get("title"), rating.get("date"),
                               v.get("transcript"), v, handle=row.get("handle"))
    slim = {k: rating[k] for k in ("date", "axes", "authority", "context", "sample", "confidence", "reasons", "notes")}
    slim["videos"] = [v["url"] for v in rating["videos"]]
    slim["cumulative"] = cumulative_axes(row, topic)  # this session's videos plus earlier ones on the topic
    row["topics"][topic] = slim


def registry_describe(data: dict, channel: str, summary: str, tags: list[str] | None = None) -> dict:
    """Store what a channel covers: one plain sentence plus category tags (lower-case)."""
    row = _row(data, channel, channel if channel.startswith("@") else None)
    row["covers"] = {"summary": summary.strip(), "tags": sorted({t.strip().lower() for t in tags or [] if t.strip()}),
                     "date": date.today().isoformat()}
    return row


def covers_topic(row: dict, topic: str) -> bool:
    """True when the channel's covers tags or summary share a term with the topic (singulars merged)."""
    cov = row.get("covers") or {}
    norm = lambda w: w[:-1] if w.endswith("s") and len(w) > 4 and not w.endswith("ss") else w  # noqa: E731
    terms = {norm(t) for t in topic_terms(topic)}
    words = {norm(w) for t in cov.get("tags") or [] for w in topic_terms(t)}
    words |= {norm(w) for w in topic_terms(cov.get("summary") or "")}
    return bool(terms & words)


def registry_observe(data: dict, channel: str, topic: str, text: str, source: str | None = None,
                     day: str | None = None) -> dict:
    """Add a dated observation (e.g. from a past report: "DJI-sponsored, paid-promotion label shown")."""
    row = _row(data, channel, channel if channel.startswith("@") else None)
    obs = {"date": day or date.today().isoformat(), "topic": (topic or "general").lower(), "text": text.strip()}
    if source:
        obs["source"] = source
    row.setdefault("observations", [])
    if not any(o["text"] == obs["text"] and o["topic"] == obs["topic"] for o in row["observations"]):
        row["observations"].append(obs)
    return row


VERDICTS = ("trusted", "mixed", "distrusted")


def registry_pin(data: dict, channel: str, verdict: str | None, note: str = "") -> dict:
    row = _row(data, channel, channel if channel.startswith("@") else None)
    if verdict in (None, "clear"):
        row.pop("pin", None)
    else:
        row["pin"] = {"verdict": verdict, "note": note, "date": date.today().isoformat()}
    return row


def stale(day: str | None, today: date | None = None) -> bool:
    try:
        d = datetime.strptime(day or "", "%Y-%m-%d").date()
    except ValueError:
        return True
    return ((today or date.today()) - d).days > STALE_DAYS


def registry_lookup(data: dict, channel: str, topic: str) -> dict | None:
    """The saved rating for this topic (or the closest topic sharing a term), with pin and staleness."""
    _, row = registry_find(data, channel)
    if not row:
        return None
    topics = row.get("topics") or {}
    if not topics and not any(row.get(k) for k in ("pin", "covers", "observations", "reviewed")):
        return None
    t = (topic or "general").lower()
    hit = topics.get(t)
    which = t
    if hit is None:
        terms = set(topic_terms(topic))
        for name, rating in topics.items():
            if terms & set(topic_terms(name)):
                hit, which = rating, name
                break
    out = {"name": row.get("name"), "handle": row.get("handle"), "pin": row.get("pin"), "covers": row.get("covers"),
           "observations": row.get("observations") or [], "familiarity": familiarity(row),
           "reviewed_ids": [e.get("id") for e in row.get("reviewed") or []]}
    if hit:
        out.update({"topic": which, "rating": hit, "stale": stale(hit.get("date")),
                    "same_topic": which == t})
    return out
