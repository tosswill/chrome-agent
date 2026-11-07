"""Disclosure signals shared by the research skills (YouTube, Reddit).

Finds sponsorship, review-unit, affiliate and promotion signals in text and
links, and scores them 0.0-1.0 with the evidence behind the score. Brand-aware:
signals tied to the brand being discussed weigh more than unrelated ones.
Stdlib only, no browser: everything here is pure and unit-tested.

Public API (signatures kept compatible with the original module):

    scan_text(text, source, link=None, subject="", *, channel=None,
              own_domains=(), context="") -> list[hit]
        Signals in one block of text, split into sentences. `subject` is the
        title (plus keywords) used to tell the reviewed brand from third
        parties. `context` is neighboring text (e.g. the previous and next
        transcript segments): it is used only to judge denials and to name the
        service behind a discount code, never as a source of hits.
    scan_segments(segments, subject="", *, channel=None, own_domains=(),
                  source_fmt="transcript {at}") -> list[hit]
        scan_text over transcript segments ({text, at, link}), each with its
        neighbors as context, de-duplicated (YouTube shows a transcript twice).
    classify_links(links, *, channel=None, description="", handle=None) -> dict
        {affiliate_links, campaign_links, short_links, self_promotion_links,
         own_domains}: affiliate, utm/campaign, short links, and the channel's
        own store/site links (self-promotion, not sponsorship).
    link_hits(classified) -> list[hit]
        Hits for a classify_links() result (or a video.js `disclosure` dict).
    brand_owned(channel, title, *, handle=None, verified=False,
                brand_domains=()) -> hit | None
        Brand-owned channel check from the channel name/handle against the
        brand in the TITLE only (never keywords or shop links).
        "strong" (weight 1.0) only with a handle matching a brand domain or a
        verified-brand flag; "possible" (0.3) otherwise.
    brand_adjust(hits, subject, channel, *, title=None, handle=None,
                 verified=False, brand_domains=()) -> list[hit]
        Brand-aware reweighting (brand affiliate links, codes, giveaways,
        campaign links) plus the brand_owned() check when `channel` is given.
    score(page, extra) -> {score, level, evidence, note[, brand_owned]}
    dedupe_hits(hits) -> list[hit]
    brand_tokens(subject) -> set[str]
    negated(text, start, end, window=6) -> bool

A hit is {signal, weight, source, text[, link][, sponsor][, via]}. Signals:
sponsor_read (0.5), ad_tag (#ad / #sponsored / paid partnership, 0.4), third_party_sponsor (0.15), review_unit (0.25),
use_my_link_or_code (0.15), affiliate_disclosure (0.1), giveaway (0.05;
brand_giveaway 0.2 when it gives away the reviewed brand), self_promotion
(0.05), rented_or_borrowed (-0.05), bought_it_myself (-0.15),
claims_not_sponsored (-0.1), editorial_independence (-0.05). Link signals:
affiliate_links (0.15), campaign_tracking (0.2 influencer-tagged, else 0.1),
self_promotion (0.05). brand_adjust adds brand_affiliate_links (0.25),
brand_code_or_link (0.2), bought_something_myself (-0.05) and
brand_owned_channel (1.0 strong / 0.3 possible).
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

GUIDE = ("Estimate from the page and transcript. Set 1.0 for brand-owned channels; raise it for channels mostly "
         "covering one brand; report the final score with reasons.")

# Words that never identify a brand (title and keyword filler, common English).
GENERIC = {
    "the", "and", "official", "channel", "tech", "review", "reviews", "glasses", "smart", "video",
    "videos", "best", "new", "with", "for", "pro", "max", "plus", "ultra", "air", "one", "app", "apps",
    "you", "your", "yours", "this", "that", "these", "those", "which", "what", "who", "why", "how",
    "should", "would", "could", "can", "will", "buy", "buying", "worth", "vs", "versus", "than", "then",
    "are", "was", "were", "has", "have", "had", "not", "but", "all", "any", "out", "get", "got", "its",
    "our", "they", "them", "their", "from", "into", "over", "under", "after", "before", "about", "just",
    "now", "still", "really", "very", "most", "more", "less", "every", "tested", "test", "testing",
    "tests", "comparison", "compared", "guide", "top", "budget", "cheap", "year", "years", "month",
    "months", "week", "day", "days", "time", "way", "here", "there", "why", "don", "isn", "doesn",
    "problem", "problems", "issue", "issues", "catch", "honest", "truth", "watch", "first", "look",
    "hands", "unboxing", "setup", "upgrade", "upgrades", "worst", "better", "good", "bad", "great",
    "real", "full", "long", "term", "update", "edition", "version", "model", "models", "part", "parts",
    "how", "make", "made", "makes", "use", "using", "used", "need", "want", "like", "love", "everything",
    "nothing", "something", "anything", "ever", "never", "again", "finally", "only", "also", "too",
    "some", "many", "much", "own", "off", "per", "via", "sponsored", "sponsor", "today", "today's",
}

PRONOUNS = {"me", "myself", "us", "them", "him", "her", "it", "anyone", "anybody", "someone", "somebody",
            "nobody", "any", "anything", "you", "everyone"}

# Services whose codes and reads are third-party sponsorships in a product review.
KNOWN_SERVICES = (
    "soundstripe", "epidemic sound", "artlist", "musicbed", "envato", "storyblocks", "motion array",
    "audible", "skillshare", "squarespace", "wix", "hostinger", "nordvpn", "expressvpn", "surfshark",
    "private internet access", "proton", "brilliant", "ground news", "incogni", "aura", "betterhelp",
    "hellofresh", "factor", "shopify", "notion", "honey", "raycon", "manscaped", "ridge", "ag1",
    "athletic greens", "displate", "1password", "dashlane", "bitwarden", "ziprecruiter", "rocket money",
    "speechify", "babbel", "masterclass", "curiositystream", "nebula", "opera gx", "raid shadow legends",
    "canva", "pcbway", "jlcpcb", "voxel", "keeps", "magic spoon", "liquid iv", "mint mobile",
)
_SERVICE_RE = re.compile(r"\b(" + "|".join(re.escape(s) for s in KNOWN_SERVICES) + r")\b", re.I)
_SERVICE_WORDS = re.compile(
    r"\b(music|songs?|soundtracks?|royalty[- ]free|stock (?:footage|music|video|photos?)|vpn|website builder|"
    r"web ?hosting|software|plugins?|plug-ins?|subscription|password manager|meal kits?|insurance|antivirus|"
    r"online courses?|audiobooks?|sound effects|sfx|luts?)\b", re.I)


# -------------------------------------------------------------------------
# Negation
# -------------------------------------------------------------------------

_NEG_WORDS = {"not", "never", "no", "nobody", "none", "neither", "nor", "without", "nothing", "cannot",
              "dont", "didnt", "doesnt", "isnt", "wasnt", "arent", "werent", "havent", "hasnt", "wont",
              "wouldnt", "aint"}
_NEG_CUES = re.compile(r"\b(no reason|not because|it'?s not like|isn'?t like|doesn'?t mean|not that|"
                       r"not saying|i wish)\b", re.I)
_HEDGE_AFTER = re.compile(r"^[\s\w'’]{0,25}?\bor (anything|whatever|something)\b", re.I)
_CLAUSE_BREAK = re.compile(r"[,;:()\"“”]|\b(but|however|although|though|whereas)\b", re.I)


def negated(text: str, start: int, end: int, window: int = 6) -> bool:
    """Whether the match text[start:end] is negated: a negation word within `window` tokens before it
    in the same clause ("never sponsored", "did not sponsor", "not sponsored by"), a denial cue earlier
    in the clause ("no reason ... that I'm sponsored"), or a hedge right after it ("... or anything")."""
    before = text[:start]
    cut = 0
    for m in _CLAUSE_BREAK.finditer(before):
        cut = m.end()
    clause = before[cut:]
    toks = [t.replace("'", "").replace("’", "") for t in re.findall(r"[A-Za-z'’]+", clause.lower())][-window:]
    if any(t in _NEG_WORDS or t.endswith("nt") and t[:-2] + "nt" in _NEG_WORDS for t in toks):
        return True
    if any(t.endswith("n") and t + "t" in _NEG_WORDS for t in toks):  # "didn't" tokenized as "didn" + "t"
        return True
    if _NEG_CUES.search(clause):
        return True
    return bool(_HEDGE_AFTER.match(text[end:end + 45]))


# -------------------------------------------------------------------------
# Patterns
# -------------------------------------------------------------------------

_SPONSOR_READ = re.compile(
    r"\b(this (?:video|episode) is (?:sponsored|brought to you) by|sponsored by|thanks? (?:you )?to \S+(?: \S+)? for "
    r"sponsoring|today'?s sponsor|this (?:video|episode)'?s sponsor|(?:the|our) sponsor (?:of|for) (?:this|today'?s) "
    r"(?:video|episode)|, (?:the|our|today'?s) sponsor\b|in (?:paid )?partnership with|paid partnership|#ad\b|"
    r"#sponsored|brought to you by)", re.I)
_SPONSOR_VERB = re.compile(r"\bsponsor(?:ed|ing|s)?\b|\bpa(?:id|ying) (?:me|us)\b", re.I)

_NAME = r"([A-Z0-9][\w&.+'’-]*(?:\s+[A-Z0-9][\w&.+-]*){0,2}|[a-z0-9-]+\.(?:com|net|org|io|co|tv|gg|app|shop|store)\b)"
_SPONSOR_NAMES = [
    re.compile(r"(?i:sponsored by|brought to you by|in (?:paid )?partnership with|today'?s sponsor(?: is)?|"
               r"this (?:video|episode)'?s sponsor(?: is)?)[\s:]+" + _NAME),
    re.compile(r"(?i:thanks?(?: you)? to|shout ?out to)\s+" + _NAME + r"\s+(?i:for sponsoring|who (?:is|are) sponsoring)"),
    re.compile(r"([A-Z][\w&.+-]*(?:\s+[A-Za-z][\w&.+-]*)?)\s*,\s*(?i:(?:who (?:is|are) )?(?:the|our|today'?s|this "
               r"video'?s) sponsor)"),
]

_REVIEW_UNIT = [
    r"\b(?:sent|send|sending|shipped)\s+(?:me|us)\b(?:\s+[\w'-]+){0,6}?\s+(?:to|for)\s+(?:review|test|testing|try|"
    r"evaluate|evaluation|check out)\b",
    r"\b(?:did|have|has|had|kindly|graciously|generously)\s+(?:also\s+)?(?:sent|send)\s+(?:me|us|over)\b",
    r"\bfor\s+(?:sending|providing|lending|loaning)\s+(?:me\s+|us\s+)?(?:this|these|the|it|them|over|out|a|an|"
    r"their|its|in)\b",
    r"\breview\s+(?:unit|sample|copy|loaner)s?\b",
    r"\bloaner\b", r"\bon loan\b", r"\bloaned\s+(?:to\s+)?(?:me|us)\b", r"\blent\s+(?:me|us)\b",
    r"\bprovided\s+(?:to\s+(?:me|us)\s+)?(?:by\s+[\w&.+-]+(?:\s+[\w&.+-]+){0,2}\s+)?for\s+(?:this\s+|the\s+|my\s+)?"
    r"(?:review|video|testing|evaluation)\b",
    r"\b(?:unit|sample|product|printer|camera|mic|microphone|device)s?\s+(?:was|were)\s+(?:kindly\s+)?(?:provided|"
    r"supplied|sent)\b",
    r"\b(?:was|were|been|got)\s+gifted\b", r"\bgifted\s+(?:to\s+)?(?:me|us)\b",
    r"\bearly access\b", r"\bunder embargo\b", r"\bembargo\s+(?:lifts?|ends?)\b",
    r"\b(?:sent|send)\s+(?:me|us)\s+(?:this|these|the|a|an|their|its|one)\b",
    r"\b(?:i|we)\s+(?:was|were|got|have been|'ve been|had been)\s+sent\s+(?:this|these|the|it|them|a|an|one|some)\b",
    r"\b(?:was|were)\s+sent\s+to\s+(?:me|us)\b", r"\bsent\s+(?:to\s+)?(?:me|us)\s+for\s+review\b",
]
_REVIEW_UNIT_RE = [re.compile(p, re.I) for p in _REVIEW_UNIT]
# A free replacement, a file, or a rental is not a review unit.
_REVIEW_EXCLUDE = re.compile(r"\b(support|warranty|replacement|replaced|rma|refund|customer service|defective|doa|"
                             r"rental|rented|renting|rent|files?|stls?|3mfs?|g-?code|tips?|tip jar|donations?|"
                             r"donate|venmo|paypal|cash ?app|zelle|ko-?fi|buy me a coffee|super ?thanks|"
                             # personal gifts: a baby shower, a registry, family or friends
                             r"(?:baby )?shower|registry|birthday|christmas|grand(?:ma|pa|mother|father|parents?)|"
                             r"in-?laws?|(?:my|our) (?:mom|dad|mum|mother|father|parents|sister|brother|aunt|uncle|"
                             r"friends?|cousin|neighbou?r)s?)\b", re.I)
_SENT_ME_OBJECT_EXCLUDE = re.compile(
    r"\b(?:sent|send)\s+(?:me|us)\s+(?:this|these|the|a|an|their|its|one)\s+(?:\w+\s+)?(e-?mails?|messages?|links?|dms?|"
    r"texts?|notes?|invoices?|quotes?|photos?|pictures?|pics?|videos?|codes?|repl(?:y|ies)|updates?|new one|spares?|"
    r"parts?|surveys?|tips?|donations?|money|coffee)\b", re.I)

_CODE_RE = re.compile(
    r"\b(use my (?:link|code)|use (?:the )?code \S+|(?:with|using) (?:the )?code \S+|code \S+ (?:at checkout|for \d+ ?%)|"
    r"discount code|promo code|coupon code|\d+ ?% off (?:with|using)|(?:buy|get|order|grab|pick up) (?:one|yours|it|them|"
    r"a pair)? ?(?:through|using|with|from|via) (?:my|the) (?:link|affiliate))", re.I)
_AFFILIATE_DISCLOSURE = re.compile(
    r"(\baffiliate links?\b|\(affiliate\)|\b(?:i|we) (?:may |might |do |will )?(?:earn|receive|get|make) (?:a )?"
    r"(?:small )?(?:commission|cut)|commission (?:from|on|if|when) |as an amazon associate|"
    r"\b(?:at|for) no (?:extra|additional) cost to you)", re.I)
# "#ad" / "#sponsored" / "paid partnership": a written sponsorship tag (disclosed brand money).
_AD_TAG = re.compile(r"(?<![\w#])#(?:ad|ads|sponsored|paidpartnership|partner)\b|\bpaid partnership with\b", re.I)
_GIVEAWAY = re.compile(r"\b(giveaway|give-?away|giving\s+(?:\w+\s+){0,4}?away|chance to win|enter to win|"
                       r"win (?:one|this|these|a|an|your own|it)\b|raffle|sweepstakes?)", re.I)
# "the biggest giveaway", "a dead giveaway that ...": a telltale sign, not a prize.
_GIVEAWAY_TELLTALE = re.compile(r"\b(?:dead|biggest|big|real|obvious|clear|easy|main|only|other|first)\s+giveaway\b|"
                                r"\bgiveaway\s+(?:to|of|that|is that|was that|here)\s+(?:the|this|it|how|which|what)\b",
                                re.I)
_GIVEAWAY_CONTEXT = re.compile(r"\b(win|winners?|enter|entry|entries|comment|subscribe|free|chance|contest|announce|"
                               r"link|rules|chosen|picked?|ends?|participate|hosting|running|doing|my|this week'?s|"
                               r"giving|prize|ship)\b", re.I)
# Only the speaker's own rental or loan counts: "I rented it", "we borrowed one from", "this is a rental
# unit". Advice ("or just rented one from a kiosk", "you can rent one") is not a disclosure.
_RENTAL = re.compile(
    r"\b(?:i|we|i'?ve|we'?ve|i'?d|we'?d|i had|we had|i just|we just)\s+(?:\w+\s+){0,2}?(?:rented|borrowed)\b|"
    r"\b(?:this|it|the (?:unit|one|machine|camera|printer|car))\s+(?:is|was)\s+(?:a\s+|actually\s+a\s+)?"
    r"(?:rental|borrowed|on loan)\b|\b(?:my|our) rental\b|\brental (?:equipment|gear|unit)s? (?:i|we)\b|"
    r"\brental (?:equipment|gear|units?)\s+(?:was\s+|were\s+)?(?:provided|supplied|courtesy|from|thanks)\b|"
    r"\b(?:rented|borrowed) from (?:the|a|my|our)\s+[A-Z]", re.I)
_BOUGHT_STRONG = re.compile(
    r"\b((?:bought|purchased|paid for) (?:it|this|them|these|this one|mine|my own|one) (?:myself|with my own money|"
    r"out of (?:my own )?pocket)|with my own money|bought (?:it|them|this|these|one) myself|i paid (?:full )?(?:retail|"
    r"full price)|(?:i|we) (?:bought|purchased) (?:it|this|them|these|this one|mine|my own|one)\b)", re.I)
_BOUGHT_WEAK = re.compile(r"\b(?:i|we) (?:bought|purchased|paid for)\b", re.I)
_NOT_SPONSORED = re.compile(
    r"\b(not (?:an? )?(?:sponsored|paid|gifted)|no(?:body| one) (?:paid|sponsored)|un-?sponsored|isn'?t (?:an? )?"
    r"sponsored|wasn'?t (?:an? )?(?:sponsored|paid)|no sponsors?\b|non-?sponsored)", re.I)
_EDITORIAL = re.compile(
    r"\b((?:they|\w+) (?:did ?n'?o?t|do ?n'?o?t|never) (?:see|review|approve|preview|get to see) (?:the|this|my) "
    r"(?:video|review)(?: before| ahead| in advance)?|(?:did ?n'?o?t|do ?n'?o?t|never) (?:have|get) (?:any )?"
    r"(?:input|say|editorial (?:control|input))|no editorial (?:control|input|say)|without (?:their|any) "
    r"(?:input|review|approval))", re.I)

WEIGHTS = {
    "sponsor_read": 0.5, "third_party_sponsor": 0.15, "review_unit": 0.25, "use_my_link_or_code": 0.15, "ad_tag": 0.4,
    "affiliate_disclosure": 0.1, "giveaway": 0.05, "brand_giveaway": 0.2, "self_promotion": 0.05,
    "rented_or_borrowed": -0.05, "bought_it_myself": -0.15, "claims_not_sponsored": -0.1,
    "editorial_independence": -0.05, "affiliate_links": 0.15, "campaign_tracking": 0.2,
    "brand_affiliate_links": 0.25, "brand_code_or_link": 0.2, "bought_something_myself": -0.05,
}
# Kept for callers that iterate the old table.
SIGNALS = [(k, v, None) for k, v in WEIGHTS.items()]


# -------------------------------------------------------------------------
# Brand helpers
# -------------------------------------------------------------------------

def brand_tokens(subject: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", subject or "")} - GENERIC


def _about(text: str, toks: set[str]) -> bool:
    low = (text or "").lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", low) for t in toks)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_CHANNEL_FILLER = re.compile(r"\b(official|channel|tv|global|store|inc|ltd|llc|hq|team|world|\(.*?\))\b", re.I)


def _channel_tokens(channel: str | None) -> set[str]:
    return {t for t in brand_tokens(_CHANNEL_FILLER.sub(" ", channel or "")) if len(t) >= 4}


def _is_third_party(name: str | None, subject: str) -> bool:
    if not name or not subject:
        return False
    ntoks = {t for t in re.findall(r"[a-z0-9]{2,}", name.lower())} - GENERIC
    stoks = {t for t in re.findall(r"[a-z0-9]{2,}", subject.lower())}
    if not ntoks:
        return False
    if ntoks & stoks:
        return False
    return _alnum(name) not in _alnum(subject)  # "PCB way" vs "PCBWay"


def _host(url: str) -> str:
    try:
        return (urlparse(url if "//" in url else "//" + url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _is_own(name: str, channel: str | None, own_domains) -> bool:
    low = (name or "").lower()
    if any(d and d.lower() in low for d in own_domains or ()):
        return True
    a = _alnum(name)
    return bool(a) and any(t in a for t in _channel_tokens(channel))


# -------------------------------------------------------------------------
# Text scanning
# -------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sponsor_name(sent: str) -> str | None:
    for rx in _SPONSOR_NAMES:
        m = rx.search(sent)
        if m:
            name = m.group(1).strip().rstrip(".,!;:'’")
            if name.lower() not in PRONOUNS and name.lower() not in GENERIC:
                return name
    return None


def _pronoun_object(sent: str, end: int) -> str | None:
    m = re.match(r"\s*([A-Za-z]+)", sent[end:])
    return m.group(1).lower() if m and m.group(1).lower() in PRONOUNS else None


def _denials(text: str) -> list[tuple[int, int]]:
    """Spans of sponsor verbs that are negated ("never sponsored me", "did not sponsor")."""
    return [(m.start(), m.end()) for m in _SPONSOR_VERB.finditer(text or "") if negated(text, m.start(), m.end())]


def _service_in(text: str) -> str | None:
    m = _SERVICE_RE.search(text or "")
    if m:
        return m.group(1)
    m = _SERVICE_WORDS.search(text or "")
    return m.group(1) if m else None


def scan_text(text: str, source: str, link: str | None = None, subject: str = "", *,
              channel: str | None = None, own_domains=(), context: str = "") -> list[dict]:
    """Find disclosure signals in `text` (see the module docstring)."""
    hits: list[dict] = []
    toks = brand_tokens(subject)
    sents = [s for s in _SENT_SPLIT.split(text or "") if s.strip()]
    ctx_denial = bool(_denials(context)) or bool(_NOT_SPONSORED.search(context or ""))

    def add(signal, sent, **extra):
        hits.append({"signal": signal, "weight": WEIGHTS[signal], "source": source, "text": sent.strip()[:220],
                     **({"link": link} if link else {}), **extra})

    for i, sent in enumerate(sents):
        near = " ".join(sents[max(0, i - 1):i + 2]) + " " + (context or "")
        denial_spans = _denials(sent)
        denied = bool(denial_spans) or bool(_NOT_SPONSORED.search(sent))
        if denied:
            add("claims_not_sponsored", sent)

        # Sponsor reads (spoken or written), with the sponsor's name when given.
        for m in _SPONSOR_READ.finditer(sent):
            if any(a <= m.end() and m.start() <= b for a, b in denial_spans) or negated(sent, m.start(), m.end()):
                if not denied:
                    add("claims_not_sponsored", sent)
                    denied = True
                break
            obj = _pronoun_object(sent, m.end())
            if obj in ("me", "myself", "anyone", "anybody", "nobody", "any"):
                break  # "you're sponsored by me": a quote or a joke, not a read
            if obj and (denied or ctx_denial or bool(_denials(near))):
                break  # "sponsored by them" next to a denial
            name = _sponsor_name(sent)
            if name and _is_own(name, channel, own_domains):
                add("self_promotion", sent, sponsor=name)
            elif name and (_is_third_party(name, subject) or _SERVICE_RE.fullmatch(name)):
                add("third_party_sponsor", sent, sponsor=name)
            else:
                add("sponsor_read", sent, sponsor=name)
            break

        # Review units, with context: replacements, files and rentals don't count.
        # The exclusion checks the neighboring sentences too: "it died under warranty. They sent me a new one."
        if not _REVIEW_EXCLUDE.search(near) and not _SENT_ME_OBJECT_EXCLUDE.search(sent):
            for rx in _REVIEW_UNIT_RE:
                m = rx.search(sent)
                if m and not negated(sent, m.start(), m.end()):
                    add("review_unit", sent)
                    break
        if _RENTAL.search(sent):
            add("rented_or_borrowed", sent)

        # Discount codes and links: a code for an unrelated service (music library, VPN, software)
        # is a third-party sponsorship, not the reviewed brand's code.
        if _AD_TAG.search(sent):
            add("ad_tag", sent)
        m = _CODE_RE.search(sent)
        if m and _AFFILIATE_DISCLOSURE.search(sent) and not re.search(r"\bcode\b", sent, re.I):
            m = None  # "I earn a commission if you buy through my links" is the disclosure, not a pitch
        if m:
            service = _service_in(sent) or (None if _about(sent, toks) else _service_in(near))
            if service and not _about(service, toks):
                add("third_party_sponsor", sent, sponsor=service, via="code")
            else:
                add("use_my_link_or_code", sent)
        if _AFFILIATE_DISCLOSURE.search(sent):
            add("affiliate_disclosure", sent)
        m = _GIVEAWAY.search(sent)
        if m and not negated(sent, m.start(), m.end()) and not _GIVEAWAY_TELLTALE.search(sent) \
                and (not re.fullmatch(r"give-?away", m.group(1), re.I) or _GIVEAWAY_CONTEXT.search(sent)):
            add("giveaway", sent)

        # "I bought it myself" must be about the product: a pronoun object, or a brand/product token nearby.
        strong = _BOUGHT_STRONG.search(sent)
        m = strong or _BOUGHT_WEAK.search(sent)
        if m and not negated(sent, m.start(), m.end()):
            rest = sent[:m.start()] + " " + sent[m.end():]
            if toks and _about(rest, toks):
                add("bought_it_myself", sent)
            elif strong and re.search(r"myself|my own money|pocket|retail|full price", sent, re.I):
                add("bought_it_myself", sent)
            elif strong and toks and _about(near, toks):
                add("bought_it_myself", sent)
        if _EDITORIAL.search(sent):
            add("editorial_independence", sent)
    return hits


def dedupe_hits(hits: list[dict]) -> list[dict]:
    """Drop repeated hits (same signal, source and text), keeping order."""
    seen, out = set(), []
    for h in hits:
        k = (h.get("signal"), h.get("source"), h.get("text"))
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


def scan_segments(segments: list[dict], subject: str = "", *, channel: str | None = None, own_domains=(),
                  source_fmt: str = "transcript {at}") -> list[dict]:
    """scan_text over transcript segments ({text, at, link}); each segment sees its neighbors as context
    (a denial in the next segment cancels "sponsored by them"). Duplicate segments are scanned once."""
    uniq, keys = [], set()
    for sg in segments or []:
        k = (sg.get("sec", sg.get("at")), sg.get("text"))
        if k not in keys:
            keys.add(k)
            uniq.append(sg)
    hits = []
    for i, sg in enumerate(uniq):
        ctx = " ".join(s.get("text", "") for s in uniq[max(0, i - 1):i] + uniq[i + 1:i + 2])
        hits += scan_text(sg.get("text", ""), source_fmt.format(**{"at": sg.get("at", "?"), **sg}),
                          sg.get("link"), subject, channel=channel, own_domains=own_domains, context=ctx)
    return dedupe_hits(hits)


# -------------------------------------------------------------------------
# Links
# -------------------------------------------------------------------------

_AFF_LINK = re.compile(
    r"amzn\.to|link\.amazon/|amazon\.[a-z.]+/.*[?&](tag|linkCode)=|geni\.us|sjv\.io|\.pxf\.io|impact\.com|howl\.me|"
    r"shareasale|skimresources|go\.magik\.ly|7tiv\.net|avantlink|rstyle\.me|linksynergy|awin1\.com|tidd\.ly|prf\.hn|"
    r"anrdoezrs|dpbolvw|tkqlhce|jdoqocy|kqzyfj|bestbuy\.7tiv|bhpho\.to|/(affiliates?|aff|partners?/ref)/|"
    r"[?&](aff|affiliate|aff_id|affid|ref_id|associate|sca_ref|rfsn|referral|refer|irclickid|ref|rf|"
    r"partner|partner_id|ranMID|ranEAID|clickid|subid|discount)=", re.I)
# A retailer or brand link carrying a referral/code parameter is a third-party affiliate link even on a
# "my favorite products:" line: the creator earns from it, but it isn't their own store.
_REF_PARAM = re.compile(r"[?&](ref|rf|aff|affiliate|aff_id|affid|ref_id|sca_ref|rfsn|referral|refer|partner|code|"
                        r"discount|coupon|irclickid|clickid)=", re.I)
# Brand short-link domains ("ankerfast.club", "go.ecoflow.com", "bluetti.link/x"): a vanity host on a
# short-link style TLD or subdomain, with a path.
_VANITY_SHORT = re.compile(r"//(?:(?:go|get|try|link|links|click|shop|buy)\.[a-z0-9-]+\.[a-z.]+|"
                           r"[a-z0-9-]+\.(?:club|link|links|page|to|ly|gg|me|deals|promo|codes?|store))/[^\s]+", re.I)
_SHORT_LINK = re.compile(r"//(bit\.ly|tinyurl\.com|t\.co|ow\.ly|rebrand\.ly|lnk\.to|linktr\.ee|da\.gd|is\.gd|cutt\.ly|"
                         r"shorturl\.at|buff\.ly|tiny\.cc)/", re.I)
_UTM = re.compile(r"[?&]utm_(campaign|source|medium)=([^&#]*)", re.I)
_UTM_STRICT = re.compile(r"kol|influencer|creator|sponsor|partner|launch|ambassador|paid|affiliate", re.I)
_CREATOR_HOSTS = re.compile(r"(^|\.)(patreon\.com|ko-fi\.com|buymeacoffee\.com|gumroad\.com|teespring\.com|"
                            r"fourthwall\.com|spreadshirt\.[a-z.]+|spring\.com|bonfire\.com|merchbar\.com)$", re.I)
_MY_LINE = re.compile(r"\b(my|our) (own )?(store|shop|site|website|merch|kits?|course|courses|patreon|presets|luts|"
                      r"book|newsletter|podcast|blog|products?|designs?|models|files|discord)\b", re.I)


def classify_links(links: list[str], *, channel: str | None = None, description: str = "",
                   handle: str | None = None) -> dict:
    """Sort description links into affiliate, campaign (utm), short and self-promotion links.
    Links to the channel's own site or store (by host name, creator platforms, or a "my store:" line)
    are self-promotion, never sponsorship, even with utm tags."""
    ch_toks = _channel_tokens(channel) | ({_alnum(handle)} if handle and len(_alnum(handle)) >= 4 else set())
    full = _alnum(_CHANNEL_FILLER.sub(" ", channel or ""))
    if len(full) >= 4:
        ch_toks.add(full)
    my_lines = [ln for ln in (description or "").splitlines() if _MY_LINE.search(ln)]
    out = {"affiliate_links": [], "campaign_links": [], "short_links": [], "self_promotion_links": [],
           "own_domains": []}
    for url in links or []:
        host = _host(url)
        stem = _alnum(host.rsplit(".", 1)[0]) if host else ""
        # The channel's own site: its host starts with a channel-name token ("vacuumwars.com" for Vacuum
        # Wars), not merely contains one ("shopsolarkits.com" isn't a "Solar ..." channel's store). On a
        # "my favorite products:" line, a link with a referral/code parameter is someone else's store paying
        # the creator: affiliate, not own.
        bare = re.sub(r"^(?:shop|store|get|go|the|my|try|buy)(?=[a-z0-9]{4})", "", stem)
        whole = {t for t in (full, _alnum(handle or "")) if len(t) >= 4}
        by_name = (stem in ch_toks or bare in ch_toks or any(stem.startswith(w) or bare.startswith(w) for w in whole)
                   or any(stem.startswith(t) for t in ch_toks if len(t) >= 6) or bool(_CREATOR_HOSTS.search(host)))
        # "?ref=TheSolarLab": the creator is the referrer, so the store is someone else's.
        refs = {_alnum(v) for k, vs in parse_qs(urlparse(url).query).items() if _REF_PARAM.search("?" + k + "=")
                for v in vs}
        referrer = bool(refs & (whole | ch_toks))
        own = bool(host) and not referrer and (
            by_name or (any(url in ln for ln in my_lines) and not _REF_PARAM.search(url)))
        if own:
            out["self_promotion_links"].append(url)
            if host and host not in out["own_domains"] and not _CREATOR_HOSTS.search(host):
                out["own_domains"].append(host)
            continue
        if _AFF_LINK.search(url):
            out["affiliate_links"].append(url)
        if _UTM.search(url):
            out["campaign_links"].append(url)
        if _SHORT_LINK.search(url) or (_VANITY_SHORT.search(url) and not _AFF_LINK.search(url)):
            out["short_links"].append(url)
            if _VANITY_SHORT.search(url) and (_REF_PARAM.search(url) or re.search(r"/[\w.-]{2,}$", url)) \
                    and url not in out["affiliate_links"]:
                out["affiliate_links"].append(url)  # brand short link with a code/ref path
    return out


def merge_link_classes(disclosure: dict, classified: dict) -> dict:
    """Union a classify_links() result into a page `disclosure` dict (e.g. from video.js), moving
    self-promotion links out of the affiliate and campaign lists."""
    d = dict(disclosure or {})
    own = set(classified.get("self_promotion_links", []))
    for k in ("affiliate_links", "campaign_links", "short_links"):
        d[k] = [u for u in dict.fromkeys(list(d.get(k) or []) + classified.get(k, [])) if u not in own]
    d["self_promotion_links"] = classified.get("self_promotion_links", [])
    d["own_domains"] = classified.get("own_domains", [])
    return d


def link_hits(classified: dict) -> list[dict]:
    """Hits for classified links: affiliate_links, campaign_tracking (stronger when influencer-tagged)
    and self_promotion."""
    hits = []
    if classified.get("affiliate_links"):
        hits.append({"signal": "affiliate_links", "weight": WEIGHTS["affiliate_links"], "source": "description",
                     "text": ", ".join(classified["affiliate_links"][:5])})
    camp = classified.get("campaign_links") or []
    if camp:
        strict = [u for u in camp if any(_UTM_STRICT.search(v) for _, v in _UTM.findall(u))]
        hits.append({"signal": "campaign_tracking", "weight": 0.2 if strict else 0.1, "source": "description",
                     "text": ", ".join((strict or camp)[:3])})
    if classified.get("self_promotion_links"):
        hits.append({"signal": "self_promotion", "weight": WEIGHTS["self_promotion"], "source": "description",
                     "text": ", ".join(classified["self_promotion_links"][:3])})
    return hits


# -------------------------------------------------------------------------
# Brand ownership and brand-aware weighting
# -------------------------------------------------------------------------

_MODELISH = re.compile(r"^(?=.*\d)[a-z0-9+]{1,8}$|^(pro|max|mini|ultra|plus|lite|air|neo|se|x|s|go|one|combo)$", re.I)


def brand_owned(channel: str | None, title: str | None, *, handle: str | None = None, verified: bool = False,
                brand_domains=()) -> dict | None:
    """Is the channel the brand in the video's title? Based only on the channel name/handle against the
    title's brand (a channel name followed by a model, e.g. "Bambu Lab H2D"), never on keywords or shop
    links. "strong" when the handle matches a brand domain or the caller verified it; "possible" otherwise."""
    name = _CHANNEL_FILLER.sub(" ", channel or "")
    ch_words = re.findall(r"[a-z0-9]+", name.lower())
    ch = "".join(ch_words)
    if len(ch) < 3:
        return None
    words = re.findall(r"[A-Za-z0-9+]+", title or "")
    low = [w.lower() for w in words]
    found = False
    for i in range(len(low)):
        for j in range(i + 1, min(len(low), i + 4) + 1):
            if "".join(low[i:j]) == ch:
                nxt = words[j] if j < len(words) else ""
                if (nxt and _MODELISH.match(nxt)) or re.search(r"\bofficial\b", channel or "", re.I):
                    found = True
    if not found:
        return None
    h = _alnum((handle or "").lstrip("@").replace("official", ""))
    stems = {_alnum(_host(d).rsplit(".", 1)[0]) for d in brand_domains or () if _host(d)}
    strong = verified or bool(h and (h in stems or ch in stems))
    level = "strong" if strong else "possible"
    return {"signal": "brand_owned_channel", "weight": 1.0 if strong else 0.3, "source": "channel",
            "brand_owned": level,
            "text": (f"Channel '{channel}' appears to be the brand itself" if strong else
                     f"Channel '{channel}' matches the brand in the title; it may be the brand's own channel")}


def brand_adjust(hits: list[dict], subject: str, channel: str | None, *, title: str | None = None,
                 handle: str | None = None, verified: bool = False, brand_domains=()) -> list[dict]:
    """Make signals brand-aware: brand affiliate links, codes and giveaways weigh more; "I bought it
    myself" counts fully only when it is about the reviewed product; a brand-owned channel check
    (title-based, see brand_owned) is added when `channel` is given."""
    toks = brand_tokens(subject)
    out = []
    for h in hits:
        about_brand = _about(h.get("text") or "", toks)
        sig = h["signal"]
        if sig == "affiliate_links" and about_brand:
            h = {**h, "signal": "brand_affiliate_links", "weight": WEIGHTS["brand_affiliate_links"]}
        elif sig == "use_my_link_or_code" and about_brand:
            h = {**h, "signal": "brand_code_or_link", "weight": WEIGHTS["brand_code_or_link"]}
        elif sig == "giveaway" and about_brand:
            h = {**h, "signal": "brand_giveaway", "weight": WEIGHTS["brand_giveaway"]}
        elif sig == "campaign_tracking" and about_brand:
            h = {**h, "weight": max(h.get("weight", 0), 0.2)}
        elif sig == "bought_it_myself" and not about_brand and h.get("source") != "title":
            h = {**h, "signal": "bought_something_myself", "weight": WEIGHTS["bought_something_myself"]}
        out.append(h)
    if channel:
        bo = brand_owned(channel, title if title is not None else subject, handle=handle, verified=verified,
                         brand_domains=brand_domains)
        if bo:
            out.insert(0, bo)
    return out


def score(page: list[dict], extra: list[dict]) -> dict:
    """Combine evidence: each category counts once, with its first (strongest-source) hit kept."""
    total, kept, seen = 0.1, [], set()
    for e in page + extra:
        if e["signal"] in seen:
            continue
        seen.add(e["signal"])
        total += e["weight"]
        kept.append(e)
    total = round(min(1.0, max(0.0, total)), 2)
    res = {"score": total, "level": "high" if total >= 0.6 else "medium" if total >= 0.3 else "low",
           "evidence": kept, "note": GUIDE}
    bo = next((e.get("brand_owned") for e in kept if e.get("brand_owned")), None)
    if bo:
        res["brand_owned"] = bo
    return res
