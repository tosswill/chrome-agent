#!/usr/bin/env python3
"""Signal metrics for a research run, computed from its saved raw results (no browser).

    research_signals.py RAW_DIR [--entity "MS-01=MS-01,MS01"]... [--brand minisforum]... [--md FILE] [--json FILE]

RAW_DIR is the run's raw folder (raw/<site>/ subfolders written with RESEARCH_RAW_DIR set). Reads every saved
result, pulls out the discussion items each site read in full (Reddit thread posts and comments, X posts and
replies, YouTube comments, retail reviews), de-duplicates them, and computes the same metrics per site:

    volume      items, distinct authors, share of items from the top 3 authors
    recency     oldest / newest, median age (days), share from the last 90 days
    engagement  median and 90th-percentile score (upvotes, likes, helpful votes), share with none
    depth       median length (chars), share under 60 chars, share of items that got a reply
    promotion   ads, brand-named accounts (--brand, X's name_matches_query, Reddit brand labels),
                affiliate/referral links, discount codes, giveaway wording
    first-hand  share matching ownership wording ("I bought", "been running mine") - a heuristic
    entities    per --entity: distinct authors mentioning it, mentions, authors with first-hand wording

Counts only: sentiment, credibility and verdicts are the researcher's judgment. Prints a markdown table
(also written with --md) and writes the full numbers as JSON (--json, default RAW_DIR/signals.json).
"""
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

AFFILIATE = re.compile(r"amzn\.to/|amazon\.[a-z.]+/\S*[?&]tag=|geni\.us/|kckb\.me/|howl\.me/|shareasale\.com|awin1\.com|"
                       r"[?&](ref|aff|affiliate|referral|ref_code)=", re.I)
# "use code SAVE10", "promo code: MINIPC", "15% off" (the code itself must look like one: capitals and digits)
DISCOUNT = re.compile(r"(?i:\b(?:discount|coupon|promo)\s+code\b|\buse\s+code\b|\bcode\s*:)\s*['\"]?[A-Z][A-Z0-9]{3,}\b|"
                      r"(?i:\b\d{1,2}\s?% off\b)")
GIVEAWAY = re.compile(r"\bgiveaway\b|\bgiving away\b|\bwin (?:a|one|this)\b", re.I)
FIRST_HAND = re.compile(
    r"\b(?:i|we)(?:'ve| have| had)?\s+(?:bought|buy|own|owned|got|run|running|ran|use|using|used|returned|built|"
    r"installed|replaced|upgraded|tested|switched)\b|\bi(?:'ve| have) (?:had|been running|been using)\b|\bmine\b|"
    r"\bmy (?:\w+ ){0,2}(?:box|unit|node|server|cluster|setup|machine|one)\b", re.I)
_REL = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_DAYS = {"second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30.4, "year": 365}


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"([\d.,]+)\s*([KMB])?", str(v), re.I)
    if not m:
        return None
    try:
        x = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return x * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1)


def _date(v, saved_at: datetime) -> datetime | None:
    if not v:
        return None
    s = str(v)
    m = _REL.search(s)
    if m:
        return saved_at - timedelta(days=int(m.group(1)) * _DAYS[m.group(2).lower()])
    for fmt in (None, "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00")) if fmt is None else datetime.strptime(s.strip()[:20], fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _item(site, key, author, text, date, score, replied=None, promoted=False, brand=False, url=None):
    return {"site": site, "key": key, "author": (author or "").strip() or None, "text": text or "", "date": date,
            "score": _num(score), "replied": replied, "promoted": bool(promoted), "brand": bool(brand), "url": url}


def _x_items(res, saved):
    posts = []
    for k in ("posts", "thread_above", "thread_below", "replies"):
        posts += res.get(k) or []
    if isinstance(res.get("post"), dict):
        posts.append(res["post"])
    out = []
    for p in posts:
        if p.get("grok"):
            continue  # an automated answer, not a person
        out.append(_item("x", p.get("url") or (p.get("handle"), (p.get("text") or "")[:80]), p.get("handle"), p.get("text"),
                         _date(p.get("date"), saved), p.get("likes"), (p.get("replies") or 0) > 0,
                         p.get("promoted"), bool(p.get("name_matches_query")), p.get("url")))
    return out


def _reddit_items(res, saved):
    th = res.get("thread")
    if not isinstance(th, dict):
        return []  # search listings are titles only; the threads opened carry the discussion
    parents = {c.get("parent") for c in th.get("comments") or []}
    brand_op = bool(th.get("brand_affiliate_label"))
    out = [_item("reddit", th.get("url"), th.get("author"), f"{th.get('title') or ''}\n{th.get('body') or ''}",
                 _date(th.get("created"), saved), th.get("score"), bool(th.get("comments_total")), False, brand_op,
                 th.get("url"))]
    for c in th.get("comments") or []:
        badges = " ".join(map(str, (c.get("badges") or []) + (c.get("roles") or []))).lower()
        out.append(_item("reddit", c.get("link") or c.get("id"), c.get("author"), c.get("text"),
                         _date(c.get("created"), saved), c.get("score"), c.get("id") in parents, False,
                         "brand" in badges or "affiliate" in badges, c.get("link")))
    return out


def _youtube_items(res, saved):
    out = []
    for c in res.get("comments") or []:
        if not isinstance(c, dict):
            continue
        out.append(_item("youtube", c.get("link") or (c.get("author"), (c.get("text") or "")[:80]), c.get("author"),
                         c.get("text"), _date(c.get("age"), saved), c.get("likes"), (_num(c.get("replies")) or 0) > 0,
                         False, False, c.get("link")))
    return out


def _review_items(site, res, saved):
    out = []
    for r in res.get("reviews") or []:
        text = "\n".join(str(r.get(k) or "") for k in ("title", "pros", "cons", "overall", "text", "body"))
        out.append(_item(site, (r.get("author"), text[:80]), r.get("author"), text, _date(r.get("date"), saved),
                         r.get("helpful"), bool(r.get("manufacturer_response"))))
    return out


def load(raw: Path) -> dict[str, list[dict]]:
    """Items per site, de-duplicated (the same post read twice counts once)."""
    sites: dict[str, dict] = {}
    for d in sorted(p for p in raw.iterdir() if p.is_dir()):
        if d.name in ("verify",):
            continue
        seen = sites.setdefault(d.name, {})
        for f in sorted(d.glob("[0-9][0-9][0-9]_*.json")):
            try:
                rec = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            res = rec.get("result") or {}
            saved = _date(rec.get("saved_at"), datetime.now(timezone.utc)) or datetime.now(timezone.utc)
            items = (_x_items(res, saved) if d.name == "x" else _reddit_items(res, saved) if d.name == "reddit"
                     else _youtube_items(res, saved) if d.name == "youtube" else _review_items(d.name, res, saved))
            for it in items:
                seen.setdefault(json.dumps(it["key"], default=str), it)
    return {k: list(v.values()) for k, v in sites.items() if v}


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def _share(n, d):
    return round(n / d, 2) if d else None


def metrics(items: list[dict], brands: list[str], now: datetime) -> dict:
    n = len(items)
    authors = [i["author"] for i in items if i["author"]]
    counts: dict[str, int] = {}
    for a in authors:
        counts[a] = counts.get(a, 0) + 1
    top3 = sum(sorted(counts.values(), reverse=True)[:3])
    dates = sorted(i["date"] for i in items if i["date"])
    ages = [(now - d).days for d in dates]
    scores = [i["score"] for i in items if i["score"] is not None]
    lens = [len(i["text"].strip()) for i in items]
    replied = [i for i in items if i["replied"] is not None]
    brand_re = re.compile("|".join(re.escape(b) for b in brands), re.I) if brands else None
    is_brand = [i["brand"] or bool(brand_re and i["author"] and brand_re.search(i["author"])) for i in items]
    return {
        "items": n, "authors": len(counts), "top3_author_share": _share(top3, n),
        "oldest": dates[0].date().isoformat() if dates else None, "newest": dates[-1].date().isoformat() if dates else None,
        "median_age_days": int(statistics.median(ages)) if ages else None,
        "last_90d_share": _share(sum(1 for a in ages if a <= 90), len(ages)),
        "score_median": _pct(scores, 0.5), "score_p90": _pct(scores, 0.9),
        "no_engagement_share": _share(sum(1 for s in scores if s <= 0), len(scores)),
        "median_chars": int(statistics.median(lens)) if lens else None,
        "short_share": _share(sum(1 for x in lens if x < 60), n),
        "replied_share": _share(sum(1 for i in replied if i["replied"]), len(replied)),
        "promoted": sum(1 for i in items if i["promoted"]),
        "brand_named_share": _share(sum(is_brand), n),
        "affiliate_links": sum(1 for i in items if AFFILIATE.search(i["text"])),
        "discount_codes": sum(1 for i in items if DISCOUNT.search(i["text"])),
        "giveaways": sum(1 for i in items if GIVEAWAY.search(i["text"])),
        "first_hand_share": _share(sum(1 for i in items if FIRST_HAND.search(i["text"])), n),
    }


def entities(items: list[dict], specs: dict[str, list[str]]) -> dict:
    out = {}
    for name, aliases in specs.items():
        rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in aliases) + r")(?![\w-])", re.I)
        hits = [i for i in items if rx.search(i["text"])]
        out[name] = {"authors": len({i["author"] for i in hits if i["author"]}), "mentions": len(hits),
                     "first_hand_authors": len({i["author"] for i in hits if i["author"] and FIRST_HAND.search(i["text"])})}
    return out


ROWS = [("items", "Items read"), ("authors", "Distinct authors"), ("top3_author_share", "Top-3 author share"),
        ("oldest", "Oldest"), ("newest", "Newest"), ("median_age_days", "Median age (days)"),
        ("last_90d_share", "Last 90 days"), ("score_median", "Score median"), ("score_p90", "Score p90"),
        ("no_engagement_share", "No engagement"), ("median_chars", "Median length (chars)"),
        ("short_share", "Under 60 chars"), ("replied_share", "Got a reply"), ("promoted", "Ads"),
        ("brand_named_share", "Brand-named accounts"), ("affiliate_links", "Affiliate links"),
        ("discount_codes", "Discount codes"), ("giveaways", "Giveaways"), ("first_hand_share", "First-hand (heuristic)")]


def _fmt(v, key):
    if v is None:
        return "—"
    if key.endswith("_share") or key in ("last_90d_share", "no_engagement_share"):
        return f"{round(v * 100)}%"
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def markdown(result: dict) -> str:
    sites = list(result["sites"])
    lines = ["| Metric | " + " | ".join(sites) + " |", "|---|" + "---|" * len(sites)]
    for key, label in ROWS:
        lines.append(f"| {label} | " + " | ".join(_fmt(result["sites"][s]["metrics"].get(key), key) for s in sites) + " |")
    ents = result.get("entities") or []
    if ents:
        lines += ["", "| Entity (authors / first-hand) | " + " | ".join(sites) + " |", "|---|" + "---|" * len(sites)]
        for e in ents:
            cells = [f"{result['sites'][s]['entities'][e]['authors']} / {result['sites'][s]['entities'][e]['first_hand_authors']}"
                     for s in sites]
            lines.append(f"| {e} | " + " | ".join(cells) + " |")
    lines += ["", "Counts from the saved raw results; first-hand is a wording heuristic. Scores aren't comparable "
              "across sites (upvotes vs likes vs helpful votes)."]
    return "\n".join(lines)


def run(raw: Path, ents: dict[str, list[str]], brands: list[str]) -> dict:
    now = datetime.now(timezone.utc)
    data = load(raw)
    return {"raw": str(raw), "computed_at": now.isoformat(timespec="seconds"), "entities": list(ents),
            "brands": brands, "sites": {s: {"metrics": metrics(items, brands, now), "entities": entities(items, ents)}
                                        for s, items in data.items()}}


def _parse_entity(v: str) -> tuple[str, list[str]]:
    name, _, al = v.partition("=")
    return name.strip(), [a.strip() for a in (al or name).split(",") if a.strip()]


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if len(argv) > 1 else 2
    raw, ents, brands, md, js = Path(argv[1]).expanduser(), {}, [], None, None
    i = 2
    while i < len(argv):
        flag, val = argv[i], argv[i + 1] if i + 1 < len(argv) else None
        if val is None:
            print(f"missing value for {flag}", file=sys.stderr)
            return 2
        if flag == "--entity":
            k, v = _parse_entity(val)
            ents[k] = v
        elif flag == "--brand":
            brands.append(val)
        elif flag == "--md":
            md = Path(val)
        elif flag == "--json":
            js = Path(val)
        else:
            print(f"unknown option {flag}", file=sys.stderr)
            return 2
        i += 2
    if not raw.is_dir():
        print(f"no such folder: {raw}", file=sys.stderr)
        return 2
    result = run(raw, ents, brands)
    (js or raw / "signals.json").write_text(json.dumps(result, indent=1))
    text = markdown(result)
    if md:
        md.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
