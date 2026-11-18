#!/usr/bin/env python3
"""Read-only Product Hunt research through the official GraphQL API v2 (developer token, no browser).

The API has no keyword search: find launches by topic, date range, ranking or the product's own website URL,
or pass a slug / producthunt.com link found elsewhere (web search, Reddit, HN). Only fixed read queries are
sent (no mutations, no free-form GraphQL). User names are redacted by the API for anyone but the token owner.

CLI (each command prints JSON):

    ph_api.py posts [--topic SLUG] [--url WEBSITE] [--after YYYY-MM-DD] [--before YYYY-MM-DD] [--featured]
                    [--order ranking|newest|votes|featured] [--max N] [--cursor C]
                       launches matching the filters (votes, comments, rank, rating, tagline, topics)
    ph_api.py post SLUG|URL [--comments N] [--comment-order votes|newest] [--replies N]
                       one launch with makers, topics, links, review rating and its comment threads
    ph_api.py topics QUERY [--max N]             topics whose name matches (slug, followers, posts count)
    ph_api.py topic SLUG                         one topic

Token: ~/.config/chrome-agent-research/producthunt_token (PH_TOKEN_FILE), read here and never printed.
Environment (PH_RESEARCH_*): GAP (1.0 s between calls), MAX_CALLS (150 per run folder), TIMEOUT (30 s).
Product Hunt allows 6,250 complexity points per 15 minutes.
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
import research_core as core  # noqa: E402
from research_core import Fail, opt  # noqa: E402

SITE = "producthunt"
ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
TOKEN_FILE = Path(os.environ.get("PH_TOKEN_FILE", "~/.config/chrome-agent-research/producthunt_token")).expanduser()
GAP = float(os.environ.get("PH_RESEARCH_GAP", "1.0"))
MAX_CALLS = int(os.environ.get("PH_RESEARCH_MAX_CALLS", "150"))
TIMEOUT = float(os.environ.get("PH_RESEARCH_TIMEOUT", "30"))
STATE = Path(os.environ.get("PH_RESEARCH_STATE", "~/.local/state/chrome-agent-research/producthunt")).expanduser()
UA = "research-helper/1.0"
ORDERS = {"ranking": "RANKING", "newest": "NEWEST", "votes": "VOTES", "featured": "FEATURED_AT"}
COMMENT_ORDERS = {"votes": "VOTES_COUNT", "newest": "NEWEST"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")

POST_FIELDS = """id name slug tagline url website votesCount commentsCount reviewsCount reviewsRating dailyRank
  monthlyRank createdAt featuredAt topics(first: 6) { nodes { slug name } }"""


class Stop(Exception):
    pass


def _count_call() -> None:
    d = core.raw_dir(SITE)
    if not d:
        return
    f = d / ".calls"
    try:
        n = int(f.read_text().strip() or 0)
    except (OSError, ValueError):
        n = 0
    if n >= MAX_CALLS:
        raise Stop(f"API call budget for this run is spent ({MAX_CALLS}; PH_RESEARCH_MAX_CALLS raises it). "
                   "Tell the user rather than continuing.")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(n + 1))


def _pace() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = STATE / "last_call"
    try:
        wait = float(stamp.read_text()) + GAP - time.time()
    except (OSError, ValueError):
        wait = 0
    if wait > 0:
        time.sleep(wait)
    stamp.write_text(str(time.time()))


def _token() -> str:
    try:
        tok = TOKEN_FILE.read_text().strip()
    except OSError:
        raise Stop(f"no Product Hunt developer token at {TOKEN_FILE}; ask the user to create one")
    if not tok:
        raise Stop(f"the Product Hunt token file {TOKEN_FILE} is empty; ask the user")
    return tok


def gql(query: str, variables: dict) -> tuple[dict, dict]:
    """Send one fixed read query. Returns (data, rate-limit info)."""
    head = query.lstrip().split("{", 1)[0].lower()
    if not head.startswith("query") or "mutation" in query.lower():
        raise Fail("refusing to send anything but a read query")
    _count_call()
    _pace()
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": f"Bearer {_token()}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw, headers = r.read(20_000_000), r.headers
    except urllib.error.HTTPError as e:
        if e.code == 429:
            reset = e.headers.get("X-Rate-Limit-Reset") if e.headers else None
            raise Stop(f"Product Hunt rate limit reached (resets in {reset or '?'} s); stop and tell the user")
        if e.code == 401:
            raise Stop("Product Hunt rejected the token (401); ask the user to create a new developer token")
        raise Fail(f"Product Hunt API error {e.code}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise Fail(f"Product Hunt API unreachable ({e})")
    try:
        out = json.loads(raw)
    except ValueError:
        raise Fail("Product Hunt API returned non-JSON")
    rate = {"remaining": headers.get("X-Rate-Limit-Remaining"), "limit": headers.get("X-Rate-Limit-Limit"),
            "resets_in_s": headers.get("X-Rate-Limit-Reset")}
    if out.get("errors"):
        msg = "; ".join(str(e.get("message")) for e in out["errors"])[:300]
        if "rate" in msg.lower() and "limit" in msg.lower():
            raise Stop(f"Product Hunt rate limit: {msg}")
        raise Fail(f"Product Hunt API: {msg}")
    return out.get("data") or {}, rate


def _day(s: str, end: bool = False) -> str:
    try:
        d = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise Fail(f"dates are YYYY-MM-DD (got {s!r})")
    return (d.replace(hour=23, minute=59, second=59) if end else d).isoformat()


def tidy_url(url: str | None) -> str | None:
    """Drop the utm_* tags the API adds (they name our API application) and keep the rest (e.g. ?comment=)."""
    if not url:
        return url
    u = urllib.parse.urlparse(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(u.query) if not k.startswith("utm_")]
    return urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(q)))


def _post(p: dict) -> dict:
    out = {k: p.get(k) for k in ("id", "name", "slug", "tagline", "url", "website", "votesCount", "commentsCount",
                                 "reviewsCount", "reviewsRating", "dailyRank", "monthlyRank",
                                 "createdAt", "featuredAt")}
    out["topics"] = [t.get("slug") for t in (p.get("topics") or {}).get("nodes") or []]
    out["url"], out["website"] = tidy_url(out.get("url")), tidy_url(out.get("website"))  # website: a PH redirect
    return out


def slug_of(s: str) -> str:
    s = s.strip()
    if s.startswith("http"):
        u = urllib.parse.urlparse(s)
        if not u.netloc.endswith("producthunt.com"):
            raise Fail("pass a Product Hunt link (producthunt.com/posts/<slug> or /products/<slug>) or a slug")
        parts = [p for p in u.path.split("/") if p]
        if "launches" in parts and parts.index("launches") + 1 < len(parts):
            s = parts[parts.index("launches") + 1]  # /products/<product>/launches/<launch slug>
        elif len(parts) >= 2 and parts[0] in ("posts", "products"):
            s = parts[1]
        else:
            raise Fail(f"can't find a slug in {s!r}; links look like producthunt.com/posts/<slug> or "
                       "/products/<product>/launches/<slug>")
    s = s.lower()
    if not SLUG_RE.match(s):
        raise Fail(f"not a Product Hunt slug: {s!r}")
    return s


def _url_variants(url: str) -> list[str]:
    """The same site spelled the other usual ways: with/without www, with/without a trailing slash."""
    u = urllib.parse.urlparse(url)
    hosts = [u.netloc, u.netloc[4:] if u.netloc.startswith("www.") else "www." + u.netloc]
    p = u.path.rstrip("/")
    out = [f"https://{h}{q}" for h in hosts for q in (p, p + "/")]
    return [x for x in dict.fromkeys(out) if x != url]


def posts(topic=None, url=None, after=None, before=None, featured=None, order="ranking", limit=20, cursor=None):
    if order not in ORDERS:
        raise Fail(f"--order takes {', '.join(ORDERS)}")
    if topic and not SLUG_RE.match(topic):
        raise Fail("--topic takes a topic slug (see `topics`)")
    if url and not url.startswith(("http://", "https://")):
        raise Fail("--url takes the product's website URL (https://...)")
    q = f"""query Posts($first: Int, $after: String, $order: PostsOrder, $topic: String, $url: String,
      $postedAfter: DateTime, $postedBefore: DateTime, $featured: Boolean) {{
      posts(first: $first, after: $after, order: $order, topic: $topic, url: $url, postedAfter: $postedAfter,
            postedBefore: $postedBefore, featured: $featured) {{
        totalCount pageInfo {{ hasNextPage endCursor }} nodes {{ {POST_FIELDS} }} }} }}"""
    v = {"first": limit, "after": cursor, "order": ORDERS[order], "topic": topic, "url": url,
         "postedAfter": _day(after) if after else None, "postedBefore": _day(before, end=True) if before else None,
         "featured": featured}
    tried = [url] if url else []
    data, rate = gql(q, {k: x for k, x in v.items() if x is not None})
    conn = data.get("posts") or {}
    if url and not conn.get("nodes"):  # the filter is an exact match: try the usual spellings of the same site
        for alt in _url_variants(url)[:3]:
            v["url"] = alt
            tried.append(alt)
            data, rate = gql(q, {k: x for k, x in v.items() if x is not None})
            conn = data.get("posts") or {}
            if conn.get("nodes"):
                break
    note = None if after else "Without --after the API only returns launches from the last month."
    return {"filters": {k: x for k, x in v.items() if x is not None and k not in ("first", "after")},
            "urls_tried": tried or None,
            "total": conn.get("totalCount"), "next_cursor": (conn.get("pageInfo") or {}).get("endCursor")
            if (conn.get("pageInfo") or {}).get("hasNextPage") else None,
            "posts": [_post(p) for p in conn.get("nodes") or []], "note": note, "rate": rate,
            "page": {"url": f"https://www.producthunt.com/topics/{topic}" if topic else "https://www.producthunt.com/"}}


def clean(text: str | None) -> str:
    """Comment HTML as plain text, links kept as URLs."""
    if not text:
        return ""
    t = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>.*?</a>', lambda m: html.unescape(m.group(1)), text, flags=re.S)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"</p>\s*<p>", "\n\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def _comment(c: dict) -> dict:
    return {"id": c.get("id"), "created": c.get("createdAt"), "votes": c.get("votesCount"),
            "text": clean(c.get("body")), "link": tidy_url(c.get("url"))}


def post(slug_or_url: str, comments: int = 50, comment_order: str = "votes", replies: int = 10) -> dict:
    slug = slug_of(slug_or_url)
    if comment_order not in COMMENT_ORDERS:
        raise Fail(f"--comment-order takes {', '.join(COMMENT_ORDERS)}")
    q = f"""query Post($slug: String, $n: Int, $order: CommentsOrder, $r: Int) {{
      post(slug: $slug) {{ {POST_FIELDS} description
        makers {{ id }} productLinks {{ type url }}
        comments(first: $n, order: $order) {{ totalCount nodes {{ id body createdAt votesCount url
          replies(first: $r, order: NEWEST) {{ totalCount nodes {{ id body createdAt votesCount url }} }}
        }} }} }} }}"""
    data, rate = gql(q, {"slug": slug, "n": comments, "order": COMMENT_ORDERS[comment_order], "r": replies})
    p = data.get("post")
    if not p:
        raise Fail(f"no Product Hunt post with slug {slug!r}")
    makers = p.get("makers") or []
    threads, n_comments = [], 0
    for c in (p.get("comments") or {}).get("nodes") or []:
        top = _comment(c)
        top["depth"] = 1
        rs = (c.get("replies") or {})
        top["replies_total"] = rs.get("totalCount")
        threads.append(top)
        n_comments += 1
        for r in rs.get("nodes") or []:
            rr = _comment(r)
            rr.update(depth=2, parent=c.get("id"))
            threads.append(rr)
            n_comments += 1
    out = _post(p)
    out.update(description=p.get("description"), makers_count=len(makers),
               product_links=[{"type": x.get("type"), "url": x.get("url")} for x in p.get("productLinks") or []],
               comments_total=(p.get("comments") or {}).get("totalCount"), comments=threads,
               comments_returned=n_comments,
               note="The API redacts every commenter (name and id), so it can't tell makers' replies from "
                    "users'. The first comment is usually the launch pitch; replies that thank people or answer "
                    "questions about the product are usually the team. Weigh comment votes and specifics instead.",
               rate=rate, page={"url": tidy_url(p.get("url"))})
    return out


def topics(query: str, limit: int = 20) -> dict:
    q = """query Topics($q: String, $n: Int) { topics(query: $q, first: $n, order: FOLLOWERS_COUNT) {
      nodes { slug name description followersCount postsCount } } }"""
    data, rate = gql(q, {"q": query, "n": limit})
    return {"query": query, "topics": (data.get("topics") or {}).get("nodes") or [], "rate": rate,
            "page": {"url": "https://www.producthunt.com/topics"}}


def topic(slug: str) -> dict:
    if not SLUG_RE.match(slug):
        raise Fail("expected a topic slug")
    q = """query Topic($slug: String) { topic(slug: $slug) { slug name description followersCount postsCount url } }"""
    data, rate = gql(q, {"slug": slug})
    t = data.get("topic")
    if not t:
        raise Fail(f"no topic {slug!r}")
    return {**t, "rate": rate, "page": {"url": t.get("url")}}


def _int(v, name):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise Fail(f"{name} needs a number")


def _dispatch(cmd: str, args: list[str]):
    args = list(args[1:])  # args[0] is the "api" placeholder (see main)
    try:
        if cmd == "posts":
            kw = dict(topic=opt(args, "--topic"), url=opt(args, "--url"), after=opt(args, "--after"),
                      before=opt(args, "--before"), featured=True if opt(args, "--featured") is True else None,
                      order=opt(args, "--order", "ranking"),
                      limit=max(1, min(_int(opt(args, "--max", 20), "--max"), 50)), cursor=opt(args, "--cursor"))
            if args:
                raise Fail(f"unexpected arguments {args}: the API has no keyword search; use --topic or --url")
            return posts(**kw)
        if cmd == "post" and args:
            n = max(1, min(_int(opt(args, "--comments", 50), "--comments"), 100))
            r = max(0, min(_int(opt(args, "--replies", 10), "--replies"), 20))
            return post(args[0], n, opt(args, "--comment-order", "votes"), r)
        if cmd == "topics" and args:
            n = max(1, min(_int(opt(args, "--max", 20), "--max"), 50))
            return topics(" ".join(args), n)
        if cmd == "topic" and args:
            return topic(args[0])
    except Stop as e:
        return {"stop": str(e)}
    return NotImplemented


def main(argv: list[str]) -> int:
    core._LOG.site = SITE
    if len(argv) >= 2 and argv[1] not in ("-h", "--help", "help"):
        argv = [argv[0], argv[1], "api", *argv[2:]]
    return core.run_cli(__doc__, _dispatch, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
