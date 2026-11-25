"""ph_api: read-only Product Hunt research helper. No network: urlopen is faked; the token is a temp file."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "skills"
                       / "producthunt-research" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import ph_api as P  # noqa: E402
import research_signals as R  # noqa: E402

UTM = "?utm_campaign=producthunt-api&utm_medium=api-v2&utm_source=Application%3A+x"
NODE = {"id": "1", "name": "Glassy", "slug": "glassy", "tagline": "AR notes", "url": "https://www.producthunt.com/posts/glassy" + UTM,
        "website": "https://www.producthunt.com/r/ABC" + UTM, "votesCount": 300, "commentsCount": 2, "reviewsCount": 1,
        "reviewsRating": 4.5, "dailyRank": 3, "monthlyRank": 40, "createdAt": "2026-10-01T07:00:00Z",
        "featuredAt": "2026-10-01T07:00:00Z", "topics": {"nodes": [{"slug": "wearables", "name": "Wearables"}]}}


class Resp(io.BytesIO):
    headers = {"X-Rate-Limit-Remaining": "6000", "X-Rate-Limit-Limit": "6250", "X-Rate-Limit-Reset": "800"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def api(tmp_path, monkeypatch):
    tok = tmp_path / "token"
    tok.write_text("secret-token-value")
    monkeypatch.setattr(P, "TOKEN_FILE", tok)
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(P, "STATE", tmp_path / "state")
    monkeypatch.setattr(P, "GAP", 0)
    P.core._LOG.site = "producthunt"
    sent = []
    answers = []

    def fake(req, timeout=None):
        body = json.loads(req.data)
        sent.append((req, body))
        assert req.get_method() == "POST" and req.full_url == P.ENDPOINT
        assert req.get_header("Authorization") == "Bearer secret-token-value"
        return Resp(json.dumps(answers.pop(0) if answers else {"data": {}}).encode())
    monkeypatch.setattr(P.urllib.request, "urlopen", fake)
    return sent, answers


def test_posts_filters_and_url_variants(api, capsys):
    sent, answers = api
    answers += [{"data": {"posts": {"totalCount": 0, "pageInfo": {}, "nodes": []}}},
                {"data": {"posts": {"totalCount": 1, "pageInfo": {"hasNextPage": False}, "nodes": [NODE]}}}]
    assert P.main(["p", "posts", "--url", "https://www.glassy.app", "--after", "2026-01-01", "--order", "votes"]) == 0
    out = json.loads(capsys.readouterr().out)
    v0, v1 = sent[0][1]["variables"], sent[1][1]["variables"]
    assert v0["url"] == "https://www.glassy.app" and v1["url"] == "https://www.glassy.app/"
    assert v0["order"] == "VOTES" and v0["postedAfter"].startswith("2026-01-01")
    assert out["posts"][0]["url"] == "https://www.producthunt.com/posts/glassy"  # utm tags stripped
    assert out["urls_tried"][:2] == ["https://www.glassy.app", "https://www.glassy.app/"]
    assert "secret-token-value" not in json.dumps(out)
    assert all(b["query"].lstrip().startswith("query") for _, b in sent)


def test_post_threads_clean_html(api, capsys):
    sent, answers = api
    post = {**NODE, "description": "Notes on your glasses", "makers": [{"id": "0"}, {"id": "0"}],
            "productLinks": [{"type": "Website", "url": "https://glassy.app"}],
            "comments": {"totalCount": 2, "nodes": [
                {"id": "c1", "body": "<p>Hi all!<br>We built <a href=\"https://glassy.app\">Glassy</a></p>",
                 "createdAt": "2026-10-01T08:00:00Z", "votesCount": 5, "url": "https://www.producthunt.com/posts/glassy?comment=c1" + UTM.replace("?", "&"),
                 "replies": {"totalCount": 1, "nodes": [{"id": "c2", "body": "Does it work on G2?", "createdAt": "2026-10-02T08:00:00Z",
                                                          "votesCount": 1, "url": "u2"}]}}]}}
    answers.append({"data": {"post": post}})
    P.main(["p", "post", "https://www.producthunt.com/products/glassy/launches/glassy", "--comments", "5"])
    out = json.loads(capsys.readouterr().out)
    assert sent[0][1]["variables"]["slug"] == "glassy"
    assert out["comments"][0]["text"] == "Hi all!\nWe built https://glassy.app"
    assert out["comments"][0]["link"] == "https://www.producthunt.com/posts/glassy?comment=c1"
    assert [c["depth"] for c in out["comments"]] == [1, 2] and out["makers_count"] == 2
    assert "is_maker" not in out["comments"][0]  # the API redacts commenters; no guessing


def test_bad_input_and_no_keyword_search(api, capsys):
    sent, _ = api
    for argv in (["posts", "smart", "glasses"], ["posts", "--order", "hot"], ["posts", "--topic", "Bad Slug"],
                 ["posts", "--url", "glassy.app"], ["post", "https://evil.example/posts/x"], ["post", "../x"],
                 ["posts", "--after", "Oct 1"]):
        assert P.main(["p", *argv]) == 1, argv
        assert "error" in json.loads(capsys.readouterr().out)
    assert not sent


def test_refuses_mutations():
    with pytest.raises(P.Fail):
        P.gql("mutation { userFollow(input: {userId: 1}) { clientMutationId } }", {})
    with pytest.raises(P.Fail):
        P.gql("query { a } mutation { b }", {})


def test_missing_token_and_rate_limit_stop(api, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(P, "TOKEN_FILE", tmp_path / "nope")
    P.main(["p", "topic", "wearables"])
    assert "token" in json.loads(capsys.readouterr().out)["stop"]
    monkeypatch.setattr(P, "TOKEN_FILE", tmp_path / "token")

    def limited(req, timeout=None):
        raise P.urllib.error.HTTPError(req.full_url, 429, "Too Many", {"X-Rate-Limit-Reset": "60"}, None)
    monkeypatch.setattr(P.urllib.request, "urlopen", limited)
    P.main(["p", "topic", "wearables"])
    assert "rate limit" in json.loads(capsys.readouterr().out)["stop"]


def test_repeat_listing_refused(api, capsys):
    _, answers = api
    answers.append({"data": {"posts": {"totalCount": 0, "pageInfo": {}, "nodes": [NODE]}}})
    assert P.main(["p", "posts", "--topic", "wearables", "--after", "2026-01-01"]) == 0
    capsys.readouterr()
    assert P.main(["p", "posts", "--topic", "wearables", "--after", "2026-01-01"]) == 1
    assert "already ran" in capsys.readouterr().out


def test_signals_counts_launch_comments(api, tmp_path, capsys):
    _, answers = api
    answers.append({"data": {"post": {**NODE, "description": "", "makers": [], "productLinks": [],
                                      "comments": {"totalCount": 1, "nodes": [{"id": "c1", "body": "great for glasses",
                                                   "createdAt": "2026-10-01T08:00:00Z", "votesCount": 2, "url": "u",
                                                   "replies": {"totalCount": 0, "nodes": []}}]}}}})
    P.main(["p", "post", "glassy"])
    capsys.readouterr()
    out = R.run(tmp_path / "raw", {"Glassy": ["glass"]}, [])
    assert out["sites"]["producthunt"]["metrics"]["items"] == 2


def test_search_page_check():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "skills"
                           / "producthunt-research" / "scripts"))
    import producthunt_tab as PT
    assert PT._is_search_page("https://www.producthunt.com/search?q=ai+weather")
    assert PT._is_search_page("https://www.producthunt.com/search/?q=x")
    assert not PT._is_search_page("https://www.producthunt.com/p/new?category=general&ref=header")
    assert not PT._is_search_page(None)
