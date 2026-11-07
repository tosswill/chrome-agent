"""research_core pure helpers: screenshot clip math and pacing. No browser."""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import research_core as R  # noqa: E402

VP = {"x": 0, "y": 0, "width": 1200, "height": 800}


def test_intersect():
    assert R.intersect({"x": 0, "y": 0, "width": 10, "height": 10}, {"x": 5, "y": 5, "width": 10, "height": 10}) == \
        {"x": 5, "y": 5, "width": 5, "height": 5}
    assert R.intersect({"x": 0, "y": 0, "width": 10, "height": 10}, {"x": 20, "y": 0, "width": 5, "height": 5}) is None


def test_page_clip_adds_scroll_offset():
    clip = R.page_clip({"x": 100, "y": 50, "width": 300, "height": 200}, scroll_x=0, scroll_y=2400, viewport=VP)
    assert clip == {"x": 100, "y": 2450, "width": 300, "height": 200}


def test_page_clip_clamps_to_viewport_and_area():
    area = {"x": 250, "y": 64, "width": 950, "height": 736}  # no top bar, no left nav
    tall = {"x": 300, "y": -100, "width": 600, "height": 2000}
    clip = R.page_clip(tall, scroll_x=0, scroll_y=1000, viewport=VP, area=area, pad=8)
    assert clip == {"x": 292, "y": 1064, "width": 616, "height": 736}


def test_page_clip_offscreen_is_none():
    assert R.page_clip({"x": 0, "y": 900, "width": 100, "height": 100}, 0, 0, viewport=VP) is None


def test_view_js_embeds_selectors():
    js = R._view_js("shreddit-comment[thingid='t1_x']", None, ["shreddit-ad-post"])
    assert "shreddit-ad-post" in js and "t1_x" in js and "scrollY" in js


def test_pace_delay_is_skewed_and_bounded():
    rng = random.Random(7)
    d = [R.pace_delay(5.0, rng=rng) for _ in range(20000)]
    short = [x for x in d if x < 20]
    longs = [x for x in d if x >= 20]
    assert min(d) >= 4.5
    assert 0.10 < len(longs) / len(d) < 0.15  # about 1 in 8
    assert max(longs) <= 60 and max(short) <= 5 * 1.1 + 5 * 3.0
    assert statistics.median(short) < statistics.mean(short)  # right-skewed
    assert all(R.pace_delay(5.0, long_p=0, rng=rng) < 20 for _ in range(2000))


def test_parse_range():
    assert R._parse_range("10-30", (20.0, 60.0)) == (10.0, 30.0)
    assert R._parse_range("bad", (20.0, 60.0)) == (20.0, 60.0)


def test_ensure_visible_fails_fast_when_hidden(monkeypatch):
    import pytest
    import research_core as C
    site = C.Site.__new__(C.Site)
    states = iter(["hidden", "hidden"])
    monkeypatch.setattr(C.Site, "evaluate", lambda self, t, e, timeout=30: next(states))
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    monkeypatch.setattr(C.Site, "place_windows", lambda self: None)
    monkeypatch.setattr(C.Site, "screen_state", staticmethod(lambda: None))
    with pytest.raises(C.Fail, match="hidden"):
        site.ensure_visible("T")


def test_ensure_visible_tolerates_brief_hidden(monkeypatch):
    import pytest
    import research_core as C
    site = C.Site.__new__(C.Site)
    states = iter(["hidden", "visible"])
    monkeypatch.setattr(C.Site, "evaluate", lambda self, t, e, timeout=30: next(states))
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    monkeypatch.setattr(C.Site, "place_windows", lambda self: None)
    monkeypatch.setattr(C.Site, "screen_state", staticmethod(lambda: None))
    site.ensure_visible("T")  # no raise


def test_place_windows_moves_only_own_browser(monkeypatch):
    import research_core as C
    site = C.Site.__new__(C.Site)
    calls = []
    def fake(self, *args):
        calls.append(args)
        if args[0] == "list-windows":
            return "11|500|4\n12|500|9\n13|777|7\n"
        if args[:2] == ("list-workspaces", "--monitor"):
            return "1\n4\n9\n"
        return ""
    monkeypatch.setenv("RESEARCH_AEROSPACE_WORKSPACE", "9")
    monkeypatch.setattr(C.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(C.Site, "_aerospace", fake)
    monkeypatch.setattr(C.Site, "_browser_pid", lambda self: 500)
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    res = site.place_windows()
    assert res == {"workspace": "9", "moved": 1, "shown": False}
    moves = [c for c in calls if c[0] == "move-node-to-workspace"]
    assert moves == [("move-node-to-workspace", "--window-id", "11", "9")]  # never 13 (the user's Chrome)
    assert not any(c[0] == "workspace" for c in calls)  # already visible: no focus change


def test_place_windows_off_without_setting(monkeypatch):
    import research_core as C
    monkeypatch.delenv("RESEARCH_AEROSPACE_WORKSPACE", raising=False)
    assert C.Site.__new__(C.Site).place_windows() is None


def test_lone_tab_reuses_only_a_blank_tab_alone_in_its_window(monkeypatch):
    import research_core as C
    site = C.Site.__new__(C.Site)
    windows = {"A": 1, "B": 2, "C": 2, "D": 3}
    monkeypatch.setattr(C.Site, "_window_of", lambda self, fid: windows[fid])
    tabs = [{"full_id": k} for k in windows]
    assert site._lone_tab([{"full_id": "C"}], tabs) is None   # shares window 2 with B
    assert site._lone_tab([{"full_id": "C"}, {"full_id": "A"}], tabs) == "A"


def _fake_ioreg(monkeypatch, users):
    import plistlib
    import subprocess
    import research_core as C
    out = plistlib.dumps({"IOConsoleUsers": users})
    monkeypatch.setattr(C.shutil, "which", lambda name: "/usr/sbin/ioreg")
    monkeypatch.setattr(C.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=b""))
    return C


def test_screen_state_locked(monkeypatch):
    C = _fake_ioreg(monkeypatch, [{"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}])
    assert C.Site.screen_state() == "locked"


def test_screen_state_unlocked_ignores_other_sessions(monkeypatch):
    import ctypes
    C = _fake_ioreg(monkeypatch, [{"kCGSSessionOnConsoleKey": True},
                                  {"kCGSSessionOnConsoleKey": False, "CGSSessionScreenIsLocked": True}])
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("no CG")))
    assert C.Site.screen_state() is None


def test_preflight_screen_blocks_open_and_hidden_message(monkeypatch):
    import pytest
    import research_core as C
    site = C.Site.__new__(C.Site)
    monkeypatch.setattr(C.Site, "screen_state", staticmethod(lambda: "locked"))
    with pytest.raises(C.Fail, match="screen is locked"):
        site.open_window()
    monkeypatch.setattr(C.Site, "evaluate", lambda self, t, e, timeout=30: "hidden")
    with pytest.raises(C.Fail, match="unlock"):
        site.ensure_visible("T")


def test_timings_attach_to_results_and_errors():
    import research_core as C
    C.timings_reset()
    with C.timed("page_load"):
        pass
    with C.timed("page_load"):
        pass
    out = C.attach_timings({"error": "x"})
    assert out["timings"]["steps"]["page_load"]["n"] == 2
    assert out["timings"]["total_s"] >= 0
    assert C.attach_timings([1]) == [1]  # non-dict results pass through


def test_run_cli_adds_timings_on_failure(capsys):
    import json
    import research_core as C

    def dispatch(cmd, args):
        with C.timed("visibility_check"):
            raise C.Fail("hidden")
    assert C.run_cli("doc", dispatch, ["x", "open"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "hidden" and "visibility_check" in out["timings"]["steps"]
