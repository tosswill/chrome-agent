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
claims_not_sponsored (-0.1; also "we don't have affiliate links"), editorial_independence (-0.05),
ownership_tie (0.3: "owned by X, whose investors founded Y"). A sponsor read by a service in the video's
own category (a password manager sponsoring a password-manager review) is a sponsor_read with
in_category, not a third-party sponsor. Link signals:
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
# What each service sells: when the video is about that category (a password-manager review sponsored by
# 1Password), the read is brand money in the reviewed field, not an unrelated third-party sponsor.
SERVICE_CATEGORIES = {
    "password": ("1password", "dashlane", "bitwarden", "proton", "nordpass", "keeper", "roboform", "lastpass",
                 "enpass"),
    "vpn": ("nordvpn", "expressvpn", "surfshark", "private internet access", "proton"),
    "privacy": ("incogni", "aura", "proton"),
    "website": ("squarespace", "wix", "hostinger", "shopify"),
}
_CATEGORY_WORDS = {"password": r"password|passkey|credential|vault", "vpn": r"\bvpns?\b",
                   "privacy": r"data broker|identity theft|privacy", "website": r"website builder|web ?hosting"}


def in_category_service(name: str | None, subject: str) -> bool:
    """Whether service `name` sells what the video (`subject`) is about."""
    low = (name or "").lower()
    for cat, names in SERVICE_CATEGORIES.items():
        if any(n in low for n in names) and re.search(_CATEGORY_WORDS[cat], subject or "", re.I):
            return True
    return False
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
    r"(?:sponsoring|partnering with (?:me|us))|today'?s sponsor|this (?:video|episode)'?s sponsor|(?:the|our) sponsor (?:of|for) (?:this|today'?s) "
    r"(?:video|episode)|, (?:the|our|today'?s) sponsor\b|in (?:paid )?partnership with|paid partnership|#ad\b|"
    r"#sponsored|brought to you by)", re.I)
_SPONSOR_VERB = re.compile(r"\bsponsor(?:ed|ing|s)?\b|\bpa(?:id|ying) (?:me|us)\b", re.I)

_NAME = r"([A-Z0-9][\w&.+'’-]*(?:\s+[A-Z0-9][\w&.+-]*){0,2}|[a-z0-9-]+\.(?:com|net|org|io|co|tv|gg|app|shop|store)\b)"
_SPONSOR_NAMES = [
    re.compile(r"(?i:sponsored by|brought to you by|in (?:paid )?partnership with|today'?s sponsor(?: is)?|"
               r"this (?:video|episode)'?s sponsor(?: is)?)[\s:]+" + _NAME),
    # "that's where today's sponsor comes in, Monarch." / "I recommend today's sponsor, G2I, to ..."
    re.compile(r"(?i:today'?s sponsor|this (?:video|episode)'?s sponsor|our sponsor)(?i:\s+comes in|\s+here)?\s*[,:—–-]\s*"
               + _NAME),
    re.compile(r"(?i:thanks?(?: you)? to|shout ?out to)\s+" + _NAME + r"\s+(?i:for (?:partnering|sponsoring|supporting) "
               r"(?:with )?(?:me|us|this|today))"),
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
# Channel memberships and Patreon perks ("members get early access to videos") are not review units.
_MEMBER_PERK = re.compile(r"\b(members?(?:hip)?|patreon|patrons?|join (?:the|our|this) channel|supporters?|"
                          r"subscribers? get|tiers?|perks?)\b|\bearly access (?:to|for) (?:(?:new|our|my|the|all|"
                          r"every|ad-free)\s+)*(?:videos?|episodes?|content|uploads?|podcasts?)\b", re.I)
_SENT_ME_OBJECT_EXCLUDE = re.compile(
    r"\b(?:sent|send)\s+(?:me|us)\s+(?:this|these|the|a|an|their|its|one)\s+(?:\w+\s+)?(e-?mails?|messages?|links?|dms?|"
    r"texts?|notes?|invoices?|quotes?|photos?|pictures?|pics?|videos?|codes?|repl(?:y|ies)|updates?|new one|spares?|"
    r"parts?|surveys?|tips?|donations?|money|coffee)\b", re.I)

_CODE_RE = re.compile(
    r"\b(use (?:my|our) (?:link|code)|(?:using|through|with|via) (?:my|our) (?:links?|code)|use (?:the )?code \S+|(?:with|using) (?:the )?code \S+|code \S+ (?:at checkout|for \d+ ?%)|"
    r"discount code|promo code|coupon code|\d+ ?% off (?:with|using)|(?:buy|get|order|grab|pick up) (?:one|yours|it|them|"
    r"a pair)? ?(?:through|using|with|from|via) (?:my|the) (?:link|affiliate))", re.I)
_AFFILIATE_DISCLOSURE = re.compile(
    r"(\b(?:we|i)(?:'re|'m| are| am)\s+(?:an?\s+)?affiliate(?:d|s)?\b|\baffiliate links?\b|\(affiliate\)|\b(?:i|we) (?:may |might |do |will )?(?:earn|receive|get|make) (?:a )?"
    r"(?:small )?(?:commission|cut)|commission (?:from|on|if|when) |as an amazon associate|"
    r"\b(?:at|for) no (?:extra|additional) cost to you)", re.I)
# An ownership tie to a company in the field: "owned by Mediatech, whose investors are the founders of Nord
# Security", "our parent company", "we are part of X".
_OWNERSHIP = re.compile(
    r"\b(?:(?:is|are|we'?re|we are|site|channel|website)\s+(?:(?:now|also|fully|partly)\s+)?(?:owned|backed|funded|operated)\s+by|"
    r"whose (?:investors|owners|founders|shareholders)|(?:our|its|the) parent company|"
    r"(?:we|this channel|vpnpro) (?:is|are) (?:a )?part of (?:the )?[A-Z])", re.I)
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
    r"sponsored|wasn'?t (?:an? )?(?:sponsored|paid)|no sponsors?\b|non-?sponsored|(?:we|i) (?:never|don'?t|do not) (?:accept|take|do) (?:any )?(?:sponsorships?|sponsors?|paid (?:promotions?|reviews?)))", re.I)
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
    "ownership_tie": 0.3, "creator_product": 0.3, "gear_affiliate_links": 0.05,
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


# A product's own author or builder recommending it: "I'm the author of Bramble", "Disclosure: I make
# Downright", "I built markjason.sh", "the thing I'm building". Used by Reddit (builder_self_promotion)
# and YouTube (creator_product).
BUILDER_ROLE = r"(?:solo |indie |lead |main )?(?:author|creator|developer|dev|maker|founder|co-?founder|builder|maintainer)s?"
BUILT_THING = (r"(?:app|tool|cli|extension|service|product|project|manager|vault|thing|editor|plugin|client|program|"
                r"utility|theme|alternative|version|one)")
BUILDER = re.compile(
    r"\b(?:i['’]?m|i am|we['’]re|we are)\s+(?:the |one of the )" + BUILDER_ROLE + r"\b"
    r"|\b(?:i['’]?m|i am|we['’]re|we are)\s+(?:a |an )?" + BUILDER_ROLE + r"\s+(?:of|behind)\b"
    r"|\((?:i'?m|i am) the (?:author|dev|developer|creator|maker)\)"
    r"|\b(?:dis(?:closure|claimer)|full disclosure|fd)\s*[:,-]?\s*(?:it'?s (?:mine|my|our)|(?:i|we)(?:'m| am|'re| are)? "
    r"(?:the |a |an |on the team |one of the )?(?:dev|developer|make|made|built|build|building|author|creator|maker|"
    r"founder|co-?founder|maintain|maintainer|work (?:on|for|at)|wrote|develop))\b"
    r"|\b(?:i['’]?m|i am|we['’]re|we are) (?:on the team )?(?:building|developing|working on|making|maintaining) "
    r"(?:this|it|an? \w*\s*" + BUILT_THING + r"|my own|our own|(?-i:[A-Z][\w.-]+)|\w+ into)"
    r"|\b(?:i|we) (?:make|maintain|develop|built|made|created|wrote|developed|launched|programmed|coded|"
    r"vibe-?coded|shipped|published|released|open[- ]sourced) (?:(?:an?|my own|our own|this|a little|a small|a free|an open[- ]source)\s+(?:[\w-]+\s+){0,2}?"
    + BUILT_THING + r"|one myself|(?-i:[A-Z][a-z]+[A-Z]?\w*)(?:\.\w+)?\b(?! (?:a|the|my|it)\b)|[\w-]+\.(?:app|io|sh|dev))\b"
    r"|\bended up (?:building|making|writing|coding) (?:my|our) own\b"
    r"|\b(?:the|this) (?:thing|app|tool|system|product|plugin|course|template) (?:that )?(?:i|we)(?:'m| am|'re| are) "
    r"(?:building|making|developing|working on)\b"
    r"|\b(?:i|we) (?:also|actually) (?:built|made|developed|programmed|coded) (?:it|this|that)\b"
    r"|\b(?:i|we) (?:programmed|coded|vibe-?coded) (?:it|this|that)\b"
    r"|\bthe app (?:i|we)(?:'m| am|'re| are)? (?:maintain|build|develop|make|work on)(?:ing)?\b"
    r"|\((?:it'?s )?(?:my|our) (?:own )?(?:project|app|tool)\)|\bmine\W{0,4}\s*(?:i|we) (?:developed|built|made)"
    r"|\b(?:try|check) out my (?:own )?(?:app|tool|project|editor|extension)\b|\bwanna try (?:out )?my (?:app|tool)\b"
    r"|\b(?:dm|message|pm)\b[^.\n]{0,30}\bsend you an? (?:free )?(?:key|license|licen[cs]e|code|promo)"
    r"|\bfull disclosure\b[^.\n]{0,40}\b(?:i|we|my|our)\b|\bshameless (?:self[- ])?(?:plug|promo(?:tion)?)\b", re.I)
# The creator works for the company behind a product they discuss: "the app I work on", "we at Morgen".
_EMPLOYER = re.compile(r"\b(?:app|product|tool|company|startup|team|platform) (?:that )?(?:i|we) (?:work|worked) (?:on|for|at)\b|"
                       r"\bwe (?:here )?at (?-i:[A-Z][\w.]+)\b(?! (?:home|work|least|first|last|all|the)\b)|"
                       r"\bi (?:work|worked) (?:at|for) (?-i:[A-Z][\w.]+)|\b(?:my|our) (?:employer|company)(?:'s)? (?:app|product)\b", re.I)


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
            elif name and in_category_service(name, subject):
                add("sponsor_read", sent, sponsor=name, in_category=True)
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
                if m and "early access" in m.group(0).lower() and _MEMBER_PERK.search(sent):
                    continue
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
            if service and not _about(service, toks) and not in_category_service(service, subject):
                add("third_party_sponsor", sent, sponsor=service, via="code")
            else:
                add("use_my_link_or_code", sent)
        m = _AFFILIATE_DISCLOSURE.search(sent)
        if m and negated(sent, m.start(), m.end()) and not re.search(r"\b(?:earn|commission|cut)\b", m.group(0), re.I):
            # "We don't have any affiliate links": a no-money claim, not a disclosure.
            if not denied:
                add("claims_not_sponsored", sent)
                denied = True
        elif m:
            add("affiliate_disclosure", sent)
            if re.search(r"affiliate(?:d|s)?\b", m.group(0), re.I) and re.search(r"\b(?:we|i)", m.group(0), re.I):
                # "We are affiliated but not sponsored": the not-sponsored claim doesn't lower the score.
                hits[:] = [h for h in hits if not (h["signal"] == "claims_not_sponsored" and h["text"] == sent.strip()[:220])]
        if _OWNERSHIP.search(sent):
            add("ownership_tie", sent)
        m = BUILDER.search(sent) or _EMPLOYER.search(sent)
        if m and not negated(sent, m.start(), m.end()):
            add("creator_product", sent, why="employer" if _EMPLOYER.search(sent) else "builder")
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


def resolve_third_party(*lists: list[dict]) -> tuple[list[dict], ...]:
    """When a video names a third-party sponsor somewhere ("Thanks to Monarch for sponsoring"), the
    unnamed sponsor read in the same video ("that's where today's sponsor comes in") and codes or links
    naming that sponsor ("using my link monarchmoney...", "promo code JANE50" next to it) belong to
    that sponsor, not to a reviewed brand: they become `third_party_sponsor` (via "resolved"). Unnamed
    reads are only reassigned when no reviewed-brand read is named. Returns the lists, rewritten."""
    every = [h for lst in lists for h in lst]
    names = sorted({h["sponsor"] for h in every if h.get("signal") == "third_party_sponsor" and h.get("sponsor")})
    if not names:
        return lists
    brand_named = any(h.get("signal") == "sponsor_read" and h.get("sponsor") for h in every)
    stems = {n: _alnum(n) for n in names if len(_alnum(n)) >= 3}

    def named_in(text: str) -> str | None:
        a = _alnum(text)
        return next((n for n, st in stems.items() if st in a), None)

    out = []
    for lst in lists:
        new = []
        for h in lst:
            sig, txt = h.get("signal"), h.get("text") or ""
            who = named_in(txt)
            if sig == "sponsor_read" and not h.get("sponsor") and not h.get("in_category") and (who or not brand_named):
                h = {**h, "signal": "third_party_sponsor", "weight": WEIGHTS["third_party_sponsor"],
                     "sponsor": who or names[0], "via": "resolved"}
            elif sig in ("use_my_link_or_code", "brand_code_or_link") and (
                    who or (len(names) == 1 and not brand_named and not h.get("brand"))):
                h = {**h, "signal": "third_party_sponsor", "weight": WEIGHTS["third_party_sponsor"],
                     "sponsor": who or names[0], "via": "code"}
            new.append(h)
        out.append(dedupe_hits(new))
    return tuple(out)


def dedupe_hits(hits: list[dict]) -> list[dict]:
    """Drop repeated hits (same signal, source and text), keeping order."""
    seen, out = set(), []
    for h in hits:
        k = (h.get("signal"), h.get("source"), h.get("text"))
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out


# Auto-caption sound tags ("[music]", "[applause]"): not words anyone said (a sponsor named "music").
_CAPTION_TAG = re.compile(r"\[(?:music|applause|laughter|laughs|__|inaudible|silence|cheering|sound|noise)\]", re.I)


def scan_segments(segments: list[dict], subject: str = "", *, channel: str | None = None, own_domains=(),
                  source_fmt: str = "transcript {at}") -> list[dict]:
    """scan_text over transcript segments ({text, at, link}); each segment sees its neighbors as context
    (a denial in the next segment cancels "sponsored by them"). Duplicate segments are scanned once."""
    uniq, keys = [], set()
    segments = [{**sg, "text": _CAPTION_TAG.sub(" ", sg.get("text") or "")} for sg in segments or []]
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
    r"partnerlinks\.io|partnerstack|\.grsm\.io|go\.nordpass\.io|go\.nordvpn\.net|refer\.[a-z0-9-]+\.[a-z]+/|"
    r"\.sjv\.io|fpr\.(?:link|to)|\.trackable\.|/(?:ref|referral|invite)/[\w-]+|"
    r"[?&](aff|affiliate|aff_id|affid|ref_id|associate|sca_ref|rfsn|referral|refer|irclickid|ref|rf|"
    r"partner|partner_id|ranMID|ranEAID|clickid|subid|discount)=", re.I)
def _AMAZON_PLAIN(url: str) -> bool:
    """A plain Amazon product or share link ("?ref=ppx_pop_mob_ap_share"): Amazon's own ref codes,
    no associate tag. amzn.to and link.amazon short links stay affiliate links."""
    u = url.lower()
    return bool(re.search(r"//(?:www\.|smile\.)?amazon\.[a-z.]+/", u)) and not re.search(r"[?&](tag|linkcode|ascsubtag)=", u)


# A retailer or brand link carrying a referral/code parameter is a third-party affiliate link even on a
# "my favorite products:" line: the creator earns from it, but it isn't their own store.
_REF_PARAM = re.compile(r"[?&](ref|rf|aff|affiliate|aff_id|affid|ref_id|sca_ref|rfsn|referral|refer|partner|code|"
                        r"discount|coupon|irclickid|clickid)=", re.I)
# Brand short-link domains ("ankerfast.club", "go.ecoflow.com", "bluetti.link/x"): a vanity host on a
# short-link style TLD or subdomain, with a path.
_VANITY_SHORT = re.compile(r"//(?:(?:go|get|try|link|links|click|shop|buy)\.[a-z0-9-]+\.[a-z.]+|"
                           r"[a-z0-9-]+\.(?:club|link|links|page|to|ly|gg|deals|promo|codes?|store))/[^\s]+", re.I)  # not .me: proton.me
# Chat and community invites: never affiliate or brand short links ("discord.gg/abc", "t.me/x").
_INVITE = re.compile(r"//(?:www\.)?(?:discord\.gg|discord\.com/invite|t\.me|telegram\.me|chat\.whatsapp\.com|"
                     r"matrix\.to|join\.slack\.com|signal\.group)/", re.I)
_SHORT_LINK = re.compile(r"//(bit\.ly|tinyurl\.com|t\.co|ow\.ly|rebrand\.ly|lnk\.to|linktr\.ee|da\.gd|is\.gd|cutt\.ly|"
                         r"shorturl\.at|buff\.ly|tiny\.cc)/", re.I)
_UTM = re.compile(r"[?&](?:utm|mtm|pk)_(campaign|source|medium|kwd)=([^&#]*)", re.I)
_UTM_STRICT = re.compile(r"kol|influencer|creator|sponsor|partner|launch|ambassador|paid|affiliate", re.I)
_CREATOR_HOSTS = re.compile(r"(^|\.)(patreon\.com|ko-fi\.com|buymeacoffee\.com|gumroad\.com|teespring\.com|"
                            r"fourthwall\.com|spreadshirt\.[a-z.]+|spring\.com|bonfire\.com|merchbar\.com)$", re.I)
# The creator's own redirect domain or path sending viewers to a vendor: "vpnpro.sale/NordPass-Discount/...",
# "cyberlabgo.com/roboform-v2", "site.com/go/nordpass". These pay the creator: affiliate links, not self-promotion.
_REDIRECT_PATH = re.compile(r"/(?:go|out|visit|recommends?|refer|deal|deals|get|try|link|links|discount|coupon|offer)"
                            r"(?:/|$)|(?:discount|coupon|deal|offer|promo|-v\d+)(?:/|$|[-_])", re.I)
_REDIRECT_TLD = re.compile(r"\.(?:sale|deals|promo|coupons?|discount|offer|link|click|go)$", re.I)
_REDIRECT_SUFFIX = {"go", "links", "link", "deals", "deal", "sale", "offers", "recommends", "out", "get", "visit", "try"}


def _own_redirect(url: str, host: str, stem: str, ch_toks: set[str], brands=()) -> bool:
    path = urlparse(url if "//" in url else "//" + url).path or ""
    if len(path.strip("/")) < 2:
        return False
    if _REDIRECT_TLD.search(host) or _REDIRECT_PATH.search(path):
        return True
    if any(stem.startswith(t) and stem[len(t):] in _REDIRECT_SUFFIX for t in ch_toks if t):
        return True
    slug = _alnum(path)
    return any(len(_alnum(b)) >= 4 and _alnum(b) in slug for b in brands or ())


_MY_LINE = re.compile(r"\b(my|our) (own )?(store|shop|site|website|merch|kits?|course|courses|patreon|presets|luts|"
                      r"book|newsletter|podcast|blog|products?|designs?|models|files|discord|membership|members?|"
                      r"investigations|work|channel)\b|\bbecom(?:e|ing) a (?:channel )?member\b|\bjoin (?:the|our) "
                      r"(?:membership|members|community)\b|\bsupport (?:us|the channel|our work)\b", re.I)
# A vendor link carrying the creator's name as its path ("vpn.example/QuadQ", "vpn.example/jct" by initials) or an
# affiliate subdomain ("affiliate.ipvanish.com/SH18"): a referral that pays the creator.
_PLATFORM_HOST = re.compile(r"(^|\.)(youtube\.com|youtu\.be|instagram\.com|twitter\.com|x\.com|facebook\.com|fb\.me|"
                            r"tiktok\.com|twitch\.tv|threads\.net|bsky\.app|patreon\.com|ko-fi\.com|discord\.gg|"
                            r"github\.com|linktr\.ee|reddit\.com)$", re.I)
_AFF_SUBDOMAIN = re.compile(r"//(?:www\.)?(?:affiliates?|partners?|refer(?:ral)?|ref|go|get|try)\.[a-z0-9-]+\.[a-z.]+/[^\s]+",
                            re.I)


def classify_links(links: list[str], *, channel: str | None = None, description: str = "",
                   handle: str | None = None, brands=()) -> dict:
    """Sort description links into affiliate, campaign (utm), short and self-promotion links.
    Links to the channel's own site or store (by host name, creator platforms, or a "my store:" line)
    are self-promotion, never sponsorship, even with utm tags, except redirects through the creator's own
    domain to a vendor ("vpnpro.sale/NordPass-Discount/", "/go/<brand>", a path naming one of `brands`):
    those are affiliate links (listed in `own_redirect_links` too)."""
    ch_toks = _channel_tokens(channel) | ({_alnum(handle)} if handle and len(_alnum(handle)) >= 4 else set())
    full = _alnum(_CHANNEL_FILLER.sub(" ", channel or ""))
    if len(full) >= 4:
        ch_toks.add(full)
    my_lines = [ln for ln in (description or "").splitlines() if _MY_LINE.search(ln)]
    out = {"affiliate_links": [], "campaign_links": [], "short_links": [], "self_promotion_links": [],
           "own_domains": [], "own_redirect_links": []}
    for url in links or []:
        host = _host(url)
        stem = _alnum(host.rsplit(".", 1)[0]) if host else ""
        # The channel's own site: its host starts with a channel-name token ("vacuumwars.com" for Vacuum
        # Wars), not merely contains one ("shopsolarkits.com" isn't a "Solar ..." channel's store). On a
        # "my favorite products:" line, a link with a referral/code parameter is someone else's store paying
        # the creator: affiliate, not own.
        bare = re.sub(r"^(?:shop|store|get|go|the|my|try|buy)(?=[a-z0-9]{4})", "", stem)
        whole = {t for t in (full, _alnum(handle or "")) if len(t) >= 4}
        inits = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", _CHANNEL_FILLER.sub(" ", channel or ""))).lower()
        by_name = ((len(inits) >= 3 and stem.startswith(inits) and stem[len(inits):] in {"", "store", "shop", "merch", "labs"})
                   or stem in ch_toks or bare in ch_toks or any(stem.startswith(w) or bare.startswith(w) for w in whole)
                   or any(stem.startswith(t) for t in ch_toks if len(t) >= 6) or bool(_CREATOR_HOSTS.search(host)))
        # "?ref=TheSolarLab": the creator is the referrer, so the store is someone else's.
        refs = {_alnum(v) for k, vs in parse_qs(urlparse(url).query).items() if _REF_PARAM.search("?" + k + "=")
                for v in vs}
        referrer = bool(refs & (whole | ch_toks))
        own = bool(host) and not referrer and (
            by_name or (any(url in ln for ln in my_lines) and not _REF_PARAM.search(url)))
        if own and _SHORT_LINK.search(url) and not by_name:
            # "Support our investigations by becoming a member: bit.ly/x": the creator's own program
            out["self_promotion_links"].append(url)
            continue
        if own and _own_redirect(url, host, stem, ch_toks | whole, brands):
            out["affiliate_links"].append(url)
            out["own_redirect_links"].append(url)
            continue
        if own:
            out["self_promotion_links"].append(url)
            if host and host not in out["own_domains"] and not _CREATOR_HOSTS.search(host):
                out["own_domains"].append(host)
            continue
        if _INVITE.search(url):
            continue
        seg = _alnum((urlparse(url).path or "").strip("/").split("/")[0]) if host else ""
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", _CHANNEL_FILLER.sub(" ", channel or ""))).lower()
        named_path = bool(seg) and not _PLATFORM_HOST.search(host) and (
            seg in ch_toks or seg in whole or (len(initials) >= 3 and seg == initials))
        if (_AFF_LINK.search(url) and not _AMAZON_PLAIN(url)) or _AFF_SUBDOMAIN.search(url) or named_path:
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
