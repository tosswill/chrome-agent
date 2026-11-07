"""YouTube research helpers (integrations/claude/skills/youtube-research/scripts). No browser."""
import json
import struct
import sys
import zlib
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "youtube-research" / "scripts"))
import tally as T  # noqa: E402
import youtube_signals as S  # noqa: E402
import youtube_tab as Y  # noqa: E402


# --- Y1: transcript dedupe -------------------------------------------------

def _segs(n):
    return [{"at": f"0:{i:02d}", "sec": i, "text": f"segment {i}"} for i in range(n)]


def test_dedupe_doubled_transcript():
    doubled = _segs(30) + _segs(30)  # the whole list rendered twice
    out, removed = S.dedupe_segments(doubled)
    assert removed == 30 and len(out) == 30
    assert [s["sec"] for s in out] == list(range(30))


def test_dedupe_keeps_same_text_at_other_times():
    segs = [{"sec": 1, "text": "yeah"}, {"sec": 5, "text": "yeah"}, {"sec": 5, "text": "yeah "}]
    out, removed = S.dedupe_segments(segs)
    assert [s["sec"] for s in out] == [1, 5] and removed == 1


def test_disclosure_mentions_not_doubled():
    import disclosure as D
    segs = [{"at": "0:11", "sec": 11, "text": "Thanks to DJI for sponsoring this video."},
            {"at": "0:20", "sec": 20, "text": "Let's get into it."}] * 2
    hits = D.scan_segments(segs, "DJI Mic Mini review")
    assert len([h for h in hits if h["signal"] == "sponsor_read"]) == 1


# --- Y5 / Y6: sponsor_bias ---------------------------------------------------

def _video(title, desc="", label=False, links=None, channel="Some Reviewer", keywords=()):
    import disclosure as D
    links = links or []
    d = {"paid_promotion_label": label, "paid_promotion_text": "Includes paid promotion" if label else None,
         "affiliate_links": [], "campaign_links": [], "short_links": []}
    d = D.merge_link_classes(d, D.classify_links(links, channel=channel, description=desc))
    return {"title": title, "description": desc, "channel": channel, "keywords": list(keywords),
            "description_links": links, "disclosure": d}


def test_third_party_sponsor_downweights_paid_label():
    v = _video("Prusa Core One+ review: worth it?", "This video is sponsored by Voxel. Get filament at voxel.example",
               label=True)
    sb = Y.sponsor_bias(v)
    lab = next(e for e in sb["evidence"] if e["signal"] == "paid_promotion_label")
    assert lab["weight"] == 0.2 and lab["third_party_sponsor"] == ["Voxel"]
    assert sb["score"] < 0.6


def test_spoken_third_party_sponsor_downweights_label():
    import disclosure as D
    v = _video("Print farm test: X2D vs U1 vs Core One", label=True)
    hits = D.scan_segments([{"at": "4:44", "sec": 284, "text": "that board was manufactured by PCBWay, the sponsor of this video"}],
                           v["title"])
    sb = Y.sponsor_bias(v, hits)
    assert any(e["signal"] == "third_party_sponsor" for e in sb["evidence"])
    assert sb["score"] < 0.6


def test_brand_sponsor_keeps_full_label():
    v = _video("DJI Mic Mini review", "This video is sponsored by DJI.", label=True)
    sb = Y.sponsor_bias(v)
    lab = next(e for e in sb["evidence"] if e["signal"] == "paid_promotion_label")
    assert lab["weight"] == 0.6 and sb["score"] >= 0.6


def test_roundup_scores_per_brand():
    desc = ("This video is sponsored by Bambu Lab.\n"
            "Bambu Lab P2S:\nhttps://bambulab.com/p2s?aff=me\n"
            "Elegoo Centauri Carbon: https://elegoo.com/cc?aff=me\n"
            "Qidi Q2 https://qidi3d.com/q2\n"
            "Creality K2 https://creality.com/k2")
    v = _video("Best enclosed 3D printer: P2S vs Centauri Carbon vs Qidi Q2 vs K2", desc, label=True,
               links=["https://bambulab.com/p2s?aff=me", "https://elegoo.com/cc?aff=me", "https://qidi3d.com/q2",
                      "https://creality.com/k2"])
    sb = Y.sponsor_bias(v)
    assert sb.get("roundup") is True
    bb = sb["by_brand"]
    assert {"Bambu Lab", "Elegoo", "Qidi", "Creality"} <= set(bb)
    assert bb["Bambu Lab"]["score"] >= 0.6
    assert bb["Qidi"]["score"] < 0.6 and bb["Creality"]["score"] < 0.6
    assert "brand_affiliate_links" in bb["Elegoo"]["signals"]
    assert "brand_affiliate_links" not in bb["Qidi"]["signals"]
    rec = {"by_brand": bb, "by_brand_shared": sb["shared_score"], "sponsor_score": sb["score"]}
    assert S.score_for_term(rec, "Bambu P2S") >= 0.6
    assert S.score_for_term(rec, "Qidi Q2") < 0.6
    assert S.score_for_term(rec, "Centauri Carbon 2") == sb["shared_score"]


def test_creator_shop_is_not_brand_owned():
    v = _video("DJI Mic 3 vs Rode Wireless Pro", "My LUTs: https://stalman.com/shop", channel="Tyler Stalman",
               links=["https://stalman.com/shop"])
    sb = Y.sponsor_bias(v)
    assert "brand_owned" not in sb


# --- Y3 / Y17 ---------------------------------------------------------------

@pytest.mark.parametrize("title,channel,handle,flag", [
    ("Top 5 Best 3D Printers", "The Foremost Picks", "@TheForemostPicksof2027", True),
    ("Best 3D printer 2026", "101 Reviews Club", None, True),
    ("Top 10 lav mics", "Top5Lab", None, True),
    ("Best wireless mic", "DEAL SCOUUT", None, True),
    ("Top 7 Best Printers of 2027", "Gadget Guy", None, True),   # two weak reasons
    ("Bambu H2C review", "CNC Kitchen", "@CNCKitchen", False),
    ("Top 5 printers", "Uncle Jessy", None, False),              # one weak reason only
    ("I tested every lav mic", "Tech Deals", None, False),
])
def test_listicle_flag(title, channel, handle, flag):
    got, reasons = S.likely_ai_listicle(title, channel, handle, year=2026)
    assert got is flag and (bool(reasons) is flag)


def test_listicle_high_uploads():
    assert S.likely_ai_listicle("Top 10 earbuds", "Gear Reviewer", uploads=4000, year=2026)[0]


def test_amazon_influencer_flag():
    # Two or more signals flag a clip.
    assert S.amazon_influencer("MAYBESTA wireless lav review", "", ["https://amzn.to/x"], length_s=95, views=300)[0]
    assert S.amazon_influencer("Great mic #amazonfinds", "https://www.amazon.com/shop/someone")[0]
    assert not S.amazon_influencer("DJI Mic 3 long-term review", "", ["https://amzn.to/x"], length_s=1500, views=90_000)[0]


def test_amazon_influencer_single_signal_never_flags():
    # An 8.5-minute video from a 164K-subscriber channel with one storefront link (2026-11-06 false positive).
    flagged, why = S.amazon_influencer("EcoFlow Delta 3 review after a year",
                                       "Shop my picks: https://www.amazon.com/shop/someone",
                                       ["https://www.amazon.com/shop/someone"], length_s=510, views=40_000,
                                       subscribers=164_000)
    assert not flagged and why == []
    assert not S.amazon_influencer("Great mic #amazonfinds")[0]


# --- Y18 --------------------------------------------------------------------

def test_comment_promo_hint():
    cs = [{"author": "@pixelhaze42", "text": "dji's ecosystem just flows so much better for actual daily shooting fr", "age": "2 days ago"},
          {"author": "@lindawhite7731", "text": "ngl the DJI workflow is so smooth, love it", "age": "2 days ago"},
          {"author": "@SunnyRiverCo", "text": "honestly, I'd go with DJI just for the seamless workflow alone. super convenient", "age": "2 days ago"},
          {"author": "@someowner", "text": "My DJI Mic 3 clip fell off twice in 3 weeks, battery is about 7 hours.", "age": "1 week ago"}]
    hint = S.comment_promo_hints(cs, ["DJI"])
    assert hint and hint["level"] == "low" and hint["count"] == 3
    assert "promo_hint" not in cs[3]


def test_no_hint_for_single_comment():
    cs = [{"author": "@a12", "text": "DJI just works fr, love it"}]
    assert S.comment_promo_hints(cs, ["DJI"]) is None and "promo_hint" not in cs[0]


# --- Y7 / Y8 ----------------------------------------------------------------

def test_unique_path(tmp_path):
    used = set()
    p = tmp_path / "2026-11-06_abc_at00m00s.jpg"
    a = S.unique_path(p, used)
    b = S.unique_path(p, used)
    assert a == p and b.name == "2026-11-06_abc_at00m00s-2.jpg"
    (tmp_path / "x.jpg").write_text("")
    assert S.unique_path(tmp_path / "x.jpg", set()).name == "x-2.jpg"


def test_img_path_unique_within_call(tmp_path):
    used = set()
    a = Y._img_path(None, str(tmp_path), "d_vid_at01m00s", False, used)
    b = Y._img_path(None, str(tmp_path), "d_vid_at01m00s", False, used)
    assert a != b


def test_player_warning():
    assert S.player_warning(1057, True) and "1057" in S.player_warning(1057, True)
    assert S.player_warning(1588, True) is None
    assert S.player_warning(989, False, largest_seen=1492)
    assert S.player_warning(900, False) is None


# --- Y16: graphic score -----------------------------------------------------

def _png(w, h, px):
    raw = b"".join(b"\x00" + bytes(c for x in range(w) for c in px(x, y)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_graphic_score_slide_beats_face():
    def slide(x, y):  # white slide with dark text lines and table rules
        if y % 12 in (0, 1) or (y % 12 < 7 and (x // 3) % 3 == 0 and 20 < x < 140):
            return (20, 20, 20)
        return (250, 250, 250)

    def face(x, y):  # a smooth skin-toned blob on a blurred background
        d = ((x - 80) ** 2 + (y - 45) ** 2) ** 0.5
        if d < 35:
            return (200 - int(d), 140 - int(d / 2), 110 - int(d / 3))
        return (60 + x // 4, 70 + y // 3, 90 + (x + y) // 8)

    s = S.graphic_score(_png(160, 90, slide))
    f = S.graphic_score(_png(160, 90, face))
    assert s["score"] > f["score"] + 0.2
    assert f["skin"] > 0.1


def test_png_decoder_handles_filters():
    # Hand-made 2x2 RGB PNG using the Sub (1) and Up (2) filters.
    raw = b"\x01" + bytes([10, 20, 30, 5, 5, 5]) + b"\x02" + bytes([1, 1, 1, 2, 2, 2])
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    w, h, rows, bpp = S._png_rgb(png)
    assert (w, h, bpp) == (2, 2, 3)
    assert list(rows[0]) == [10, 20, 30, 15, 25, 35] and list(rows[1]) == [11, 21, 31, 17, 27, 37]


# --- Y12: saved transcripts -------------------------------------------------

def test_save_transcript(tmp_path):
    segs = [{"at": "0:00", "sec": 0, "text": "hello", "link": "https://www.youtube.com/watch?v=abcdefghijk&t=0s"},
            {"at": "1:05", "sec": 65, "text": "world", "link": "https://www.youtube.com/watch?v=abcdefghijk&t=65s"}]
    meta = {"video_id": "abcdefghijk", "title": "T", "channel": "C", "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "published": "2026-10-01", "language": "English (auto-generated)",
            "sponsor_bias": {"score": 0.45, "level": "medium", "confidence": "medium",
                             "evidence": [{"signal": "third_party_sponsor", "weight": 0.15, "source": "transcript 0:11", "text": "x"}]}}
    r = S.save_transcript(tmp_path, meta, segs, today=date(2026, 11, 6))
    assert r["reused"] is False and r["json"].endswith("2026-11-06_abcdefghijk_transcript.json")
    doc = json.loads(Path(r["json"]).read_text())
    assert doc["total_segments"] == 2 and doc["segments"][1] == {"sec": 65, "at": "1:05", "link": segs[1]["link"], "text": "world"}
    assert doc["sponsor_bias"]["score"] == 0.45 and doc["fetched_at"]
    md = Path(r["md"]).read_text()
    assert f"[1:05]({segs[1]['link']}) world" in md and "Sponsor bias at fetch: 0.45" in md
    again = S.save_transcript(tmp_path, meta, segs, today=date(2026, 11, 7))
    assert again["reused"] is True and again["json"] == r["json"]


# --- S1: tally wiring -------------------------------------------------------

def test_loaded_video_merges_with_search_record(tmp_path, monkeypatch):
    monkeypatch.setattr(Y.SITE, "state", tmp_path)
    fid = "ABCDEF0123456789"
    Y.SITE._write_claim(fid, {"created": 0, "last_used": 0, "loads": 0})
    Y._seen_add(fid, [{"id": "V3X8Xe5W04g", "url": "https://www.youtube.com/watch?v=V3X8Xe5W04g", "via": "search",
                       "title": "Bambu H3C review", "channel": "CNC Kitchen"},
                      {"id": "zzzzzzzzzzz", "url": "u2", "via": "search", "title": "Snapmaker U1 first look",
                       "channel": "Top5Lab", "likely_ai_listicle": True}],
                {"Top5Lab": {"listicle": True}})
    data = {"video_id": "V3X8Xe5W04g", "url": "https://www.youtube.com/watch?v=V3X8Xe5W04g", "title": "Bambu H3C review",
            "channel": "CNC Kitchen", "description": "Links: H2C, H2D and the Snapmaker U1",
            "description_links": ["https://bambulab.com/h2d"]}
    Y._seen_add(fid, [Y._video_record(data, {"score": 0.35, "transcript_scanned": False})])
    items = Y._seen_load(fid)["items"]
    assert len(items) == 2 and items[0]["via"] == "video"
    res = Y.tally(fid, ["H2D", "Snapmaker U1", "H3C"])
    rows = {r["term"]: r for r in res["tally"]}
    assert rows["H2D"]["channels"] == 1 and rows["H2D"]["independent_channels"] == 1
    assert rows["Snapmaker U1"]["channels"] == 2 and rows["Snapmaker U1"]["independent_channels"] == 1
    assert rows["Snapmaker U1"]["excluded"].get("listicle") == 1
    assert rows["H3C"]["examples"][0]["link"]
    assert res["loaded_videos"] == 1


def test_sponsored_video_not_independent_and_transcript_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(Y.SITE, "state", tmp_path)
    fid = "FEDCBA9876543210"
    Y.SITE._write_claim(fid, {"created": 0, "last_used": 0, "loads": 0})
    data = {"video_id": "WtVT6gLdgoI", "url": "https://www.youtube.com/watch?v=WtVT6gLdgoI", "title": "Best mic for vlogging",
            "channel": "Tyler Stalman", "description": "", "description_links": []}
    Y._seen_add(fid, [Y._video_record(data, {"score": 0.3})])
    segs = [{"text": "the DJI Mic Mini is my pick", "link": "https://www.youtube.com/watch?v=WtVT6gLdgoI&t=11s"}]
    Y._seen_add(fid, [Y._video_record(data, {"score": 1.0, "transcript_scanned": True}, segs)])
    row = Y.tally(fid, ["DJI Mic Mini"])["tally"][0]
    assert row["channels"] == 1 and row["independent_channels"] == 0
    assert row["examples"][0]["field"] == "transcript"
    assert Y.tally(fid, ["DJI Mic Mini"], include_transcripts=False)["tally"][0]["items"] == 0


def test_tally_aliases(tmp_path, monkeypatch):
    monkeypatch.setattr(Y.SITE, "state", tmp_path)
    fid = "0011223344556677"
    Y.SITE._write_claim(fid, {"created": 0, "last_used": 0, "loads": 0})
    Y._seen_add(fid, [{"id": "aaaaaaaaaaa", "via": "search", "title": "Lark M2S range test", "channel": "A"}])
    row = Y.tally(fid, ["Lark M2"], aliases="Lark M2=M2S")["tally"][0]
    assert row["channels"] == 1


def test_video_id_of():
    assert Y.video_id_of("https://youtu.be/ILY1WO-ju50?t=3") == "ILY1WO-ju50"
    assert Y.video_id_of("https://www.youtube.com/watch?v=ILY1WO-ju50&t=4s") == "ILY1WO-ju50"
    assert Y.video_id_of("https://www.youtube.com/shorts/ILY1WO-ju50") == "ILY1WO-ju50"
    assert Y.video_id_of("ILY1WO-ju50") == "ILY1WO-ju50"


def test_scan_points():
    assert Y._scan_points(0, 15, 4) == [0]
    pts = Y._scan_points(720, 1140, 4)
    assert len(pts) == 4 and 720 < pts[0] < pts[-1] < 1140


def test_spaced_third_party_sponsor_name_gets_the_label():
    import disclosure as D
    desc = ("Snapmaker U1: https://snapmaker.sjv.io/x\nElegoo CC2: https://elegoo.sjv.io/y\n"
            "Boards by PCBWay: https://pcbway.com/?from=me\nBambu Lab P2S and Prusa Core One are in the test too.")
    v = _video("Bambu vs Prusa vs Snapmaker vs Elegoo - Tested to Failure", desc, label=True,
               links=["https://snapmaker.sjv.io/x", "https://elegoo.sjv.io/y", "https://pcbway.com/?from=me"])
    hits = D.scan_segments([{"at": "4:44", "sec": 284, "text": "that board was manufactured by PCB way, the sponsor of this video."}],
                           v["title"])
    sb = Y.sponsor_bias(v, hits)
    bb = sb["by_brand"]
    assert "Nothing" not in bb
    assert bb["Bambu Lab"]["score"] < 0.6 and "paid_promotion_label" not in bb["Bambu Lab"]["signals"]
    assert sb["shared_score"] < 0.6


def test_outermost_ads():
    r = lambda y, h: {"x": 96, "y": y, "width": 923, "height": h}
    ads = [{"selector": "ytd-ad-slot-renderer", "rect": r(128, 266)},
           {"selector": "ytd-in-feed-ad-layout-renderer", "rect": r(128, 266)},
           {"selector": "ytd-search-pyv-renderer", "rect": r(112, 282)},
           {"selector": "ytd-ad-slot-renderer", "rect": r(967, 280)}]
    out = S.outermost_ads(ads)
    assert [a["rect"]["y"] for a in out] == [112, 967]



def test_quiet_after_recovers_once_at_end_on_watch_page(monkeypatch):
    calls = []
    monkeypatch.setattr(Y.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(Y.SITE, "evaluate", lambda fid, js, timeout=30: "/watch" if "pathname" in js else False)
    monkeypatch.setattr(Y, "_ensure_quiet", lambda fid: calls.append(fid) or {})
    wrapped = Y._quiet_after(lambda target, x: x * 2)
    assert wrapped("T", 21) == 42
    assert calls == ["T"]


def test_quiet_after_skips_off_watch_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(Y.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(Y.SITE, "evaluate", lambda fid, js, timeout=30: "/results" if "pathname" in js else False)
    monkeypatch.setattr(Y, "_ensure_quiet", lambda fid: calls.append(fid) or {})
    Y._quiet_after(lambda target: None)("T")
    assert calls == []


def test_ensure_quiet_escapes_only_when_fullscreen(monkeypatch):
    keys = []
    state = {"fs": True}
    def ev(fid, js, timeout=30):
        if "fullscreenElement" in js:
            return state["fs"]
        return False  # no <video>
    def key(fid, k, *a, **kw):
        keys.append(k); state["fs"] = False
    monkeypatch.setattr(Y.SITE, "evaluate", ev)
    monkeypatch.setattr(Y, "_key", key)
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    assert Y._ensure_quiet("T")["left_fullscreen"] is True
    Y._ensure_quiet("T")
    assert keys == ["Escape"]  # pressed once, only while fullscreen


# --- chapters / duration / search settling (video.js, search_results.js) -----

def test_video_js_chapters_and_duration_present():
    js = (ROOT / "skills" / "youtube-research" / "scripts" / "video.js").read_text()
    assert "chapters_source" in js and "duration:" in js
    assert "get(\"v\") === vd.videoId" in js  # markers are filtered to this video's own links


def test_search_results_js_reads_visible_results_only():
    js = (ROOT / "skills" / "youtube-research" / "scripts" / "search_results.js").read_text()
    assert "getClientRects().length > 0" in js and "ytd-search ytd-video-renderer" in js


def test_settle_results_waits_for_stable_echoed_list(monkeypatch):
    reads = iter([{"on_results": True, "echoed": False, "n": 2, "first": "a"},
                  {"on_results": True, "echoed": True, "n": 9, "first": "b"},
                  {"on_results": True, "echoed": True, "n": 15, "first": "b"},
                  {"on_results": True, "echoed": True, "n": 15, "first": "b"}])
    monkeypatch.setattr(Y.SITE, "evaluate", lambda *a, **k: next(reads))
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    assert Y._settle_results("T", "lav mic") == {"settled": True, "results_visible": 15}


def test_settle_results_times_out(monkeypatch):
    monkeypatch.setattr(Y.SITE, "evaluate", lambda *a, **k: {"on_results": True, "echoed": False, "n": 3})
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    res = Y._settle_results("T", "q", timeout=0.01)
    assert res["settled"] is False and "partial" in res["note"]


def test_settle_results_stable_empty_page(monkeypatch):
    monkeypatch.setattr(Y.SITE, "evaluate", lambda *a, **k: {"on_results": True, "echoed": True, "n": 0})
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    res = Y._settle_results("T", "q")
    assert res["settled"] is True and res["results_visible"] == 0 and "Shorts" in res["note"]


# --- content-farm clips -----------------------------------------------------------

def test_near_duplicate_clips_flags_one_channel():
    rs = [{"channel": "Origin Finds", "title": f"This carpet cleaner is amazing #{i}", "length": "0:41"} for i in range(4)]
    rs += [{"channel": "Vacuum Wars", "title": "Best carpet cleaners 2026", "length": "18:02"},
           {"channel": "Solo", "title": "Bissell Big Green review", "length": "0:59"}]
    out = S.near_duplicate_clips(rs)
    assert list(out) == ["Origin Finds"] and "near-identical" in out["Origin Finds"][0]


def test_near_duplicate_clips_ignores_long_or_distinct():
    rs = [{"channel": "A", "title": t, "length": "12:00"} for t in ("x one test", "x one test", "x one test")]
    rs += [{"channel": "B", "title": t, "length": "0:30"} for t in ("Shark review", "Bissell steam", "Hoover leak fix")]
    assert S.near_duplicate_clips(rs) == {}


# --- brands: gear lists, brand+word hosts --------------------------------------------

_DESC = """Best power stations tested.
Bluetti AC180: https://www.bluettipower.com/products/ac180?ref=abc
Anker SOLIX: https://ankerfast.club/anker.solix_tsl
EcoFlow Delta 3: https://amzn.to/abc

Current Video Gear
Camera: Sony A7IV https://amzn.to/s1
Mic: Rode Wireless Pro https://amzn.to/r1
Phone: Samsung S24

Filmed with a Canon R5"""


def test_detect_brands_skips_gear_and_finds_brand_hosts():
    links = ["https://www.bluettipower.com/products/ac180?ref=abc", "https://ankerfast.club/anker.solix_tsl",
             "https://amzn.to/abc", "https://amzn.to/s1", "https://amzn.to/r1"]
    found = S.detect_brands("Bluetti vs EcoFlow vs Anker", _DESC, links)
    assert set(found) == {"Bluetti", "EcoFlow", "Anker"}
    assert not {"Sony", "Rode", "Samsung", "Canon"} & set(found)


def test_strip_gear_blocks():
    out = S.strip_gear_blocks(_DESC)
    assert "Sony" not in out and "Rode" not in out and "Canon" not in out and "Bluetti AC180" in out


def test_per_brand_attributes_affiliate_link_to_bluetti():
    import disclosure as D
    links = ["https://www.bluettipower.com/products/ac180?ref=abc", "https://amzn.to/abc"]
    d = D.classify_links(links, channel="The Solar Lab", description=_DESC)
    pb = S.per_brand([], ["Bluetti", "EcoFlow", "Anker"], _DESC, d)
    assert "brand_affiliate_links" in pb["by_brand"]["Bluetti"]["signals"]


# --- transcript coverage ------------------------------------------------------------------

def test_transcript_coverage_finds_gaps_and_tail():
    segs = [{"sec": s, "text": str(s)} for s in list(range(0, 306, 5)) + [446, 538]]
    cov = S.transcript_coverage(segs, 600)
    assert not cov["complete"]
    assert [(g["from"], g["to"]) for g in cov["gaps"]] == [("5:05", "7:26"), ("7:26", "8:58")]
    assert cov["missing_tail_s"] == 62


def test_transcript_coverage_complete():
    segs = [{"sec": s, "text": str(s)} for s in range(0, 600, 4)]
    assert S.transcript_coverage(segs, 610)["complete"]


def test_merge_segments_unions_and_sorts():
    a = [{"sec": 0, "text": "a"}, {"sec": 10, "text": "b"}]
    b = [{"sec": 10, "text": "b"}, {"sec": 5, "text": "m"}]
    assert [s["sec"] for s in S.merge_segments(a, b)] == [0, 5, 10]


def test_collect_transcript_scrolls_until_bottom(monkeypatch):
    pages = [[{"sec": 0, "text": "a"}], [{"sec": 30, "text": "b"}], [{"sec": 60, "text": "c"}], [], []]
    state = {"i": 0}
    boxes = [{"x": 0, "y": 0, "width": 300, "height": 400, "top": 0, "max": 900}]

    def ev(t, js, timeout=30):
        if js == Y._TRANSCRIPT_BOX_JS:
            top = min(900, 300 * state["i"])
            return {**boxes[0], "top": top}
        segs = pages[min(state["i"], len(pages) - 1)]
        return {"segments": segs}

    def wheel(t, dy, x=None, y=None, area=None):
        state["i"] += 1
    monkeypatch.setattr(Y.SITE, "evaluate", ev)
    monkeypatch.setattr(Y.SITE, "wheel", wheel)
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    segs, info = Y._collect_transcript("T", [{"sec": 0, "text": "a"}], 70)
    assert [s["sec"] for s in segs] == [0, 30, 60] and info["scrolled"] and info["reached_bottom"]


def test_collect_transcript_keeps_scrolling_past_idle_stretch(monkeypatch):
    # Live 2026-11-07: rows in later chapters appear only near the end; 6 idle steps mid-list isn't the end.
    state = {"i": 0}

    def ev(t, js, timeout=30):
        if js == Y._TRANSCRIPT_BOX_JS:
            return {"x": 0, "y": 0, "width": 300, "height": 400, "top": min(3000, 300 * state["i"]), "max": 3000}
        return {"segments": [{"sec": 600, "text": "late"}] if state["i"] >= 9 else []}

    def wheel(t, dy, x=None, y=None, area=None):
        state["i"] += 1
    monkeypatch.setattr(Y.SITE, "evaluate", ev)
    monkeypatch.setattr(Y.SITE, "wheel", wheel)
    monkeypatch.setattr(Y, "pause", lambda *a, **k: None)
    segs, info = Y._collect_transcript("T", [{"sec": 0, "text": "a"}], 700)
    assert [s["sec"] for s in segs] == [0, 600] and info["reached_bottom"]


def test_gap_note_names_incomplete_creator_track():
    assert "creator-uploaded" in Y._gap_note(["en", "en (auto)"])
    assert "silent" in Y._gap_note(["en (auto)"])


def test_save_transcript_replaces_partial_copy(tmp_path):
    meta = {"video_id": "pfoegHxzzm8", "title": "t"}
    S.save_transcript(tmp_path, meta, [{"sec": 0, "text": "a"}], today=date(2026, 11, 7))
    again = S.save_transcript(tmp_path, {**meta, "coverage": {"gaps": []}},
                              [{"sec": 0, "text": "a"}, {"sec": 9, "text": "b"}], today=date(2026, 11, 8))
    assert not again["reused"] and "replaced_partial" in again
    assert len(list(tmp_path.glob("*_pfoegHxzzm8_transcript.json"))) == 1
    same = S.save_transcript(tmp_path, meta, [{"sec": 0, "text": "a"}], today=date(2026, 11, 9))
    assert same["reused"]
