"""Channel map (YouTube): axis heuristics, registry, and the no-load `channels` command."""
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "youtube-research" / "scripts"))
import channel_map as CM  # noqa: E402
import youtube_tab as Y  # noqa: E402

TESTER = ("We tested each machine side by side on the same stain. The stain test: we measured 220 ml "
          "recovered in 45 seconds, and a sound meter read 78 dB. Test number 2 repeated the test; on average "
          "it dried in 3 hours. ") * 6
SPEC = "According to the manufacturer it boasts a powerful motor. Features include a big tank. " * 6


def rec(url, signals=(), segments_text=None, **kw):
    r = {"url": url, "title": kw.pop("title", url), "channel": kw.pop("channel", "Ch"), "via": "video",
         "signals": [dict(signal=s) if isinstance(s, str) else s for s in signals], "length_s": 600, **kw}
    if segments_text is not None:
        r["segments"] = [{"text": segments_text}]
    return r


def test_independence_penalizes_brand_money_not_generic_affiliates():
    clean, _ = CM.video_independence(rec("a", ["affiliate_links", "bought_it_myself"]))
    sponsored, why = CM.video_independence(rec("b", ["sponsor_read", "review_unit"]))
    assert clean >= 4.5 and sponsored <= 1.5 and "sponsor_read" in why
    assert CM.video_independence(rec("c", [], likely_ai_listicle=True))[0] <= 1.5


def test_transparency_none_without_money_and_penalizes_undisclosed_brand_links():
    assert CM.video_transparency(rec("a", ["bought_it_myself"]))[0] is None
    disclosed, _ = CM.video_transparency(rec("b", ["affiliate_links", "affiliate_disclosure"]))
    hidden, why = CM.video_transparency(rec("c", ["brand_code_or_link", "affiliate_links"]))
    assert disclosed == 5.0 and hidden <= 2.0 and any("brand_code_or_link" in w for w in why)
    late, why = CM.video_transparency(rec("d", [{"signal": "sponsor_read", "sec": 500}]))
    assert late < 5 and "9:" not in "".join(why) and any("8:20" in w for w in why)


def test_rigor_separates_testing_from_spec_reading():
    hi, d = CM.video_rigor(rec("a", segments_text=TESTER))
    lo, _ = CM.video_rigor(rec("b", segments_text=SPEC))
    assert hi >= 3.5 and lo == 0.0 and d["hits"]["instrument"] >= 1
    assert CM.video_rigor(rec("c"))[0] is None  # no transcript -> unknown, not zero


def test_rate_channel_axes_confidence_authority_and_quadrant():
    recs = [rec(f"v{i}", ["affiliate_links", "affiliate_disclosure"], TESTER, channel="Tester") for i in range(3)]
    r = CM.rate_channel("Tester", recs, {"handle": "@tester", "recent_titles": ["Carpet cleaner test", "Drill test"]},
                        "carpet cleaners", {"Tester": 4})
    assert r["confidence"] == "high" and r["axes"]["independence"] >= 4.5
    assert r["context"]["focus"] == 0.5 and r["context"]["trust_citations"] == 4
    assert r["authority"] >= 3.5 and CM.quadrant(r) == "trusted tester"
    shallow = CM.rate_channel("Promo", [rec("p", ["sponsor_read", "brand_code_or_link"], SPEC)], {}, "carpet cleaners")
    assert CM.quadrant(shallow) == "promotional / shallow" and shallow["confidence"] == "low"


def test_registry_put_pin_lookup_and_staleness(tmp_path):
    p = tmp_path / "reg.json"
    reg = CM.registry_load(p)
    r = CM.rate_channel("Project Farm", [rec("v", [], TESTER)], {"handle": "@ProjectFarm"}, "carpet cleaners")
    CM.registry_put(reg, r)
    CM.registry_pin(reg, "project farm", "trusted", "gold standard")
    CM.registry_save(p, reg)
    reg = CM.registry_load(p)
    hit = CM.registry_lookup(reg, "@ProjectFarm", "carpet cleaners")
    assert hit["same_topic"] and hit["pin"]["verdict"] == "trusted" and not hit["stale"]
    near = CM.registry_lookup(reg, "Project Farm", "carpet cleaner machines")
    assert near["topic"] == "carpet cleaners" and not near["same_topic"]
    assert CM.stale((date.today() - timedelta(days=200)).isoformat())
    CM.registry_pin(reg, "Project Farm", "clear")
    assert "pin" not in CM.registry_find(reg, "Project Farm")[1]


def test_channels_command_groups_loaded_videos_and_lists_candidates(monkeypatch, tmp_path):
    seen = {"items": [rec("https://youtu.be/a", ["affiliate_links"], TESTER, channel="Tester", id="a"),
                      rec("https://youtu.be/b", ["sponsor_read"], SPEC, channel="Promo", id="b"),
                      {"id": "c", "url": "https://youtu.be/c", "channel": "Listy", "via": "search", "title": "x"},
                      {"id": "d", "url": "https://youtu.be/d", "channel": "Listy", "via": "search", "title": "y"}],
            "channels": {"Tester": {"handle": "@tester", "recent_titles": ["carpet test"]}, "Listy": {"listicle": True}}}
    monkeypatch.setattr(Y.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(Y, "_seen_load", lambda fid: seen)
    monkeypatch.setattr(Y, "CHANNELS", tmp_path / "reg.json")
    out = Y.channels("T", "carpet cleaners", "Tester=3", save=True)
    names = [r["channel"] for r in out["channels"]]
    assert names[0] == "Tester" and "Promo" in names and out["saved"]
    assert out["candidates"] == [{"channel": "Listy", "search_results": 2, "listicle": True}]
    assert Y.registry("list")["channels"] and Y.registry("pin", "Promo", "distrusted")["pin"]["verdict"] == "distrusted"
    again = Y.channels("T", "carpet cleaners")
    promo = next(r for r in again["channels"] if r["channel"] == "Promo")
    assert promo["registry"]["pin"]["verdict"] == "distrusted"


def test_cli_parses_cited_and_registry_pin(monkeypatch):
    calls = {}
    monkeypatch.setattr(Y, "channels", lambda *a: calls.setdefault("channels", a))
    monkeypatch.setattr(Y, "registry", lambda *a: calls.setdefault("registry", a))
    Y._dispatch("channels", ["T", "--topic", "carpet cleaners", "--cited", "Project Farm=4", "--save"])
    Y._dispatch("registry", ["pin", "Project", "Farm", "trusted", "--note", "gold standard"])
    assert calls["channels"] == ("T", "carpet cleaners", "Project Farm=4", True)
    assert calls["registry"] == ("pin", "Project Farm", "trusted", "gold standard")
    assert Y._parse_cited("Project Farm=4; Vacuum Wars=2") == {"Project Farm": 4, "Vacuum Wars": 2}


def test_affiliate_roundup_quadrant():
    recs = [rec(f"v{i}", ["affiliate_links", "affiliate_disclosure"], "Number one on our list is great. " * 40,
                title=f"Best Carpet Cleaners 202{i}") for i in range(2)]
    r = CM.rate_channel("Roundups", recs, {}, "carpet cleaners")
    assert r["context"]["affiliate_share"] == 1.0
    assert CM.quadrant(r) == "affiliate roundup (light testing)"
    single = CM.rate_channel("Single", [rec("s", ["affiliate_links", "affiliate_disclosure"], "It is nice. " * 40,
                                            title="Hatch Rest 2 review")], {}, "x")
    assert CM.quadrant(single) == "affiliate-led review (light testing)"
    ad = CM.rate_channel("Ad", [rec("a", ["ad_tag", "review_unit", "use_my_link_or_code", "affiliate_links"],
                                    "It is nice. " * 40, title="Best sound machine")], {}, "x")
    assert CM.quadrant(ad) == "promotional / shallow"


def test_draft_covers_and_topic_matching(tmp_path):
    info = {"description": "We test products head to head.",
            "recent_titles": ["Best Cordless Drill? Let's Find Out!", "Which Impact Driver Is Best?",
                              "Cordless Drills Tested", "Best Car Wax", "Car Wax vs Ceramic Coating",
                              "Impact Driver Showdown"]}
    d = CM.draft_covers(info)
    assert {"cordless", "drill", "impact", "driver", "car", "wax"} <= set(d["tags"]) and d["summary"] is None
    reg = CM.registry_load(tmp_path / "r.json")
    CM.registry_describe(reg, "Project Farm", "Head-to-head tests of tools, automotive and household products.",
                         ["Power Tools", "automotive", "carpet cleaners"])
    row = CM.registry_find(reg, "Project Farm")[1]
    assert CM.covers_topic(row, "cordless power tools") and CM.covers_topic(row, "carpet cleaner")
    assert not CM.covers_topic(row, "gaming headsets")
    assert CM.registry_lookup(reg, "Project Farm", "gaming headsets")["covers"]["tags"][0] == "automotive"


def test_registry_describe_cli_and_topic_list(monkeypatch, tmp_path):
    monkeypatch.setattr(Y, "CHANNELS", tmp_path / "reg.json")
    out = Y._dispatch("registry", ["describe", "Project", "Farm", "--covers", "Tool and household product tests.",
                                   "--tags", "power tools, carpet cleaners"])
    assert out["covers"]["tags"] == ["carpet cleaners", "power tools"]
    listed = Y._dispatch("registry", ["list", "--topic", "carpet cleaner machines"])
    assert [c["channel"] for c in listed["channels"]] == ["Project Farm"]
    assert Y._dispatch("registry", ["list", "--topic", "espresso"])["channels"] == []


def test_mixed_pin_and_observations(monkeypatch, tmp_path):
    monkeypatch.setattr(Y, "CHANNELS", tmp_path / "reg.json")
    assert Y._dispatch("registry", ["pin", "Linus", "Tech", "Tips", "mixed", "--note", "discloses"])["pin"]["verdict"] == "mixed"
    out = Y._dispatch("registry", ["observe", "Tyler", "Stalman", "--topic", "wireless mics",
                                   "--note", "DJI-sponsored, label shown", "--source", "https://youtu.be/x"])
    assert out["observations"][0]["source"] == "https://youtu.be/x"
    Y._dispatch("registry", ["observe", "Tyler", "Stalman", "--topic", "wireless mics", "--note", "DJI-sponsored, label shown"])
    reg = CM.registry_load(tmp_path / "reg.json")
    assert len(CM.registry_find(reg, "Tyler Stalman")[1]["observations"]) == 1  # deduplicated
    assert CM.registry_lookup(reg, "Tyler Stalman", "x")["observations"]


def test_brand_supplied_light_testing_quadrant():
    r = CM.rate_channel("Mid", [rec("v", ["review_unit", "brand_giveaway"], "We tested it. " * 30)], {}, "x")
    assert 2.5 <= r["axes"]["independence"] < 3.5 and CM.quadrant(r) == "brand-supplied, light testing"


def test_reviewed_list_familiarity_and_cumulative(monkeypatch, tmp_path):
    reg = CM.registry_load(tmp_path / "r.json")
    r1 = CM.rate_channel("Tester", [rec("https://www.youtube.com/watch?v=AAAAAAAAAAA", [], TESTER, channel="Tester")],
                         {}, "carpet cleaners")
    CM.registry_put(reg, r1)
    r2 = CM.rate_channel("Tester", [rec("https://www.youtube.com/watch?v=AAAAAAAAAAA", [], TESTER, channel="Tester"),
                                    rec("https://youtu.be/BBBBBBBBBBB", ["sponsor_read"], None, channel="Tester")],
                         {}, "carpet cleaners")
    CM.registry_put(reg, r2)
    CM.registry_add_video(reg, "Tester", "https://www.youtube.com/watch?v=CCCCCCCCCCC", "drills", "Drill test")
    row = CM.registry_find(reg, "Tester")[1]
    fam = CM.familiarity(row)
    assert fam["videos"] == 3 and fam["with_transcript"] == 1 and fam["level"] == "moderate"
    assert fam["by_topic"] == {"carpet cleaners": 2, "drills": 1}
    cum = row["topics"]["carpet cleaners"]["cumulative"]
    assert cum["videos"] == 2 and cum["rigor"] is not None and cum["independence"] < 5
    hit = CM.registry_lookup(reg, "Tester", "carpet cleaners")
    assert set(hit["reviewed_ids"]) == {"AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"}


def test_channels_marks_videos_reviewed_before(monkeypatch, tmp_path):
    seen = {"items": [rec("https://youtu.be/AAAAAAAAAAA", [], TESTER, channel="Tester", id="AAAAAAAAAAA"),
                      rec("https://youtu.be/DDDDDDDDDDD", [], TESTER, channel="Tester", id="DDDDDDDDDDD")],
            "channels": {}}
    monkeypatch.setattr(Y.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(Y, "_seen_load", lambda fid: seen)
    monkeypatch.setattr(Y, "CHANNELS", tmp_path / "reg.json")
    reg = CM.registry_load(tmp_path / "reg.json")
    CM.registry_add_video(reg, "Tester", "https://www.youtube.com/watch?v=AAAAAAAAAAA", "carpet cleaners")
    CM.registry_save(tmp_path / "reg.json", reg)
    row = Y.channels("T", "carpet cleaners")["channels"][0]
    assert {v["video_id"]: v["reviewed_before"] for v in row["videos"]} == {"AAAAAAAAAAA": True, "DDDDDDDDDDD": False}
    assert row["registry"]["familiarity"]["videos"] == 1
    assert Y.registry("show", "Tester")["familiarity"]["videos"] == 1



def test_registry_identity_and_dedupe(tmp_path):
    reg = CM.registry_load(tmp_path / "r.json")
    CM.registry_describe(reg, "No Sleepless Nights", "Measured white noise machine tests.", ["sleep"])
    r = CM.rate_channel("No Sleepless Nights", [rec("https://youtu.be/AAAAAAAAAAA", [], TESTER)],
                        {"handle": "@nosleeplessnights1"}, "baby white noise machines")
    CM.registry_put(reg, r)
    assert len(reg["channels"]) == 1
    row = CM.registry_find(reg, "@nosleeplessnights1")[1]
    assert row["covers"] and row["handle"] == "@nosleeplessnights1" and CM.familiarity(row)["videos"] == 1
    # an old file with split rows heals on load
    reg2 = {"channels": {"dmfinds": {"name": "DM Finds", "handle": None, "topics": {},
                                     "reviewed": [{"id": "X", "topics": ["a"]}], "covers": {"summary": "s", "date": "2026-11-07"}},
                         "dm-finds": {"name": "DM Finds", "handle": "@dm-finds", "topics": {"a": {"date": "2026-11-07"}}}}}
    CM.registry_save(tmp_path / "old.json", reg2)
    healed = CM.registry_load(tmp_path / "old.json")
    assert list(healed["channels"]) == ["dm-finds"]
    row = healed["channels"]["dm-finds"]
    assert row["covers"]["summary"] == "s" and CM.familiarity(row)["videos"] == 1 and "a" in row["topics"]


def test_rigor_counts_distance_tests_and_tables():
    text = ("I tested their maximum volume from 1 meter away, and you can see in this table that the majority "
            "reached over 60 decibels, with some approaching 80 decibels. The battery lasted 40 hours. ") * 3
    score, d = CM.video_rigor(rec("n", segments_text=text, length_s=600))
    assert d["hits"]["method"] >= 1 and d["hits"]["data_table"] >= 1 and score >= 2.5


def test_study_citations_are_not_product_testing():
    text = ("A 2014 study published in Pediatrics found machines reached 85 dB at 30 cm. The AAP suggests 50 dB. "
            "Research shows babies sleep 20 minutes longer. ") * 4
    score, d = CM.video_rigor(rec("e", segments_text=text, length_s=600))
    assert score <= 1.5 and d.get("kind", "").startswith("evidence explainer")
    r = CM.rate_channel("Explainer", [rec("e", [], text, length_s=600)], {}, "x")
    assert any("evidence explainer" in n for n in r["notes"])
    assert CM.quadrant(r).startswith("evidence explainer")
