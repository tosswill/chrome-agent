#!/usr/bin/env python3
"""Polite, read-only Reddit research in a shared, signed-in chrome-agent browser.

Each agent claims its own window in one long-lived browser, so agents share the
Reddit login but never each other's tabs; all page loads share one pacing lock.

CLI (each command prints JSON):

    reddit_tab.py open [--wait [SECONDS]]           claim a window -> {"target": ID}
    reddit_tab.py search ID QUERY [--sub S] [--sort relevance|new|top|comments] [--time hour|day|week|month|year|all] [--no-type]
                                                    typed into Reddit's search box when the window is on a
                                                    Reddit page and the filters are Reddit's defaults
                                                    (relevance, all time); otherwise a search URL load.
                                                    `via` says which
    reddit_tab.py search ID --url URL               any Reddit search/listing URL
    reddit_tab.py thread ID URL [--max N] [--replies [N]] [--expand N] [--brand NAME ...]
                                                    open a thread (clicking it in the current or the last
                                                    results, going back to them first), read it, return
                                                    post + comments with permalinks and a promo_bias score
    reddit_tab.py back ID                           browser back (mouse back button), e.g. to the results
    reddit_tab.py tally ID NAME [NAME ...] [--alias "Name=Alias1,Alias2; ..."] [--exclude-op]
                                                    distinct users, polarity and upvotes behind each name,
                                                    among everything this window has seen (no page load)
    reddit_tab.py shot ID [--at post | --comment ID | --selector CSS] [--full] [--out-dir DIR | --out PATH]
                                                    visible page content, or one element scrolled into view
    reddit_tab.py nav ID URL                        paced load, page state only
    reddit_tab.py eval ID JS|@file.js               run JS in your tab (escape hatch)
    reddit_tab.py close ID                          release the window
    reddit_tab.py list | gc                         show / clean up claims

The same functions back the reddit_* tools of the chrome-agent MCP server.

Environment (REDDIT_TAB_*): INSTANCE (reddit-research-01), MAX (3 windows),
GAP (4 s between loads, all agents), IDLE (1800 s), MAX_LOADS (40 per window),
STATE (/tmp/chrome-agent-reddit), TYPE (1: type searches when possible; 0: always
load search URLs), READ_CAP (45 s: longest reading dwell per thread). Needs
`chrome-agent` on PATH.
"""
import json
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from research_core import Fail, Site, common_cli, opt, pause, run_cli  # noqa: E402
import disclosure as D  # noqa: E402
import tally as TL  # noqa: E402

SORTS = ("relevance", "new", "top", "comments", "hot")
TIMES = ("hour", "day", "week", "month", "year", "all")


def _js(name: str) -> str:
    return (HERE / name).read_text()


def _stop_reason(state: dict, site: Site) -> str | None:
    if state.get("blocked"):
        return "Reddit is showing a block or CAPTCHA page. Stop and ask the user; do not retry."
    if not state.get("loggedIn"):
        return ("Reddit is not signed in. Ask the user to log in manually in the browser window, "
                f"then save it: chrome-agent save {site.instance} --overwrite")
    return None


# The page content in viewport coordinates: below the top bar (username, avatar), right of the left
# navigation (joined communities) when it is shown.
CONTENT_JS = """(() => { const vw = innerWidth, vh = innerHeight;
  const hb = document.querySelector('reddit-header-large, header')?.getBoundingClientRect();
  const top = hb && hb.height > 0 ? Math.max(0, hb.bottom) : 0;
  const nav = document.querySelector('#left-sidebar-container, flex-left-nav-container, #left-sidebar');
  const nr = nav && nav.getBoundingClientRect();
  const left = nr && nr.width > 0 && nr.right < vw * 0.5 ? Math.max(0, nr.right) : 0;
  return {x: left, y: top, width: vw - left, height: vh - top}; })()"""

# The reading column (posts and comments), for where the pointer rests while scrolling.
MAIN_JS = """(() => { const vw = innerWidth, vh = innerHeight;
  const hb = document.querySelector('reddit-header-large, header')?.getBoundingClientRect();
  const top = hb && hb.height > 0 ? Math.max(0, hb.bottom) : 0;
  const m = document.querySelector('#main-content, main, shreddit-comment-tree, shreddit-post');
  const r = m ? m.getBoundingClientRect() : {left: 0, right: vw};
  const x0 = Math.max(0, r.left), x1 = Math.min(vw, r.right);
  return x1 - x0 > 80 ? {x: x0, y: top, width: x1 - x0, height: vh - top} : {x: 0, y: top, width: vw, height: vh - top}; })()"""

# Promoted posts and ad slots (feeds, comment pages, sidebar), for `ads_in_view` in shots.
AD_SELECTORS = ("shreddit-ad-post", "shreddit-comments-page-ad", "shreddit-comment-tree-ad",
                "shreddit-sidebar-ad", "[data-testid='promoted-post']")

SITE = Site(
    prefix="REDDIT_TAB",
    default_instance="reddit-research-01",
    hosts=("reddit.com",),
    forbidden=(".json", "/api/"),
    ready_js=("document.readyState === 'complete' && "
              "!!document.querySelector(\"shreddit-post, shreddit-comment, a[href*='/comments/']\")"),
    page_state_js=_js("page_state.js"),
    stop_reason=_stop_reason,
    default_gap=4.0,
    content_js=CONTENT_JS,
    ad_selectors=AD_SELECTORS,
)
INSTANCE = SITE.instance
_SHOTS = SITE.state / "shots"


# -------------------------------------------------------------------------
# Pure helpers (unit-tested, no browser)
# -------------------------------------------------------------------------

def _post_id(url: str) -> str | None:
    m = re.search(r"/comments/([a-z0-9]+)", url or "", re.I)
    return m.group(1) if m else None


def _comment_id(ref: str) -> str:
    """'abc123', 't1_abc123' or a comment permalink -> 'abc123'."""
    ref = (ref or "").strip()
    m = re.search(r"/comment/([a-z0-9]+)", ref, re.I) or re.search(r"/comments/[a-z0-9]+/[^/]+/([a-z0-9]+)", ref, re.I)
    if m:
        return m.group(1)
    return ref.removeprefix("t1_").strip("/")


# Moderators (R6, R11) --------------------------------------------------------

_USERNAME = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
_NOT_USERS = {"subscriber-count", "view-all", "moderators", "message", "message-the-mods", "about", "wiki"}
MOD_TOOLS = {"automoderator", "comment-nuke", "purge-user", "automod-sync", "modmail-userinfo", "toolbox",
             "botdefense", "modsupportbot", "reddit", "redditcareresources", "floodassistant", "repostsleuthbot",
             "sub-stats-bot", "flair-assistant", "bot-bouncer", "hive-protect", "modqueue-nuke", "mod-helper",
             "remove-macros", "admin-tattler", "evasion-guard", "post-guidance", "community-hub"}
_TOOL_WORDS = re.compile(r"(nuke|purge|automod|modmail|modqueue|mod-?tools?|devvit|-sync$|-guard$|-assistant$|"
                         r"-bouncer$|-helper$|-tattler$)", re.I)
_BOT_NAME = re.compile(r"(?:Bot|BOT|[-_]bot|bot[-_]?\d+)$")


def is_utility_account(name: str, flair: str | None = None) -> bool:
    """AutoModerator, *Bot / *-bot accounts and mod-tool (Devvit app) accounts (R11)."""
    low = (name or "").lower()
    if low in MOD_TOOLS or _TOOL_WORDS.search(low) or _BOT_NAME.search(name or ""):
        return True
    return bool(low.endswith("bot") and re.search(r"\bbot\b", flair or "", re.I))


def clean_moderators(raw: list[dict]) -> tuple[list[dict], list[str]]:
    """Real u/ names only (no 'subscriber-count' or numeric ids), de-duplicated in page order, without
    utility bots and mod tools (returned separately). Deterministic for the same input."""
    mods, skipped, seen = [], [], set()
    for m in raw or []:
        name = (m.get("name") or "").strip().removeprefix("u/").strip("/")
        if not _USERNAME.match(name) or name.isdigit() or name.lower() in _NOT_USERS or name.lower() in seen:
            continue
        seen.add(name.lower())
        flair = (m.get("flair") or "").strip() or None
        if is_utility_account(name, flair):
            skipped.append(name)
            continue
        mods.append({"name": name, "flair": flair})
    return mods, skipped


# Brands (R5, R11, R12) --------------------------------------------------------

KNOWN_BRANDS = {b.split()[0].lower() for b in TL.GARBLES} | {
    "xreal", "viture", "rokid", "rayneo", "sony", "flashforge", "sovol", "voron", "e3d", "tascam", "shure",
    "zoom", "logitech", "razer", "corsair", "garmin", "apple", "samsung", "google", "pixel", "oneplus", "xiaomi",
    "bose", "anker", "eufy", "ecoflow", "jackery", "qidi", "creality", "elegoo", "dyson",
    "roborock", "ecovacs", "meta", "oculus", "valve", "steam", "nintendo", "gopro", "insta360", "canon", "nikon",
    "fujifilm", "panasonic", "lumix", "sigma", "tamron", "godox", "aputure", "nanlite", "smallrig", "tilta",
    "blackmagic", "atomos", "deity", "sennheiser", "audiotechnica", "hollyland", "saramonic", "comica", "boya",
    "synco", "ugreen", "framework", "lenovo", "dell", "asus", "acer", "msi", "nvidia", "amd", "intel", "kobra",
    "snapmaker", "bambu", "formlabs", "phrozen", "artillery", "kingroon", "ratrig", "flsun", "peopoly", "raise3d",
}
# Words that look like names in title-case titles but are never a brand.
COMMON = D.GENERIC | {
    "printer", "printers", "printing", "print", "prints", "mic", "mics", "microphone", "camera", "cameras",
    "wireless", "system", "systems", "work", "help", "question", "advice", "recommendation", "recommendations",
    "need", "looking", "first", "beginner", "beginners", "home", "office", "studio", "doc", "show", "talk",
    "podcast", "podcasting", "videography", "photography", "audio", "video", "sound", "lav", "lavalier",
    "headphones", "earbuds", "monitor", "laptop", "phone", "phones", "glasses", "headset", "filament", "resin",
    "issue", "issues", "problem", "problems", "anyone", "everyone", "please", "thanks", "thank", "update",
    "official", "discussion", "thread", "weekly", "daily", "megathread", "vs", "versus", "worth", "verdict",
    "parts", "functional", "multi", "color", "colour", "kit", "kits", "pack", "budget", "cheap", "sucks",
    "interference", "buzz", "weddings", "wedding", "noise", "best", "good", "bad", "new", "old", "used",
}
_ALLCAPS_STOP = {"OP", "PSA", "USB", "TV", "LED", "PLA", "PETG", "ABS", "TPU", "DIY", "FDM", "SLA", "AMS", "AI",
                 "PC", "UK", "US", "USA", "EU", "FPS", "RGB", "HELP", "NEW", "WTF", "IMO", "TIL", "OMG", "ELI5",
                 "AMA", "FAQ", "PSU", "CPU", "GPU", "RAM", "SSD", "HDD", "DM", "PM", "IRL", "TLDR", "EDIT",
                 "UPDATE", "RF", "UHF", "VHF", "XLR", "TRS", "BT", "HDMI", "VR", "AR", "XR", "MR", "OLED", "LCD",
                 "NFC", "GPS", "APP", "WIFI", "ISO", "RAW", "LOG", "MP", "MM", "IS", "IT", "OK", "NOT", "AND",
                 "THE", "FOR", "VS", "BUY", "WHY", "HOW", "WHAT", "BEST", "WORST", "HELP!", "ANY", "NO", "YES"}
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+&'-]*")


def _tok(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def brand_candidates(title: str = "", query: str = "", flair: str = "", explicit=()) -> tuple[set, set]:
    """Brand tokens for a thread: (strong, weak). Strong: known brands, explicit --brand names, quoted
    or capitalized query words, ALLCAPS words and names followed by a model number. Weak: other
    capitalized words in a title that isn't written in Title Case. Category words never count."""
    strong, weak = set(), set()
    for e in explicit or ():
        for w in _WORD.findall(e):
            if len(_tok(w)) >= 2:
                strong.add(_tok(w))
    texts = [(title or "", "title"), (flair or "", "flair"), (query or "", "query")]
    for text, kind in texts:
        words = _WORD.findall(text)
        caps = [w for w in words if w[0].isupper()]
        title_case = len(words) >= 3 and len(caps) / len(words) > 0.6
        quoted = " ".join(re.findall(r'"([^"]+)"', text))
        for i, w in enumerate(words):
            t = _tok(w)
            if len(t) < 2 or t in COMMON or t.isdigit():
                continue
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if t in KNOWN_BRANDS:
                strong.add(t)
            elif 3 <= len(t) <= 7 and w.isupper() and w not in _ALLCAPS_STOP and re.search(r"[A-Z]{2}", w):
                strong.add(t)
            elif len(t) >= 3 and re.fullmatch(r"[A-Za-z]{0,3}\d{1,4}[A-Za-z+]{0,4}", nxt or "") and w[0].isupper():
                strong.add(t)  # "Kobra 3", "Lark M2", "Mic 3" (the word before a model number)
            elif kind == "query" and len(t) >= 3 and (w[0].isupper() or w in quoted):
                strong.add(t)
            elif kind == "flair" and len(t) >= 3 and w[0].isupper():
                weak.add(t)
            elif kind == "title" and len(t) >= 4 and w[0].isupper() and i > 0 and not title_case:
                weak.add(t)
    return strong, weak - strong


def _name_has(name: str, token: str, strong: bool) -> bool:
    n = _tok(name)
    if not n or not token:
        return False
    if strong:
        return token in n if len(token) >= 3 else n.startswith(token)
    # Weak tokens: only at the start of the name or as a whole name segment.
    segs = [_tok(s) for s in re.split(r"[_\-\d]+", name or "") if s]
    return len(token) >= 4 and (n.startswith(token) or token in segs)


def name_brand(name: str, strong: set, weak: set = frozenset()) -> str | None:
    """The brand token a username or subreddit name contains, if any (longest first, deterministic)."""
    for t in sorted(strong, key=lambda x: (-len(x), x)):
        if _name_has(name, t, True):
            return t
    for t in sorted(weak, key=lambda x: (-len(x), x)):
        if _name_has(name, t, False):
            return t
    return None


# Known brands that are also everyday words or common subreddit prefixes: they name a subreddit's
# brand only when the thread or query mentions them.
AMBIGUOUS = {"meta", "steam", "valve", "pixel", "zoom", "apple", "sigma", "deity", "framework", "artillery",
             "kobra", "synco", "boya", "dell", "acer", "intel", "amd", "msi", "google", "even", "bose", "canon"}


def subreddit_brand(sub: str, strong: set, weak: set = frozenset()) -> str | None:
    """The brand a subreddit is named after (r/dji, r/DJIMic, r/Xreal, r/prusa3d), never a category
    sub (r/podcasting, r/3Dprinting, r/videography) (R12): the name must equal or start with a brand
    token from the thread/query, or with a distinctive known brand."""
    raw = (sub or "").removeprefix("r/")
    s = _tok(raw)
    if not s or s in COMMON:
        return None
    known = {t for t in KNOWN_BRANDS if t not in AMBIGUOUS}
    for t in sorted(known | strong, key=lambda x: (-len(x), x)):
        if len(t) < 3 or t in COMMON or not s.startswith(t):
            continue
        if s == t or len(t) >= 4:
            return t
        nxt = raw[len(t):len(t) + 1]  # 3-letter brands need a word break: r/DJIMic, not r/djing
        if nxt and (nxt.isupper() or nxt.isdigit() or nxt in "_-"):
            return t
    for t in sorted(weak, key=lambda x: (-len(x), x)):
        if len(t) >= 4 and s.startswith(t):
            return t
    return None


_ROLE_SUFFIX = re.compile(r"^(?P<b>[A-Za-z0-9][A-Za-z0-9_-]*?)[_-]?(?P<k>support|official|team|staff|help|helpdesk|"
                          r"care|cs|rep|hq|cares)$", re.I)
_ROLE_PREFIX = re.compile(r"^(?P<k>official|team)[_-](?P<b>[A-Za-z0-9][A-Za-z0-9_-]{2,})$", re.I)
_TEAM_FLAIR = re.compile(r"\b(team|staff|employee|official|support|rep|representative|community manager|"
                         r"cm|engineer|developer|dev|founder|ceo|pr|marketing)\b", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@((?:[a-z0-9-]+\.)+[a-z]{2,})\b", re.I)
FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
             "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me", "gmx.com",
             "gmx.de", "gmx.net", "mail.com", "yandex.com", "yandex.ru", "zoho.com", "qq.com", "163.com",
             "126.com", "web.de", "hey.com", "fastmail.com", "tutanota.com", "duck.com", "reddit.com",
             "example.com"}


def company_emails(text: str) -> list[str]:
    """Email domains in `text` that aren't free-mail providers."""
    out = []
    for dom in _EMAIL.findall(text or ""):
        d = dom.lower()
        if d not in FREE_MAIL and d not in out:
            out.append(d)
    return out


def _domain_core(domain: str) -> str:
    """'support.e3d-online.com' -> 'e3donline'; 'bambulab.com' -> 'bambulab'."""
    parts = domain.lower().split(".")
    core = parts[-2] if len(parts) >= 2 else parts[0]
    return _tok(core)


def _shares_core(name: str, domain: str) -> bool:
    n, c = _tok(name), _domain_core(domain)
    if len(c) >= 3 and (c in n or n in c):
        return True
    # "e3dsupport" vs "e3d-online": a common prefix of 3+ characters
    k = 0
    while k < min(len(n), len(c)) and n[k] == c[k]:
        k += 1
    return k >= 3


def brand_account(author: str | None, flair: str | None = None, text: str = "", *, strong: set = frozenset(),
                  weak: set = frozenset(), sub_brand: str | None = None, mod_names=()) -> dict | None:
    """Why `author` looks like a brand account, or None (R5). Returns {author, reason, brand, confidence}.

    - username = brand + support/official/team/staff/help (e3dsupport, BambuLab_Support, team-xreal)
    - flair "<Brand> team/staff/employee/official/support" (or any team-style flair when the user moderates)
    - a company email in their text whose domain matches their username, or "contact us" wording
    - flair equal to the brand name (josefprusa: "Prusa") when the user also moderates
    """
    if not author or author in ("[deleted]", "AutoModerator"):
        return None
    brands = set(strong) | ({sub_brand} if sub_brand else set())
    is_mod = author.lower() in {m.lower() for m in mod_names}
    m = _ROLE_SUFFIX.match(author) or _ROLE_PREFIX.match(author)
    emails = company_emails(text)
    if m:
        b = _tok(m.group("b"))
        if b and (b in brands or name_brand(b, brands, weak) or any(_shares_core(b, d) for d in emails)
                  or (sub_brand and sub_brand in b)):
            return {"author": author, "reason": f"username: brand + '{m.group('k')}'", "brand": name_brand(b, brands, weak) or b,
                    "confidence": "likely"}
        if len(b) >= 2:
            return {"author": author, "reason": f"username ends in '{m.group('k')}'", "brand": b, "confidence": "possible"}
    fl = (flair or "").strip()
    if fl:
        fb = name_brand(fl, brands, weak) or (sub_brand if sub_brand and sub_brand in _tok(fl) else None)
        if _TEAM_FLAIR.search(fl) and (fb or is_mod):
            return {"author": author, "reason": f"flair '{fl}'", "brand": fb, "confidence": "likely"}
        if is_mod and fb and len(_tok(fl)) <= len(fb) + 3:
            return {"author": author, "reason": f"moderator with brand flair '{fl}'", "brand": fb, "confidence": "likely"}
    for d in emails:
        if _shares_core(author, d):
            return {"author": author, "reason": f"posts a company email (@{d}) matching the username",
                    "brand": _domain_core(d), "confidence": "likely"}
    if emails and re.search(r"\b(contact|email|e-mail|reach|write to|message) us\b|\bour (support|team|customer)\b",
                            text or "", re.I):
        return {"author": author, "reason": f"speaks for a company (@{emails[0]})", "brand": _domain_core(emails[0]),
                "confidence": "likely"}
    if is_mod:
        b = name_brand(author, brands, weak)
        if b:
            return {"author": author, "reason": f"moderator whose name contains '{b}'", "brand": b, "confidence": "likely"}
    return None


# Comment selection (R8, R13) -------------------------------------------------

def _empty(c: dict) -> bool:
    t = (c.get("text") or "").strip()
    return not t or t in ("[deleted]", "[removed]", "[ Removed by Reddit ]") or t.startswith("[ Removed by")


def select_comments(comments: list[dict], host: str | None, max_comments: int = 15, replies: int = 0,
                    read_bottom: float | None = None) -> tuple[list[dict], dict]:
    """Pick the comments to return, in page order:

    - up to `max_comments` top-level comments; deleted/removed or empty ones and empty AutoModerator
      stickies are skipped and don't use a slot (counted in skipped_deleted / skipped_automod)
    - under each top-level comment in range (including skipped deleted ones), the original poster's
      depth-1 replies, plus up to `replies` depth-1 replies from anyone
    - with `read_bottom` (document px reached while reading), only comments that were scrolled into
      view; the rest are counted in not_scrolled_into_view

    Returns (comments, counts)."""
    out = []
    counts = {"comments_rendered": len(comments), "skipped_deleted": 0, "skipped_automod": 0,
              "collapsed_chains": sum(1 for c in comments if c.get("collapsed")), "not_scrolled_into_view": 0}
    kept_top, in_range, per_parent = 0, False, 0
    for c in comments:
        depth = c.get("depth") or 0
        if depth == 0:
            per_parent = 0
            if kept_top >= max_comments:
                in_range = False
                continue
            in_range = True
            if c.get("author") == "AutoModerator" and (_empty(c) or c.get("stickied")):
                counts["skipped_automod"] += 1
                continue
            if c.get("deleted") or _empty(c):
                counts["skipped_deleted"] += 1
                continue
            if read_bottom is not None and (c.get("top") or 0) > read_bottom:
                counts["not_scrolled_into_view"] += 1
                in_range = False
                continue
            out.append(c)
            kept_top += 1
            continue
        if not in_range or depth != 1:
            continue
        if c.get("deleted") or _empty(c):
            counts["skipped_deleted"] += 1
            continue
        is_op = bool(host) and c.get("author") == host
        if not is_op and per_parent >= replies:
            continue
        if read_bottom is not None and (c.get("top") or 0) > read_bottom:
            counts["not_scrolled_into_view"] += 1
            continue
        if not is_op:
            per_parent += 1
        out.append(c)
    counts["comments_returned"] = len(out)
    return out, counts


# Promotion score ----------------------------------------------------------------

def promo_bias(data: dict, query: str = "", brands=()) -> dict:
    """0.0-1.0 likelihood that a thread is brand promotion or astroturf, with evidence. Page-only:
    account age and karma aren't checked (that would cost a profile load per user).

    Deterministic: brand moderators, brand accounts, the evidence text and the score all come from
    the same sorted sets. Utility bots and mod tools never count as brand moderators (R11); a
    subreddit counts as the brand's own only when its name is a brand (R12)."""
    sub = (data.get("subreddit") or "").removeprefix("r/")
    title = data.get("title") or ""
    strong, weak = brand_candidates(title, query, data.get("flair") or "", brands)
    sub_brand = subreddit_brand(sub, strong, weak)
    if sub_brand:
        strong = strong | {sub_brand}
    subject = " ".join([title, data.get("flair") or "", " ".join(sorted(strong))])
    mods, _ = clean_moderators(data.get("moderators") or [])
    mod_names = [m["name"] for m in mods]

    brand_mods = []
    for m in mods:
        why = brand_account(m["name"], m.get("flair"), "", strong=strong, weak=weak, sub_brand=sub_brand,
                            mod_names=mod_names)
        if why and why["confidence"] == "likely":
            brand_mods.append({"name": m["name"], "flair": m.get("flair"), "reason": why["reason"]})
    brand_mods.sort(key=lambda m: m["name"].lower())
    brand_mod_set = {m["name"].lower() for m in brand_mods}

    page = D.scan_text(title, "title", data.get("url"), subject)
    page += D.scan_text(data.get("body", ""), "post", data.get("url"), subject)
    page += [{**h, "source": "post links", "link": data.get("url")}
             for h in D.link_hits(D.classify_links((data.get("links") or []) + ([data["linked_url"]] if data.get("linked_url") else [])))
             if h["signal"] != "self_promotion"]
    if data.get("brand_affiliate_label"):
        page.insert(0, {"signal": "brand_affiliate_label", "weight": 0.5, "source": "post",
                        "text": f"Reddit labels the post: {data['brand_affiliate_label']}", "link": data.get("url")})
    post_brand = brand_account(data.get("author"), data.get("author_flair"), data.get("body", ""), strong=strong,
                               weak=weak, sub_brand=sub_brand, mod_names=mod_names)
    if post_brand and post_brand["confidence"] != "likely" and (data.get("author") or "").lower() not in brand_mod_set:
        post_brand = None
    if post_brand or (data.get("author") or "").lower() in brand_mod_set:
        reason = post_brand["reason"] if post_brand else "brand moderator"
        page.insert(0, {"signal": "brand_account_post", "weight": 0.6, "source": "post",
                        "text": f"Posted by u/{data.get('author')} ({reason})", "link": data.get("url")})

    extra, brand_comments, possible = [], [], []
    seen_authors = set()
    for c in data.get("comments", []):
        a = c.get("author")
        why = brand_account(a, c.get("flair"), c.get("text", ""), strong=strong, weak=weak, sub_brand=sub_brand,
                            mod_names=mod_names)
        if a and a.lower() in brand_mod_set and not why:
            why = {"reason": "brand moderator", "confidence": "likely", "brand": None}
        if why and a not in seen_authors:
            seen_authors.add(a)
            entry = {"author": a, "reason": why["reason"], "flair": c.get("flair"), "badges": c.get("badges"),
                     "score": c.get("score"), "link": c.get("link")}
            (brand_comments if why["confidence"] == "likely" else possible).append(entry)
        for h in D.scan_text(c.get("text", ""), f"comment by u/{a}", c.get("link"), subject):
            if h["signal"] in ("use_my_link_or_code", "affiliate_disclosure", "sponsor_read", "third_party_sponsor",
                               "review_unit"):
                extra.append(h)
        for h in D.link_hits(D.classify_links(c.get("links") or [])):
            if h["signal"] == "affiliate_links":
                extra.append({**h, "source": f"comment by u/{a}", "link": c.get("link")})
    if brand_comments:
        names = sorted({b["author"] for b in brand_comments}, key=str.lower)
        extra.insert(0, {"signal": "brand_accounts_in_thread", "weight": 0.15, "source": "comments",
                         "text": _names_text(names), "link": brand_comments[0]["link"]})
    if brand_mods:
        extra.append({"signal": "brand_moderated_subreddit", "weight": 0.2, "source": "sidebar",
                      "text": f"r/{sub} moderators include {len(brand_mods)} brand account(s): " + _names_text(
                          [m["name"] + (f" ({m['flair']})" if m.get("flair") else "") for m in brand_mods])})
    elif sub_brand:
        extra.append({"signal": "brand_subreddit", "weight": 0.1, "source": "subreddit",
                      "text": f"r/{sub} is named after the brand '{sub_brand}'; it may be close to the brand"})
    res = D.score(D.brand_adjust(page, subject, None), D.brand_adjust(extra, subject, None))
    res["brand_accounts"] = brand_comments
    if possible:
        res["possible_brand_accounts"] = possible
    res["brand_moderators"] = [m["name"] for m in brand_mods]
    res["brand_tokens"] = sorted(strong)
    res["ai_declared_comments"] = sum(1 for c in data.get("comments", []) if c.get("ai_declared"))
    res["confidence"] = "medium" if data.get("sidebar", True) else "low"
    res["note"] = ("Page-only: account age and karma aren't checked. A brand account or brand subreddit is "
                   "context, not proof of bad faith; weigh the substance of what they say.")
    if not data.get("moderators") and not data.get("sidebar", True):
        res["note"] += (" The community sidebar (moderators) wasn't rendered, probably because the window is "
                        "narrow, so brand moderation couldn't be checked.")
    return res


def _names_text(names: list[str], limit: int = 8) -> str:
    shown = ", ".join(f"u/{n}" for n in names[:limit])
    return shown + (f" (+{len(names) - limit} more)" if len(names) > limit else "")


# Records for tally (S1) -------------------------------------------------------

def search_records(posts: list[dict]) -> list[dict]:
    out = []
    for p in posts:
        out.append({"id": p["id"], "thread": p["id"], "via": "search", "url": p["url"], "title": p.get("title", ""),
                    "text": "", "author": p.get("author"), "score": p.get("score"), "sub": p.get("sub"),
                    "is_op": True})
        if p.get("snippet"):
            out.append({"id": "t1_" + p["snippet_link"].rstrip("/").rsplit("/", 1)[-1] if p.get("snippet_link") else None,
                        "thread": p["id"], "via": "search", "url": p.get("snippet_link") or p["url"],
                        "link": p.get("snippet_link"), "title": "", "text": p["snippet"], "author": None})
    return [r for r in out if r.get("id")]


def thread_records(data: dict, comments: list[dict], brand_authors: set) -> list[dict]:
    pid = data.get("id") or _post_id(data.get("url", ""))
    host = data.get("author")
    recs = [{"id": pid, "thread": pid, "via": "post", "url": data["url"], "title": data.get("title", ""),
             "text": data.get("body", ""), "author": host, "score": data.get("score"), "is_op": True,
             "brand": host in brand_authors, "links": (data.get("links") or []) + ([data["linked_url"]] if data.get("linked_url") else []),
             "sub": data.get("subreddit")}]
    for c in comments:
        recs.append({"id": "t1_" + (c.get("id") or c.get("link") or ""), "thread": pid, "via": "comment",
                     "url": c.get("link"), "link": c.get("link"), "title": "", "text": c.get("text", ""),
                     "author": c.get("author"), "score": c.get("score"), "is_op": bool(host) and c.get("author") == host,
                     "brand": c.get("author") in brand_authors, "links": c.get("links") or []})
    return recs


# -------------------------------------------------------------------------
# Window state
# -------------------------------------------------------------------------

def _claim(full_id: str) -> dict:
    return SITE.claims().get(full_id) or {}


def _update_claim(full_id: str, **kv) -> None:
    c = SITE.claims().get(full_id)
    if c is None:
        return
    for k, v in kv.items():
        if v is None:
            c.pop(k, None)
        else:
            c[k] = v
    SITE._write_claim(full_id, c)


def _uncount_load(full_id: str) -> None:
    """Undo a load counted for a click that didn't navigate (the fallback load counts instead)."""
    c = SITE.claims().get(full_id)
    if c is not None and c.get("loads", 0) > 0:
        c["loads"] -= 1
        SITE._write_claim(full_id, c)


def _seen_add(full_id: str, items: list[dict]) -> None:
    c = SITE.claims().get(full_id)
    if c is None:
        return
    c["seen"] = TL.upsert(c.get("seen", []), items, cap=1500)
    SITE._write_claim(full_id, c)


def _href(full_id: str) -> str:
    try:
        return SITE.evaluate(full_id, "location.href") or ""
    except Fail:
        return ""


def _state(full_id: str, **extra) -> dict:
    state = SITE.evaluate(full_id, SITE.page_state_js) or {}
    state.update(extra)
    reason = SITE.stop_reason(state, SITE)
    if reason:
        state["stop"] = reason
    return state


def _time_origin(full_id: str) -> float | None:
    try:
        return SITE.evaluate(full_id, "performance.timeOrigin")
    except Fail:
        return None


# -------------------------------------------------------------------------
# Keys (trusted keyboard input)
# -------------------------------------------------------------------------

def _key(full_id: str, key: str, code: str, vk: int, modifiers: int = 0, commands: list[str] | None = None,
         text: str | None = None) -> None:
    """Press and release one key (a trusted key event). `text` (e.g. "\\r" for Enter) adds the char
    event that makes the key type or submit."""
    down = {"type": "rawKeyDown", "key": key, "code": code, "windowsVirtualKeyCode": vk, "modifiers": modifiers}
    if commands:
        down["commands"] = commands
    SITE.cdp(full_id, "Input.dispatchKeyEvent", down)
    if text is not None:
        SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": "char", "key": key, "text": text, "unmodifiedText": text})
    time.sleep(random.uniform(0.04, 0.11))
    SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code,
                                                 "windowsVirtualKeyCode": vk, "modifiers": modifiers})


def _type_text(full_id: str, text: str) -> None:
    """Type like a person: per-key delays, a little slower after spaces and punctuation, an
    occasional short hesitation."""
    for ch in text:
        SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch, "unmodifiedText": ch})
        time.sleep(random.uniform(0.02, 0.06))
        SITE.cdp(full_id, "Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
        gap = random.uniform(0.06, 0.19)
        if ch in ' "()':
            gap += random.uniform(0.05, 0.2)
        if random.random() < 0.05:
            gap += random.uniform(0.3, 0.8)
        time.sleep(gap)


def _select_all(full_id: str) -> None:
    mac = sys.platform == "darwin"
    _key(full_id, "a", "KeyA", 65, modifiers=4 if mac else 2, commands=["selectAll"])


# -------------------------------------------------------------------------
# Operations (used by the CLI and the MCP server)
# -------------------------------------------------------------------------

def open_window(wait_seconds: float = 0) -> dict:
    return SITE.open_window(wait_seconds)


def nav(target: str, url: str) -> dict:
    return SITE.nav(target, url)


def search_url(query: str, subreddit: str | None = None, sort: str | None = "relevance",
               time_filter: str | None = None) -> str:
    sort = sort or "relevance"
    if sort not in SORTS:
        raise Fail(f"sort must be one of {SORTS}")
    if time_filter is not None and time_filter not in TIMES:
        raise Fail(f"time must be one of {TIMES}")
    base = f"https://www.reddit.com/r/{quote(subreddit.removeprefix('r/'))}/search/" if subreddit \
        else "https://www.reddit.com/search/"
    params = {"q": query, "type": "posts", "sort": sort}
    if time_filter:
        params["t"] = time_filter
    return base + "?" + urlencode(params, quote_via=quote)


_SEARCH_JS = """(() => { const h = document.querySelector('reddit-search-large');
  if (!h) return null; let scope = null; try { scope = JSON.parse(h.getAttribute('scope') || 'null'); } catch (e) {}
  const f = h.shadowRoot && h.shadowRoot.querySelector('faceplate-search-input');
  const r = h.getBoundingClientRect();
  return {scope, value: f ? (f.value || '') : null, chip: !!(h.shadowRoot && h.shadowRoot.querySelector('rpl-input-chip, faceplate-chip, [data-testid*=chip]')) || !!document.querySelector('rpl-input-chip'),
          rect: {x: r.x, y: r.y, width: r.width, height: r.height}, vw: innerWidth}; })()"""

_RESULTS_READY = ("document.readyState === 'complete' && (!!document.querySelector(\"a[data-testid='post-title-text'], "
                  "a[data-testid='post-title'], shreddit-post\") || /no results|hm\\.\\.\\. we couldn/i.test(document.body.innerText.slice(0, 3000)))")


def _typed_search(full_id: str, query: str, subreddit: str | None) -> tuple[dict | None, str]:
    """Click Reddit's search box, select what's in it, type the query and press Enter. Returns
    (page state, "") on success, or (None, reason) when typing isn't possible here (nothing was
    submitted, so no load was used)."""
    cur = _href(full_id)
    if urlparse(cur).hostname is None or not urlparse(cur).hostname.endswith("reddit.com"):
        return None, "the window isn't on a Reddit page yet"
    info = SITE.evaluate(full_id, _SEARCH_JS)
    if not info or not info.get("rect") or info["rect"]["width"] < 40:
        return None, "no search box on this page"
    scope = info.get("scope") or {}
    if subreddit:
        path = (scope.get("path") or "").lower().rstrip("/")
        if path != "/r/" + subreddit.lower().removeprefix("r/"):
            return None, f"the search box isn't scoped to r/{subreddit.removeprefix('r/')}"
    r = info["rect"]
    SITE.click(full_id, r["x"] + r["width"] * random.uniform(0.3, 0.7), r["y"] + r["height"] * random.uniform(0.4, 0.6))
    pause(random.uniform(0.3, 0.7))
    _select_all(full_id)
    pause(random.uniform(0.15, 0.35))
    if not subreddit and (scope.get("type") or "global") != "global":
        # Clear the field, then one more Backspace removes the "r/<sub>" scope chip.
        _key(full_id, "Backspace", "Backspace", 8, commands=["deleteBackward"])
        pause(random.uniform(0.2, 0.4))
        _key(full_id, "Backspace", "Backspace", 8, commands=["deleteBackward"])
        pause(random.uniform(0.2, 0.4))
        # The scope attribute doesn't always follow the chip, so check the chip itself.
        if SITE.evaluate(full_id, "!!document.querySelector('rpl-input-chip')"):
            _key(full_id, "Escape", "Escape", 27)
            return None, "couldn't remove the subreddit scope from the search box"
    _type_text(full_id, query)
    pause(random.uniform(0.3, 0.8))
    now = SITE.evaluate(full_id, _SEARCH_JS) or {}
    if (now.get("value") or "").strip() != query.strip():
        _key(full_id, "Escape", "Escape", 27)
        return None, "the search box didn't take the typed text"
    loads = SITE._count_load(full_id)
    SITE._pace()
    _key(full_id, "Enter", "Enter", 13, text="\r")
    want_path = f"/r/{subreddit.lower().removeprefix('r/')}/search" if subreddit else "/search"
    deadline = time.time() + 15
    while time.time() < deadline:
        pause(0.5, fuzz=0.2)
        u = urlparse(_href(full_id))
        q = parse_qs(u.query).get("q", [""])[0]
        if "/search" in u.path and q.strip() == query.strip():
            SITE.wait_for(full_id, _RESULTS_READY, timeout=15, settle=SITE.settle)
            if u.path.lower().rstrip("/") != want_path:
                # Submitted, but in the wrong scope: that was a real load; the URL load follows.
                return {"loads": loads, "_failed": True, "_loaded": True}, f"the typed search ran in {u.path}"
            return _state(full_id, loads=loads), ""
    return {"loads": loads, "_failed": True}, "the results page didn't open after Enter"


def search(target: str, query: str | None = None, *, url: str | None = None,
           subreddit: str | None = None, sort: str | None = None, time_filter: str | None = None,
           typed: bool | None = None) -> dict:
    """Run a Reddit search (or load a listing URL) and return the unique posts on it.

    The query is typed into Reddit's search box like a person when the window is already on a
    Reddit page, the filters are Reddit's defaults (relevance, all time), and the box has the right
    scope (site-wide, or the subreddit you're in). Otherwise the search URL is loaded. `page.via`
    says which: "typed", "url", or "url (<why typing wasn't used>)"."""
    full_id = SITE.resolve(target)
    if typed is None:
        typed = os.environ.get("REDDIT_TAB_TYPE", "1") != "0"
    page, why = None, ""
    if url:
        why = "url given"
    elif not query:
        raise Fail("search needs a query or --url")
    elif not typed:
        why = "typing disabled"
    elif (sort or "relevance") != "relevance" or (time_filter or "all") != "all":
        why = "sort/time filter"
    else:
        page, why = _typed_search(full_id, query, subreddit)
        if page is not None and page.pop("_failed", False):
            if not page.get("_loaded"):
                _uncount_load(full_id)
            page = None
    if page is None:
        page = SITE.nav(full_id, url or search_url(query or "", subreddit, sort, time_filter))
        page["via"] = "url" + (f" ({why})" if why and why != "url given" else "")
    else:
        page["via"] = "typed"
    if "stop" in page:
        return {"page": page, "posts": []}
    posts = SITE.evaluate(full_id, _js("search_results.js")) or []
    _update_claim(full_id, last_search={"url": page.get("url") or _href(full_id), "query": query or "",
                                        "ids": [p["id"] for p in posts], "time_origin": _time_origin(full_id)},
                  from_search=None)
    _seen_add(full_id, search_records(posts))
    return {"page": page, "posts": posts}


# Back --------------------------------------------------------------------------

_PDP_BACK = "pdp-back-button"


def back(target: str) -> dict:
    """Go back one page the way a person would: the mouse's back button at the pointer (falling
    back to Reddit's own back arrow, then the browser's back command, which is what the toolbar
    button does). Reddit restores the previous page in-app, so going back to search results is not
    a page load; if the browser had to fetch the page again it is counted. Returns
    {page: {url, via, restored, loads}, posts?} (posts: the cached results when back on them)."""
    full_id = SITE.resolve(target)
    hist = SITE.cdp(full_id, "Page.getNavigationHistory")
    idx, entries = hist.get("currentIndex", 0), hist.get("entries", [])
    if idx <= 0 or not entries:
        raise Fail("there is no earlier page in this window to go back to")
    prev = entries[idx - 1]
    SITE.check_url(prev["url"])
    before = _href(full_id)
    origin = _time_origin(full_id)
    via = None
    SITE.touch(full_id)

    def moved() -> bool:
        for _ in range(12):
            pause(0.5, fuzz=0.2)
            if _href(full_id) != before:
                return True
        return False

    x, y = SITE.pointer_into(full_id, SITE.evaluate(full_id, MAIN_JS))
    pause(random.uniform(0.2, 0.6))
    SITE.cdp(full_id, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "back",
                                                   "buttons": 8, "clickCount": 1})
    time.sleep(random.uniform(0.06, 0.13))
    SITE.cdp(full_id, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "back",
                                                   "buttons": 0, "clickCount": 1})
    if moved():
        via = "mouse back button"
    elif SITE.rect(full_id, _PDP_BACK) and SITE.click_on(full_id, _PDP_BACK, look=random.uniform(0.3, 0.8)) and moved():
        via = "Reddit back arrow"
    else:
        SITE.cdp(full_id, "Page.navigateToHistoryEntry", {"entryId": prev["id"]})
        via = "browser back (toolbar command; fallback)"
        moved()
    SITE.wait_for(full_id, _RESULTS_READY if "/search" in prev["url"] else SITE.ready_js, timeout=15, settle=1.0)
    now_origin = _time_origin(full_id)
    c = _claim(full_id)
    ls = c.get("last_search") or {}
    if now_origin == origin:
        restored = "in-app (same document, no page load)"
        loads = c.get("loads", 0)
    elif ls.get("time_origin") and now_origin == ls.get("time_origin"):
        restored = "back/forward cache (no page load)"
        loads = c.get("loads", 0)
    else:
        loads = SITE._count_load(full_id)
        restored = "page fetched again (counted as a load)"
    page = _state(full_id, via=via, restored=restored, loads=loads)
    out = {"page": page}
    if "/search" in urlparse(page.get("url") or "").path:
        posts = SITE.evaluate(full_id, _js("search_results.js")) or []
        out["posts"] = posts
        if ls:
            _update_claim(full_id, last_search={**ls, "url": page.get("url"), "ids": [p["id"] for p in posts] or ls.get("ids", [])},
                          from_search=None)
    return out


# Threads ----------------------------------------------------------------------

def _title_sel(pid: str) -> str:
    return (f'a[data-testid="post-title-text"][href*="/comments/{pid}/"], '
            f'a[data-testid="post-title"][href*="/comments/{pid}/"], '
            f'shreddit-post[id="t3_{pid}"] a[slot="full-post-link"], shreddit-post[id="t3_{pid}"] a[slot="title"]')


def _visible_link(full_id: str, pid: str) -> bool:
    try:
        return bool(SITE.evaluate(full_id, f"!!document.querySelector({json.dumps(_title_sel(pid))})"))
    except Fail:
        return False


def _arrived(full_id: str, pid: str, timeout: float = 12) -> bool:
    return SITE.wait_for(full_id, f"location.pathname.includes('/comments/{pid}/') && "
                                  f"!!document.querySelector('shreddit-post[id=\"t3_{pid}\"]') && document.readyState === 'complete'",
                         timeout=timeout, settle=SITE.settle)


def _click_result(full_id: str, pid: str) -> bool:
    """Click the result's title with the human pointer. REDDIT_TAB_TEST_MISS=N makes the first N
    clicks miss (the pointer goes there but doesn't press), to test the fallback."""
    miss = int(os.environ.get("REDDIT_TAB_TEST_MISS", "0") or 0)
    c = _claim(full_id)
    if miss and c.get("test_missed", 0) < miss:
        _update_claim(full_id, test_missed=c.get("test_missed", 0) + 1)
        r = SITE.scroll_into_view(full_id, _title_sel(pid))
        if r:
            SITE.move(full_id, r["x"] + r["width"] * 0.5, r["y"] + r["height"] * 0.5)
        return r is not None
    return SITE.click_on(full_id, _title_sel(pid), look=random.uniform(0.6, 1.8))


def _open_thread(full_id: str, url: str, pid: str) -> dict:
    """Open a thread the way a person would (R1, R3):

    1. already open in this window: nothing to load
    2. its title is on the current page: click it
    3. it was in this window's last search results and we're on a thread opened from them: go back
       (no page load), then click it
    4. otherwise: load the URL

    After a click the URL must reach /comments/<id>/; if it doesn't, the click is retried once
    (scrolled into view again), then the URL is loaded. `opened` says what happened: "already open",
    "clicked", "clicked (after back)", "clicked (second try)", "url (click missed)" or "url"."""
    cur = _href(full_id)
    if _post_id(cur) == pid and "/comment/" not in urlparse(cur).path:
        return _state(full_id, loads=_claim(full_id).get("loads", 0), opened="already open")
    went_back = None
    if not _visible_link(full_id, pid):
        c = _claim(full_id)
        ls = c.get("last_search") or {}
        if pid in (ls.get("ids") or []) and c.get("from_search") == ls.get("url"):
            try:
                b = back(full_id)["page"]
                went_back = b
            except Fail:
                went_back = None
    if _visible_link(full_id, pid):
        loads = SITE._count_load(full_id)
        SITE._pace()
        results_url = _href(full_id)
        for attempt in (1, 2):
            if not _click_result(full_id, pid):
                break
            if _arrived(full_id, pid):
                opened = "clicked" + (" (after back)" if went_back else "") + (" (second try)" if attempt == 2 else "")
                st = _state(full_id, loads=loads, opened=opened)
                if went_back:
                    st["back"] = {k: went_back.get(k) for k in ("via", "restored")}
                if "/search" in urlparse(results_url).path:
                    _update_claim(full_id, from_search=(_claim(full_id).get("last_search") or {}).get("url"))
                return st
            if _post_id(_href(full_id)) not in (None, pid):
                break  # landed somewhere else; load the URL
            pause(random.uniform(0.6, 1.4))
        _uncount_load(full_id)
        st = SITE.nav(full_id, url)
        st["opened"] = "url (click missed)"
        if went_back:
            st["back"] = {k: went_back.get(k) for k in ("via", "restored")}
        _update_claim(full_id, from_search=None)
        return st
    st = SITE.nav(full_id, url)
    st["opened"] = "url"
    _update_claim(full_id, from_search=None)
    if went_back:
        st["back"] = {k: went_back.get(k) for k in ("via", "restored")}
    return st


_EXPAND_POINT_JS = """((id) => {
  const c = document.querySelector(`shreddit-comment[thingid="t1_${id}"]`);
  const d = c && ([...c.children].find(e => e.tagName === 'DETAILS') || c.querySelector('details'));
  const s = d && d.querySelector(':scope > summary');
  if (!s) return null;
  const btn = [...s.querySelectorAll('button')].find(b => /expand|toggle/i.test(b.getAttribute('aria-label') || ''));
  const r = (btn || s).getBoundingClientRect();
  if (r.bottom < 60 || r.top > innerHeight - 20) return {offscreen: true};
  for (let i = 0; i < 12; i++) {
    const x = r.left + r.width * (btn ? 0.5 : (0.55 + 0.4 * Math.random())), y = r.top + r.height * (0.35 + 0.3 * Math.random());
    const e = document.elementFromPoint(x, y);
    if (e && (btn ? btn.contains(e) : s.contains(e)) && !e.closest('a, faceplate-hovercard')) return {x, y};
  }
  return null; })"""


def _expand(full_id: str, comment_id: str, area: dict | None) -> bool:
    """Expand one collapsed comment chain with a human click on its row (fetches a fragment, not a
    page load)."""
    sel = f'shreddit-comment[thingid="t1_{comment_id}"]'
    if SITE.scroll_into_view(full_id, sel, area=area) is None:
        return False
    pt = SITE.evaluate(full_id, f"({_EXPAND_POINT_JS})({json.dumps(comment_id)})")
    if not pt or "x" not in pt:
        return False
    pause(random.uniform(0.4, 1.0))
    SITE.click(full_id, pt["x"], pt["y"])
    ok = SITE.wait_for(full_id, f"(() => {{ const c = document.querySelector('{sel}'); const d = c && "
                                f"([...c.children].find(e => e.tagName === 'DETAILS') || c.querySelector('details')); "
                                f"return !!d && d.hasAttribute('open'); }})()", timeout=6)
    pause(random.uniform(0.8, 1.6))
    return bool(ok)


_GEOM_JS = """(() => ({y: scrollY, vh: innerHeight, doc: document.documentElement.scrollHeight}))()"""
_TEXT_BAND_JS = """((a, b) => { let n = 0;
  for (const e of document.querySelectorAll('shreddit-post [slot="text-body"], shreddit-comment [slot="comment"]')) {
    const r = e.getBoundingClientRect(), t = r.top + scrollY, bt = r.bottom + scrollY;
    if (bt <= a || t >= b) continue;
    const frac = Math.min(1, (Math.min(b, bt) - Math.max(a, t)) / Math.max(1, bt - t));
    n += (e.innerText || '').length * frac; }
  return Math.round(n); })"""


def _read_thread(full_id: str, target_bottom: float, cap: float) -> dict:
    """Scroll down through the thread with the mouse wheel as if reading (B4, B6): dwell scales with
    the text that comes into view, with occasional longer pauses and small scroll-backs, and the
    pointer drifts a little while resting. Stops once `target_bottom` (document px) has been in view,
    at the end of the page, or after `cap` seconds. Returns {read_bottom, seconds, scrolls, scroll_backs}."""
    t0 = time.time()
    area = SITE.evaluate(full_id, MAIN_JS)
    g = SITE.evaluate(full_id, _GEOM_JS) or {"y": 0, "vh": 800, "doc": 800}
    seen_bottom = g["y"] + g["vh"]
    first = SITE.evaluate(full_id, f"({_TEXT_BAND_JS})(0, {seen_bottom})") or 0
    SITE.read_pause(full_id, min(6.0, 1.0 + first / random.uniform(60, 110)), area=area)
    scrolls = backs = 0
    stuck = 0
    while time.time() - t0 < cap:
        if seen_bottom >= target_bottom + 40:
            break
        dy = g["vh"] * random.uniform(0.45, 0.8)
        SITE.wheel(full_id, dy, area=area)
        scrolls += 1
        pause(random.uniform(0.25, 0.5), fuzz=0)
        g2 = SITE.evaluate(full_id, _GEOM_JS) or g
        if g2["y"] <= g["y"] + 2:
            stuck += 1
            if stuck >= 2:
                break  # end of page (or Reddit still loading more)
            pause(1.0)
            continue
        stuck = 0
        new_bottom = g2["y"] + g2["vh"]
        chars = SITE.evaluate(full_id, f"({_TEXT_BAND_JS})({seen_bottom}, {new_bottom})") or 0
        seen_bottom = max(seen_bottom, new_bottom)
        g = g2
        dwell = 0.6 + chars / random.uniform(55, 100)  # skimming speed, characters per second
        if random.random() < 0.1:
            dwell += random.uniform(2.5, 7.0)  # stopped to read something properly
        SITE.read_pause(full_id, min(dwell, 9.0), area=area)
        if random.random() < 0.12 and scrolls > 1:
            SITE.wheel(full_id, -random.uniform(120, 380), area=area)  # glance back up
            backs += 1
            SITE.read_pause(full_id, random.uniform(0.8, 2.2), area=area)
            SITE.wheel(full_id, random.uniform(120, 380), area=area)
            pause(random.uniform(0.3, 0.6), fuzz=0)
            g = SITE.evaluate(full_id, _GEOM_JS) or g
            seen_bottom = max(seen_bottom, g["y"] + g["vh"])
    return {"read_bottom": seen_bottom, "seconds": round(time.time() - t0, 1), "scrolls": scrolls,
            "scroll_backs": backs}


def _target_bottom(data: dict, max_comments: int, replies: int) -> float:
    """How far down the page the comments we'd return reach (document px)."""
    sel, _ = select_comments(data["comments"], data.get("author"), max_comments, replies)
    if not sel:
        return data.get("post_bottom") or 0
    # Read a little into the replies under the last kept comment (its element holds its whole subtree).
    last = max(sel, key=lambda c: c.get("top") or 0)
    return (last.get("top") or 0) + min(600, max(0, (last.get("bottom") or 0) - (last.get("top") or 0)))


def thread(target: str, url: str, max_comments: int = 15, replies: int = 0, expand: int | None = None,
           brands: list[str] | None = None) -> dict:
    """Open a thread (clicking it in the results when possible), read down it, and return the post
    and its comments: top-level ones, the OP's replies under them, and with `replies` up to that many
    depth-1 replies from anyone per comment. Deleted/empty comments don't use slots; counts explain
    a short list. `expand`: collapsed chains to open with a click (default: sometimes one)."""
    if not url.startswith("http"):
        url = "https://www.reddit.com/" + url.lstrip("/")
    full_id = SITE.resolve(target)
    pid = _post_id(url)
    explicit_sort = "sort=" in url
    if explicit_sort or not pid:
        page = SITE.nav(full_id, url)
        page["opened"] = "url"
        _update_claim(full_id, from_search=None)
    else:
        page = _open_thread(full_id, url, pid)
    if "stop" in page:
        return {"page": page, "thread": None}
    data = SITE.evaluate(full_id, _js("thread.js"))
    if not data or data.get("missing"):
        says = (data or {}).get("page_says")
        note = (f"the thread page says: {says!r}" if says else
                f"no post on this page ({(data or {}).get('url') or _href(full_id)}); the URL may not be a thread, "
                "or the page didn't finish loading. Nothing indicates it was removed.")
        return {"page": page, "thread": None, "note": note}
    cap = float(os.environ.get("REDDIT_TAB_READ_CAP", "45"))
    reading = _read_thread(full_id, _target_bottom(data, max_comments, replies), cap)

    # Collapsed chains (R13b): sometimes open one, like a curious reader; always when asked.
    data = SITE.evaluate(full_id, _js("thread.js")) or data
    want = (1 if random.random() < 0.35 else 0) if expand is None else max(0, int(expand))
    expanded = []
    if want:
        area = SITE.evaluate(full_id, MAIN_JS)
        cands = [c for c in data["comments"] if c.get("collapsed") and c.get("id") and c.get("depth", 0) <= 1
                 and (c.get("top") or 0) <= reading["read_bottom"] + 2000]
        for c in cands[:want]:
            if _expand(full_id, c["id"], area):
                expanded.append(c["id"])
        if expanded:
            data = SITE.evaluate(full_id, _js("thread.js")) or data
            reading["read_bottom"] = max(reading["read_bottom"], (SITE.evaluate(full_id, "scrollY + innerHeight") or 0))
    all_comments = data["comments"]
    chosen, counts = select_comments(all_comments, data.get("author"), max_comments, replies,
                                     read_bottom=reading["read_bottom"])
    counts.update(comments_total=data.get("comments_total"), expanded_chains=len(expanded))
    last = _claim(full_id).get("last_search") or {}
    readable = [c for c in all_comments if (c.get("top") or 0) <= reading["read_bottom"]]
    data["promo_bias"] = promo_bias({**data, "comments": readable}, query=last.get("query", ""), brands=brands or ())
    mods, skipped = clean_moderators(data.get("moderators") or [])
    data["moderators"] = mods
    if skipped:
        data["moderators_skipped"] = skipped
    brand_authors = {b["author"] for b in data["promo_bias"]["brand_accounts"]} | \
        set(data["promo_bias"]["brand_moderators"])
    for c in chosen:
        c["is_op"] = c.get("author") == data.get("author")
        if c.get("author") in brand_authors:
            c["brand_account"] = True
        for k in ("top", "bottom", "collapsed", "stickied", "deleted"):
            c.pop(k, None)
        if not c.get("links"):
            c.pop("links", None)
        if not c.get("roles"):
            c.pop("roles", None)
    data["comments"] = chosen
    data["counts"] = counts
    data["reading"] = reading
    data["comment_order"] = ("sort=" + url.split("sort=")[1].split("&")[0]) if explicit_sort else "Reddit default (best)"
    for k in ("post_bottom", "doc_height", "view_height", "scroll_y"):
        data.pop(k, None)
    _seen_add(full_id, thread_records(data, chosen, brand_authors))
    return {"page": page, "thread": data}


# Tally ----------------------------------------------------------------------------

def tally(target: str, terms: list[str], aliases: str | dict | None = None, exclude_op: bool = False) -> dict:
    """Per name: threads, distinct users (independent = not brand accounts), polarity, and upvotes
    (raw, recommend-weighted, complaint-weighted) behind the mentions, among everything this window
    has seen, with linked snippets. Search-title-only hits are kept apart (search_only_threads); the
    OP counts once per thread (cross-posts collapse), and an OP's question isn't an endorsement."""
    full_id = SITE.resolve(target)
    seen = _claim(full_id).get("seen", [])
    res = TL.tally(seen, terms, aliases=aliases, exclude_op=exclude_op)
    res["note"] = ("Counts come only from what this window has seen. `users`/`independent_users` and the upvotes "
                   "count loaded posts and comments; search titles and snippets are in `search_only_threads`. "
                   "Brand accounts don't count as independent. recommend_upvotes counts only mentions that read as "
                   "recommendations; raw_upvotes are the comments' scores whatever they say. Polarity is a "
                   "heuristic; read the examples.")
    return res


# Screenshots ----------------------------------------------------------------------

_OWN_RECT_JS = """((id) => {
  const c = document.querySelector(`shreddit-comment[thingid="t1_${id}"]`);
  if (!c) return null;
  const parts = [...c.querySelectorAll('[slot="commentMeta"], [slot="comment"], shreddit-comment-action-row, [slot="commentAvatar"]')]
    .filter(e => e.closest('shreddit-comment') === c).map(e => e.getBoundingClientRect()).filter(r => r.width > 0 && r.height > 0);
  if (!parts.length) return null;
  const hb = document.querySelector('reddit-header-large, header')?.getBoundingClientRect();
  const top = hb && hb.height > 0 ? Math.max(0, hb.bottom) : 0;
  const x0 = Math.min(...parts.map(r => r.left)) - 6, x1 = Math.max(...parts.map(r => r.right)) + 6;
  const y0 = Math.max(top, Math.min(...parts.map(r => r.top)) - 6), y1 = Math.min(innerHeight, Math.max(...parts.map(r => r.bottom)) + 6);
  return {x: Math.max(0, x0), y: y0, width: Math.min(innerWidth, x1) - Math.max(0, x0), height: Math.max(1, y1 - y0)}; })"""

_IN_VIEW_JS = """(() => [...document.querySelectorAll('shreddit-post, shreddit-comment')].some(e => {
  const r = e.getBoundingClientRect(); return r.bottom > 80 && r.top < innerHeight - 40; }))()"""


def shot(target: str, out: str | None = None, out_dir: str | None = None, full: bool = False,
         at: str | None = None, comment: str | None = None, selector: str | None = None) -> dict:
    """Screenshot of the page content (top bar and left navigation excluded), or of one target
    scrolled into view with the mouse wheel first: `at="post"`, `comment=<id or permalink>` (just
    that comment, without its replies), or a CSS `selector`. `ads_in_view` lists promoted posts or
    ad slots inside the image."""
    full_id = SITE.resolve(target)
    SITE.touch(full_id)
    url = _href(full_id)
    slug = _post_id(url) or "page"
    cid = _comment_id(comment) if comment else None
    if cid:
        slug += f"-{cid}"
    name = f"{date.today().isoformat()}_reddit-{slug}_shot-{time.strftime('%H%M%S')}.{'png' if full else 'jpg'}"
    path = Path(out) if out else Path(out_dir or _SHOTS) / name
    max_width = None if full else 1600
    area = SITE.evaluate(full_id, MAIN_JS)
    if at and at != "post":
        raise Fail("--at takes 'post' (use --comment ID for a comment, or --selector CSS)")
    if cid:
        sel = f'shreddit-comment[thingid="t1_{cid}"]'
        if SITE.rect(full_id, sel) is None:
            raise Fail(f"comment {cid} isn't rendered on this page (it may be collapsed, deleted, or further down "
                       "than Reddit has loaded). Open its permalink with thread or nav first.")
        own_sel = f'shreddit-comment[thingid="t1_{cid}"] [slot="commentMeta"]'
        SITE.scroll_into_view(full_id, own_sel, area=area)
        pause(0.4)
        res = SITE.capture(full_id, path, area_js=f"({_OWN_RECT_JS})({json.dumps(cid)})", max_width=max_width)
        res["target"] = {"comment": cid}
    elif at == "post" or selector:
        sel = selector or "shreddit-post"
        res = SITE.capture(full_id, path, selector=sel, max_width=max_width)
        res["target"] = {"selector": sel} if selector else {"at": "post"}
    else:
        res = SITE.capture(full_id, path, max_width=max_width)
        if not SITE.evaluate(full_id, _IN_VIEW_JS):
            res["warning"] = ("No post or comment is in view (the window may be scrolled to the sidebar or page end). "
                              "Retake with --at post or --comment ID.")
    res.update(path=str(Path(res["path"]).expanduser()), url=url,
               check=("Look at the image before using it. If ads_in_view is not empty, caption the ad as an ad, or "
                      "scroll and retake. Usernames in the thread are visible; that's fine."))
    return res


def run_js(target: str, js: str):
    return SITE.run_js(target, js)


def close(target: str) -> dict:
    return SITE.close(target)


def list_claims() -> list[dict]:
    return SITE.list_claims()


def gc() -> dict:
    return SITE.gc()


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------

def _multi(args: list[str], name: str) -> list[str]:
    vals = []
    while name in args:
        v = opt(args, name)
        if isinstance(v, str):
            vals.append(v)
    return vals


def _dispatch(cmd: str, args: list[str]):
    if cmd == "search":
        url = opt(args, "--url")
        sub, sort, t = opt(args, "--sub"), opt(args, "--sort"), opt(args, "--time")
        typed = False if opt(args, "--no-type", False) else None
        if not args or (len(args) < 2 and not url):
            raise Fail("usage: search ID QUERY [--sub S] [--sort S] [--time T] [--no-type] | search ID --url URL")
        return search(args[0], " ".join(args[1:]) or None, url=url, subreddit=sub, sort=sort, time_filter=t,
                      typed=typed)
    if cmd == "thread":
        n = int(opt(args, "--max", 15))
        rep = opt(args, "--replies", 0)
        rep = 3 if rep is True else int(rep)
        exp = opt(args, "--expand")
        exp = None if exp is None else (1 if exp is True else int(exp))
        brands = _multi(args, "--brand")
        if len(args) != 2:
            raise Fail("usage: thread ID URL [--max N] [--replies [N]] [--expand N] [--brand NAME ...]")
        return thread(args[0], args[1], n, replies=rep, expand=exp, brands=brands)
    if cmd == "back":
        if len(args) != 1:
            raise Fail("usage: back ID")
        return back(args[0])
    if cmd == "tally":
        alias = "; ".join(_multi(args, "--alias")) or None
        exclude_op = bool(opt(args, "--exclude-op", False))
        if len(args) < 2:
            raise Fail('usage: tally ID NAME [NAME ...] [--alias "Name=A,B; Other=C"] [--exclude-op]')
        return tally(args[0], args[1:], aliases=alias, exclude_op=exclude_op)
    if cmd == "shot":
        out, out_dir, full = opt(args, "--out"), opt(args, "--out-dir"), bool(opt(args, "--full", False))
        at, comment, selector = opt(args, "--at"), opt(args, "--comment"), opt(args, "--selector")
        if len(args) != 1:
            raise Fail("usage: shot ID [--at post | --comment ID | --selector CSS] [--full] [--out-dir DIR | --out PATH]")
        return shot(args[0], out, out_dir, full, at=at, comment=comment, selector=selector)
    return common_cli(SITE, cmd, args)


if __name__ == "__main__":
    sys.exit(run_cli(__doc__, _dispatch, sys.argv))
