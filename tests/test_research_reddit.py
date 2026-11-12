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
    monkeypatch.setattr(R, "tally", lambda t, names, aliases=None, exclude_op=False, near=None: got.update(
        t=t, names=names, aliases=aliases, exclude_op=exclude_op, **({"near": near} if near else {})) or {})
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


# --- sign-in check: an unrendered header is not "signed out" -----------------------------------

@pytest.mark.parametrize("logged_in,cookie,stops", [
    (True, None, False),    # account menu found
    (None, True, False),    # header not rendered yet, valid session cookie
    (False, True, False),   # log-in button shown, but the session cookie is valid
    (None, None, False),    # can't tell either way: don't stop, flag it
    (False, False, True),   # log-in button and no session cookie: really signed out
    (None, False, True),    # no header and no session cookie
])
def test_stop_reason_sign_in(monkeypatch, logged_in, cookie, stops):
    monkeypatch.setattr(R, "_session_cookie_valid", lambda site: cookie)
    state = {"loggedIn": logged_in, "blocked": False}
    reason = R._stop_reason(state, R.SITE)
    assert bool(reason) is stops
    if not stops and logged_in is not True:
        assert "login_check" in state


def test_stop_reason_block_wins(monkeypatch):
    monkeypatch.setattr(R, "_session_cookie_valid", lambda site: True)
    assert "block" in R._stop_reason({"loggedIn": True, "blocked": True}, R.SITE)



# --- 2026-11-07 open items --------------------------------------------------------

def test_brand_tokens_keep_multiword_names_and_skip_category_nouns():
    strong, weak = R.brand_candidates("Highly recommend the Bissell Big Green carpet cleaning machine",
                                      "Big Green carpet cleaner worth it")
    assert "biggreen" in strong and "big" not in strong and "green" not in strong
    assert "carpet" not in strong | weak
    strong, _ = R.brand_candidates("x", '"Bissell Big Green" broke')
    assert strong == {"bissell"}
    strong, _ = R.brand_candidates("x", '"EcoFlow Delta 3" problems')
    assert strong == {"ecoflow"}


def test_category_subreddits_are_not_brand_subreddits():
    strong, weak = R.brand_candidates("Looking for a Carpet Cleaner – Bissell vs. Hoover vs. Shark?",
                                      "Bissell Revolution vs Hoover carpet cleaner")
    assert R.subreddit_brand("CarpetCleaning", strong, weak) is None
    assert R.subreddit_brand("carpetcleaningporn", strong, weak) is None
    # A weak (capitalized title word) token still names a subreddit that is exactly that name.
    assert R.subreddit_brand("Lindhaus", set(), {"lindhaus"}) == "lindhaus"
    assert R.subreddit_brand("Ecoflow", {"ecoflow"}) == "ecoflow"


@pytest.mark.parametrize("flair,sub_brand,is_mod,hit", [
    ("EcoFlow | Ryan", "ecoflow", False, True),
    ("CS_Team_EU", "ecoflow", False, True),
    ("EcoFlow team", None, False, True),
    ("Prusa", None, True, True),
    ("everyone forgets about Lindhaus… team", None, True, False),
    ("Delta 3 owner", "ecoflow", False, False),
    ("EcoFlow Delta 3 | River 2", "ecoflow", False, False),
    ("EcoFlow | Delta", "ecoflow", False, True),  # one plain word after the brand reads as a name
    ("CS_Team_EU", None, False, False),  # support flair needs the brand's own subreddit
])
def test_staff_flair(flair, sub_brand, is_mod, hit):
    brands = {"ecoflow", "prusa"} | ({sub_brand} if sub_brand else set())
    assert bool(R.staff_flair(flair, brands, set(), sub_brand=sub_brand, is_mod=is_mod)) is hit


def test_lindhaus_flair_moderator_is_not_a_brand_moderator():
    data = {"subreddit": "r/VacuumCleaners", "title": "Hoover Smartwash problem, is this something I can fix?",
            "url": "https://www.reddit.com/r/VacuumCleaners/comments/1p1mcsl/x/", "body": "", "comments": [],
            "moderators": [{"name": "vacuumsaregreat", "flair": "everyone forgets about Lindhaus… staff"}]}
    pb = R.promo_bias(data, '"Hoover SmartWash" problems')
    assert pb["brand_moderators"] == [] and pb["score"] == 0.1


def test_ecoflow_staff_moderators_found():
    data = {"subreddit": "r/Ecoflow", "title": "Delta 3 problem", "url": "https://www.reddit.com/r/Ecoflow/comments/a/x/",
            "body": "", "comments": [],
            "moderators": [{"name": "hiccpetExpert", "flair": "EcoFlow | Ryan"},
                           {"name": "FondantOk8397", "flair": "CS_Team_EU"}, {"name": "someowner", "flair": "River 2"}]}
    pb = R.promo_bias(data, '"EcoFlow Delta 3" problems')
    assert pb["brand_moderators"] == ["FondantOk8397", "hiccpetExpert"]


def _cm(**kw):
    data = {"author": "op", "url": "https://www.reddit.com/r/x/comments/p1/t/", "body": "", "links": []}
    data.update(kw)
    return {h["signal"]: h for h in R.content_marketing(data, data.pop("comments", []) if "comments" in data else [])}


def test_content_marketing_op_edit_article():
    hits = _cm(body="Quick update mates, someone suggested this very helpful article that lists down 9 most "
                    "effective carpet cleaners.\n\nMy carpets are a war zone.",
               links=["https://wearenthusiast.com/best-carpet-cleaner/"])
    assert "op_edit_points_to_article" in hits and "wearenthusiast.com" in hits["op_edit_points_to_article"]["text"]
    # The same wording without an outside link isn't flagged.
    assert "op_edit_points_to_article" not in _cm(body="Edit: thanks, I read a guide and bought one.")


def test_content_marketing_repeated_links_and_listicle():
    links = ["https://medium.com/@x/top-5-a"] * 5
    assert _cm(links=links)["repeated_external_links"]["weight"] == 0.2
    body = "\n".join(f"{i}. Product {i} is great" for i in range(1, 6))
    assert "repeated_external_links" in _cm(body=body, links=["https://someblog.com/best"])
    # Image or Reddit links never count.
    assert "repeated_external_links" not in _cm(links=["https://i.imgur.com/a.jpg"] * 4)


def test_content_marketing_partner_short_links():
    c = [{"author": "u1", "text": "got mine here", "links": ["https://ebay.io/abc123"], "link": "L1"}]
    assert "partner_short_links" in _cm(comments=c)
    c = [{"author": "u1", "text": "got mine here", "links": ["https://www.ebay.com/itm/123"], "link": "L1"}]
    assert "partner_short_links" not in _cm(comments=c)


def test_content_marketing_dangling_roundup():
    c = [{"author": "u1", "link": "L1", "text": "honestly the Bissell big green is easily the best option imo. you can "
                                                 "find a helpful breakdown of the top carpet cleaners in this thread"},
         {"author": "u2", "link": "L2", "text": "This carpet cleaner use-case roundup gives a few better options to compare."}]
    assert "dangling_roundup_reference" in _cm(comments=c)
    # One neutral mention of "this guide" alone isn't enough.
    assert "dangling_roundup_reference" not in _cm(comments=[{"author": "u3", "link": "L3",
                                                              "text": "I followed this guide and it was fine."}])


def test_content_marketing_spec_sheet_pitch():
    c = [{"author": "u1", "link": "L1", "text": "DREAME N20 is perfect for this situation, 212°F steam kills bacteria and "
                                                 "allergens from pets and kids, auto-mix solution so you don't have to "
                                                 "think about ratios. used it with pets at home"}]
    assert "spec_sheet_pitch" in _cm(comments=c)
    c = [{"author": "u2", "link": "L2", "text": "I have a big green. It did really good getting red wine and pet stains "
                                                 "out, though it is loud and heavy to carry upstairs."}]
    assert "spec_sheet_pitch" not in _cm(comments=c)


def test_content_marketing_self_declared_bias():
    c = [{"author": "pro", "link": "L1", "text": "I'm a bit biased, but I only really use products from a website "
                                                  "called encapstore.com, primarily their Green Dragon."}]
    assert "self_declared_bias" in _cm(comments=c)
    assert "self_declared_bias" not in _cm(comments=[{"author": "u", "link": "L", "text": "I'm biased toward Bissell."}])


def test_off_scope():
    posts = [{"sub": "Ecoflow"}, {"sub": "ecoflow"}, {"sub": "SolarDIY"}, {"sub": None}]
    assert R.off_scope(posts, "r/Ecoflow") == ["SolarDIY"]
    assert R.off_scope([{"sub": "Ecoflow"}], "Ecoflow") == []


def test_read_cap_grows_with_thread(monkeypatch):
    monkeypatch.delenv("REDDIT_TAB_READ_CAP", raising=False)
    monkeypatch.delenv("REDDIT_TAB_READ_CAP_MAX", raising=False)
    short = {"author": "op", "body": "q", "comments": [{"depth": 0, "author": "a", "text": "ok", "top": 1}]}
    assert R.read_cap(short, 15, 0) == 45
    long = {"author": "op", "body": "q" * 500,
            "comments": [{"depth": 0, "author": f"a{i}", "text": "x" * 400, "top": i} for i in range(28)]}
    assert 90 < R.read_cap(long, 30, 0) <= 150
    monkeypatch.setenv("REDDIT_TAB_READ_CAP_MAX", "60")
    assert R.read_cap(long, 30, 0) == 60


def test_typed_search_js_scopes_chip_check_to_search_box():
    assert "document.querySelector('rpl-input-chip')" not in R._SEARCH_JS
    assert "reddit-search-large" in R._CHIP_NOW


def _fake_search_site(monkeypatch, results):
    navs = []
    monkeypatch.setattr(R.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(R.SITE, "ensure_visible", lambda t: None)
    monkeypatch.setattr(R.SITE, "nav", lambda t, u: navs.append(u) or {"url": u, "loads": len(navs)})
    seq = iter(results)
    monkeypatch.setattr(R.SITE, "evaluate", lambda t, js, **k: next(seq))
    monkeypatch.setattr(R, "_update_claim", lambda *a, **k: None)
    monkeypatch.setattr(R, "_seen_add", lambda *a, **k: None)
    monkeypatch.setattr(R, "_time_origin", lambda *a: None)
    monkeypatch.setattr(R, "_href", lambda *a: "https://www.reddit.com/")
    return navs


def test_empty_search_returns_note(monkeypatch):
    navs = _fake_search_site(monkeypatch, [[]])
    res = R.search("T", '"EcoFlow Delta 3" (problem OR issue)', typed=False)
    assert res["posts"] == [] and "Simplify the query" in res["page"]["note"] and len(navs) == 1


def test_typed_sub_search_that_went_site_wide_is_reloaded(monkeypatch):
    navs = _fake_search_site(monkeypatch, [[{"id": "a", "sub": "SolarDIY", "url": "u"}, {"id": "b", "sub": "Ecoflow", "url": "u"}],
                                           [{"id": "c", "sub": "Ecoflow", "url": "u"}]])
    monkeypatch.setattr(R, "_typed_search", lambda *a: ({"loads": 1}, ""))
    res = R.search("T", '"EcoFlow Delta 3" problems', subreddit="Ecoflow")
    assert navs and "/r/Ecoflow/search/" in navs[0]
    assert res["page"]["via"].startswith("url (the typed search wasn't limited")
    assert [p["id"] for p in res["posts"]] == ["c"] and "note" not in res["page"]


def test_affiliate_community_sidebar():
    side = ("Created Aug 8, 2024 Public COMMUNITY BOOKMARKS Power Station Deals R/PORTABLE_POWERSTATION RULES 1 Genuine "
            "Deals & Self-Promotion Allowed DISCLAIMER By using our links to make a purchase, you can help support us as we "
            "may earn a small commission. As an Amazon Associate, we earn from qualifying purchases.")
    assert "commission" in R.affiliate_community(side)
    data = {"subreddit": "r/Portable_PowerStation", "title": "The best portable power stations", "url": "u", "body": "",
            "comments": [], "moderators": [], "sidebar_text": side}
    pb = R.promo_bias(data, "best portable power station for camping")
    assert any(e["signal"] == "affiliate_community" for e in pb["evidence"]) and pb["score"] >= 0.35
    assert R.affiliate_community("Be civil. No spam. Search before posting.") is None


def test_chip_js_ignores_hidden_chips():
    # The chip check only reports a chip with a visible box (a removed chip can linger in the DOM).
    assert "b.width > 0 && b.height > 0" in R.CHIP_JS and "/r\\//" not in R.CHIP_JS


def test_review_unit_ignores_warranty_replacements_and_family_gifts():
    import disclosure as D
    sig = lambda t: [h["signal"] for h in D.scan_text(t, "comment")]  # noqa: E731
    assert "review_unit" not in sig("Our Hatch died after 8 months under warranty. They sent me a new one right away.")
    assert "review_unit" not in sig("My sister gifted us a Hatch for the baby shower.")
    assert "review_unit" in sig("Hatch sent me this to review.")


def test_affiliate_community_ignores_rules_banning_affiliate_links():
    assert R.affiliate_community("Rule 4: No affiliate links or referral codes.") is None
    assert R.affiliate_community("Affiliate links will be removed and repeat offenders banned.") is None
    assert R.affiliate_community("As an Amazon Associate, we earn from qualifying purchases.")


def test_sidebar_rule_lists_banning_affiliates_dont_count():
    assert R.affiliate_community("Rules\n• No Review Blogs, Spam, Link Shorteners, or Affiliate Links\n• Be civil") is None
    assert R.affiliate_community("Prohibited:\nAffiliate links or free/discounted product\nAsking for money") is None
    assert R.affiliate_community("This subreddit may earn a commission from links.") is not None


def test_short_role_suffix_needs_a_separator():
    assert R.brand_account("value-no-mics", None, "", strong={"value"}, weak=set()) is None or \
        R.brand_account("value-no-mics", None, "", strong={"value"}, weak=set()).get("confidence") != "strong"
    hit = R.brand_account("ecoflow_cs", None, "", strong={"ecoflow"}, weak=set())
    assert hit and "cs" in hit["reason"]


def test_time_filter_ignored_is_noted():
    from datetime import date
    posts = [{"created": "2025-12-01"}, {"created": "2026-01-10"}, {"created": "2026-11-01"}]
    assert R.time_outside(posts, "month", date(2026, 11, 7)) == 2
    assert R.time_outside(posts, None) == 0


def test_coordinated_links_and_named_guide_plugs():
    data = {"author": "op", "url": "https://www.reddit.com/r/x/comments/abc/", "body": "Which carpet cleaner?"}
    comments = [{"author": f"u{i}", "text": "Love it, works great.", "links": ["https://lovable.dev/badge"],
                 "link": f"l{i}"} for i in range(4)]
    hits = R.content_marketing(data, comments)
    co = [h for h in hits if h["signal"] == "coordinated_links"]
    assert co and co[0]["weight"] == 0.3
    plug = [{"author": "z", "text": "Check out the Newborn Sound Machine Guide, it covers all of them.", "links": [],
             "link": "lz"}]
    assert any(h["signal"] == "dangling_roundup_reference" for h in R.content_marketing(data, plug))


def test_reviewer_names_are_not_brand_tokens(monkeypatch, tmp_path):
    import json
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"channels": {"projectfarm": {"name": "Project Farm"}, "hoover": {"name": "Hoover"}}}))
    monkeypatch.setenv("YOUTUBE_TAB_CHANNELS", str(p))
    monkeypatch.setattr(R, "_REVIEWERS", None)
    assert R._reviewer_names() == {"projectfarm"}


def test_title_brands_two_words_and_misspelled():
    strong, _ = R.brand_candidates("For a home carpet cleaning machine, should I purchase Bissel Big Green or Rug Doctor?")
    assert {"bissell", "rugdoctor"} <= strong


def test_revealed_replies_and_duplicates():
    comments = [{"id": "a", "depth": 0, "author": "u1", "text": "Top comment here, quite long."},
                {"id": "b", "depth": 1, "parent": "a", "author": "u2", "text": "[deleted]", "deleted": True,
                 "collapsed": True},
                {"id": "c", "depth": 2, "parent": "b", "author": "u3", "text": "Hidden reply that was revealed."},
                {"id": "d", "depth": 0, "author": "spam", "text": "Try the lovable badge app, it's great."},
                {"id": "e", "depth": 0, "author": "spam", "text": "Try the lovable badge app, it's great."}]
    out, counts = R.select_comments(comments, "op", 15, 0, revealed={"b"})
    ids = [c["id"] for c in out]
    assert ids == ["a", "c", "d"] and out[1]["revealed"] and counts["revealed_replies"] == 1
    assert counts["skipped_duplicates"] == 1


# --- password-manager run (2026-11-07) ------------------------------------------------------------

def test_digit_brands_category_words_and_split_queries():
    strong, _ = R.brand_candidates("Is 1Password worth it over Bitwarden?", "KeePassXC Strongbox sync", "")
    assert {"1password", "bitwarden", "keepassxc", "strongbox"} <= strong and "keepassxcstrongbox" not in strong
    assert R.brand_candidates("Password manager for family", "Password Manager family", "")[0] == set()
    assert R.subreddit_brand("PasswordManagers", set()) is None


def test_staff_tags_and_flair():
    kw = dict(strong={"bitwarden"}, sub_brand="bitwarden")
    assert R.brand_account("Sam_BW", None, "", mod_names=["Sam_BW"], **kw)["confidence"] == "likely"
    assert R.brand_account("xybitw", None, "", mod_names=["xybitw"], **kw)["confidence"] == "likely"
    assert R.brand_account("bob-uk", None, "", **kw) is None
    kw = dict(strong={"1password"}, sub_brand="1password")
    assert R.brand_account("1P_Jordan", None, "", mod_names=["1P_Jordan"], **kw)["confidence"] == "likely"
    assert R.brand_account("1PasswordCS-Casey", None, "", **kw)["confidence"] == "likely"
    assert R.brand_account("someone", "1Password Community Manager", "", **kw)["confidence"] == "likely"


def test_builder_self_promotion_and_bots():
    data = {"author": "op", "url": "u", "body": "Which manager?", "links": []}
    comments = [{"author": "dev1", "text": "Hi, I'm the author of Bramble, it does this.", "link": "c1", "links": []},
                {"author": "AutoModerator", "text": "Join our Discord", "link": "c2",
                 "links": ["https://discord.gg/abc?ref=x"]}]
    hits = R.content_marketing(data, comments)
    assert [h["signal"] for h in hits] == ["builder_self_promotion"]
    res = R.promo_bias({**data, "title": "Which manager?", "subreddit": "r/ClaudeCode", "comments": comments})
    assert "affiliate_links" not in [e["signal"] for e in res["evidence"]]


def test_stale_query_tokens_dropped():
    res = R.promo_bias({"title": "1Password price increase", "subreddit": "r/1Password", "body": "", "comments": []},
                       query="Apple Passwords family")
    assert "apple" not in res["brand_tokens"] and "1password" in res["brand_tokens"]


# --- markdown-editor run (2026-11-08) ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Disclosure: I make Downright", "Shameless self-promotion: I make Raute", "Disclaimer: I'm the dev",
    "I'm the developer", "Mine ;-) I developed Markify", "Ended up building my own: Mandrill",
    "I also programmed it lol", "(my project)", "I vibe-coded one myself", "Hey you wanna try out my app?",
    "the app I am maintaining called Hatchdoor", "I built markjason.sh", "I'm building Grape",
    "(disclosure: I am on the team building it)", "I made writedown.app",
    "Shoot me a DM and I'll send you a key for cyberwriter"])
def test_builder_phrasings(text):
    assert R._BUILDER.search(text)


@pytest.mark.parametrize("text", [
    "I made the switch to Obsidian", "I built my vault over years", "we made it work with Syncthing",
    "I developed a habit", "i'm a developer and use VS Code", "I created a folder for each project",
    "I made Obsidian my daily driver", "Disclaimer: I participate in the Amazon Associates program"])
def test_builder_not_ordinary_use(text):
    assert not R._BUILDER.search(text)


def test_builder_launch_title_and_several_builders():
    h = _cm(title="I built a WYSIWYG Markdown editor that doesn't rewrite your Markdown",
            comments=[{"author": "a", "text": "Disclosure: I make Downright", "link": "c1"},
                      {"author": "b", "text": "I'm building Grape, try it", "link": "c2"}])
    b = h["builder_self_promotion"]
    assert b["source"] == "post" and "+2 more users" in b["text"] and b["weight"] > 0.25


def test_app_store_links_from_several_users_are_not_coordinated():
    cs = [{"author": f"u{i}", "text": "this one", "links": [f"https://apps.apple.com/us/app/x/id{100 + i}"]}
          for i in range(4)]
    assert "coordinated_links" not in _cm(comments=cs)
    same = [{"author": f"u{i}", "text": "this one", "links": ["https://apps.apple.com/us/app/x/id100"]}
            for i in range(4)]
    assert "coordinated_links" in _cm(comments=same)


def test_markdown_category_words_are_not_brands():
    strong, _ = R.brand_candidates(title="Best Markdown Editor for Mac?", query="markdown editor")
    assert not strong & {"markdown", "markdowneditor", "editor", "notes", "wysiwyg"}
    assert R.subreddit_brand("r/Markdown", {"markdown"}) is None and R.subreddit_brand("r/foss", {"foss"}) is None
    strong, _ = R.brand_candidates(title="Typora alternatives that are free?")
    assert "typora" in strong


def test_crossposts_marked_in_search_results():
    base = {"author": "dev1", "title": "I built a Markdown editor", "created": "2026-09-28T21:12:08", "score": 9,
            "comments": 52}
    posts = [{**base, "id": "a", "sub": "Markdown"}, {**base, "id": "b", "sub": "software"},
             {"id": "c", "sub": "x", "author": "z", "title": "other", "created": "2026-01-01"}]
    assert R.mark_crossposts(posts) == 2
    assert posts[1]["crosspost_of"] == "a" and posts[0]["counts_shared"] and "crosspost_of" not in posts[2]


# --- private-streaming run (2026-11-12) ---------------------------------------------------------------

def test_streaming_category_words_are_not_brands():
    strong, _ = R.brand_candidates(title="Best streaming device for privacy? Smart TV, DNS, HDR, Widevine, Netflix")
    assert not strong


def test_staff_flair_needs_a_person_name_and_role_of_project():
    assert R.staff_flair("Mint | VOID", {"mint"}) is None
    assert R.staff_flair("EcoFlow | Ryan", {"ecoflow"})
    data = {"subreddit": "r/homeassistant", "title": "Which media box?", "url": "https://www.reddit.com/r/x/comments/p/t/",
            "author": "op", "body": "", "comments": [], "sidebar": True,
            "moderators": [{"name": "pat_dev", "flair": "Founder of Home Assistant"}]}
    pb = R.promo_bias(data)
    assert pb["brand_moderators"] == ["pat_dev"]
