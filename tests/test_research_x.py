"""X (Twitter) research helper (generated scaffold). No browser."""
import json
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "skills" / "x-research" / "scripts"))
import x_tab as H  # noqa: E402


def test_only_the_site_loads():
    import pytest
    H.SITE.check_url("https://www.x.com/")
    with pytest.raises(H.Fail):
        H.SITE.check_url("https://example.com/")


def test_block_page_stops():
    assert H._stop_reason({"blocked": True}, H.SITE)
    assert H._stop_reason({"loggedIn": True}, H.SITE) is None


def test_forbidden_paths():
    import pytest
    for u in ("https://x.com/i/api/graphql/abc", "https://x.com/compose/post", "https://x.com/i/flow/login",
              "https://x.com/settings/account", "https://x.com/messages"):
        with pytest.raises(H.Fail):
            H.SITE.check_url(u)
    H.SITE.check_url("https://x.com/search?q=sony&src=typed_query")


def test_status_ids_and_handles():
    import pytest
    assert H._status_id("https://x.com/feni_book/status/2105507899988672858") == "2105507899988672858"
    assert H._status_id("https://twitter.com/a/status/123/photo/1") == "123"
    with pytest.raises(H.Fail):
        H._status_id("https://x.com/feni_book")
    assert H._handle("@SonyAlphaLab") == "SonyAlphaLab"
    assert H._handle("https://x.com/SonyAlphaLab/") == "SonyAlphaLab"
    with pytest.raises(H.Fail):
        H._handle("not a handle!")


def test_wall_stops_and_merge_dedupes():
    assert "rate limit" in H._stop_reason({"blocked": True, "blocked_by": "X rate limit"}, H.SITE)
    seen = {}
    assert H._merge(seen, [{"url": "https://x.com/a/status/1"}, {"url": "https://x.com/a/status/1"}]) == 1
    assert H._merge(seen, [{"url": "https://x.com/a/status/2"}, {"url": "https://x.com/a/status/1"}]) == 1


def test_summary():
    s = H._summary([{"handle": "@a", "verified": True, "date": "2026-11-02", "promoted": True},
                    {"handle": "@b", "date": "2026-10-01", "community_note": True}, {"handle": "@a"}])
    assert s == {"posts": 3, "authors": 2, "verified_authors": 1, "promoted": 1, "with_community_note": 1,
                 "name_matches_query": 0, "grok": 0, "oldest": "2026-10-01", "newest": "2026-11-02"}


def test_hyphenated_terms_are_quoted():
    assert H._quote_hyphenated("MS-01 homelab") == ('"MS-01" homelab', ["MS-01"])
    q, done = H._quote_hyphenated('"MS-A2" N150 since:2025-06-01 -filter:replies Wi-Fi')
    assert q == '"MS-A2" N150 since:2025-06-01 -filter:replies "Wi-Fi"' and done == ["Wi-Fi"]
    assert H._quote_hyphenated("mini pc -is:retweet") == ("mini pc -is:retweet", [])


def test_query_terms_and_name_matches():
    assert H._query_terms('minisforum "MS-01" homelab since:2025-06-01 -filter:replies') == ["minisforum"]
    posts = [{"handle": "@Hi_MINISFORUM", "name": "MINISFORUM"}, {"handle": "@rapterron", "name": "R"}]
    H._flag_name_matches(posts, ["minisforum"])
    assert posts[0]["name_matches_query"] == "minisforum" and "name_matches_query" not in posts[1]


def test_thread_split():
    main = {"handle": "@a"}
    below, replies = H._split_thread(main, [{"handle": "@a"}, {"handle": "@a"}, {"handle": "@b"}, {"handle": "@a"}])
    assert [p["handle"] for p in below] == ["@a", "@a"] and [p["handle"] for p in replies] == ["@b", "@a"]
    assert H._split_thread(None, [{"handle": "@b"}]) == ([], [{"handle": "@b"}])


def test_long_or_chains_are_refused_before_loading():
    import pytest
    H._check_human_query("grok bot I wish since:2026-08-11 lang:en")
    H._check_human_query('"grok bot" OR "grok bots" limits')
    H._check_human_query("muse OR dots OR duet wish")  # two ORs is the limit
    H._check_human_query('"this OR that OR other OR more" agent')  # OR inside quotes is text
    with pytest.raises(H.Fail, match="3 OR operators"):
        H._check_human_query('("grok bot" OR "grok bots") ("I wish" OR "needs to" OR "please add") lang:en')
    bad = ('("grok bot" OR "grok bots") ("I wish" OR "needs to" OR "please add" OR "feature request" '
           'OR "should be able" OR "why cant") since:2026-08-11 lang:en')
    with pytest.raises(H.Fail, match="Search like a person"):
        H._check_human_query(bad)
    with pytest.raises(H.Fail):
        H.search("anything", bad)  # refused before resolving the window or loading a page


# --- shot --photo / --selector: element-only reference images without X's side bars (2026-11-25) ---------

def _shot_site(monkeypatch, visible):
    """`visible`: selector -> how many photos of that kind are in view."""
    import x_tab as X
    calls = {"eval": [], "capture": [], "screenshot": []}

    def evaluate(fid, expr, timeout=None):
        calls["eval"].append(expr)
        for sel, n in visible.items():
            if json.dumps(sel) in expr:
                k = int(expr.split("const hit = vis[")[1].split("]")[0])
                return k < n
        return None

    monkeypatch.setattr(X.SITE, "resolve", lambda t: t)
    monkeypatch.setattr(X.SITE, "evaluate", evaluate)
    monkeypatch.setattr(X.SITE, "capture", lambda fid, path, selector=None, **k: calls["capture"].append(selector) or {"clip": {}})
    monkeypatch.setattr(X.SITE, "screenshot", lambda fid, path, max_width=None: calls["screenshot"].append(path))
    return X, calls


def test_shot_photo_prefers_open_viewer(monkeypatch, tmp_path):
    import x_tab as X
    X_, calls = _shot_site(monkeypatch, {X.PHOTO_SELECTORS[0]: 1, X.PHOTO_SELECTORS[1]: 4})
    res = X_.shot("T1", str(tmp_path / "a.jpg"), photo=1)
    assert calls["capture"] == ["[data-ca-photo='1']"] and not calls["screenshot"]
    assert json.dumps(X.PHOTO_SELECTORS[0]) in calls["eval"][0] and "vis[0]" in calls["eval"][0]
    assert res["path"].endswith("a.jpg")


def test_shot_photo_uses_photos_in_view_and_errors_when_missing(monkeypatch, tmp_path):
    import x_tab as X
    X_, calls = _shot_site(monkeypatch, {X.PHOTO_SELECTORS[0]: 0, X.PHOTO_SELECTORS[1]: 2})
    X_.shot("T1", str(tmp_path / "b.jpg"), photo=2)
    assert json.dumps(X.PHOTO_SELECTORS[1]) in calls["eval"][-1] and "vis[1]" in calls["eval"][-1]
    with pytest.raises(X.Fail):
        X_.shot("T1", str(tmp_path / "c.jpg"), photo=3)


def test_visible_photo_js_filters_to_viewport_and_sorts():
    import x_tab as X
    js = X._mark_visible_photo_js("img", 2)
    assert "r.top < innerHeight" in js and "r.bottom > 0" in js and ".sort(" in js and "vis[1]" in js


def test_shot_dispatch_parses_flags(monkeypatch):
    import x_tab as X
    seen = {}
    monkeypatch.setattr(X, "shot", lambda t, out=None, selector=None, photo=None: seen.update(t=t, out=out, sel=selector, photo=photo) or {})
    X._dispatch("shot", ["T1", "--photo", "2", "--out", "reports/x/a.jpg"])
    assert seen == {"t": "T1", "out": "reports/x/a.jpg", "sel": None, "photo": 2}
