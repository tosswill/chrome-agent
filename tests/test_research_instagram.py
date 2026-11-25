"""Instagram research helper (generated scaffold). No browser."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "instagram-research" / "scripts"))
import instagram_tab as H  # noqa: E402


def test_only_the_site_loads():
    import pytest
    H.SITE.check_url("https://www.instagram.com/")
    with pytest.raises(H.Fail):
        H.SITE.check_url("https://example.com/")


def test_block_page_stops():
    assert H._stop_reason({"blocked": True}, H.SITE)
    assert H._stop_reason({"loggedIn": True}, H.SITE) is None


import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402


def test_handles_and_post_links():
    assert H._handle("@yanet.garcia_1") == "yanet.garcia_1"
    assert H._handle("https://www.instagram.com/someone/") == "someone"
    for bad in ("explore", "p", "a b", "x" * 31, "../etc"):
        with pytest.raises(H.Fail):
            H._handle(bad)
    assert H._post_url("https://www.instagram.com/someone/p/ABC123xyz/?img_index=2") == "https://www.instagram.com/p/ABC123xyz/"
    assert H._post_url("https://instagram.com/reel/XYZ_98765/") == "https://www.instagram.com/reel/XYZ_98765/"
    with pytest.raises(H.Fail):
        H._post_url("https://example.com/p/ABC/")


def test_new_account_defaults_are_gentle():
    assert H.SITE.max_claims == 1 and H.SITE.max_loads == 15 and H.SITE.default_gap >= 25 and H.DAY_CAP == 30


def test_day_cap_counts_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "DAY_LOG", tmp_path / "day.json")
    monkeypatch.setattr(H, "DAY_CAP", 2)
    old = [__import__("time").time() - 90000]  # older than 24 h: doesn't count
    (tmp_path / "day.json").write_text(json.dumps(old))
    assert H._count_day_load() == 1 and H._count_day_load() == 2
    with pytest.raises(H.Fail, match="daily cap"):
        H._count_day_load()


def test_every_load_hits_the_day_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(H, "_site_count_load", lambda fid: calls.append("window") or 1)
    monkeypatch.setattr(H, "_count_day_load", lambda: calls.append("day") or 1)
    assert H.SITE._count_load("T1") == 1 and calls == ["window", "day"]


def test_shot_dispatch(monkeypatch):
    seen = {}
    monkeypatch.setattr(H, "shot", lambda t, out=None, sel=None, photo=False, grid=None: seen.update(t=t, out=out, sel=sel, photo=photo, grid=grid) or {})
    H._dispatch("shot", ["T1", "--grid", "3", "--out", "reports/x/a.jpg"])
    assert seen == {"t": "T1", "out": "reports/x/a.jpg", "sel": None, "photo": False, "grid": 3}
    H._dispatch("shot", ["T1", "--photo"])
    assert seen["photo"] is True


def test_mark_js_picks_largest_and_grid_order():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")

    def run(js):
        prog = ("const innerHeight=900, innerWidth=1200;"
                "const mk=(t,l,w,h,inGrid)=>({attrs:{},setAttribute(k,v){this.attrs[k]=v},removeAttribute(){},"
                " closest:()=>inGrid?{}:null, getBoundingClientRect:()=>({top:t,bottom:t+h,left:l,right:l+w,width:w,height:h})});"
                "const els=[mk(100,600,300,300,true), mk(100,300,300,300,true), mk(450,0,300,300,true), mk(-800,0,600,600,true), mk(50,0,900,700,false)];"
                "const document={querySelectorAll:s=>s.startsWith('[data-ca-shot]')?[]:els};"
                f"const ok={js}; console.log(JSON.stringify([ok, els.map(e=>e.attrs['data-ca-shot']||null)]));")
        return json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True).stdout)

    assert run(H._mark_js("photo")) == [True, [None, None, None, None, "1"]]  # largest in view, not the off-screen one
    assert run(H._mark_js("grid", 1)) == [True, [None, "1", None, None, None]]  # top row, leftmost
    assert run(H._mark_js("grid", 3))[1][2] == "1"
    assert run(H._mark_js("grid", 4))[0] is False


def _search_site(monkeypatch, focused, on_site=True, panel_open=False):
    calls = {"typed": [], "keys": [], "uncount": 0}

    def evaluate(fid, expr, timeout=None):
        if expr == "location.hostname":
            return "www.instagram.com" if on_site else "example.com"
        if expr == "location.href":
            return "https://www.instagram.com/"
        if "document.activeElement" in expr:
            return focused
        if ".some(i =>" in expr:
            return panel_open
        if "setAttribute('data-ca-search'" in expr:
            return True
        if "const box = document.querySelector" in expr:
            return [{"kind": "profile", "handle": "someone"}]
        return {"url": "https://www.instagram.com/", "loggedIn": True}

    monkeypatch.setattr(H.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(H.SITE, "ensure_visible", lambda *a, **k: None)
    monkeypatch.setattr(H.SITE, "evaluate", evaluate)
    monkeypatch.setattr(H.SITE, "_count_load", lambda fid: 1)
    monkeypatch.setattr(H.SITE, "uncount_load", lambda fid: calls.__setitem__("uncount", calls["uncount"] + 1))
    monkeypatch.setattr(H.SITE, "_pace", lambda *a: None)
    monkeypatch.setattr(H.SITE, "click_on", lambda *a, **k: True)
    monkeypatch.setattr(H.SITE, "wait_for", lambda *a, **k: True)
    monkeypatch.setattr(H.SITE, "read_pause", lambda *a, **k: None)
    monkeypatch.setattr(H.SITE, "type_text", lambda fid, t: calls["typed"].append(t))
    monkeypatch.setattr(H.SITE, "key", lambda fid, k, *a, **kw: calls["keys"].append(k))
    monkeypatch.setattr(H.SITE, "log_page", lambda *a, **k: None)
    return calls


def test_search_types_into_the_panel_and_reads_results(monkeypatch):
    calls = _search_site(monkeypatch, focused=True)
    res = H.search("T1", "weather girl")
    assert calls["typed"] == ["weather girl"] and res["results"][0]["handle"] == "someone"


def test_search_never_types_when_box_not_focused(monkeypatch):
    calls = _search_site(monkeypatch, focused=False)
    with pytest.raises(H.Fail, match="nothing was typed"):
        H.search("T1", "weather girl")
    assert calls["typed"] == [] and calls["keys"] == ["Escape"]


def test_search_needs_an_instagram_page_and_a_sane_query(monkeypatch):
    _search_site(monkeypatch, focused=True, on_site=False)
    with pytest.raises(H.Fail, match="open an Instagram page"):
        H.search("T1", "x")
    with pytest.raises(H.Fail):
        H.search("T1", "")
