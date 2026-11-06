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
