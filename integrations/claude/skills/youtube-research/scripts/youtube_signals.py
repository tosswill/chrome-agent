"""Pure YouTube-side heuristics for youtube_tab.py: no browser, stdlib only, unit-tested.

    dedupe_segments(segments) -> (segments, removed)          Y1: one copy of each (sec, text)
    transcript_coverage(segments, length_s)                   first/last, gaps, complete
    merge_segments(*lists)                                    union of reads at several scroll positions
    likely_ai_listicle(title, channel, handle, uploads, year) Y3: (bool, reasons)
    near_duplicate_clips(results)                             content-farm hint: {channel: reasons}
    amazon_influencer(title, description, links, length_s, views)  Y17: (bool, reasons)
    detect_brands(title, description, links, own_domains)     brands named in a video (Y6), gear lists skipped
    strip_gear_blocks(description)                            the description without the creator's gear lists
    per_brand(hits, brands, description, links)               Y6: {by_brand, shared_score}
    score_for_term(record, term)                              the sponsor score that applies to a tally term
    comment_promo_hints(comments, brands)                     Y18: low-confidence seeded-comment hint
    unique_path(path, used)                                   Y7: never overwrite a frame from the same call
    player_warning(width, theater, largest_seen)              Y8
    graphic_score(png_bytes)                                  Y16: rank frames by "graphic-ness"
    save_transcript(out_dir, meta, segments)                  Y12: JSON + Markdown, reused when present
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_TRANSCRIPTS = Path("~/.local/share/chrome-agent-research/youtube-transcripts")

# -------------------------------------------------------------------------
# Y1: transcript segments
# -------------------------------------------------------------------------


def dedupe_segments(segments: list[dict]) -> tuple[list[dict], int]:
    """Drop repeated segments (same start second and text), keeping first-seen order. YouTube can render
    the transcript twice (engagement panel and description section), which doubles the list."""
    seen, out = set(), []
    for s in segments or []:
        k = (s.get("sec"), re.sub(r"\s+", " ", (s.get("text") or "")).strip())
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out, len(segments or []) - len(out)


def transcript_coverage(segments: list[dict], length_s: int | None = None) -> dict:
    """How much of the video the segments cover: first/last segment start, gaps longer than
    max(45 s, 6x the median spacing), and the missing tail. `complete` is False when there are gaps or the
    last segment starts more than 60 s before the end. Gaps can be genuine silence (music, b-roll)."""
    secs = sorted({s["sec"] for s in segments or [] if s.get("sec") is not None})
    if not secs:
        return {"complete": False, "segments": 0, "gaps": [], "note": "no timed segments"}
    steps = sorted(b - a for a, b in zip(secs, secs[1:]))
    median = steps[len(steps) // 2] if steps else 0
    limit = max(45, 6 * median)
    gaps = [{"from": _clock(a), "to": _clock(b), "seconds": b - a} for a, b in zip(secs, secs[1:]) if b - a > limit]
    tail = (length_s - secs[-1]) if length_s else None
    out = {"segments": len(secs), "first": _clock(secs[0]), "last": _clock(secs[-1]),
           "length": _clock(length_s) if length_s else None, "gaps": gaps,
           "complete": not gaps and (tail is None or tail <= 60) and secs[0] <= 60}
    if tail is not None and tail > 60:
        out["missing_tail_s"] = tail
    return out


def _clock(sec: float | None) -> str | None:
    if sec is None:
        return None
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def merge_segments(*lists: list[dict]) -> list[dict]:
    """Union of segment lists read at different scroll positions, sorted by start time."""
    seen, out = set(), []
    for lst in lists:
        for s in lst or []:
            k = (s.get("sec"), re.sub(r"\s+", " ", s.get("text") or "").strip())
            if k not in seen:
                seen.add(k)
                out.append(s)
    return sorted(out, key=lambda s: (s.get("sec") is None, s.get("sec") or 0))


# -------------------------------------------------------------------------
# Y3: AI-listicle channels / Y17: Amazon-influencer clips
# -------------------------------------------------------------------------

_LISTICLE_NAME = re.compile(
    r"(picks?\b|picks?of|reviews?\s*(?:club|lab|hub|zone|guru|world|daily|central|expert|insider|corner|scout)|"
    r"scou+t|\btop\s*\d+|top\d+|\d+\s*best|best\s*(?:ones|buys?|picks|choices?|products?|reviews?|gear|gadgets)\b|"
    r"\branked\b|product\s*(?:reviews?|guides?)|gadget\s*(?:hub|zone|world|lab))", re.I)
_LISTICLE_TITLE = re.compile(r"\b(top|best)\s*\d+\b", re.I)
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def likely_ai_listicle(title: str = "", channel: str | None = None, handle: str | None = None,
                       uploads: int | None = None, year: int | None = None) -> tuple[bool, list[str]]:
    """Heuristic: future-year channel names/handles or titles, generic "Picks / Reviews Club / Top5Lab"
    channel names, "Top 10" titles, and very high upload counts. One strong reason, or two weak ones."""
    year = year or date.today().year
    strong, weak = [], []
    name = f"{channel or ''} {handle or ''}"
    if any(int(y) > year for y in _YEAR.findall(name)):
        strong.append("future year in channel name/handle")
    if _LISTICLE_NAME.search(name.replace("@", " ")):
        strong.append("generic listicle channel name")
    if any(int(y) > year for y in _YEAR.findall(title or "")):
        weak.append("future year in title")
    if _LISTICLE_TITLE.search(title or ""):
        weak.append("'Top N' title")
    if uploads and uploads >= 1500:
        weak.append(f"very high upload count ({uploads})")
    if re.search(r"\bdeals?\b", name, re.I):
        weak.append("deals channel name")
    flagged = bool(strong) or len(weak) >= 2
    return flagged, (strong + weak) if flagged else []


def _len_s(text) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))(?::(\d{2}))?\s*", str(text or ""))
    if not m:
        return None
    parts = [int(x) for x in m.groups() if x is not None]
    sec = 0
    for x in parts:
        sec = sec * 60 + x
    return sec


def _title_words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (t or "").lower()) if not w.isdigit() or len(w) > 4}


def near_duplicate_clips(results: list[dict], min_count: int = 3) -> dict[str, list[str]]:
    """Content-farm hint: one channel with `min_count`+ results in the same list that are short clips
    (under 3 minutes, or Shorts) with near-identical titles (word overlap >= 0.5 with another of them).
    Returns {channel: reasons}."""
    by_ch: dict[str, list[dict]] = {}
    for r in results or []:
        if r.get("channel"):
            by_ch.setdefault(r["channel"], []).append(r)
    out = {}
    for ch, rs in by_ch.items():
        short = [r for r in rs if r.get("type") == "short" or ((_len_s(r.get("length")) or 10**6) < 180)]
        if len(short) < min_count:
            continue
        words = [_title_words(r.get("title")) for r in short]
        alike = {i for i in range(len(short)) for j in range(len(short)) if i != j and words[i] and words[j]
                 and len(words[i] & words[j]) / len(words[i] | words[j]) >= 0.5}
        if len(alike) >= min_count:
            out[ch] = [f"{len(alike)} near-identical short clips from one channel in these results"]
    return out


_STOREFRONT = re.compile(r"amazon\.[a-z.]+/(shop|live|influencers?)/|amazon\.[a-z.]+/stores/|/influencer/", re.I)
_AMAZON_ANY = re.compile(r"amazon\.[a-z.]+/|amzn\.to/|amzn\.eu/|link\.amazon/|a\.co/", re.I)
_AMZ_TAGS = re.compile(r"#(amazonfinds?|amazonmusthaves?|founditonamazon|amazoninfluencer|amazonreview|"
                       r"amazongadgets?|amazonfavorites?)\b", re.I)


def amazon_influencer(title: str = "", description: str = "", links=(), length_s: int | None = None,
                      views: int | None = None, subscribers: int | None = None) -> tuple[bool, list[str]]:
    """Amazon-influencer style clip. Needs TWO or more signals: storefront link, #amazonfinds-style tags,
    a very short "review" with Amazon links, a clip under 90 s with Amazon links, little reach. A single
    storefront link is common on ordinary review channels (an 8.5-minute video from a 164K channel was
    flagged on one link), so it never flags alone. Returns (flagged, reasons); reasons are [] when not
    flagged."""
    reasons = []
    links = list(links or [])
    amazon = any(_AMAZON_ANY.search(u) for u in links) or bool(_AMAZON_ANY.search(description or ""))
    if any(_STOREFRONT.search(u) for u in links) or _STOREFRONT.search(description or ""):
        reasons.append("Amazon storefront link")
    if _AMZ_TAGS.search(f"{title} {description}"):
        reasons.append("Amazon-finds hashtag")
    if (length_s is not None and length_s < 300 and re.search(r"\breview\b", title or "", re.I) and amazon):
        reasons.append("short 'review' with Amazon links")
    elif length_s is not None and length_s < 90 and amazon:
        reasons.append("clip under 90 s with Amazon links")
    if amazon and ((views is not None and views < 10_000) or (subscribers is not None and subscribers < 5_000)):
        reasons.append("little reach")
    flagged = len(reasons) >= 2
    return flagged, reasons if flagged else []


# -------------------------------------------------------------------------
# Brands, roundups and per-brand sponsor scores (Y6)
# -------------------------------------------------------------------------

KNOWN_BRANDS = [
    "Bambu Lab", "Prusa", "Creality", "Elegoo", "Qidi", "Anycubic", "Snapmaker", "Flashforge", "Sovol", "Voron",
    "Artillery", "Ankermake", "Formlabs", "Raise3D", "Ultimaker", "Phrozen", "Voxel", "Polymaker", "eSun",
    "DJI", "Rode", "Hollyland", "Sennheiser", "Insta360", "Saramonic", "Shure", "Deity", "Tascam",
    "Boya", "Comica", "SmallRig", "Synco", "Sony", "Canon", "Nikon", "Fujifilm", "Panasonic", "GoPro",
    "Apple", "Samsung", "Google", "Meta", "Xreal", "Viture", "Rokid", "RayNeo", "Even Realities", "Brilliant Labs",
    "Logitech", "Razer", "Corsair", "Anker", "Ugreen", "Belkin", "Asus", "Acer", "Lenovo", "Dell", "HP", "MSI",
    "Garmin", "Fitbit", "Xiaomi", "OnePlus", "Bose", "Jabra", "Elgato", "Blue Yeti", "Audio-Technica",
    "EcoFlow", "Bluetti", "Jackery", "Goal Zero", "Renogy", "Oupes", "Vtoman", "Pecron", "Allpowers", "Fossibot",
    "Bissell", "Hoover", "Shark", "Rug Doctor", "Tineco", "Dyson", "Vax", "Kärcher", "Dreame", "Roborock",
    "Ecovacs", "iRobot", "Eufy", "Kenmore", "Miele", "Sebo", "Uwant",
    "Hatch", "Yogasleep", "Marpac", "LectroFan", "Adaptive Sound Technologies", "Snooz", "Momcozy", "Dreamegg",
    "Nanit", "Owlet", "Loftie", "HoMedics", "Letsfit", "Frida", "Munchkin", "Skip Hop", "Baby Brezza", "Tommee Tippee",
    "Graco", "Ergobaby", "Elvie", "Philips Avent", "VTech", "Infant Optics", "Cubo Ai", "Miku",
]
BRAND_ALIASES = {"Bambu Lab": ["Bambu", "BambuLab", "Bambu Labs"], "Rode": ["RØDE"], "Prusa": ["Prusa Research"],
                 "Kärcher": ["Karcher"], "EcoFlow": ["Eco Flow"], "Rug Doctor": ["RugDoctor"],
                 "Qidi": ["Qidi Tech", "QIDI"], "Insta360": ["Insta 360"], "Even Realities": ["Even G1", "Even G2"]}
# Hosts that never name a reviewed brand.
_PLATFORM = re.compile(r"(^|\.)(youtube|youtu|google|goo|amazon|amzn|instagram|facebook|fb|twitter|x|tiktok|"
                       r"threads|discord|patreon|ko-fi|buymeacoffee|linktr|bit|tinyurl|geni|da|is|cutt|t|ow|"
                       r"reddit|github|twitch|spotify|apple|bhphotovideo|bhpho|bestbuy|walmart|ebay|aliexpress|"
                       r"temu|etsy|kickstarter|indiegogo|wikipedia|imgur|gumroad|shopify|myshopify|linkin|"
                       r"linkedin|pinterest|snapchat|telegram|whatsapp|paypal|sjv|pxf|howl|impact|skimresources|"
                       r"shareasale|awin1|tidd|prf|avantlink|rstyle|linksynergy|lnk|rebrand|shorturl|buff|tiny)\.",
                       re.I)
_URL = re.compile(r"https?://\S+")


def _fold(s: str) -> str:
    return (s or "").lower().replace("ø", "o").replace("é", "e").replace("ä", "a")


# The creator's own equipment lists ("My gear:", "Current Video Gear", "Camera: ...") name camera and mic
# brands that are not reviewed in the video.
_GEAR_HEAD = re.compile(
    r"^\W*(?:(?:my|our|current|video|filming|camera|youtube|studio|recording)\s+){0,3}"
    r"(?:gear|equipment|kit|setup|set-?up)(?:\s+(?:i|we)\s+use|\s+used|\s+list)?\b[^\n]{0,30}:?\s*$|"
    r"^\W*(?:what|gear|equipment|tools?)\s+(?:i|we)\s+use\b[^\n]{0,40}:?\s*$|"
    r"^\W*(?:filmed|shot|recorded)\s+(?:with|on|using)\b", re.I)
_GEAR_LINE = re.compile(r"^\W*(?:main |b-?roll |second |a-?cam |b-?cam )?(?:cameras?|lens(?:es)?|mics?|microphones?|lav(?:alier)?s?|"
                        r"lights?|lighting|tripods?|gimbals?|audio|recorder|sd cards?|monitor|drone|editing|"
                        r"computer|laptop)\s*[:\-–]", re.I)


def strip_gear_blocks(description: str) -> str:
    """The description without the creator's gear/equipment sections: a gear header and the lines under it
    (until a blank line), and single "Camera: ..."/"Mic: ..." lines."""
    out, skipping = [], False
    for ln in (description or "").splitlines():
        if _GEAR_HEAD.search(ln):
            skipping = not re.match(r"^\W*(?:filmed|shot|recorded)\b", ln, re.I)
            continue
        if skipping:
            if not ln.strip():
                skipping = False
                out.append(ln)
            continue
        if _GEAR_LINE.search(ln):
            continue
        out.append(ln)
    return "\n".join(out)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def _word_re(name: str) -> re.Pattern:
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", _fold(name)) if p]
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-]*".join(parts) + r"(?![a-z0-9])")


def _mentions(text: str, brand: str) -> bool:
    t = _fold(text)
    return any(_word_re(n).search(t) for n in [brand] + BRAND_ALIASES.get(brand, []))


def detect_brands(title: str = "", description: str = "", links=(), own_domains=()) -> list[str]:
    """Brands named in the title or description: a built-in brand list, plus link hosts (not platforms,
    networks, or the channel's own sites) whose name also appears in the text."""
    kept = strip_gear_blocks(description)
    text = _URL.sub(" ", f"{title}\n{kept}")
    found = [b for b in KNOWN_BRANDS if _mentions(text, b)]
    gear_links = set(re.findall(r"https?://\S+", description or "")) - set(re.findall(r"https?://\S+", kept))
    own = {_alnum(d) for d in own_domains or ()}
    words = _alnum(text)
    from urllib.parse import urlparse
    for u in links or []:
        try:
            host = (urlparse(u).hostname or "").lower().removeprefix("www.")
        except ValueError:
            continue
        if (not host or u in gear_links or _PLATFORM.search(host + ".")
                or any(o and o in _alnum(host) for o in own)):
            continue
        stem = host.split(".")[-2] if host.count(".") >= 1 else host
        if len(stem) < 4 or _alnum(stem) in {_alnum(b) for b in found}:
            continue
        if any(_alnum(stem) in _alnum(b) or _alnum(b) in _alnum(stem) for b in found):
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(stem) + r"(?![a-z0-9])", _fold(text)):
            found.append(stem.capitalize())
            continue
        # A brand site whose name is the brand plus a word ("bluettipower.com", "ankerfast.club"): a
        # capitalized word of 4+ letters in the text that the host name starts with.
        for w in dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z]{3,}\b", text)):
            if _alnum(stem).startswith(_alnum(w)) and len(_alnum(stem)) > len(_alnum(w)) \
                    and _alnum(w) not in {"https", "http", "shop", "store", "check", "link", "free", "best"}:
                if not any(_alnum(w) == _alnum(b) for b in found):
                    found.append(w)
                break
    return found


def _link_brands(url: str, description: str, brands: list[str]) -> list[str]:
    """Brands named on the description line holding `url`, or on the line before it (a label)."""
    lines = (description or "").splitlines()
    for i, ln in enumerate(lines):
        if url in ln:
            here = [b for b in brands if _mentions(_URL.sub(" ", ln), b) or _alnum(b) in _alnum(url)]
            if here:
                return here
            prev = next((lines[j] for j in range(i - 1, -1, -1) if lines[j].strip()), "")
            return [b for b in brands if _mentions(_URL.sub(" ", prev), b)]
    return [b for b in brands if _alnum(b) in _alnum(url)]


_PER_BRAND_SIGNALS = {"sponsor_read", "third_party_sponsor", "review_unit", "use_my_link_or_code", "brand_code_or_link",
                      "giveaway", "brand_giveaway", "bought_it_myself", "bought_something_myself"}


def per_brand(hits: list[dict], brands: list[str], description: str = "", links_by_kind: dict | None = None,
              score_fn=None) -> dict:
    """Split sponsor evidence by brand. A hit naming a brand (its text or `sponsor`) goes to that brand;
    unnamed hits are shared by all brands. Affiliate/campaign links are attributed by their description
    line. The paid-promotion label goes to the brand(s) named in sponsor reads, else it is shared.
    Returns {by_brand: {brand: {score, level, signals}}, shared_score}."""
    import disclosure as D  # noqa: WPS433 (lib is on sys.path in youtube_tab)
    score_fn = score_fn or D.score
    description = strip_gear_blocks(description)
    per = {b: [] for b in brands}
    shared: list[dict] = []
    sponsored_brands: set[str] = set()
    label = None
    for h in hits:
        sig = h.get("signal")
        if sig == "paid_promotion_label":
            label = h
            continue
        if sig in ("affiliate_links", "brand_affiliate_links", "campaign_tracking"):
            continue  # re-attributed per link below
        sp = _alnum(h.get("sponsor") or "")
        named = [b for b in brands if _mentions(f"{h.get('sponsor') or ''} {h.get('text') or ''}", b)
                 or (sp and len(sp) >= 3 and (_alnum(b) in sp or sp in _alnum(b)))]
        if sig in _PER_BRAND_SIGNALS and named:
            for b in named:
                hb = dict(h)
                if sig == "use_my_link_or_code":
                    hb.update(signal="brand_code_or_link", weight=D.WEIGHTS["brand_code_or_link"])
                elif sig == "giveaway":
                    hb.update(signal="brand_giveaway", weight=D.WEIGHTS["brand_giveaway"])
                elif sig == "third_party_sponsor":
                    hb.update(signal="sponsor_read", weight=D.WEIGHTS["sponsor_read"])  # it is this brand's read
                elif sig == "bought_something_myself":
                    hb.update(signal="bought_it_myself", weight=D.WEIGHTS["bought_it_myself"])
                per[b].append(hb)
                if hb["signal"] == "sponsor_read":
                    sponsored_brands.add(b)
        else:
            shared.append(h)
    for kind in ("affiliate_links", "campaign_links"):
        for url in (links_by_kind or {}).get(kind) or []:
            owners = _link_brands(url, description, brands)
            sig = "brand_affiliate_links" if kind == "affiliate_links" else "campaign_tracking"
            hit = {"signal": sig, "weight": D.WEIGHTS[sig] if sig in D.WEIGHTS else 0.2, "source": "description",
                   "text": url}
            if owners:
                for b in owners:
                    if not any(x["signal"] == sig for x in per[b]):
                        per[b].append(hit)
            elif not any(x["signal"] == ("affiliate_links" if kind == "affiliate_links" else sig) for x in shared):
                shared.append({**hit, "signal": "affiliate_links" if kind == "affiliate_links" else sig,
                               "weight": D.WEIGHTS["affiliate_links"] if kind == "affiliate_links" else 0.1})
    if label:
        if sponsored_brands:
            for b in sponsored_brands:
                per[b].insert(0, label)
        else:
            shared.insert(0, label)
    out = {}
    for b in brands:
        res = score_fn(per[b], shared)
        out[b] = {"score": res["score"], "level": res["level"],
                  "signals": [e["signal"] for e in res["evidence"]]}
    sh = score_fn([], shared)
    return {"by_brand": out, "shared_score": sh["score"]}


def score_for_term(rec: dict, term: str) -> float | None:
    """The sponsor score of `rec` that applies to tally term `term`: in a multi-brand video, the score of
    the brand named in the term (else the shared score); otherwise the video's overall score."""
    bb = rec.get("by_brand")
    if not bb:
        return rec.get("sponsor_score")
    hits = [b for b in bb if _mentions(term, b)]
    if hits:
        return max(bb[b]["score"] for b in hits)
    return rec.get("by_brand_shared", rec.get("sponsor_score"))


# -------------------------------------------------------------------------
# Y18: seeded-comment hint
# -------------------------------------------------------------------------

_SLANG = re.compile(r"\b(fr|ngl|lowkey|highkey|tbh|imo|imho|deadass|istg|no cap|fam|bruh|goated|slaps|fire|"
                    r"vibes?|hands down|literally|honestly|tho)\b", re.I)
_PRAISE = re.compile(r"\b(flows?|seamless(?:ly)?|game ?changer|super (?:convenient|easy|smooth)|so (?:smooth|good|"
                     r"much better|easy)|just works|nothing beats|go with|workflow|ecosystem|love (?:it|mine|my|the)|"
                     r"best (?:choice|option|value|purchase)|worth every|highly recommend|can'?t go wrong|"
                     r"(?:way|so much) better|convenient|no brainer|blown away)\b", re.I)
_SPECIFIC = re.compile(r"\d|\b(?:battery|hours?|range|firmware|update|meters?|feet|ft|db|hz|khz|mm|gb|setting|"
                       r"noise floor|clip|case|wind|hiss|dropout|latency|pairing)\b", re.I)
_GENERIC_HANDLE = re.compile(r"^@?[a-z]+(?:[a-z]+)?\d{2,5}$|^@?[A-Z][a-z]+[A-Z][a-z]+$")


def _words(t: str) -> set[str]:
    return set(re.findall(r"[a-z']{3,}", (t or "").lower()))


def comment_promo_hints(comments: list[dict], brands: list[str]) -> dict | None:
    """Flag short, generic, slangy pro-brand praise without specifics (each comment gets `promo_hint`
    reasons); return a low-confidence summary when two or more look alike. Never proof of seeding."""
    flagged = []
    for c in comments or []:
        text = c.get("text") or ""
        if len(text) > 220 or not text:
            continue
        brand = [b for b in brands if _mentions(text, b)]
        if not brand or not _PRAISE.search(text) or _SPECIFIC.search(text):
            continue
        reasons = ["short generic praise naming " + ", ".join(brand)]
        if _SLANG.search(text):
            reasons.append("slang filler")
        if _GENERIC_HANDLE.match((c.get("author") or "").strip()):
            reasons.append("generic handle")
        if len(reasons) < 2:
            continue
        c["promo_hint"] = reasons
        flagged.append(c)
    if len(flagged) < 2:
        for c in flagged:
            c.pop("promo_hint", None)
        return None
    similar = any(len(_words(a["text"]) & _words(b["text"])) / max(1, len(_words(a["text"]) | _words(b["text"]))) >= 0.3
                  for i, a in enumerate(flagged) for b in flagged[i + 1:])
    ages = {c.get("age") for c in flagged}
    reasons = [f"{len(flagged)} short, generic pro-brand comments without specifics"]
    if similar:
        reasons.append("similar wording")
    if len(ages) == 1 and None not in ages:
        reasons.append(f"posted around the same time ({ages.pop()})")
    return {"level": "low", "count": len(flagged), "authors": [c.get("author") for c in flagged],
            "reasons": reasons,
            "note": "Possibly seeded comments (low confidence). Don't count them as owner experience."}


# -------------------------------------------------------------------------
# Y7 / Y8
# -------------------------------------------------------------------------

def unique_path(path: Path, used: set | None = None) -> Path:
    """`path`, or `<stem>-2<ext>`, `-3`, ... when this call already wrote it or the file exists."""
    path = Path(path)
    used = used if used is not None else set()
    cand, n = path, 2
    while str(cand) in used or cand.expanduser().exists():
        cand = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    used.add(str(cand))
    return cand


def outermost_ads(ads: list[dict]) -> list[dict]:
    """One entry per ad unit: drop ads whose rect lies (mostly) inside a larger reported ad (YouTube nests
    ytd-ad-slot-renderer > ytd-in-feed-ad-layout-renderer > ...)."""
    def area(r):
        return max(0.0, r["width"]) * max(0.0, r["height"])

    def overlap(a, b):
        w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
        h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
        return max(0.0, w) * max(0.0, h)

    kept: list[dict] = []
    for ad in sorted(ads, key=lambda a: -area(a["rect"])):
        if not any(overlap(ad["rect"], k["rect"]) >= 0.8 * area(ad["rect"]) for k in kept):
            kept.append(ad)
    return sorted(kept, key=lambda a: a["rect"]["y"])


THEATER_MIN_WIDTH = 1280
MIN_PLAYER_WIDTH = 640


def player_warning(width: float, theater: bool, largest_seen: float | None = None) -> str | None:
    """Warn when the player is noticeably smaller than usual (a window manager may have resized it)."""
    w = round(width)
    small = (w < MIN_PLAYER_WIDTH or (theater and w < THEATER_MIN_WIDTH)
             or (largest_seen and w < 0.85 * largest_seen))
    if not small:
        return None
    seen = f" (it was {round(largest_seen)} px earlier)" if largest_seen and largest_seen > w else ""
    return (f"The player is only {w} px wide{' in theater mode' if theater else ''}{seen}, so details may be hard "
            "to read. Tell the user the research window is small (a window manager may be tiling it); "
            "don't resize it yourself.")


# -------------------------------------------------------------------------
# Y16: graphic-ness of a frame (stdlib PNG decode)
# -------------------------------------------------------------------------

def _png_rgb(data: bytes) -> tuple[int, int, list[bytes], int]:
    """Decode an 8-bit RGB/RGBA, non-interlaced PNG into rows of raw pixels. Returns (w, h, rows, bpp)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat = 8, b""
    w = h = ctype = 0
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or ctype not in (2, 6) or inter:
                raise ValueError("unsupported PNG")
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + n
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[x] = (line[x] + pr) & 0xFF
        rows.append(bytes(line))
        prev = line
    return w, h, rows, bpp


def graphic_score(png: bytes, max_side: int = 160) -> dict:
    """How much a frame looks like a graphic (chart, table, slide, UI) rather than a talking head:
    edge density (text and lines), coverage by a few flat colors, and little skin tone. 0..1, higher = more
    graphic. Works on a small PNG (the caller captures one at low scale)."""
    w, h, rows, bpp = _png_rgb(png)
    step = max(1, max(w, h) // max_side)
    gray, colors, skin, n = [], {}, 0, 0
    for y in range(0, h, step):
        row, g = rows[y], []
        for x in range(0, w, step):
            r, gg, b = row[x * bpp], row[x * bpp + 1], row[x * bpp + 2]
            g.append((r * 30 + gg * 59 + b * 11) // 100)
            q = (r >> 4, gg >> 4, b >> 4)
            colors[q] = colors.get(q, 0) + 1
            if r > 95 and gg > 40 and b > 20 and r > gg and r > b and r - gg > 15 and max(r, gg, b) - min(r, gg, b) > 15:
                skin += 1
            n += 1
        gray.append(g)
    edges = total = 0
    for y in range(len(gray) - 1):
        for x in range(len(gray[y]) - 1):
            d = abs(gray[y][x] - gray[y][x + 1]) + abs(gray[y][x] - gray[y + 1][x])
            edges += d > 60
            total += 1
    edge_density = edges / max(1, total)
    top = sum(sorted(colors.values(), reverse=True)[:5]) / max(1, n)
    skin_ratio = skin / max(1, n)
    score = 0.45 * min(1.0, edge_density / 0.12) + 0.35 * top + 0.2 * (1 - min(1.0, skin_ratio / 0.25))
    return {"score": round(score, 3), "edge_density": round(edge_density, 3), "flat_colors": round(top, 3),
            "skin": round(skin_ratio, 3)}


# -------------------------------------------------------------------------
# Y12: saved transcripts
# -------------------------------------------------------------------------

def _mmss(sec) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def save_transcript(out_dir: str | Path | None, meta: dict, segments: list[dict], today: date | None = None) -> dict:
    """Write the full transcript as `YYYY-MM-DD_<id>_transcript.json` (metadata + segments {sec, at, link,
    text}) and a readable `.md` next to it. If a transcript of this video is already in `out_dir`, it is
    reused (not rewritten). Returns {json, md, reused}."""
    d = Path(out_dir or DEFAULT_TRANSCRIPTS).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    vid = meta.get("video_id") or "video"
    existing = sorted(d.glob(f"*_{vid}_transcript.json"))
    replaced = None
    if existing:
        j = existing[-1]
        try:
            had = int(json.loads(j.read_text()).get("total_segments") or 0)
        except (OSError, ValueError):
            had = 0
        if had >= len(segments or []):
            md = j.with_suffix(".md")
            return {"json": str(j), "md": str(md) if md.exists() else None, "reused": True,
                    "note": "A transcript of this video was already saved there; it was reused, not rewritten."}
        replaced = had  # the earlier copy was partial: write the fuller one
        for old in (j, j.with_suffix(".md")):
            old.unlink(missing_ok=True)
    stem = f"{(today or date.today()).isoformat()}_{vid}_transcript"
    segs = [{"sec": s.get("sec"), "at": s.get("at") or _mmss(s.get("sec")), "link": s.get("link"),
             "text": s.get("text")} for s in segments]
    sb = meta.get("sponsor_bias") or {}
    doc = {
        "video_id": vid, "title": meta.get("title"), "channel": meta.get("channel"), "url": meta.get("url"),
        "published": meta.get("published"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "language": meta.get("language"),
        "sponsor_bias": {"score": sb.get("score"), "level": sb.get("level"), "confidence": sb.get("confidence"),
                         "evidence": [{k: e.get(k) for k in ("signal", "weight", "source", "text", "link") if e.get(k) is not None}
                                      for e in sb.get("evidence", [])]} if sb else None,
        "total_segments": len(segs), "coverage": meta.get("coverage"), "segments": segs,
    }
    j = d / f"{stem}.json"
    j.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    lines = [f"# {meta.get('title') or vid}", ""]
    for label, val in (("Channel", meta.get("channel")), ("URL", meta.get("url")), ("Published", meta.get("published")),
                       ("Fetched", doc["fetched_at"]), ("Language", meta.get("language"))):
        if val:
            lines.append(f"- {label}: {val}")
    if sb:
        sigs = ", ".join(e.get("signal", "?") for e in sb.get("evidence", []))
        lines.append(f"- Sponsor bias at fetch: {sb.get('score')} ({sb.get('level')}, {sb.get('confidence')} confidence)"
                     + (f": {sigs}" if sigs else ""))
    cov = meta.get("coverage") or {}
    gaps = ", ".join(f"{g['from']}–{g['to']}" for g in cov.get("gaps") or [])
    lines += [f"- Segments: {len(segs)}" + (f" (gaps: {gaps})" if gaps else ""), ""]
    lines += [f"[{s['at']}]({s['link']}) {s['text']}" if s.get("link") else f"[{s['at']}] {s['text']}" for s in segs]
    md = d / f"{stem}.md"
    md.write_text("\n".join(lines) + "\n")
    out = {"json": str(j), "md": str(md), "reused": False}
    if replaced is not None:
        out["replaced_partial"] = f"an earlier copy with {replaced} segments was replaced"
    return out
