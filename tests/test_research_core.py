"""research_core pure helpers: screenshot clip math and pacing. No browser."""
import random
import pytest
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
    monkeypatch.delenv("RESEARCH_AEROSPACE_PARK", raising=False)
    monkeypatch.setattr(C.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(C.Site, "_aerospace", fake)
    monkeypatch.setattr(C.Site, "_browser_pid", lambda self: 500)
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    res = site.place_windows()
    assert res == {"workspace": "9", "moved": 1, "shown": False}
    moves = [c for c in calls if c[0] == "move-node-to-workspace"]
    assert moves == [("move-node-to-workspace", "--window-id", "11", "9")]  # never 13 (the user's Chrome)
    assert not any(c[0] == "workspace" for c in calls)  # already visible: no focus change


def test_place_windows_parks_idle_windows_without_switching(monkeypatch):
    import research_core as C
    site = C.Site.__new__(C.Site)
    calls = []

    def fake(self, *args):
        calls.append(args)
        if args[0] == "list-windows":
            return ("11|500|9|research-idle - Google Chrome\n12|500|4|Some video - YouTube - Google Chrome\n"
                    "13|500|8|research-idle - Google Chrome\n14|777|9|research-idle - Google Chrome\n")
        if args[:2] == ("list-workspaces", "--monitor"):
            return "1\n5\n9\n"
        return ""
    monkeypatch.setenv("RESEARCH_AEROSPACE_WORKSPACE", "9")
    monkeypatch.setenv("RESEARCH_AEROSPACE_PARK", "8")
    monkeypatch.setattr(C.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.setattr(C.Site, "_aerospace", fake)
    monkeypatch.setattr(C.Site, "_browser_pid", lambda self: 500)
    monkeypatch.setattr(C.Site, "_mark_idle_tabs", lambda self: None)
    monkeypatch.setattr(C, "pause", lambda *a, **k: None)
    res = site.place_windows()
    assert res == {"workspace": "9", "moved": 1, "shown": False, "park": "8", "parked": 1}
    moves = sorted(c for c in calls if c[0] == "move-node-to-workspace")
    assert moves == [("move-node-to-workspace", "--window-id", "11", "8"),
                     ("move-node-to-workspace", "--window-id", "12", "9")]  # 13 already parked; 14 isn't ours
    assert not any(c[0] == "workspace" for c in calls)  # never switches workspaces


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


def test_nested_timings_not_double_counted():
    import time as _t
    import research_core as rc
    rc.timings_reset()
    with rc.timed("scroll_to"):
        with rc.timed("wheel"):
            _t.sleep(0.05)
    out = rc.attach_timings({})["timings"]
    assert out["steps"]["scroll_to"]["n"] == 1 and out["steps"]["wheel"]["n"] == 1
    assert out["other_s"] < 0.04  # the wheel is inside scroll_to, counted once


def test_raw_save_index_and_repeat_guard(tmp_path, monkeypatch):
    import json as _j
    import research_core as rc
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path))
    monkeypatch.setattr(rc._LOG, "site", "demo", raising=False)
    name = rc.save_raw("search", ["ABCD1234", "Mini  PC homelab"], {"page": {"url": "https://x.com/s"}, "posts": [{"truncated": True}]})
    assert name and (tmp_path / "demo" / name).exists()
    rc.save_raw("search", ["ABCD1234", "other"], {"error": "boom"})
    idx = [_j.loads(l) for l in (tmp_path / "demo" / "index.jsonl").read_text().splitlines()]
    assert idx[0]["partial"] == ["truncated"] and idx[0]["url"] == "https://x.com/s"
    assert idx[1]["ok"] is False and idx[1]["file"] is None
    # same search from another window, different spacing/case: refused; a new wording or command isn't
    assert rc.repeat_search("search", ["ZZZZ9999", "mini pc  HOMELAB"])["file"] == name
    assert rc.repeat_search("search", ["ZZZZ9999", "mini pc proxmox"]) is None
    assert rc.repeat_search("thread", ["ZZZZ9999", "mini pc homelab"]) is None
    assert rc.save_raw("close", ["ABCD1234"], {"closed": "x"}) is None


def test_no_raw_dir_saves_nothing(monkeypatch):
    import research_core as rc
    monkeypatch.delenv("RESEARCH_RAW_DIR", raising=False)
    assert rc.save_raw("search", ["A", "q"], {"posts": []}) is None and rc.repeat_search("search", ["A", "q"]) is None


def test_verify_pass_sees_research_searches(tmp_path, monkeypatch):
    import research_core as rc
    monkeypatch.setattr(rc._LOG, "site", "x", raising=False)
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path))
    name = rc.save_raw("search", ["A", "mini pc homelab"], {"posts": []})
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "verify"))
    hit = rc.repeat_search("search", ["B", "mini pc homelab"])
    assert hit and hit["file"] == f"x/{name}"
    assert rc.repeat_search("search", ["B", "mini pc homelab measured watts"]) is None


def test_repeat_guard_ignores_max(tmp_path, monkeypatch):
    import research_core as rc
    monkeypatch.setattr(rc._LOG, "site", "x", raising=False)
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path))
    rc.save_raw("search", ["A", "q", "--max", "10"], {"posts": []})
    assert rc.repeat_search("search", ["B", "q", "--max", "40"])
    assert rc.repeat_search("search", ["B", "q", "--latest"]) is None
    assert rc.save_raw("open", ["--wait"], {"target": "A"}) is None


def test_repeat_guard_scope_and_override(tmp_path, monkeypatch):
    import research_core as rc
    monkeypatch.setattr(rc._LOG, "site", "reddit", raising=False)
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path))
    rc.save_raw("search", ["A", "mini pc", "--sub", "homelab"], {"posts": []})
    assert rc.repeat_search("search", ["A", "mini pc", "--sub", "selfhosted"]) is None
    assert rc.repeat_search("search", ["A", "mini pc", "--sub", "homelab", "--sort", "new"]) is None
    assert rc.repeat_search("search", ["A", "mini pc", "--sub", "homelab"])
    monkeypatch.setenv("RESEARCH_ALLOW_REPEAT", "1")
    assert rc.repeat_search("search", ["A", "mini pc", "--sub", "homelab"]) is None


def _held_site(tmp_path, monkeypatch, signed_in=None):
    import research_core as C
    monkeypatch.setenv("TSTHOLD_TAB_STATE", str(tmp_path / "state"))
    site = C.Site(prefix="TSTHOLD_TAB", default_instance="tst", hosts=("example.com",), ready_js="1",
                  page_state_js="1", stop_reason=lambda st, s: None,
                  session_check=(lambda s: signed_in) if signed_in is not None else None)
    stops = []
    monkeypatch.setattr(C.Site, "ca", lambda self, *a, **k: stops.append(a) or "")
    monkeypatch.setattr(C.Site, "workspace", lambda self: "9")
    monkeypatch.setattr(C.Site, "targets", lambda self, restore=False: [])
    return C, site, stops


def test_held_window_survives_close_and_gc(tmp_path, monkeypatch):
    C, site, stops = _held_site(tmp_path, monkeypatch)
    site._write_claim("ABCDEF123", {"created": 0, "last_used": 0, "loads": 1})
    info = site.hold("ABCDEF123", "not signed in", "https://example.com/")
    assert info["workspace"] == "9" and "chrome-agent save tst --overwrite" in info["user_action"]
    res = site.close("ABCDEF")
    assert res["closed"] is None and res["left_open"] == "ABCDEF123" and not stops
    site.idle = 0  # everything counts as idle: a held window still isn't reaped
    with site._locked("claims.lock"):
        assert site._gc_locked({"ABCDEF123"}) == []
    assert site.close("ABCDEF", force=True) == {"closed": "ABCDEF123"}
    assert ("stop", "tst", "--target-id", "ABCDEF123") in stops and "ABCDEF123" not in site.claims()


def test_holds_released_once_signed_in(tmp_path, monkeypatch):
    C, site, stops = _held_site(tmp_path, monkeypatch, signed_in=False)
    site.hold("HELD1", "not signed in")
    assert site._release_holds_if_signed_in() == [] and "HELD1" in site.claims()
    site.session_check = lambda s: True
    assert site._release_holds_if_signed_in() == ["HELD1"]
    assert "HELD1" not in site.claims() and ("stop", "tst", "--target-id", "HELD1") in stops


def test_record_stop_holds_the_window(tmp_path, monkeypatch):
    C, site, stops = _held_site(tmp_path, monkeypatch)
    monkeypatch.setattr(C, "_log_on", lambda: False)
    C._LOG.stop_logged = False
    site._write_claim("W1", {"created": 0, "last_used": 0, "loads": 0})
    state = {"stop": "CAPTCHA", "url": "https://example.com/x"}
    site.record_stop("W1", state)
    assert site.claims()["W1"]["hold"] and state["left_open"]["url"] == "https://example.com/x"


@pytest.mark.parametrize("desc,blocked", [
    ({"tag": "button", "text": "Upvote"}, True),
    ({"tag": "button", "text": "(17)", "attrs": "vote-button"}, True),
    ({"tag": "button", "text": "Follow"}, True),
    ({"tag": "button", "text": "Reply"}, True),
    ({"tag": "button", "text": "Share"}, True),
    ({"tag": "button", "text": "", "attrs": "aria-label=Like this post"}, True),
    ({"tag": "button", "text": "", "attrs": "styles_commentButton"}, True),
    ({"tag": "a", "text": "Sign out", "href": "https://x.example/logout"}, True),
    ({"tag": "button", "text": "Comment", "in_form": True}, True),
    ({"tag": "input", "text": "", "editable": True}, True),
    ({"tag": "button", "text": "Go", "type": "submit", "in_form": True}, True),
    ({"tag": "button", "text": "Show more"}, False),
    ({"tag": "button", "text": "Reviews"}, False),
    ({"tag": "a", "text": "12 comments", "href": "https://x.example/c"}, False),
    ({"tag": "a", "text": "Share your feedback with the community", "href": "https://x.example/f"}, False),
    ({"tag": "button", "text": "Canva Create and share beautiful designs with your team"}, False),  # a result card
    ({"tag": "button", "text": "Search", "type": "submit", "in_form": True, "search_form": True}, False),
])
def test_write_action_detection(desc, blocked):
    import research_core as C
    assert (C._write_action(desc) is not None) == blocked, (desc, C._write_action(desc))


# --- typed search types only into the focused search box (2026-11-24: a Product Hunt header click opened
# "Start new thread" and the query went there) ----------------------------------------------------------

def _typed_site(tmp_path, monkeypatch, focused):
    C, site, _ = _held_site(tmp_path, monkeypatch)
    calls = {"typed": [], "keys": [], "nav": [], "uncount": 0}
    focus = list(focused)  # answers for successive focus checks

    def evaluate(fid, expr, timeout=None):
        if "document.activeElement" in expr:
            return focus.pop(0)
        if expr == "location.hostname":
            return "example.com"
        if expr == "location.href":
            return "https://example.com/p/x"
        if expr.startswith("!!document.querySelector"):
            return True
        return {"url": "https://example.com/search?q=q"}

    monkeypatch.setattr(site, "resolve", lambda t: t)
    monkeypatch.setattr(site, "ensure_visible", lambda *a, **k: None)
    monkeypatch.setattr(site, "evaluate", evaluate)
    monkeypatch.setattr(site, "_count_load", lambda fid: 1)
    monkeypatch.setattr(site, "_pace", lambda *a: None)
    monkeypatch.setattr(site, "click_on", lambda *a, **k: True)
    monkeypatch.setattr(site, "type_text", lambda fid, text: calls["typed"].append(text))
    monkeypatch.setattr(site, "key", lambda fid, k, *a, **kw: calls["keys"].append(k))
    monkeypatch.setattr(site, "wait_for", lambda *a, **k: True)
    monkeypatch.setattr(site, "uncount_load", lambda fid: calls.__setitem__("uncount", calls["uncount"] + 1))
    monkeypatch.setattr(site, "nav", lambda t, url: calls["nav"].append(url) or {"url": url})
    monkeypatch.setattr(C, "pause", lambda *a: None)
    return site, calls


def test_typed_search_never_types_when_box_not_focused(tmp_path, monkeypatch):
    site, calls = _typed_site(tmp_path, monkeypatch, focused=[False])
    page = site.typed_search("T1", "input#q", "weather briefing", "https://example.com/search?q=weather+briefing")
    assert calls["typed"] == [] and "Enter" not in calls["keys"] and calls["keys"] == ["Escape"]
    assert calls["nav"] == ["https://example.com/search?q=weather+briefing"] and calls["uncount"] == 1
    assert page["via"] == "url" and "didn't focus" in page["typed_fallback"]


def test_typed_search_no_enter_when_focus_lost_while_typing(tmp_path, monkeypatch):
    site, calls = _typed_site(tmp_path, monkeypatch, focused=[True, False])
    page = site.typed_search("T1", "input#q", "weather", "https://example.com/search?q=weather")
    assert calls["typed"] == ["weather"] and "Enter" not in calls["keys"]
    assert page["via"] == "url" and "lost focus" in page["typed_fallback"]


def test_typed_search_types_and_submits_when_focused(tmp_path, monkeypatch):
    site, calls = _typed_site(tmp_path, monkeypatch, focused=[True, True])
    page = site.typed_search("T1", "input#q", "weather", "https://example.com/search?q=weather")
    assert calls["typed"] == ["weather"] and calls["keys"] == ["Enter"] and calls["nav"] == []
    assert page["via"] == "typed"


def test_box_focused_js_checks_url_focus_and_value():
    import research_core as C
    js = C.Site._box_focused_js("[data-test=header-search-input]", "https://x.com/a", "Weather Briefing ")
    assert 'location.href === "https://x.com/a"' in js and "document.activeElement" in js
    assert '"weather briefing"' in js and "textarea" in js
