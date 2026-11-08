"""Shared tally engine (integrations/claude/lib/tally.py). No browser."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import tally as T  # noqa: E402


def names_in(text, names, **kw):
    return [(n, text[s:e]) for n, s, e in T.find_mentions(text, T.build_terms(names, **kw))]


# --- matching ------------------------------------------------------------

@pytest.mark.parametrize("text,names,expected", [
    ("I love my Core One+", ["Core One"], []),
    ("Core One+ and Core One", ["Core One", "Core One+"], [("Core One+", "Core One+"), ("Core One", "Core One")]),
    ("The Core One L is huge", ["Core One"], []),
    ("MK4S beats the MK4.", ["MK4"], [("MK4", "MK4")]),
    ("Centauri Carbon 2 vs Centauri Carbon", ["Centauri Carbon"], [("Centauri Carbon", "Centauri Carbon")]),
    ("Q2C or Q2?", ["Q2"], [("Q2", "Q2")]),
    ("A1 mini and A1 combo", ["A1"], [("A1", "A1")]),
    ("DJI Mic 3 and the DJI Mic", ["DJI Mic"], [("DJI Mic", "DJI Mic")]),
    ("had the MK4 3 months now", ["MK4"], [("MK4", "MK4")]),
    ("Wireless Microphone review", ["Wireless Micro"], []),
    ("Minimic Pro", ["Mic Pro"], []),
    ("RØDE Wireless PRO", ["Rode Wireless Pro"], [("Rode Wireless Pro", "RØDE Wireless PRO")]),
])
def test_whole_name_longest_match(text, names, expected):
    assert names_in(text, names) == expected


def test_aliases():
    got = names_in("C1 is fine; core-one also; Core1 too", ["Core One"], aliases="Core One=C1,Core 1,Core1")
    assert [n for n, _ in got] == ["Core One"] * 3


def test_parse_aliases():
    assert T.parse_aliases("Core One=C1,Core 1; MK4S=MK 4S") == {"Core One": ["C1", "Core 1"], "MK4S": ["MK 4S"]}


def test_builtin_garbles_and_override():
    assert names_in("the Bumble Lab P2S is great", ["Bambu P2S"]) == [("Bambu P2S", "Bumble Lab P2S")]
    assert names_in("Cheety Q2 and Eligu Centauri", ["Qidi Q2", "Elegoo Centauri"]) == [
        ("Qidi Q2", "Cheety Q2"), ("Elegoo Centauri", "Eligu Centauri")]
    assert names_in("Pruza MK4", ["Prusa MK4"]) == [("Prusa MK4", "Pruza MK4")]
    assert names_in("the Bumble Lab P2S", ["Bambu P2S"], garbles={}) == []


def test_snippet_maps_back_to_original_text():
    text = "Long intro. " * 20 + "The RØDE Wireless PRO was flawless for me." + " Outro." * 20
    (_, s, e), = T.find_mentions(text, T.build_terms(["Rode Wireless Pro"]))
    snip = T.snippet(text, s, e, width=30)
    assert "RØDE Wireless PRO" in snip and snip.startswith("…") and snip.endswith("…")


# --- polarity -------------------------------------------------------------

@pytest.mark.parametrize("text,pol", [
    ("I'd highly recommend the P2S", "recommend"),
    ("No issues at all with my P2S", "recommend"),
    ("Mine broke after a week and I returned it", "complain"),
    ("Wouldn't recommend the P2S", "complain"),
    ("Should I get the P2S?", "question"),
    ("I have a P2S next to my desk", "mention"),
])
def test_polarity(text, pol):
    assert T.polarity(text) == pol


# --- records --------------------------------------------------------------

def test_merge_richer_record_wins():
    search = {"id": "vid1", "url": "u", "via": "search", "title": "Short title", "channel": "Chan"}
    video = {"id": "vid1", "url": "u", "via": "video", "title": "Full title", "text": "Description about the P2S",
             "links": ["https://amzn.to/x"], "sponsor_score": 0.9}
    (m,) = T.merge_records([search, video])
    assert m["title"] == "Full title" and m["sponsor_score"] == 0.9 and m["channel"] == "Chan"
    assert m["links"] == ["https://amzn.to/x"] and m["sources"] == ["search", "video"]


def test_upsert_keeps_loaded_video():
    seen = T.upsert([], [{"id": "v", "via": "search", "title": "t"}])
    seen = T.upsert(seen, [{"id": "v", "via": "video", "title": "t", "text": "P2S in the description"}])
    assert len(seen) == 1 and seen[0]["via"] == "video"


# --- tally ---------------------------------------------------------------

def reddit_seen():
    return [
        {"id": "t1", "thread": "t1", "via": "search", "title": "Core One worth it?", "url": "https://r/t1"},
        {"id": "t2", "thread": "t2", "via": "post", "author": "op1", "title": "Core One or P2S?",
         "text": "Which should I get?", "score": 30, "url": "https://r/t2"},
        {"id": "t3", "thread": "t3", "via": "post", "author": "op1", "title": "Core One or P2S?",
         "text": "Cross-posted.", "score": 5, "url": "https://r/t3"},
        {"id": "c1", "thread": "t2", "via": "comment", "author": "u1", "score": 326,
         "text": "I designed these parts on my Core One, which sits in the garage.", "link": "https://r/c1"},
        {"id": "c2", "thread": "t2", "via": "comment", "author": "u2", "score": 40,
         "text": "Highly recommend the Core One, rock solid.", "link": "https://r/c2"},
        {"id": "c3", "thread": "t3", "via": "comment", "author": "e3dsupport", "brand": True, "score": 10,
         "text": "The Core One is great, email support@e3d.com", "link": "https://r/c3"},
        {"id": "c4", "thread": "t3", "via": "comment", "author": "u3", "score": 12,
         "text": "My Core One+ clogs constantly.", "link": "https://r/c4"},
    ]


def test_reddit_tally():
    res = T.tally(reddit_seen(), ["Core One", "Core One+"])
    row = {r["term"]: r for r in res["tally"]}
    co = row["Core One"]
    assert co["threads"] == 1  # the OP's cross-post collapses into one thread
    assert co["search_only_threads"] == 1
    assert co["users"] == 4 and co["independent_users"] == 2  # brand account out; OP question doesn't count
    assert co["excluded"] == {"op_question": 2, "brand_account": 1}
    assert co["raw_upvotes"] == 366 and co["recommend_upvotes"] == 40
    assert co["polarity"]["recommend"] == 2 and co["polarity"]["question"] == 2
    assert all(e["link"] for e in co["examples"])
    cop = row["Core One+"]
    assert cop["independent_users"] == 1 and cop["complain_upvotes"] == 12


def test_exclude_op():
    seen = [{"id": "p", "thread": "p", "via": "post", "author": "op", "title": "My P2S review",
             "text": "The P2S is great.", "score": 50}]
    assert T.tally(seen, ["P2S"])["tally"][0]["independent_users"] == 1
    assert T.tally(seen, ["P2S"], exclude_op=True)["tally"][0]["independent_users"] == 0


def youtube_seen():
    return [
        {"id": "a", "via": "search", "title": "DJI Mic Mini review", "channel": "Tyler"},
        {"id": "a", "via": "video", "title": "DJI Mic Mini vs Mini 2", "channel": "Tyler",
         "text": "Thanks to DJI for sponsoring", "sponsor_score": 1.0},
        {"id": "b", "via": "search", "title": "Best lav mics", "channel": "Gabe"},
        {"id": "b", "via": "video", "title": "Best lav mics", "channel": "Gabe",
         "text": "My pick is the DJI Mic Mini", "sponsor_score": 0.3,
         "segments": [{"text": "the Hollyland Lark M2 sounds great", "link": "https://y/b?t=60"}]},
        {"id": "c", "via": "search", "title": "Top 5 DJI Mic Mini picks", "channel": "Top5Lab", "listicle": True},
        {"id": "d", "via": "search", "title": "Lark M2 unboxing", "channel": "Top5Lab"},
    ]


def test_youtube_tally_merges_and_carries_sponsor_scores():
    res = T.tally(youtube_seen(), ["DJI Mic Mini", "Hollyland Lark M2"], group="channel", group_label="channels",
                  separate_search=False)
    row = {r["term"]: r for r in res["tally"]}
    mini = row["DJI Mic Mini"]
    assert res["seen_items"] == 4  # search and video records merged by id
    assert mini["channels"] == 3 and mini["independent_channels"] == 1
    assert mini["excluded"] == {"sponsored": 1, "listicle": 1}
    lark = row["Hollyland Lark M2"]
    # The transcript mention counts (with the segment link); the listicle channel is out channel-wide.
    assert lark["independent_channels"] == 1
    assert any(e["link"] == "https://y/b?t=60" and e["field"] == "transcript" for e in lark["examples"])
    assert T.tally(youtube_seen(), ["Hollyland Lark M2"], group="channel", group_label="channels",
                   separate_search=False, include_transcripts=False)["tally"][0]["independent_channels"] == 0


def test_group_flags_and_custom_independence():
    seen = [{"id": "x", "via": "video", "title": "P2S review", "channel": "A"}]
    kw = dict(group="channel", group_label="channels", separate_search=False)
    assert T.tally(seen, ["P2S"], group_flags={"A": {"brand_owned": "strong"}}, **kw)["tally"][0][
        "independent_channels"] == 0
    assert T.tally(seen, ["P2S"], independence=lambda r: "custom", **kw)["tally"][0]["excluded"] == {"custom": 1}
    # "possible" brand ownership is flagged elsewhere but doesn't exclude.
    seen[0]["brand_owned"] = "possible"
    assert T.tally(seen, ["P2S"], **kw)["tally"][0]["independent_channels"] == 1


# --- 2026-11-07 open items --------------------------------------------------------

def test_new_garbles():
    recs = [{"id": "c1", "via": "comment", "thread": "p1", "author": "a", "text": "My Eccoflow died after a year.",
             "score": 3},
            {"id": "c2", "via": "comment", "thread": "p1", "author": "b", "text": "blietti has been solid for me.",
             "score": 5}]
    rows = {r["term"]: r for r in T.tally(recs, ["EcoFlow", "Bluetti"])["tally"]}
    assert rows["EcoFlow"]["users"] == 1 and rows["EcoFlow"]["polarity"]["complain"] == 1
    assert rows["Bluetti"]["users"] == 1 and rows["Bluetti"]["polarity"]["recommend"] == 1


def test_polarity_is_per_clause():
    text = "Love my Bluetti, but the EcoFlow died after two months."
    recs = [{"id": "c1", "via": "comment", "thread": "p1", "author": "a", "text": text, "score": 10}]
    rows = {r["term"]: r for r in T.tally(recs, ["EcoFlow", "Bluetti"])["tally"]}
    assert rows["Bluetti"]["polarity"]["recommend"] == 1 and rows["Bluetti"]["complain_upvotes"] == 0
    assert rows["EcoFlow"]["polarity"]["complain"] == 1 and rows["EcoFlow"]["recommend_upvotes"] == 0
    # No tone in the clause: the whole sentence decides.
    assert T._clause_around("The Jackery, which I returned, was a problem.", 4, 11)


def test_exclude_op_drops_op_from_users():
    recs = [{"id": "p1", "via": "post", "thread": "p1", "author": "op1", "title": "Hoover Power Scrub vs ProHeat?",
             "text": "", "is_op": True},
            {"id": "p2", "via": "post", "thread": "p2", "author": "op2", "title": "x", "text": "I had a Power Scrub before",
             "is_op": True},
            {"id": "c1", "via": "comment", "thread": "p2", "author": "u1", "text": "The Power Scrub leaked on me.",
             "score": 4}]
    row = T.tally(recs, ["Power Scrub"], exclude_op=True)["tally"][0]
    assert row["users"] == 1 and row["independent_users"] == 1 and row["threads"] == 1
    assert row["excluded"]["op"] == 2
    row = T.tally(recs, ["Power Scrub"])["tally"][0]
    assert row["users"] == 3


def test_implied_title_only_endorsements():
    recs = [{"id": "p1", "via": "post", "thread": "p1", "author": "op", "title": "Highly recommend the Bissell Big Green",
             "text": "", "is_op": True, "score": 2700},
            {"id": "c1", "via": "comment", "thread": "p1", "author": "u1", "depth": 0,
             "text": "Love mine. Bought it 8 years ago. No regrets.", "score": 100},
            {"id": "c2", "via": "comment", "thread": "p1", "author": "u2", "depth": 1, "text": "Love mine too!", "score": 5},
            {"id": "c3", "via": "comment", "thread": "p1", "author": "u3", "depth": 0, "text": "There's always Miele.",
             "score": 59},
            {"id": "c4", "via": "comment", "thread": "p1", "author": "op", "depth": 0, "is_op": True,
             "text": "Great machine", "score": 9},
            # A two-product title doesn't count.
            {"id": "p2", "via": "post", "thread": "p2", "author": "op2", "title": "Big Green or Rug Doctor?", "is_op": True},
            {"id": "c5", "via": "comment", "thread": "p2", "author": "u4", "depth": 0, "text": "Love it, great buy",
             "score": 3}]
    row = {r["term"]: r for r in T.tally(recs, ["Big Green", "Rug Doctor"])["tally"]}["Big Green"]
    imp = row["implied"]
    assert imp["threads"] == 1 and imp["users"] == 1 and imp["upvotes"] == 100
    assert imp["examples"][0]["author"] == "u1"
    # Kept apart: the main counts have the post's own title only, not the replies.
    assert row["recommend_upvotes"] == 2700 and row["users"] == 2  # the two OPs
    yt = T.tally(recs, ["Big Green"], group="channel", group_label="channels", separate_search=False)["tally"][0]
    assert "implied" not in yt


def test_polarity_reads_common_endorsements():
    import tally as T
    for t in ["Hatch is the answer.", "The Dohm is the gold standard.", "Get the LectroFan, you won't regret it.",
              "Our Hatch has been going strong for 3 years.", "We swear by our Dohm."]:
        assert T.polarity(t) == "recommend", t
    assert T.polarity("The Hatch app is garbage.") == "complain"


def test_negated_praise_is_a_complaint():
    import tally as T
    assert T.polarity("The Shark is not great for deep cleaning.") == "complain"
    assert T.polarity("Honestly it isn't that good on stairs.") == "complain"


def test_bare_alias_after_another_brand_is_not_this_product():
    import tally as T
    terms = T.build_terms(["Hoover SmartWash"], "Hoover SmartWash=SmartWash")
    assert T.find_mentions("I love my Vax SmartWash.", terms) == []
    assert T.find_mentions("Our SmartWash leaks.", terms)


# --- password-manager run (2026-11-07) ------------------------------------------------------------

def test_longer_name_claims_its_words_across_terms():
    recs = [{"id": "c1", "via": "comment", "author": "a", "thread": "t", "text": "I moved from Proton Pass to KeePassXC."}]
    rows = {r["term"]: r for r in T.tally(recs, ["Proton Pass", "pass"])["tally"]}
    assert rows["Proton Pass"]["users"] == 1 and rows["pass"]["users"] == 0


def test_near_requires_context_and_common_words_warn():
    recs = [{"id": "c1", "via": "comment", "author": "a", "thread": "t", "text": "You need to pass the test."},
            {"id": "c2", "via": "comment", "author": "b", "thread": "t", "text": "I use pass with gpg and git."}]
    plain = T.tally(recs, ["pass"])["tally"][0]
    assert plain["users"] == 2 and "common word" in plain["warning"]
    near = T.tally(recs, ["pass"], near="pass=gpg,gopass,password-store")["tally"][0]
    assert near["users"] == 1 and near["dropped_not_near"] == 1 and "warning" not in near


def test_short_lowercase_alias_is_case_exact():
    recs = [{"id": "c1", "via": "comment", "author": "a", "thread": "t", "text": "OP should just switch."},
            {"id": "c2", "via": "comment", "author": "b", "thread": "t", "text": "Run op inject in your shell."}]
    row = T.tally(recs, ["1Password"], aliases="1Password=op")["tally"][0]
    assert row["users"] == 1


def test_short_answer_takes_tone_from_next_sentence():
    assert T.tally([{"id": "c", "via": "comment", "author": "a", "thread": "t",
                     "text": "For me, it's Roboform. It's fast, reliable and cheap."}],
                   ["RoboForm"])["tally"][0]["polarity"]["recommend"] == 1
    assert T.polarity("Roboform has been treating me well.") == "recommend"


def test_repeat_promoters_across_threads():
    recs = [{"id": f"c{i}", "via": "comment", "author": "fan", "thread": f"t{i}", "text": "RoboForm is great."}
            for i in range(3)] + [{"id": "x", "via": "comment", "author": "other", "thread": "t9",
                                   "text": "RoboForm is great."}]
    row = T.tally(recs, ["RoboForm"])["tally"][0]
    assert row["repeat_promoters"] == [{"author": "fan", "threads": 3}]


def test_markdown_run_polarity_fixes():
    recs = [{"id": "c1", "via": "comment", "author": "a", "score": 17,
             "text": "I'm not aware of Typora, but I use MacDown 3000 and recommended it."},
            {"id": "c2", "via": "comment", "author": "b", "score": 3, "text": "Typora is still king to me."},
            {"id": "c3", "via": "comment", "author": "c", "score": 2, "text": "I can't recommend Zettlr enough."},
            {"id": "c4", "via": "comment", "author": "d", "score": 2, "text": "I enjoy using Kate."}]
    rows = {r["term"]: r for r in T.tally(recs, ["Typora", "MacDown 3000", "Zettlr", "Kate"])["tally"]}
    # the MacDown recommendation isn't credited to Typora; "still king" is praise
    assert rows["Typora"]["recommend_upvotes"] == 3 and rows["Typora"]["polarity"]["mention"] == 1
    assert rows["MacDown 3000"]["recommend_upvotes"] == 17
    assert rows["Zettlr"]["polarity"]["recommend"] == 1 and rows["Kate"]["polarity"]["recommend"] == 1
    assert "common word" in rows["Kate"]["warning"]
    assert T.polarity("I can't recommend it.") == "complain"


def test_everyday_word_app_names_warn():
    for name in ("Scratch", "Paper", "Zed", "Panda"):
        assert T.ambiguous_variants(T.Term(name, [name]))
    assert not T.ambiguous_variants(T.Term("Obsidian", ["Obsidian"]))
