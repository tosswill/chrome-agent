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
from datetime import date, datetime
from pathlib import Path

STALE_DAYS = 180

# --- independence -------------------------------------------------------------------------------
# Penalty per signal category (each counts once per video). Third-party sponsors (a VPN read) and
# generic store affiliate links barely affect whether the review itself is independent.
_INDEP_PENALTY = {
    "brand_owned_channel": 4.0, "sponsor_read": 2.5, "paid_promotion_label": 2.0, "review_unit": 1.5,
    "brand_code_or_link": 1.0, "brand_affiliate_links": 1.0, "brand_giveaway": 1.0, "campaign_tracking": 0.5,
    "use_my_link_or_code": 0.5, "affiliate_links": 0.3, "shopping_commission": 0.3, "third_party_sponsor": 0.3,
    "giveaway": 0.3, "rented_or_borrowed": 0.0, "self_promotion": 0.1,
}
_INDEP_BONUS = {"bought_it_myself": 0.5, "bought_something_myself": 0.3, "claims_not_sponsored": 0.3,
                "editorial_independence": 0.6}

# --- transparency -------------------------------------------------------------------------------
_MONEY = {"sponsor_read", "review_unit", "paid_promotion_label", "brand_code_or_link", "brand_affiliate_links",
          "affiliate_links", "use_my_link_or_code", "shopping_commission", "brand_giveaway", "campaign_tracking"}
_DISCLOSED_BY = {  # money signal -> signals that count as its disclosure
    "affiliate_links": {"affiliate_disclosure"},
    "shopping_commission": {"affiliate_disclosure", "paid_promotion_label"},
    "use_my_link_or_code": {"affiliate_disclosure", "sponsor_read", "paid_promotion_label"},
    "brand_code_or_link": {"sponsor_read", "paid_promotion_label", "affiliate_disclosure"},
    "brand_affiliate_links": {"sponsor_read", "paid_promotion_label", "affiliate_disclosure"},
    "campaign_tracking": {"sponsor_read", "paid_promotion_label"},
    "brand_giveaway": {"sponsor_read", "paid_promotion_label", "review_unit"},
}
_UNDISCLOSED_PENALTY = {"brand_code_or_link": 2.5, "brand_affiliate_links": 2.5, "campaign_tracking": 1.5,
                        "brand_giveaway": 1.5, "use_my_link_or_code": 1.0, "affiliate_links": 1.0,
                        "shopping_commission": 0.5}

# --- rigor --------------------------------------------------------------------------------------
_UNIT = (r"(?:%|percent|degrees?|°|psi|watts?|amps?|volts?|mah|wh|kwh|db|decibels?|lbs?|pounds?|ounces?|oz|"
         r"grams?|kg|ml|milliliters?|liters?|litres?|gallons?|gal|inch(?:es)?|mm|cm|feet|ft|seconds?|secs?|"
         r"minutes?|mins?|hours?|rpm|cfm|air ?watts|pa\b|kpa|lux|nits|fps|mph)")
_MEASURE = re.compile(r"\b\d+(?:[.,]\d+)?\s*" + _UNIT, re.I)
_PATTERNS = {
    "controlled_test": re.compile(
        r"\b(?:side[- ]by[- ]side|same (?:amount|stain|carpet|conditions|test|spot)|each (?:machine|one|unit|"
        r"model) (?:got|gets|was)|control(?:led)? (?:test|sample|group)|three (?:passes|runs|trials)|"
        r"(?:test|trial|run) (?:number|#)\s*\d|averaged|on average|repeat(?:ed)? (?:the|this) test|"
        r"to keep (?:it|things) fair|head[- ]to[- ]head)\b", re.I),
    "testing_language": re.compile(r"\b(?:we|i) (?:tested|measured|weighed|timed|compared|ran (?:each|the|a) test)\b"
                                   r"|\b(?:test results?|the results|winner of this test|scored|ranked)\b", re.I),
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
                         r"here'?s how (?:the|this) test works|to make (?:it|this) a fair test)\b", re.I),
    "long_term": re.compile(r"\b(?:\d+|six|several|a few) (?:months?|years?) (?:later|of use|in|of owning)\b"
                            r"|\blong[- ]term (?:test|review|update)\b|\bafter (?:a|one|two|\d+) years?\b", re.I),
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


def rigor_hits(text: str) -> dict[str, int]:
    out = {"measurement": len(_MEASURE.findall(text or ""))}
    for k, rx in _PATTERNS.items():
        out[k] = len(rx.findall(text or ""))
    return out


def video_rigor(rec: dict) -> tuple[float | None, dict]:
    """Rigor from the transcript (None without one). Densities are per 10 minutes of video."""
    segs = rec.get("segments")
    if not segs:
        return None, {}
    text = " ".join(s.get("text") or "" for s in segs)
    hits = rigor_hits(text)
    minutes = (rec.get("length_s") or 0) / 60 or max(1.0, len(text.split()) / 150)  # ~150 spoken words/min
    per10 = {k: v * 10 / minutes for k, v in hits.items()}
    score = (min(2.5, per10["measurement"] * 0.08) + min(1.2, per10["controlled_test"] * 0.4)
             + min(1.2, per10["testing_language"] * 0.15)
             + min(1.0, per10["named_test"] * 0.25)
             + min(1.0, hits["claim_check"] * 0.5) + min(1.0, per10["comparative"] * 0.3) + (0.6 if hits["instrument"] else 0)
             + (0.4 if hits["method"] else 0) + (0.4 if hits["long_term"] else 0)
             - min(1.0, per10["spec_sheet"] * 0.3))
    if rec.get("likely_ai_listicle"):
        score -= 2
    return _clamp(score), {"hits": hits, "minutes": round(minutes, 1)}


def topic_terms(topic: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (topic or "").lower()) if w not in _STOP and len(w) > 2]


def focus(recent_titles: list[str], topic: str) -> float | None:
    terms = topic_terms(topic)
    if not recent_titles or not terms:
        return None
    hit = sum(1 for t in recent_titles if any(w in t.lower() for w in terms))
    return round(hit / len(recent_titles), 2)


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


def authority(rigor: float | None, trust: int | None, fcs: float | None) -> float | None:
    """Derived: rigor (70%) + trust (30%; 3+ independent citations = full), plus up to +0.4 for focus.
    Focus is a small bonus only, so a generalist tester (Project Farm) isn't marked down for covering
    many categories."""
    if rigor is None and not trust:
        return None
    t = min(1.0, (trust or 0) / 3) * 5
    return _clamp(0.7 * (rigor if rigor is not None else 1.5) + 0.3 * t + 0.4 * (fcs or 0))


def rate_channel(name: str, recs: list[dict], info: dict | None = None, topic: str = "",
                 cited: dict[str, int] | None = None) -> dict:
    info = info or {}
    per_video, ind, tra, rig, why_i, why_t = [], [], [], [], [], []
    for r in recs:
        i, wi = video_independence(r)
        t, wt = video_transparency(r)
        g, gd = video_rigor(r)
        ind.append(i)
        tra.append(t)
        rig.append(g)
        why_i += [f"{w} ({r.get('url')})" for w in wi]
        why_t += [f"{w} ({r.get('url')})" for w in wt]
        per_video.append({"url": r.get("url"), "title": r.get("title"), "independence": i, "transparency": t,
                          "rigor": g, **({"rigor_hits": gd["hits"]} if gd else {}),
                          "sponsor_score": r.get("sponsor_score")})
    with_tx = sum(1 for g in rig if g is not None)
    aff = {"affiliate_links", "shopping_commission", "use_my_link_or_code", "brand_affiliate_links"}
    aff_share = round(sum(1 for r in recs if aff & {x.get("signal") for x in _signals(r)}) / len(recs), 2) if recs else None
    fcs = focus(info.get("recent_titles") or [], topic)
    trust = _cited(cited, name, info.get("handle"))
    axes = {"independence": _mean(ind), "transparency": _mean(tra), "rigor": _mean(rig)}
    conf = "high" if len(recs) >= 3 and with_tx >= 2 else "medium" if len(recs) >= 2 and with_tx >= 1 else "low"
    notes = []
    if axes["transparency"] is None:
        notes.append("transparency: nothing to disclose in the sample")
    if with_tx == 0:
        notes.append("rigor unknown: no transcript read for this channel")
    if any(r.get("likely_ai_listicle") or r.get("amazon_influencer") for r in recs):
        notes.append("listicle or Amazon-influencer signals")
    return {
        "channel": name, "handle": info.get("handle"), "topic": topic, "date": date.today().isoformat(),
        "axes": axes, "authority": authority(axes["rigor"], trust, fcs),
        "context": {"subscribers": info.get("subscribers"), "focus": fcs, "trust_citations": trust,
                    "affiliate_share": aff_share,
                    "recent_uploads_seen": len(info.get("recent_titles") or [])},
        "sample": {"videos": len(recs), "with_transcript": with_tx}, "confidence": conf,
        "reasons": {"independence": why_i[:6], "transparency": why_t[:6]},
        "notes": notes, "videos": per_video,
    }


def quadrant(r: dict) -> str:
    a = r.get("axes") or {}
    rig, ind = a.get("rigor"), a.get("independence")
    if rig is None or ind is None:
        return "unrated (needs a transcript)" if ind is not None else "unrated"
    hi_r, hi_i = rig >= 3, ind >= 3.5
    if rig < 1.5 and ((r.get("context") or {}).get("affiliate_share") or 0) >= 0.8:
        return "affiliate roundup (light testing)"
    return {(True, True): "trusted tester", (True, False): "rigorous but brand-tied",
            (False, True): "independent, light testing", (False, False): "promotional / shallow"}[(hi_r, hi_i)]


# --- registry -----------------------------------------------------------------------------------
def _key(name: str | None, handle: str | None) -> str:
    return (handle or name or "").lower().lstrip("@").replace(" ", "")


def registry_load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault("channels", {})
    return data


def registry_save(path: Path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


def registry_find(data: dict, channel: str) -> tuple[str | None, dict | None]:
    k = _key(channel, None)
    for key, row in data["channels"].items():
        if key == k or _key(row.get("name"), None) == k or _key(None, row.get("handle")) == k:
            return key, row
    return None, None


def registry_put(data: dict, rating: dict) -> None:
    key = _key(rating["channel"], rating.get("handle"))
    row = data["channels"].setdefault(key, {"name": rating["channel"], "handle": rating.get("handle"), "topics": {}})
    row["name"], row["handle"] = rating["channel"], rating.get("handle") or row.get("handle")
    if rating["context"].get("subscribers"):
        row["subscribers"] = rating["context"]["subscribers"]
    slim = {k: rating[k] for k in ("date", "axes", "authority", "context", "sample", "confidence", "reasons", "notes")}
    slim["videos"] = [v["url"] for v in rating["videos"]]
    row["topics"][(rating.get("topic") or "general").lower()] = slim


def registry_pin(data: dict, channel: str, verdict: str | None, note: str = "") -> dict:
    key, row = registry_find(data, channel)
    if row is None:
        key = _key(channel, None)
        row = data["channels"].setdefault(key, {"name": channel, "handle": None, "topics": {}})
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
    t = (topic or "general").lower()
    hit = topics.get(t)
    which = t
    if hit is None:
        terms = set(topic_terms(topic))
        for name, rating in topics.items():
            if terms & set(topic_terms(name)):
                hit, which = rating, name
                break
    out = {"name": row.get("name"), "handle": row.get("handle"), "pin": row.get("pin")}
    if hit:
        out.update({"topic": which, "rating": hit, "stale": stale(hit.get("date")),
                    "same_topic": which == t})
    return out
