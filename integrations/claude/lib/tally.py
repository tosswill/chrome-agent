"""Shared tally engine for the research skills (Reddit, YouTube): who mentions which product, how
independently, and in what tone. Stdlib only, no browser; pure functions, unit-tested.

Records
-------
A record is one thing a window has seen, as a dict. Recognized fields (all optional except one of
`id`/`url`):

    id          stable item id: video id, Reddit post id, or comment permalink id (the merge key)
    url, link   where it lives (`link` wins for snippets, e.g. a comment permalink)
    via         "search" | "video" | "post" | "comment" | "transcript" | ...
    title, text the text to search (title, description/body/comment)
    links       list of URLs (searched too, e.g. description links)
    transcript  full transcript text, or `segments`: [{text, link, at}] (each scanned with its own link)
    author      Reddit username        channel   YouTube channel name   (the "group")
    thread      Reddit post id the item belongs to (comments), for thread counts and OP-once
    score       upvotes
    is_op       the item is by the thread's original poster
    brand       the author is a brand account (R5)
    sponsor_score   0.0-1.0 sponsor_bias score of the item (YouTube)
    brand_owned     True / "strong" (excluded) or "possible" (kept, flagged)
    listicle / likely_ai_listicle / amazon_influencer   flags (Y3/Y17), excluded from independent counts

API
---
    parse_aliases(spec) -> {name: [alias, ...]}
        "Core One=C1,Core 1,Core1; MK4S=MK 4S" (";" or newline between groups).
    build_terms(names, aliases=None, garbles=None) -> list[Term]
        One Term per name with its aliases and built-in caption-garble variants (GARBLES). Pass
        garbles={} to disable the built-in map, or a dict to replace it.
    find_mentions(text, terms) -> list[(term_name, start, end)]
        Whole-name, longest-match, non-overlapping matches. A name followed by a model suffix
        ("Core One+", "MK4S", "Centauri Carbon 2", "Q2C", "A1 mini") is not a match for the
        shorter name. Case- and accent-insensitive ("RØDE" = "Rode"); spaces/hyphens are flexible.
    snippet(text, start, end, width=90) -> str
    polarity(text) -> "recommend" | "complain" | "question" | "mention"
        Heuristic tone of the sentence around a mention.
    record_key(rec) -> str
    richness(rec) -> int
    merge_records(records) -> list[record]
        One record per id; the richer record wins field by field (a loaded video beats its search
        result for title, description, links and sponsor_score), missing fields are filled from the
        others, links and `sources` are unioned.
    upsert(seen, items, cap=1500) -> list[record]
        merge_records(seen + items), keeping the most recently seen `cap` records. Use it to store a
        window's `seen` list so a loaded video replaces its thin search record instead of being dropped.
    default_independence(rec, sponsor_threshold=0.6) -> str | None
        Why a record isn't independent: "brand_account", "brand_owned", "sponsored", "listicle", or None.
    tally(records, names, *, aliases=None, garbles=None, group="author", group_label="users",
          separate_search=True, op_once=True, exclude_op=False, include_transcripts=True,
          sponsor_threshold=0.6, independence=None, group_flags=None, max_examples=5) -> dict
        Reddit: tally(seen, names)  (users, OP-once, search titles kept apart)
        YouTube: tally(seen, names, group="channel", group_label="channels", separate_search=False)

tally() output, per term (sorted by independent groups, then recommend-weighted upvotes):

    term, aliases, items, threads, search_only_threads (when separate_search),
    implied (group="author" only): {threads, users, upvotes, examples} for top-level replies that read
        as a recommendation without naming the product, in threads whose post title names only this
        product (title-only endorsements such as "Love mine"); kept apart from the counts below,
    <group_label>, independent_<group_label>, raw_upvotes, recommend_upvotes, complain_upvotes,
    polarity {recommend, complain, question, mention, mixed}, excluded {reason: n},
    by_group [{<group>, items, independent, reasons, polarity, max_sponsor_score}],
    examples [{snippet, link, via, <group>, score, polarity, independent}]

Independence: a record is not independent when default_independence() (or the `independence`
callable you pass) gives a reason. For YouTube, sponsor scores carry over to the channel: a channel is
independent for a term only if none of its records mentioning the term is excluded, and channel-wide
flags (strong brand ownership, listicle) from any of its records, or from `group_flags`
({channel: {"listicle": True, ...}}), exclude it everywhere.

Polarity is read on the clause around each mention (cut at "but", "while", "whereas", ";" ...)
when that clause has a tone of its own, else on the whole sentence, so "Love my Bluetti, but the
EcoFlow died" counts as praise for one and a complaint about the other.

exclude_op=True drops the original poster's mentions entirely: they count toward no users, threads
or polarity (only `excluded.op`).

Upvotes: `raw_upvotes` sums the scores of independent records mentioning the term (each record once),
`recommend_upvotes`/`complain_upvotes` only those whose mention reads as a recommendation/complaint,
so a passing mention in a 326-upvote comment is not reported as an endorsement.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Common caption garbles and spellings per brand. Applied to any name containing the brand (as a
# whole word), and as aliases of the bare brand. Overridable (garbles={} disables it).
GARBLES: dict[str, list[str]] = {
    "Bambu Lab": ["Bumble Lab", "Bamboo Lab", "Bambu Labs", "Bamboo Labs", "Bumble Labs", "Bambu", "Bamboo"],
    "Bambu": ["Bamboo", "Bumble", "Bambu Lab", "Bambu Labs", "Bumble Lab", "Bamboo Lab", "Bamboo Labs"],
    "Qidi": ["Cheety", "Qidi Tech", "Chidi", "Kiddy", "Qd"],
    "Elegoo": ["Eligu", "Algo", "Elego", "Ellegoo", "Eleggo"],
    "Prusa": ["Pruza", "Prusa3D", "Prusa Research", "Prussa"],
    "Creality": ["Kreality", "Crealty"],
    "Anycubic": ["Any Cubic", "Anycubic"],
    "Snapmaker": ["Snap Maker"],
    "Hollyland": ["Holly Land", "Holy Land", "Holyland"],
    "Rode": ["RØDE", "Rhode"],
    "DJI": ["D.J.I.", "D J I"],
    "Sennheiser": ["Senheiser", "Sennhieser"],
    "Insta360": ["Insta 360", "Insta three sixty"],
    "Saramonic": ["Sara Monic"],
    "EcoFlow": ["Eccoflow", "Eco Flow", "Ecoflo", "Ecoflow"],
    "Synology": ["sonology", "Sinology", "Synalogy"],
    "Bluetti": ["blietti", "Bluety", "Blueti", "Bluetty"],
    "Jackery": ["Jackary", "Jakery"],
    "Bissell": ["Bissel", "Bisell"],
}

# Tokens that make a longer, different product name when they follow a name ("A1 mini", "Core One L").
_SUFFIX_WORDS = {"pro", "max", "plus", "mini", "ultra", "lite", "se", "air", "neo", "xl", "l", "s", "c", "k",
                 "x", "ii", "iii", "iv", "v", "2s", "3s", "4s", "gen", "mk2", "mk3", "mk4", "hs", "evo"}
# A number after a name is a model number unless a unit follows ("MK4 3 months", "P2S 2 years").
_UNIT_AFTER_NUMBER = re.compile(r"^\s*(?:years?|yrs?|months?|weeks?|days?|hours?|hrs?|times?|x\b|of\b|%|"
                                r"percent|kg|g\b|mm|cm|m\b|ft|feet|meters?|people|units?|prints?|of them)", re.I)

_FOLD = {"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ß": "ss", "ł": "l", "Ł": "l",
         "đ": "d", "Đ": "d", "ı": "i", "’": "'", "‘": "'", "–": "-", "—": "-"}


def _fold(text: str) -> tuple[str, list[int]]:
    """Lowercased, accent-free text, and for each output character its index in the input."""
    out, idx = [], []
    for i, ch in enumerate(text or ""):
        rep = _FOLD.get(ch)
        if rep is None:
            rep = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        for c in rep.lower():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _name_regex(name: str) -> str:
    folded, _ = _fold(name.strip())
    parts = [p for p in re.split(r"[\s\-_]+", folded) if p]
    # Inner separators are flexible: "Core One" = "Core-One" = "CoreOne"; "+" etc. stay literal.
    body = r"[\s\-_]*".join(re.escape(p) for p in parts)
    return rf"(?<![a-z0-9]){body}"


@dataclass
class Term:
    name: str
    variants: list[str] = field(default_factory=list)
    patterns: list[re.Pattern] = field(default_factory=list)
    near: list[str] = field(default_factory=list)  # context words required near a mention (--near)
    exact_case: set = field(default_factory=set)    # folded variants that must match the written case


# Product names that are also everyday words: their counts need a context check ("pass" the CLI vs.
# "pass the test", "OP" the poster vs. 1Password's `op`).
COMMON_WORDS = {"pass", "op", "keeper", "one", "core", "go", "air", "edge", "echo", "nest", "ring", "arc", "bolt",
                "note", "sense", "plus", "rest", "dot", "home", "mini", "max", "pro", "live", "surface", "spark",
                "ghost", "signal", "bear", "things", "notes", "box", "drive", "keys", "vault", "safe", "gate",
                "scratch", "paper", "nodes", "panda", "kate", "zed", "craft", "draft", "drafts", "ink", "write",
                "writer", "focus", "typewriter", "simple", "standard", "marker", "atom", "brackets", "mark", "notion",
                "bike", "day one", "agenda", "capacities", "reflect", "zettel"}


def ambiguous_variants(term: "Term") -> list[str]:
    """The term's name/aliases that are everyday words (and not written case-exact)."""
    return [v for v in term.variants if _fold(v)[0].strip() in COMMON_WORDS and _fold(v)[0] not in term.exact_case]


def _near_ok(text: str, start: int, end: int, words: list[str], window: int = 160) -> bool:
    """Whether one of `words` appears within `window` characters of text[start:end]."""
    ctx = _fold(text[max(0, start - window):start] + " " + text[end:end + window])[0]
    return any(re.search(rf"(?<![a-z0-9]){re.escape(_fold(w)[0].strip())}(?![a-z0-9])", ctx) for w in words if w.strip())


def parse_near(spec: str | dict | None) -> dict[str, list[str]]:
    """'pass=password-store,gopass,cli; op=cli,1password' -> {'pass': [...], 'op': [...]}."""
    return parse_aliases(spec)


def _repeat_promoters(main: list, thread_of, indep_fn, min_threads: int = 3) -> list[dict]:
    """Users who recommend the term in `min_threads`+ different threads: one enthusiast (or a seeded
    account) repeated across threads, not several independent owners. Reported, not excluded."""
    by: dict[str, set] = {}
    for r, pol, _ in main:
        a = r.get("author")
        if not a or r.get("via") == "search" or pol not in ("recommend", "mixed") or indep_fn(r):
            continue
        by.setdefault(a, set()).add(thread_of(r))
    return [{"author": a, "threads": len(t)} for a, t in sorted(by.items(), key=lambda kv: -len(kv[1]))
            if len(t) >= min_threads]


def parse_aliases(spec: str | dict | None) -> dict[str, list[str]]:
    """'Core One=C1,Core 1,Core1; MK4S=MK 4S' -> {'Core One': ['C1', 'Core 1', 'Core1'], 'MK4S': ['MK 4S']}.
    A dict is returned as is (values may be lists or comma strings)."""
    if not spec:
        return {}
    if isinstance(spec, dict):
        return {k: (v.split(",") if isinstance(v, str) else list(v)) for k, v in spec.items()}
    out: dict[str, list[str]] = {}
    for group in re.split(r"[;\n]+", spec):
        if "=" not in group:
            continue
        name, rest = group.split("=", 1)
        name = name.strip()
        if name:
            out.setdefault(name, []).extend(a.strip() for a in re.split(r"[,|]", rest) if a.strip())
    return out


def _garble_variants(name: str, garbles: dict[str, list[str]]) -> list[str]:
    out = []
    folded = _fold(name)[0]
    for brand in sorted(garbles, key=len, reverse=True):
        fb = _fold(brand)[0]
        m = re.search(rf"(?<![a-z0-9]){re.escape(fb)}(?![a-z0-9])", folded)
        if not m:
            continue
        for g in garbles[brand]:
            out.append(folded[:m.start()] + _fold(g)[0] + folded[m.end():])
        break  # replace the longest brand form only ("Bambu Lab" before "Bambu")
    return out


def build_terms(names: list[str], aliases: dict | str | None = None,
                garbles: dict[str, list[str]] | None = None, near: dict | str | None = None) -> list[Term]:
    """Terms for `names`, each with its aliases and garble variants (see GARBLES). `near` maps a name to
    context words, one of which must appear near each mention. Names or aliases of 1-3 letters written in
    lowercase match lowercase only ("op" is not "OP"); written in capitals they match any case."""
    amap = parse_aliases(aliases)
    nmap = parse_near(near)
    gmap = GARBLES if garbles is None else garbles
    terms = []
    for name in names:
        variants = [name] + [a for a in amap.get(name, [])]
        for v in list(variants):
            variants += _garble_variants(v, gmap)
        seen, uniq = set(), []
        for v in variants:
            k = _fold(v)[0].strip()
            if k and k not in seen:
                seen.add(k)
                uniq.append(v)
        exact = {_fold(v)[0].strip() for v in uniq if re.fullmatch(r"[a-z]{1,3}", v.strip())}
        terms.append(Term(name=name, variants=uniq, patterns=[re.compile(_name_regex(v)) for v in uniq],
                          near=[w.strip() for w in nmap.get(name, []) if w.strip()], exact_case=exact))
    return terms


def _right_boundary_ok(folded: str, end: int) -> bool:
    """Whether a match ending at `end` is the whole name, not the start of a longer model name."""
    if end < len(folded) and (folded[end].isalnum() or folded[end] == "+"):
        return False  # "MK4S", "Q2C", "Core One+"
    rest = folded[end:]
    m = re.match(r"[\s\-_]+([a-z0-9+]+)", rest)
    if not m:
        return True
    nxt = m.group(1)
    if nxt.startswith("+"):
        return False
    if nxt in _SUFFIX_WORDS:
        return False
    if re.fullmatch(r"\d{1,2}[a-z]?", nxt):
        return bool(_UNIT_AFTER_NUMBER.match(rest[m.end():]))  # "Centauri Carbon 2" vs "MK4 3 months"
    return True


# Brands whose model names collide across makers (SmartWash: Hoover vs Vax; Revolution; Pro).
BRAND_WORDS = {"hoover", "vax", "bissell", "shark", "ninja", "dyson", "tineco", "kenmore", "karcher", "dreame",
               "roborock", "ecovacs", "eufy", "miele", "sebo", "rug", "ecoflow", "bluetti", "jackery", "anker",
               "dji", "rode", "hollyland", "sony", "canon", "nikon", "bambu", "prusa", "creality", "elegoo", "qidi",
               "hatch", "yogasleep", "lectrofan", "snooz", "dreamegg", "momcozy"}


def _other_brand_before(folded: str, start: int, term: "Term") -> bool:
    """True when the word just before a match is a different brand than the term's own ("Vax SmartWash"
    for "Hoover SmartWash" via a bare "SmartWash" alias)."""
    own = set(re.findall(r"[a-z0-9]+", _fold(term.name)[0]))
    if not own & BRAND_WORDS:
        return False  # "Power Scrub", "Big Green": no brand of its own, so any brand may precede it
    prev = re.findall(r"[a-z0-9]+", folded[max(0, start - 20):start])
    return bool(prev) and prev[-1] in BRAND_WORDS and prev[-1] not in own


def find_mentions(text: str, terms: list[Term]) -> list[tuple[str, int, int]]:
    """Whole-name, longest-match, non-overlapping mentions: [(term_name, start, end)] in `text` indices."""
    folded, idx = _fold(text or "")
    cands = []
    for t in terms:
        for v, p in zip(t.variants, t.patterns):
            exact = _fold(v)[0].strip() in t.exact_case
            for m in p.finditer(folded):
                if exact and text[idx[m.start()]:idx[m.end() - 1] + 1] != v.strip():
                    continue  # "op" written lowercase: "OP" is the original poster, not the CLI
                if _right_boundary_ok(folded, m.end()) and not _other_brand_before(folded, m.start(), t):
                    cands.append((m.start(), m.end(), t.name))
    chosen: list[tuple[int, int, str]] = []
    for c in sorted(cands, key=lambda c: (-(c[1] - c[0]), c[0])):  # longest first among overlaps
        if all(c[1] <= o[0] or c[0] >= o[1] for o in chosen):
            chosen.append(c)
    out = []
    for s, e, name in sorted(chosen):
        out.append((name, idx[s], idx[e - 1] + 1))
    return out


def snippet(text: str, start: int, end: int, width: int = 90) -> str:
    """About `width` characters of context on each side of text[start:end], cut at word boundaries."""
    a, b = max(0, start - width), min(len(text), end + width)
    while a > 0 and not text[a - 1].isspace() and start - a < width + 15:
        a -= 1
    while b < len(text) and not text[b].isspace() and b - end < width + 15:
        b += 1
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


# -------------------------------------------------------------------------
# Polarity
# -------------------------------------------------------------------------

_Q_START = re.compile(r"^\s*(should i|is (?:the|it|this)|are (?:the|they)|which|anyone|has anyone|does anyone|"
                      r"did anyone|how (?:is|are|does|do)|what about|thoughts on|worth (?:it|getting)|can (?:the|i)|"
                      r"would you|help me|looking for)\b", re.I)
_REC_NEG = re.compile(r"\b(no (?:issues?|problems?|complaints?|regrets?)|zero (?:issues?|problems?|complaints?)|"
                      r"never (?:had|have) (?:an? )?(?:issues?|problems?)|haven'?t had (?:any|a) (?:issues?|problems?)|"
                      r"can'?t go wrong|couldn'?t be happier|not a single (?:issue|problem)|won'?t regret|"
                      r"(?:never|haven'?t|didn'?t|don'?t) regrett?(?:ed)?|zero regrets?|no regrets?|"
                      r"can'?t recommend (?:it |them |this |\w+ )?(?:enough|highly enough)|"
                      # "dead easy", "dead simple": praise, not "died"
                      r"dead (?:easy|simple|quiet|silent|reliable|on|set on))\b", re.I)
_COMP_NEG = re.compile(r"\b(not worth|(?:don'?t|do not|never|wouldn'?t|would not|won'?t|shouldn'?t) (?:get|buy|go with|bother with|use|touch)|wouldn'?t recommend|would not recommend|don'?t recommend|do not recommend|"
                       r"can'?t recommend|cannot recommend|don'?t buy|do not buy|stay away|not reliable|doesn'?t work|"
                       r"does not work|didn'?t work|stopped working|not happy|wasn'?t happy|not impressed|"
                       r"(?:not|isn'?t|wasn'?t|aren'?t) (?:that |very |so |really |super |too )?(?:great|good|"
                       r"impressive|strong|powerful|effective|reliable|worth it))\b", re.I)
_REC = re.compile(r"\b(recommend(?:ed)?|love (?:it|mine|my|the)|loving|great|excellent|amazing|awesome|fantastic|"
                  r"best|happy with|worth it|go with|get the|buy the|solid|reliable|rock solid|works (?:great|well|"
                  r"perfectly)|perfect|favou?rite|my pick|top pick|highly|impressed|winner|no brainer|beast|"
                  r"is the answer|gold standard|swear by|hands down|best (?:option|choice|solution|one|thing) (?:i'?ve|i have|we'?ve) "
                  r"(?:found|used|tried|seen)|by far|going strong|still going|life ?saver|"
                  r"game ?changer|can'?t live without|worth every penny|must[- ]have|holy grail|saved our|"
                  r"(?:we|i) use (?:the|a|our|my)|we have (?:the|a|two|three)|treating (?:me|us) (?:well|great|good|"
                  r"right)|been (?:great|fantastic|flawless|rock solid|solid)|never let (?:me|us) down|"
                  r"(?:i|we)(?:'ve| have) been using (?:it|them|\w+) (?:for|since) (?:years|ages|a decade|\d+)|"
                  r"(?:still|is|the) king|just what (?:i|we) (?:need|needed|wanted)|my (?:fav|fave|go-?to|daily driver)|"
                  r"(?:i|we) (?:really |still |thoroughly )?(?:enjoy|like|adore) (?:using|it|this|that|\w+ (?:a lot|so much))|"
                  r"is so (?:fast|good|nice|clean|snappy|smooth|light)|can'?t recommend (?:it |\w+ )?enough|"
                  r"(?:just |i'?d |i would |you should |should )?(?:get|grab|buy) (?:an?|yourself an?)|go (?:with|for) (?:an?|the)|"
                  r"use (?:just )?(?:that|it|one)|(?:i'?d|i would) (?:go|pick|choose)|my vote|vote for)\b", re.I)
_COMP = re.compile(r"\b(problems?|issues?|broke|broken|fail(?:s|ed|ing|ure)?|clog(?:s|ged|ging)?|jam(?:s|med)?|"
                   r"return(?:ed|ing)?|refund|regret|avoid|terrible|awful|worst|garbage|junk|disappoint(?:ed|ing)?|"
                   r"defect(?:ive)?|doa|hate|limitation|unreliable|poor|sucks?|annoying|warranty|rma|nightmare|"
                   r"overpriced|flaky|bug(?:s|gy)?|noisy|hiss|dropouts?|cutting out|died|dead|leak(?:s|ed|ing|y)?|"
                   r"crap|crappy|piece of (?:junk|crap|shit)|mess|bloat(?:ed|ware)?|ad[- ]?(?:bloated|infested|riddled|filled)|"
                   r"ads? everywhere|full of ads|spy(?:ing|ware)?|enshittifi\w*|unusable|laggy|sluggish)\b", re.I)


def polarity(text: str) -> str:
    """Heuristic tone of a sentence: "question", "recommend", "complain", or "mention"."""
    t = text or ""
    if t.rstrip().endswith("?") or _Q_START.search(t):
        return "question"
    rec = len(_REC_NEG.findall(t))
    t2 = _REC_NEG.sub(" ", t)  # "can't recommend it enough" is praise, not "can't recommend"
    comp = len(_COMP_NEG.findall(t2))
    rest = _COMP_NEG.sub(" ", t2)
    rec += len(_REC.findall(rest)) + (1 if _POINT_TO.search(t) else 0)
    comp += len(_COMP.findall(rest))
    if rec > comp:
        return "recommend"
    if comp > rec:
        return "complain"
    return "mention"


def _sentence_around(text: str, start: int, end: int) -> str:
    a = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    bs = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    b = min(bs) + 1 if bs else len(text)
    return text[a:b]


# Contrast breaks inside a sentence: "Love my Bluetti, but the EcoFlow died" is praise for one and a
# complaint about the other.
_CONTRAST = re.compile(r"[;]|,?\s+\b(?:but|however|whereas|while|although|though|unlike|compared to|"
                       r"except|meanwhile)\b|,\s+(?:and\s+)?(?:the|my|our|their)\s+|"
                       r",\s+(?=(?:instead\s+|rather\s+)?(?:look at|check out|consider|go for|try)\b)", re.I)
# A clause that starts by pointing elsewhere ("..., look at UGreen") recommends what it names.
_POINT_TO = re.compile(r"^\W*(?:instead\s+|rather\s+)?(?:look at|check out|consider|go for|try)\s+(?:the\s+)?(?-i:[A-Z])", re.I)


_UNFAMILIAR = re.compile(r"\b(?:not (?:aware of|familiar with)|never (?:used|tried|heard of)|haven'?t (?:used|tried)|"
                         r"don'?t know|no experience with|can'?t speak (?:to|for))\b", re.I)


def _clause_around(text: str, start: int, end: int, others=()) -> str:
    """The part of the sentence around a mention, cut at contrast words, when that part has a tone of
    its own; otherwise the whole sentence (so "It broke twice. Avoid the X" still reads as a complaint).
    The clause stays neutral when it says the writer doesn't know the product ("I'm not aware of
    Typora, but I use MacDown and recommend it") or when the toned rest of the sentence names another
    product (`others`: (start, end) spans of other mentions in `text`)."""
    sent_start = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    sent = _sentence_around(text, start, end)
    s, e = start - sent_start, end - sent_start
    cuts = [0] + [m.start() for m in _CONTRAST.finditer(sent)] + [len(sent)]
    a = max(c for c in cuts if c <= s)
    b = min(c for c in cuts if c >= e)
    clause = sent[a:b]
    if len(cuts) > 2 and clause.strip():
        if polarity(clause) != "mention" or _UNFAMILIAR.search(clause):
            return clause
        if any(sent_start <= os_ < sent_start + len(sent) and not (a <= os_ - sent_start < b) for os_, _ in others):
            return clause
    return sent


_ANSWER = re.compile(r"^\W*(?:for me,?\s*(?:it'?s|it is)?|(?:i|we) (?:use|went with|chose|picked|switched to|moved to)|"
                     r"(?:it'?s|go with|try|get)|(?:\+1|this|same)\W*(?:for)?)?\s*$", re.I)


def _short_answer_polarity(text: str, start: int, end: int) -> str | None:
    """A short answer naming the product ("For me, it's RoboForm." / "RoboForm.") followed by praise or
    a complaint in the next sentence: the next sentence carries the tone."""
    s0 = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    s1 = min(ends) if ends else len(text)
    before, after = text[s0:start], text[end:s1]
    if not _ANSWER.match(before) or re.search(r"\w{3,}", after) or s1 >= len(text):
        return None
    nxt = _sentence_around(text, s1 + 1, s1 + 1)
    p = polarity(nxt)
    return p if p in ("recommend", "complain") else None


# Post titles that ask for a pick: a short top-level reply naming a product there is a suggestion.
_ASK_TITLE = re.compile(r"\?\s*$|\b(?:best|which|recommend\w*|suggest\w*|alternatives?|looking for|options?|"
                        r"what (?:should|would|do|to|is the best|are (?:you|the best|good))|"
                        r"help (?:me )?(?:choose|pick|decide)|should i (?:get|buy|use|go)|instead of|replace(?:ment)?)\b", re.I)


def _bare_suggestion(rec: dict, text: str, start: int, ask_threads: set) -> bool:
    """A short top-level reply to a "what should I use?" post whose first sentence names the product with
    no tone words ("RaspberryPi with Kodi", "Apple TV, and never connect the TV"): it answers the question,
    so it counts as a recommendation."""
    if rec.get("via") != "comment" or rec.get("is_op") or (rec.get("depth") or 0) != 0:
        return False
    if str(rec.get("thread")) not in ask_threads:
        return False
    if len(re.findall(r"\w+", text)) > 20 or _UNFAMILIAR.search(text) or polarity(text) in ("question", "complain"):
        return False
    first_end = min([i for i in (text.find(c) for c in ".!?\n") if i != -1] or [len(text)])
    # The answer leads: the name within the first three words of a short first sentence (not "I designed
    # these parts on my X", a passing mention in an ask thread).
    return (start <= first_end and len(re.findall(r"\w+", text[:first_end])) <= 8
            and len(re.findall(r"\w+", text[:start])) <= 2)


# -------------------------------------------------------------------------
# Records
# -------------------------------------------------------------------------

def record_key(rec: dict) -> str:
    return str(rec.get("id") or rec.get("url") or rec.get("link") or id(rec))


_VIA_RANK = {"video": 4, "post": 4, "comment": 4, "transcript": 3, "channel": 2, "search": 1}


def richness(rec: dict) -> int:
    """How much a record knows: loaded pages beat search results; text, links, scores add."""
    r = _VIA_RANK.get(rec.get("via") or "", 2) * 100
    r += min(len(rec.get("text") or ""), 5000) // 50
    r += 10 * len(rec.get("links") or [])
    r += 30 * (rec.get("sponsor_score") is not None) + 20 * bool(rec.get("transcript") or rec.get("segments"))
    return r


def merge_records(records: list[dict]) -> list[dict]:
    """One record per key (see record_key); the richer record wins field by field, the others fill gaps.
    Keeps first-seen order."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(record_key(r), []).append(r)
    out = []
    for recs in groups.values():
        if len(recs) == 1:
            out.append(dict(recs[0]))
            continue
        ranked = sorted(recs, key=richness, reverse=True)
        merged: dict = {}
        for r in ranked:
            for k, v in r.items():
                if k not in merged or merged[k] in (None, "", [], {}):
                    merged[k] = v
        links = []
        for r in ranked:
            links += [u for u in r.get("links") or [] if u not in links]
        if links:
            merged["links"] = links
        merged["sources"] = sorted({s for r in recs for s in (r.get("sources") or [r.get("via") or "?"])})
        out.append(merged)
    return out


def upsert(seen: list[dict], items: list[dict], cap: int = 1500) -> list[dict]:
    """Merge new items into a stored `seen` list by key (richer record wins); keep the newest `cap`."""
    merged = merge_records(list(seen or []) + list(items or []))
    return merged[-cap:]


def default_independence(rec: dict, sponsor_threshold: float = 0.6) -> str | None:
    """Why `rec` doesn't count as independent, or None."""
    if rec.get("brand"):
        return "brand_account"
    bo = rec.get("brand_owned")
    if bo is True or bo == "strong" or bo == "yes":
        return "brand_owned"
    if (rec.get("sponsor_score") or 0) >= sponsor_threshold:
        return "sponsored"
    if rec.get("listicle") or rec.get("likely_ai_listicle") or rec.get("amazon_influencer"):
        return "listicle"
    return None


_CHANNEL_WIDE = {"brand_owned", "listicle", "brand_account"}
# Soft reasons keep a record out of the counts without marking its author/channel as non-independent.
_SOFT = {"op", "op_question"}


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _fold(t or "")[0]).strip()


def _parts(rec: dict, include_transcripts: bool) -> list[tuple[str, str, str | None]]:
    """(field, text, link) pieces of a record to scan."""
    link = rec.get("link") or rec.get("url")
    parts = [("title", rec.get("title") or "", link), ("text", rec.get("text") or "", link)]
    if rec.get("links"):
        parts.append(("links", " ".join(rec["links"]), link))
    if include_transcripts:
        if rec.get("segments"):
            parts += [("transcript", s.get("text") or "", s.get("link") or link) for s in rec["segments"]]
        elif rec.get("transcript"):
            parts.append(("transcript", rec["transcript"], link))
    return [p for p in parts if p[1]]


# A title asking to replace or compare a product: replies there endorse something else.
_NOT_ABOUT = re.compile(r"\b(?:vs\.?|versus|or|alternatives?|instead of|isn'?t|is not|aren'?t|other than|non-|replac(?:e|ing|ement)|switch(?:ing)? (?:from|away)|"
                        r"ditch(?:ing)?|leav(?:e|ing)|other than|besides|without|better than|ditched|moving (?:off|away))\b", re.I)


def _implied(recs: list[dict], term: Term, terms: list[Term], indep_fn, max_examples: int) -> dict:
    """Endorsements that don't name the product: top-level replies that read as a recommendation
    ("Love mine. No regrets.") in a thread whose post title names this product and no other tallied
    product. Kept apart from the main counts; read the examples before using them."""
    posts = {}
    for r in recs:
        if r.get("via") == "post" and r.get("title") and not _NOT_ABOUT.search(r["title"]):
            named = {t.name for t in terms if find_mentions(r["title"], [t])}
            if named == {term.name}:
                posts[str(r.get("thread") or r.get("id"))] = r
    users, up, examples = set(), 0, []
    for r in recs:
        if r.get("via") != "comment" or str(r.get("thread")) not in posts or r.get("is_op"):
            continue
        if (r.get("depth") or 0) != 0 or indep_fn(r):
            continue
        if re.fullmatch(r"(?i)automoderator|.*bot", r.get("author") or ""):
            continue  # bots and mod tools never endorse
        txt = r.get("text") or ""
        if not txt or find_mentions(txt, terms) or polarity(txt) != "recommend":
            continue
        a = r.get("author") or "?"
        if a in users:
            continue
        users.add(a)
        up += max(0, r.get("score") or 0)
        examples.append({"snippet": snippet(txt, 0, 0, 120).lstrip("…"), "link": r.get("link") or r.get("url"),
                         "author": a, "score": r.get("score")})
    examples.sort(key=lambda x: -(x["score"] or 0))
    return {"threads": len(posts), "users": len(users), "upvotes": up, "examples": examples[:max_examples]}


def tally(records: list[dict], names: list[str], *, aliases: dict | str | None = None,
          garbles: dict | None = None, group: str = "author", group_label: str = "users",
          separate_search: bool = True, op_once: bool = True, exclude_op: bool = False,
          include_transcripts: bool = True, sponsor_threshold: float = 0.6, independence=None,
          group_flags: dict | None = None, max_examples: int = 5, near: dict | str | None = None,
          max_groups: int = 12,
          compete: list[str] | None = None) -> dict:
    """Count mentions of `names` in `records` (see the module docstring for fields and output).
    `compete`: other names that take part in longest-match ("Proton Pass" claims its "Pass") but get
    no row of their own (used when the caller tallies one name at a time)."""
    others = [c for c in (compete or []) if c not in names]
    all_terms = build_terms(list(names) + others, aliases, garbles, near)
    terms = all_terms[:len(names)]
    recs = merge_records(records)
    indep_fn = independence or (lambda r: default_independence(r, sponsor_threshold))

    # Thread keys, with OP cross-posts (same author, same title) collapsed into one thread.
    canon: dict[str, str] = {}
    if op_once:
        first_by_post: dict[tuple, str] = {}
        for r in recs:
            if r.get("via") in ("post", "search") and r.get("title"):
                tid = str(r.get("thread") or r.get("id") or record_key(r))
                k = (r.get("author") or "", _norm_title(r["title"]))
                canon[tid] = first_by_post.setdefault(k, tid)

    def thread_of(r):
        tid = str(r.get("thread") or r.get("id") or record_key(r))
        return canon.get(tid, tid)

    # Channel-wide exclusions (strong brand ownership, listicles), from any record or group_flags.
    wide: dict[str, set[str]] = {}
    for r in recs:
        g = r.get(group)
        reason = indep_fn(r)
        if g and reason in _CHANNEL_WIDE:
            wide.setdefault(g, set()).add(reason)
    for g, flags in (group_flags or {}).items():
        for k, v in (flags or {}).items():
            if v:
                reason = default_independence({k: v}, sponsor_threshold) or k
                wide.setdefault(g, set()).add(reason)

    ask_threads = {str(r.get("thread") or r.get("id")) for r in recs
                   if r.get("via") == "post" and _ASK_TITLE.search(r.get("title") or "")}

    rows = []
    mention_cache: dict = {}
    for term in terms:
        hits = []  # (record, polarity, snippet, link)
        near_dropped = 0
        for r in recs:
            pols, first = [], None
            for fld, txt, link in _parts(r, include_transcripts):
                key = (id(r), fld, txt)
                if key not in mention_cache:
                    mention_cache[key] = find_mentions(txt, all_terms)
                for _, s, e in (m for m in mention_cache[key] if m[0] == term.name):
                    if term.near and not _near_ok(txt, s, e, term.near):
                        near_dropped += 1
                        continue
                    others = [(s2, e2) for n2, s2, e2 in mention_cache[key] if n2 != term.name]
                    pol = polarity(_clause_around(txt, s, e, others))
                    if pol == "mention":
                        pol = _short_answer_polarity(txt, s, e) or pol
                    if pol == "mention" and fld == "text" and _bare_suggestion(r, txt, s, ask_threads):
                        pol = "recommend"
                    pols.append(pol)
                    if first is None or (first[0] == "mention" and pol != "mention"):
                        first = (pol, snippet(txt, s, e), link, fld)
            if not pols:
                continue
            kinds = {p for p in pols if p in ("recommend", "complain")}
            pol = "mixed" if len(kinds) == 2 else (kinds.pop() if kinds else
                                                   "question" if "question" in pols else "mention")
            hits.append((r, pol, first))

        is_search = (lambda r: r.get("via") == "search") if separate_search else (lambda r: False)
        main = [h for h in hits if not is_search(h[0])]
        search_only = [h for h in hits if is_search(h[0])]

        excluded: dict[str, int] = {}
        groups: dict[str, dict] = {}
        pol_counts = {"recommend": 0, "complain": 0, "question": 0, "mention": 0, "mixed": 0}
        raw = rec_up = comp_up = 0
        examples, ex_keys = [], set()
        op_dropped: set[int] = set()
        for r, pol, (_, snip, link, fld) in main:
            g = r.get(group) or "?"
            reason = indep_fn(r)
            if reason is None and g in wide:
                reason = sorted(wide[g])[0]
            is_op = bool(r.get("is_op") or (group == "author" and r.get("via") == "post"))
            if reason is None and is_op and (exclude_op or pol == "question"):
                reason = "op" if exclude_op else "op_question"  # an OP's question is not an endorsement
            if reason == "op":
                # exclude_op drops the OP's mentions entirely: not a user, not a thread, no polarity.
                excluded["op"] = excluded.get("op", 0) + 1
                op_dropped.add(id(r))
                continue
            pol_counts[pol] += 1
            e = groups.setdefault(g, {group: g, "items": 0, "independent": False, "reasons": [],
                                      "polarity": {}, "max_sponsor_score": None, "_hard": False})
            e["items"] += 1
            e["polarity"][pol] = e["polarity"].get(pol, 0) + 1
            if r.get("sponsor_score") is not None:
                e["max_sponsor_score"] = max(e["max_sponsor_score"] or 0, r["sponsor_score"])
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
                if reason not in _SOFT:
                    e["_hard"] = True
                if reason not in e["reasons"]:
                    e["reasons"].append(reason)
            else:
                e["independent"] = True
                sc = max(0, r.get("score") or 0)
                raw += sc
                rec_up += sc if pol == "recommend" else 0
                comp_up += sc if pol == "complain" else 0
            k = (g, snip)
            if k not in ex_keys:
                ex_keys.add(k)
                examples.append({"snippet": snip, "link": link, "via": r.get("via"), "field": fld, group: g,
                                 "score": r.get("score"), "polarity": pol, "independent": reason is None})
        for e in groups.values():
            e["independent"] = e["independent"] and not e.pop("_hard")
        threads = {thread_of(r) for r, _, _ in main if id(r) not in op_dropped}
        indep_groups = [g for g, e in groups.items() if e["independent"] and g != "?"]
        row = {"term": term.name, "aliases": term.variants[1:], "items": len(main), "threads": len(threads),
               group_label: len([g for g in groups if g != "?"]), f"independent_{group_label}": len(indep_groups),
               "raw_upvotes": raw, "recommend_upvotes": rec_up, "complain_upvotes": comp_up,
               "polarity": pol_counts, "excluded": excluded,
               "by_group": sorted(groups.values(), key=lambda e: (-e["independent"], -e["items"]))[:max_groups],
               "examples": sorted(examples, key=lambda x: (-(x["independent"]), -(x["score"] or 0),
                                                           x["polarity"] == "mention"))[:max_examples]}
        shown = {k: f"{min(len(v), n)} of {len(v)}" for k, v, n in (("by_group", groups, max_groups),
                                                                   ("examples", examples, max_examples)) if len(v) > n}
        if shown:  # say so instead of cutting silently; --groups / --examples show more
            row["truncated"] = shown
        if term.near:
            row["near"] = term.near
            row["dropped_not_near"] = near_dropped
        amb = ambiguous_variants(term)
        if amb and not term.near:
            row["warning"] = (f"{', '.join(amb)} is also a common word; counts may include unrelated uses. Read "
                              f"the examples, or pass --near \"{term.name}=word,word\" to require context words.")
        if group == "author":
            row["implied"] = _implied(recs, term, terms, indep_fn, max_examples)
            rep_users = _repeat_promoters(main, thread_of, indep_fn)
            if rep_users:
                row["repeat_promoters"] = rep_users
        if separate_search:
            st = {thread_of(r) for r, _, _ in search_only} - threads
            row["search_only_threads"] = len(st)
            row["search_titles"] = [{"snippet": s[1], "link": s[2]} for _, _, s in search_only][:3]
        rows.append(row)
    rows.sort(key=lambda r: (-r[f"independent_{group_label}"], -r["recommend_upvotes"], -r["items"]))
    return {"seen_items": len(recs), "tally": rows}
