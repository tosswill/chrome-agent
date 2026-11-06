"""Reddit research helper (integrations/claude/skills/reddit-research/scripts/reddit_tab.py).
Pure functions only: no browser."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "reddit-research" / "scripts"))
import reddit_tab as R  # noqa: E402


# --- moderators (R6, R11) ------------------------------------------------------

def test_clean_moderators_drops_junk_and_bots():
    raw = [{"name": "subscriber-count"}, {"name": "167488462789590057"}, {"name": "Prusa3D", "flair": None},
           {"name": "josefprusa", "flair": "Prusa"}, {"name": "VideographyBot", "flair": "/r/videography Bot"},
           {"name": "AutoModerator"}, {"name": "comment-nuke"}, {"name": "purge-user"}, {"name": "automod-sync"},
           {"name": "Prusa3D"}, {"name": "a"}, {"name": "u/realhuman_42", "flair": "  "}]
    mods, skipped = R.clean_moderators(raw)
    assert [m["name"] for m in mods] == ["Prusa3D", "josefprusa", "realhuman_42"]
    assert mods[2]["flair"] is None
    assert set(skipped) == {"VideographyBot", "AutoModerator", "comment-nuke", "purge-user", "automod-sync"}


@pytest.mark.parametrize("name,flair,expected", [
    ("VideographyBot", None, True), ("save-video-bot", None, True), ("repostsleuthbot", None, True),
    ("modmail-userinfo", None, True), ("Main-Promotion7272", None, False), ("Talbot", None, False),
    ("abbot", "Bot", True), ("abbot", None, False), ("e3dsupport", None, False),
])
def test_utility_accounts(name, flair, expected):
    assert R.is_utility_account(name, flair) is expected


# --- brands (R5, R11, R12) ------------------------------------------------------

def test_brand_candidates():
    strong, weak = R.brand_candidates("Anycubic Kobra X vs Bambu A1, what's the verdict?", '"Kobra X" (problem OR issue)')
    assert {"anycubic", "kobra", "bambu"} <= strong
    assert "verdict" not in strong | weak and "problem" not in strong
    strong, _ = R.brand_candidates("Dji Mic 3 TROUBLESHOOTING")
    assert "dji" in strong and "troubleshooting" not in strong and "mic" not in strong
    strong, weak = R.brand_candidates("Which Wireless Lav System for Doc Work?")
    assert not strong and not weak  # a Title Case title of category words names no brand


@pytest.mark.parametrize("sub,title,expected", [
    ("dji", "Dji Mic 3 TROUBLESHOOTING", "dji"),
    ("DJIMic", "Noise cancelling turns itself on", "dji"),
    ("djing", "Best controller?", None),
    ("prusa3d", "Is the CORE One+ worth it", "prusa"),
    ("podcasting", "Best lavalier mic pack? Samson sucks", None),
    ("videography", "Interference / Buzz with Lark Max / DJI Mic 2 at weddings", None),
    ("3Dprinting", "Best printer for functional parts", None),
    ("Xreal", "XREAL One Pro or Viture?", "xreal"),
    ("PixelArt", "My first piece", None),
])
def test_subreddit_brand(sub, title, expected):
    strong, weak = R.brand_candidates(title)
    assert R.subreddit_brand(sub, strong, weak) == expected


@pytest.mark.parametrize("author,flair,text,kw,confidence", [
    ("e3dsupport", None, "Email us at support@e3d-online.com and we'll sort it.", {}, "likely"),
    ("BambuLab_Support", None, "", {"strong": {"bambulab"}}, "likely"),
    ("lifesupport", None, "", {}, "possible"),
    ("someuser", "Prusa team", "", {"strong": {"prusa"}}, "likely"),
    ("someuser", "Prusa MK4S, Core One", "", {"strong": {"prusa"}}, None),  # owned gear, not staff
    ("josefprusa", "Prusa", "", {"sub_brand": "prusa", "mod_names": ["josefprusa"]}, "likely"),
    ("josefprusa", "Prusa", "", {"sub_brand": "prusa"}, None),  # brand flair alone, not a moderator
    ("helpful_user", None, "Just email support@bambulab.com, they replaced mine.", {"strong": {"bambu"}}, None),
    ("rep_account", None, "Please contact us at help@acme-audio.com", {}, "likely"),
    ("[deleted]", None, "", {}, None),
])
def test_brand_account(author, flair, text, kw, confidence):
    got = R.brand_account(author, flair, text, **kw)
    assert (got or {}).get("confidence") == confidence


def test_company_emails():
    assert R.company_emails("mail me@gmail.com or support@e3d-online.com, Support@E3D-Online.com") == ["e3d-online.com"]


# --- promo_bias -------------------------------------------------------------------

PRUSA_MODS = [{"name": "Prusa3D", "flair": None}, {"name": "josefprusa", "flair": "Prusa"},
              {"name": "PrusaResearch_Team", "flair": None}, {"name": "random_mod", "flair": None},
              {"name": "subscriber-count", "flair": None}, {"name": "AutoModerator", "flair": None}]


def _thread(**kw):
    base = {"title": "Core One or MK4S for functional parts?", "subreddit": "r/prusa3d", "url": "https://x/1",
            "author": "op_user", "body": "", "flair": None, "moderators": PRUSA_MODS, "comments": [], "sidebar": True}
    base.update(kw)
    return base


def test_promo_bias_brand_moderators_deterministic():
    a = R.promo_bias(_thread())
    b = R.promo_bias(_thread(title="Totally different title about nozzles"))
    assert a["brand_moderators"] == b["brand_moderators"] == ["josefprusa", "Prusa3D", "PrusaResearch_Team"]
    ev = next(e for e in a["evidence"] if e["signal"] == "brand_moderated_subreddit")
    for n in a["brand_moderators"]:
        assert f"u/{n}" in ev["text"]  # evidence names every one counted
    assert "3 brand account" in ev["text"]


def test_promo_bias_utility_bot_not_brand_moderator():
    res = R.promo_bias(_thread(title="Interference / Buzz with Lark Max / DJI Mic 2 at weddings",
                               subreddit="r/videography",
                               moderators=[{"name": "VideographyBot", "flair": "/r/videography Bot"},
                                           {"name": "someone", "flair": None}]))
    assert res["brand_moderators"] == []
    assert res["score"] <= 0.1
    assert not any(e["signal"].startswith("brand_") for e in res["evidence"])


def test_promo_bias_category_sub_not_brand_subreddit():
    res = R.promo_bias(_thread(title="Best lavalier mic pack? Samson sucks", subreddit="r/podcasting", moderators=[]))
    assert not any(e["signal"] == "brand_subreddit" for e in res["evidence"])
    res = R.promo_bias(_thread(title="Dji Mic 3 TROUBLESHOOTING", subreddit="r/dji", moderators=[]))
    assert any(e["signal"] == "brand_subreddit" for e in res["evidence"])


def test_promo_bias_brand_comment_account():
    c = {"author": "e3dsupport", "text": "Sorry about that, email support@e3d-online.com", "score": 3,
         "link": "https://x/1/c1", "flair": None, "badges": []}
    res = R.promo_bias(_thread(subreddit="r/3Dprinting", moderators=[], comments=[c]))
    assert [b["author"] for b in res["brand_accounts"]] == ["e3dsupport"]
    assert any(e["signal"] == "brand_accounts_in_thread" for e in res["evidence"])


def test_promo_bias_review_unit_false_positives_stay_low():
    c1 = {"author": "u1", "text": "Support sent me a new one for free after the hotend died.", "link": "l1"}
    c2 = {"author": "u2", "text": "The files provided by prusa are great.", "link": "l2"}
    res = R.promo_bias(_thread(subreddit="r/3Dprinting", moderators=[], comments=[c1, c2]))
    assert not any(e["signal"] == "review_unit" for e in res["evidence"])


def test_promo_bias_narrow_window_note():
    res = R.promo_bias(_thread(moderators=[], sidebar=False))
    assert res["confidence"] == "low" and "sidebar" in res["note"]


# --- comment selection (R8, R13) ---------------------------------------------------

def _c(i, depth, author="u", text="hello", **kw):
    return {"id": f"c{i}", "depth": depth, "author": author, "text": text, "top": i * 100, "bottom": i * 100 + 90,
            "score": 1, **kw}


def test_select_comments_skips_deleted_and_automod_without_using_slots():
    cs = [_c(1, 0, "AutoModerator", "", stickied=True), _c(2, 0, "[deleted]", "", deleted=True),
          _c(3, 1, "op", "OP reply under a deleted comment"), _c(4, 1, "other", "a reply"),
          _c(5, 0, "a"), _c(6, 0, "b"), _c(7, 0, "c")]
    out, counts = R.select_comments(cs, "op", max_comments=2)
    assert [c["id"] for c in out] == ["c3", "c5", "c6"]
    assert counts["skipped_deleted"] == 1 and counts["skipped_automod"] == 1
    assert counts["comments_rendered"] == 7 and counts["comments_returned"] == 3


def test_select_comments_replies_from_anyone():
    cs = [_c(1, 0), _c(2, 1, "x"), _c(3, 1, "y"), _c(4, 1, "z"), _c(5, 2, "deep"), _c(6, 1, "op")]
    out, _ = R.select_comments(cs, "op", max_comments=5, replies=2)
    assert [c["id"] for c in out] == ["c1", "c2", "c3", "c6"]
    out, _ = R.select_comments(cs, "op", max_comments=5)
    assert [c["id"] for c in out] == ["c1", "c6"]


def test_select_comments_only_what_was_scrolled_into_view():
    cs = [_c(i, 0) for i in range(1, 8)]
    out, counts = R.select_comments(cs, "op", max_comments=10, read_bottom=450)
    assert [c["id"] for c in out] == ["c1", "c2", "c3", "c4"]
    assert counts["not_scrolled_into_view"] == 3


def test_select_comments_removed_text_is_deleted():
    cs = [_c(1, 0, "x", "[removed]"), _c(2, 0, "y", "real")]
    out, counts = R.select_comments(cs, None, 5)
    assert [c["id"] for c in out] == ["c2"] and counts["skipped_deleted"] == 1


# --- tally wiring (S1) ---------------------------------------------------------------

def _seen():
    posts = [{"id": "p1", "url": "https://www.reddit.com/r/a/comments/p1/", "title": "Core One+ or MK4S?",
              "author": "asker", "score": 5, "sub": "a"},
             {"id": "p2", "url": "https://www.reddit.com/r/b/comments/p2/", "title": "DJI Mic 3 issue", "author": "z",
              "score": 1, "sub": "b", "snippet": "the Core One is great",
              "snippet_link": "https://www.reddit.com/r/b/comments/p2/comment/s1/"}]
    seen = R.TL.upsert([], R.search_records(posts))
    data = {"id": "p1", "url": "https://www.reddit.com/r/a/comments/p1/", "title": "Core One+ or MK4S?",
            "author": "asker", "body": "Which should I buy?", "score": 5, "subreddit": "r/a", "links": []}
    comments = [{"id": "k1", "author": "fan", "text": "Get the Core One+, it's excellent.", "score": 40,
                 "link": "https://www.reddit.com/r/a/comments/p1/comment/k1/"},
                {"id": "k2", "author": "e3dsupport", "text": "The Core One+ works great with our hotend.", "score": 2,
                 "link": "https://www.reddit.com/r/a/comments/p1/comment/k2/"},
                {"id": "k3", "author": "grump", "text": "My MK4S had constant clogs, avoid.", "score": 12,
                 "link": "https://www.reddit.com/r/a/comments/p1/comment/k3/"}]
    return R.TL.upsert(seen, R.thread_records(data, comments, {"e3dsupport"}))


def test_records_merge_search_into_post():
    seen = _seen()
    p1 = [r for r in seen if r["id"] == "p1"]
    assert len(p1) == 1 and p1[0]["via"] == "post"
    assert {r["id"] for r in seen} == {"p1", "p2", "t1_s1", "t1_k1", "t1_k2", "t1_k3"}


def test_tally_wiring():
    res = R.TL.tally(_seen(), ["Core One+", "Core One", "MK4S"])
    rows = {r["term"]: r for r in res["tally"]}
    c1p = rows["Core One+"]
    assert c1p["users"] == 3 and c1p["independent_users"] == 1  # asker's question, brand account excluded
    assert c1p["excluded"].get("brand_account") == 1
    assert c1p["recommend_upvotes"] == 40
    assert rows["Core One"]["items"] == 0 and rows["Core One"]["search_only_threads"] == 1
    assert rows["MK4S"]["complain_upvotes"] == 12


def test_cli_alias_and_exclude_op(monkeypatch):
    got = {}
    monkeypatch.setattr(R, "tally", lambda t, names, aliases=None, exclude_op=False: got.update(
        t=t, names=names, aliases=aliases, exclude_op=exclude_op) or {})
    R._dispatch("tally", ["ID", "Core One", "MK4S", "--alias", "Core One=C1,Core 1", "--alias", "MK4S=MK 4S",
                          "--exclude-op"])
    assert got == {"t": "ID", "names": ["Core One", "MK4S"], "aliases": "Core One=C1,Core 1; MK4S=MK 4S",
                   "exclude_op": True}


# --- small helpers -------------------------------------------------------------------

@pytest.mark.parametrize("ref,expected", [
    ("omy2ksd", "omy2ksd"), ("t1_omy2ksd", "omy2ksd"),
    ("https://www.reddit.com/r/anycubic/comments/1titgwb/comment/omy2ksd/", "omy2ksd"),
])
def test_comment_id(ref, expected):
    assert R._comment_id(ref) == expected


def test_search_url_defaults():
    u = R.search_url('"P2S" (problem OR issue)')
    assert "sort=relevance" in u and "&t=" not in u and "%22P2S%22%20%28problem%20OR%20issue%29" in u
    assert "t=year" in R.search_url("x", "BambuLab", "new", "year") and "/r/BambuLab/search/" in R.search_url("x", "BambuLab")
    with pytest.raises(R.Fail):
        R.search_url("x", time_filter="decade")
