"""hn_api: read-only Hacker News research helper. No network: urlopen is faked."""
import io
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "skills"
                       / "hackernews-research" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import hn_api as H  # noqa: E402
import research_signals as R  # noqa: E402

STORY = {"id": 42, "type": "story", "author": "op", "created_at": "2026-11-01T00:00:00Z", "title": "Show HN: G2 glasses",
         "url": "https://github.com/a/b", "points": 120, "text": None,
         "children": [{"id": 43, "type": "comment", "author": "u1", "created_at": "2026-11-01T01:00:00Z",
                       "text": "<p>I use it daily &amp; it <a href=\"https://x.example/y\">works</a></p>", "parent_id": 42,
                       "children": [{"id": 44, "type": "comment", "author": "op", "created_at": "2026-11-01T02:00:00Z",
                                     "text": "Thanks!", "parent_id": 43, "children": []}]},
                      {"id": 45, "type": "comment", "author": None, "text": None, "parent_id": 42, "children": []}]}
HITS = {"hits": [{"objectID": "42", "title": "Show HN: G2 glasses", "url": "https://github.com/a/b", "author": "op",
                  "points": 120, "num_comments": 3, "created_at": "2026-11-01T00:00:00Z", "_tags": ["story", "show_hn"]},
                 {"objectID": "43", "author": "u1", "comment_text": "I use it", "story_id": 42, "story_title": "Show HN",
                  "parent_id": 42, "created_at": "2026-11-01T01:00:00Z", "_tags": ["comment"]}],
        "nbHits": 2, "page": 0, "nbPages": 1}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(H, "STATE", tmp_path / "state")
    monkeypatch.setattr(H, "GAP", 0)
    H.core._LOG.site = "hackernews"
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        assert req.get_method() == "GET" and req.get_header("User-agent") == "research-helper/1.0"
        u = urllib.parse.urlparse(req.full_url)
        if u.path.startswith("/api/v1/search"):
            return Resp(json.dumps(HITS).encode())
        if u.path == "/api/v1/items/42":
            return Resp(json.dumps(STORY).encode())
        if u.path == "/v0/user/pg.json":
            return Resp(json.dumps({"id": "pg", "karma": 155000, "created": 1160418092, "about": "<i>bio</i>",
                                    "submitted": [1, 2, 3]}).encode())
        if u.path == "/v0/topstories.json":
            return Resp(json.dumps([42]).encode())
        if u.path == "/v0/item/42.json":
            return Resp(json.dumps({"id": 42, "type": "story", "title": "T", "by": "op", "score": 9,
                                    "descendants": 3, "time": 1762000000}).encode())
        raise AssertionError(req.full_url)
    monkeypatch.setattr(H.urllib.request, "urlopen", fake)
    return calls


def test_search_builds_filters_and_saves_raw(net, tmp_path, capsys):
    assert H.main(["h", "search", "even g2", "--tags", "story", "--since", "2026-01-01", "--min-points", "50",
                   "--url-only", "--sort", "date"]) == 0
    out = json.loads(capsys.readouterr().out)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(net[0].full_url).query)
    assert urllib.parse.urlparse(net[0].full_url).path == "/api/v1/search_by_date"
    assert q["tags"] == ["story"] and q["restrictSearchableAttributes"] == ["url"]
    assert q["numericFilters"] == ["created_at_i>=1767225600,points>=50"]
    assert out["hits"][0]["type"] == "show_hn" and out["hits"][1]["type"] == "comment"
    assert out["hits"][1]["story_hn_url"] == "https://news.ycombinator.com/item?id=42"
    idx = (tmp_path / "raw" / "hackernews" / "index.jsonl").read_text().splitlines()
    assert json.loads(idx[0])["nargs"][0] == "even g2"
    assert H.main(["h", "search", "even g2", "--tags", "story", "--since", "2026-01-01", "--min-points", "50",
                   "--url-only", "--sort", "date"]) == 1  # exact repeat refused
    capsys.readouterr()


def test_bad_arguments(net, capsys):
    for argv in (["search", "x", "--tags", "nope"], ["search", "x", "--since", "Jan 1"],
                 ["search", "x", "--author", "a b"], ["item", "abc"], ["user", "../etc"], ["front", "hot"]):
        assert H.main(["h", *argv]) == 1, argv
        assert "error" in json.loads(capsys.readouterr().out)
    assert not net


def test_item_flattens_tree_and_cleans_html(net, capsys):
    H.main(["h", "item", "42"])
    out = json.loads(capsys.readouterr().out)
    assert out["comments_total"] == 3 and [c["depth"] for c in out["comments"]] == [1, 2, 1]
    c = out["comments"][0]
    assert c["text"] == "I use it daily & it https://x.example/y" and c["replies"] == 1
    assert out["comments"][2]["deleted"] is True
    H.main(["h", "item", "42", "--max-comments", "1"])
    assert json.loads(capsys.readouterr().out)["comments_truncated"] is True


def test_user_and_front(net, capsys):
    H.main(["h", "user", "pg"])
    u = json.loads(capsys.readouterr().out)
    assert u["karma"] == 155000 and u["created"] == "2006-10-09" and u["about"] == "bio" and u["submitted_count"] == 3
    H.main(["h", "front", "top", "--max", "5"])
    f = json.loads(capsys.readouterr().out)
    assert f["stories"][0] == {**f["stories"][0], "rank": 1, "points": 9, "comments": 3}


def test_only_hn_hosts(net):
    with pytest.raises(H.Fail):
        H.http_get("https://evil.example/x")
    with pytest.raises(H.Fail):
        H.http_get("http://hn.algolia.com/api/v1/search")


def test_budget_and_rate_limit_stop(net, monkeypatch, capsys):
    monkeypatch.setattr(H, "MAX_CALLS", 0)
    H.main(["h", "user", "pg"])
    assert "budget" in json.loads(capsys.readouterr().out)["stop"]
    monkeypatch.setattr(H, "MAX_CALLS", 10)

    def limited(req, timeout=None):
        raise H.urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
    monkeypatch.setattr(H.urllib.request, "urlopen", limited)
    H.main(["h", "user", "pg"])
    assert "rate limit" in json.loads(capsys.readouterr().out)["stop"]


def test_signals_reads_hn_threads(net, tmp_path, capsys):
    H.main(["h", "item", "42"])
    capsys.readouterr()
    out = R.run(tmp_path / "raw", {"G2": ["g2", "daily"]}, [])
    m = out["sites"]["hackernews"]["metrics"]
    assert m["items"] == 3 and m["authors"] == 2  # story + 2 live comments (deleted one skipped)
    assert out["sites"]["hackernews"]["entities"]["G2"]["authors"] == 2
