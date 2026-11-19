"""X (Twitter) research helper (generated scaffold). No browser."""
import sys
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
