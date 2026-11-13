"""Topic brand files, rescore, open-time checks, transcript quality, bare suggestions and repeat builders.
No browser. Names in the fixtures are made up."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "reddit-research" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "youtube-research" / "scripts"))
import brands as B  # noqa: E402
import research_core as C  # noqa: E402
import reddit_tab as R  # noqa: E402
import tally as T  # noqa: E402
import youtube_signals as S  # noqa: E402
import youtube_tab as Y  # noqa: E402


@pytest.fixture
def topics(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "TOPICS", tmp_path / "topics")
    yield tmp_path / "topics"
    Y._use_topic(None)
    R._use_topic(None)


# --- topic brand files -------------------------------------------------------

def test_brand_file_add_merge_and_remove(topics):
    out = B.add("Smart Kettles", ["Quillo", "Brewzen"], aliases="Brewzen=Brew Zen,BrewZen 2", common="gooseneck")
    assert out["path"].endswith("smart-kettles.json")
    B.add("smart kettles", ["quillo", "Tavi"], everyday="Tavi Kettle=Tavi")  # same file, case-insensitive merge
    d = B.load("Smart Kettles")
    assert d["brands"] == ["Quillo", "Brewzen", "Tavi", "Tavi Kettle"]
    assert d["aliases"] == {"Brewzen": ["Brew Zen", "BrewZen 2"]}
    assert d["everyday"] == {"Tavi Kettle": ["Tavi"]} and d["common"] == ["gooseneck"]
    B.remove("Smart Kettles", ["Tavi"])
    assert "Tavi" not in B.load("Smart Kettles")["brands"]
    assert B.listing()[0]["brands"] == 3
    assert B.load("nothing here")["brands"] == []  # a missing file is empty, never an error


def test_brand_command_errors(topics):
    with pytest.raises(ValueError, match="--topic"):
        B.command("show")
    with pytest.raises(ValueError, match="needs names"):
        B.command("add", "x")


def test_youtube_topic_overlay_detects_and_resets(topics):
    B.add("smart kettles", ["Quillo"], aliases="Quillo=Quillo One", everyday="Tavi Kettle=Tavi")
    base = len(S.KNOWN_BRANDS)
    assert "Quillo" not in S.detect_brands("Quillo One review", "")
    Y._use_topic("smart kettles")
    assert "Quillo" in S.detect_brands("Quillo One review", "")
    assert "Tavi Kettle" in S.detect_brands("Is the Tavi worth it", "")       # everyday name, capitalized
    assert "Tavi Kettle" not in S.detect_brands("a tavi of tea", "")           # case-sensitive
    assert "quillo" in Y._KNOWN_FLAT
    Y._use_topic(None)
    assert len(S.KNOWN_BRANDS) == base and "Quillo" not in S.BRAND_ALIASES and not S.TOPIC_EVERYDAY
    assert "quillo" not in Y._KNOWN_FLAT


def test_reddit_topic_overlay_tokens(topics):
    B.add("smart kettles", ["Quillo", "Brew Zen"], everyday="Tavi Kettle=Tavi", common="Gooseneck")
    R._use_topic("smart kettles")
    assert {"quillo", "brewzen", "tavi"} <= R.KNOWN_BRANDS
    assert "tavi" in R.AMBIGUOUS and "gooseneck" in R.COMMON and "tavi" in T.COMMON_WORDS
    assert R.subreddit_brand("r/QuilloKettles", set()) == "quillo"
    R._use_topic(None)
    assert "quillo" not in R.KNOWN_BRANDS and "tavi" not in T.COMMON_WORDS


def test_tally_aliases_from_topic():
    data = {"aliases": {"Brewzen": ["Brew Zen"]}}
    assert B.tally_aliases(data, ["brewzen", "Quillo"], None) == {"brewzen": ["Brew Zen"]}
    assert B.tally_aliases(data, ["Brewzen"], {"Brewzen": ["BZ"]}) == {"Brewzen": ["BZ"]}  # the user's wins


# --- rescore -----------------------------------------------------------------

def _page(vid, channel, handle, title, desc=""):
    return {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title, "channel": channel,
            "handle": handle, "description": desc, "description_links": [], "length_s": 900, "published": "2026-10-01",
            "keywords": [], "disclosure": {}, "subscribers": 12000}


def test_rescore_rerates_from_saved_pages_and_full_transcripts(tmp_path, monkeypatch, topics):
    import channel_map as CM
    pages, trans, raw = tmp_path / "pages", tmp_path / "tx", tmp_path / "raw"
    for d in (pages, trans, raw):
        d.mkdir()
    reg_path = tmp_path / "channels.json"
    monkeypatch.setattr(Y, "PAGES", pages)
    monkeypatch.setattr(Y, "TRANSCRIPTS", trans)
    monkeypatch.setattr(Y, "CHANNELS", reg_path)
    p1 = _page("aaaaaaaaaaa", "Kettle Lab", "@kettlelab", "Smart kettle test")
    (pages / "aaaaaaaaaaa.json").write_text(json.dumps(p1))
    # an agent's raw copy of a second video, plus a grep-only transcript next to the full saved one
    p2 = _page("bbbbbbbbbbb", "Kettle Lab", "@kettlelab", "Kettle temperature accuracy")
    (raw / "v2.json").write_text(json.dumps({"video": p2}))
    full = [{"sec": i * 10, "text": f"we measured {i} degrees with a thermocouple, timed it twice"} for i in range(80)]
    (raw / "t2.json").write_text(json.dumps({"segments": full[:3]}))  # no video_id: grep output, ignored
    (trans / "2026-11-01_bbbbbbbbbbb_transcript.json").write_text(json.dumps({"video_id": "bbbbbbbbbbb",
                                                                              "segments": full}))
    reg = {"channels": {}}
    rating = CM.rate_channel("Kettle Lab", [Y._video_record(p1, {"score": 0.1, "evidence": []})],
                             {"handle": "@kettlelab"}, "smart kettles")
    rating["quadrant"] = CM.quadrant(rating)
    CM.registry_put(reg, rating)
    reg["channels"][next(iter(reg["channels"]))]["topics"]["smart kettles"]["videos"].append(p2["url"])
    CM.registry_save(reg_path, reg)

    with pytest.raises(C.Fail, match="Topics: smart kettles"):
        Y.rescore("vacuum robots")
    out = Y.rescore("Smart Kettles", [str(raw)])
    assert out["videos_rescored"] == 2 and out["missing_pages"] == [] and not out["saved"]
    row = out["channels"][0]
    assert row["videos"] == 2 and row["was"]["quadrant"] == rating["quadrant"]
    assert json.loads(reg_path.read_text()) == reg  # dry run: registry untouched
    out = Y.rescore("smart kettles", [str(raw)], save=True)
    assert out["saved"] and Path(out["backup"]).exists()


def test_save_page_keeps_screen_data(tmp_path, monkeypatch):
    monkeypatch.setattr(Y, "PAGES", tmp_path)
    Y._save_page({"video_id": "ccccccccccc", "title": "t", "sponsor_bias": {"score": 1}})
    Y._save_page({"video_id": "ccccccccccc", "title": "t"}, screen_data={"table_like": True})
    d = json.loads((tmp_path / "ccccccccccc.json").read_text())
    assert d["screen_data"] == {"table_like": True} and "sponsor_bias" not in d


# --- open-time checks ----------------------------------------------------------

def _site(tmp_path, monkeypatch, **kw):
    monkeypatch.setenv("TESTSITE_TAB_STATE", str(tmp_path))
    site = C.Site(prefix="TESTSITE_TAB", default_instance="t-01", hosts=("example.com",), ready_js="1",
                  page_state_js="1", stop_reason=lambda st, s: None if st.get("loggedIn") else "not signed in: ask",
                  **kw)
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    monkeypatch.setattr(C.Site, "preflight_screen", lambda self: None)
    monkeypatch.setattr(C.Site, "targets", lambda self, restore=False: [{"full_id": "AAA1", "id": "AAA1",
                                                                          "url": "about:blank"}])
    monkeypatch.setattr(C.Site, "_lone_tab", lambda self, blank, tabs: "AAA1")
    monkeypatch.setattr(C.Site, "place_windows", lambda self: None)
    monkeypatch.setattr(C.Site, "ensure_visible", lambda self, t, grace=3.0: None)
    return site


def test_open_stops_when_signed_out(tmp_path, monkeypatch):
    closed = []
    site = _site(tmp_path, monkeypatch, session_check=lambda s: False)
    monkeypatch.setattr(C.Site, "close", lambda self, t: closed.append(t) or {})
    res = site.open_window()
    assert res["stop"] == "not signed in: ask" and res["target"] is None and closed == ["AAA1"]


def test_open_warns_on_narrow_window(tmp_path, monkeypatch):
    site = _site(tmp_path, monkeypatch, session_check=lambda s: None, min_width=1300)
    monkeypatch.setattr(C.Site, "evaluate", lambda self, t, e, timeout=30: 1061)
    monkeypatch.setenv("TESTSITE_TAB_AEROSPACE_WORKSPACE", "9")
    monkeypatch.setattr(C.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(C.Site, "_aerospace", lambda self, *a: "Google Chrome\nGoogle Chrome\nGoogle Chrome\n")
    res = site.open_window()
    assert res["target"] == "AAA1" and res["window"]["width"] == 1061
    assert "3 browser windows share aerospace workspace 9" in res["window"]["warning"]


def test_youtube_session_from_cookies(monkeypatch):
    site = Y.SITE
    monkeypatch.setattr(type(site), "targets", lambda self, restore=False: [{"id": "T"}])
    monkeypatch.setattr(type(site), "cdp", lambda self, t, m, p=None, timeout=60: {"cookies": [{"name": "YSC"}]})
    assert Y._yt_session(site) is False
    monkeypatch.setattr(type(site), "cdp", lambda self, t, m, p=None, timeout=60:
                        {"cookies": [{"name": "LOGIN_INFO", "expires": -1}]})
    assert Y._yt_session(site) is True


# --- transcript quality ----------------------------------------------------------

def test_transcript_quality_flags_fragments_and_repeats():
    good = [{"sec": i * 4, "text": "and so we tested the kettle again with the lid off"} for i in range(300)]
    cov = S.transcript_coverage([{**s, "text": f"{s['text']} {i}"} for i, s in enumerate(good)], 1200)
    assert cov["usable"] and cov["words_per_minute"] > 100
    music = [{"sec": i * 30, "text": "[Music]"} for i in range(40)]
    cov = S.transcript_coverage(music, 1200)
    assert not cov["usable"] and "words per minute" in cov["quality_warning"] and "distinct" in cov["quality_warning"]


# --- bare suggestions and repeat builders ------------------------------------------

def test_bare_suggestion_counts_in_ask_threads_only():
    recs = [{"id": "p1", "thread": "p1", "via": "post", "title": "Best kettle for tea?", "author": "op"},
            {"id": "c1", "thread": "p1", "via": "comment", "depth": 0, "author": "u1", "text": "Quillo with the app off",
             "score": 4},
            {"id": "c2", "thread": "p1", "via": "comment", "depth": 0, "author": "u2",
             "text": "I spilled tea on my Quillo last week while cleaning the counter", "score": 2},
            {"id": "c3", "thread": "p1", "via": "comment", "depth": 1, "author": "u3", "text": "Quillo", "score": 1},
            {"id": "p2", "thread": "p2", "via": "post", "title": "My kitchen tour", "author": "op2"},
            {"id": "c4", "thread": "p2", "via": "comment", "depth": 0, "author": "u4", "text": "Quillo on the left",
             "score": 9}]
    row = T.tally(recs, ["Quillo"])["tally"][0]
    assert row["polarity"]["recommend"] == 1 and row["recommend_upvotes"] == 4


def test_repeat_builder_across_threads():
    d = {"url": "https://www.reddit.com/r/tea/comments/b/x/", "title": "Kettle apps?", "subreddit": "r/tea",
         "author": "op", "body": "which app?",
         "comments": [{"author": "janedev", "text": "I built Brewlog, a tiny kettle timer. Feedback welcome",
                       "link": "https://www.reddit.com/r/tea/comments/b/x/c1/"}]}
    first = R.promo_bias(d)
    assert [e["signal"] for e in first["evidence"]] == ["builder_self_promotion"]
    assert first["evidence"][0]["users"] == ["janedev"]
    again = R.promo_bias(d, prior_builders={"janedev": ["https://www.reddit.com/r/tea/comments/a/y/", d["url"]]})
    rb = next(e for e in again["evidence"] if e["signal"] == "repeat_builder")
    assert "1 other thread" in rb["text"] and again["score"] > first["score"]

