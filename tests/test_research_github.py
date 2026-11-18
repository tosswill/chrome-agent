"""github_api: read-only GitHub research helper. No network: gh is faked."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "skills" / "github-research"
                       / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import github_api as G  # noqa: E402
import research_signals as R  # noqa: E402


class FakeGh:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, cmd, capture_output=True, text=True, timeout=None):
        self.calls.append(cmd)
        path = cmd[2] if cmd[2] == "graphql" else cmd[5]
        status, body, headers = self.routes.get(path, (404, '{"message":"Not Found"}', {}))
        if callable(body):
            body = body(cmd)
        head = f"HTTP/2.0 {status} OK\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        out = body if path == "graphql" else head + "\r\n" + body
        return subprocess.CompletedProcess(cmd, 1 if status >= 400 else 0, out, "")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setattr(G, "STATE", tmp_path / "state")
    monkeypatch.setattr(G, "GAP", 0)
    monkeypatch.setattr(G, "SEARCH_GAP", 0)
    G.core._LOG.site = "github"
    return tmp_path


def _repo(name, stars=3, pushed="2026-11-01T00:00:00Z"):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "stargazers_count": stars,
            "pushed_at": pushed, "license": {"spdx_id": "MIT"}, "owner": {"type": "User"}}


def test_every_rest_call_is_get_and_search_saves_raw(env, monkeypatch, capsys):
    fake = FakeGh({"search/repositories": (200, json.dumps({"total_count": 1, "items": [_repo("a/b", 40)]}),
                                           {"X-RateLimit-Remaining": "29"})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    assert G.main(["g", "search", "even g2", "--sort", "stars"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["repos"][0]["stars"] == 40 and out["quota"]["remaining"] == 29
    assert all(c[:4] == ["gh", "api", "-X", "GET"] for c in fake.calls)
    assert "q=even g2" in fake.calls[0] and "sort=stars" in fake.calls[0]
    idx = [json.loads(l) for l in (env / "raw" / "github" / "index.jsonl").read_text().splitlines()]
    assert idx[0]["nargs"][0] == "even g2"  # the query, not the placeholder, is compared for repeats
    assert G.main(["g", "search", "even g2", "--sort", "stars"]) == 1  # exact repeat refused
    assert "already ran" in capsys.readouterr().out


def test_rate_limit_is_a_stop(env, monkeypatch, capsys):
    fake = FakeGh({"repos/a/b": (403, '{"message":"API rate limit exceeded"}',
                                 {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000000"})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    G.main(["g", "repo", "a/b"])
    assert "rate limit" in json.loads(capsys.readouterr().out)["stop"]


def test_call_budget_stops(env, monkeypatch, capsys):
    monkeypatch.setattr(G, "MAX_CALLS", 1)
    fake = FakeGh({"users/x": (200, json.dumps({"login": "x", "type": "User"}), {}),
                   "users/x/repos": (200, "[]", {})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    G.main(["g", "user", "x"])
    assert "budget" in json.loads(capsys.readouterr().out)["stop"]


def test_links_parses_headings_and_batches_graphql(env, monkeypatch, capsys):
    readme = ("# List\n## Agents\n- [cc](https://github.com/o/cc) - Claude\n- dup https://github.com/o/cc\n"
              "## Apps\n- [x](https://github.com/p/x.git) and https://github.com/sponsors/me\n"
              "- gone https://github.com/q/gone\n")

    def gql(cmd):
        q = cmd[-1]
        assert q.startswith("query=query {") and "mutation" not in q
        return json.dumps({"data": {"r0": {"nameWithOwner": "o/cc", "url": "u", "stargazerCount": 9,
                                           "pushedAt": "2026-11-01T00:00:00Z"},
                                    "r1": {"nameWithOwner": "p/x", "url": "u2", "stargazerCount": 2}, "r2": None}})
    fake = FakeGh({"repos/me/list/readme": (200, readme, {}), "graphql": (200, gql, {})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    G.main(["g", "links", "me/list"])
    out = json.loads(capsys.readouterr().out)
    assert out["links_found"] == 3 and [r["full_name"] for r in out["repos"]] == ["o/cc", "p/x"]
    assert out["repos"][0]["heading"] == "Agents" and out["missing"][0]["repo"] == "q/gone"
    assert sum(1 for c in fake.calls if c[2] == "graphql") == 1
    assert (env / "raw" / "github" / "readmes").is_dir()


def test_links_section_filter(env, monkeypatch, capsys):
    readme = "## Agents\nhttps://github.com/o/cc\n## Apps\nhttps://github.com/p/x\n"
    fake = FakeGh({"repos/me/list/readme": (200, readme, {}),
                   "graphql": (200, json.dumps({"data": {"r0": {"nameWithOwner": "o/cc", "url": "u"}}}), {})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    G.main(["g", "links", "me/list", "--section", "agent"])
    out = json.loads(capsys.readouterr().out)
    assert out["links_found"] == 1 and out["repos"][0]["full_name"] == "o/cc"


def test_bad_repo_name_and_404(env, monkeypatch, capsys):
    monkeypatch.setattr(G.subprocess, "run", FakeGh({}))
    assert G.main(["g", "repo", "not a repo"]) == 1
    assert "OWNER/NAME" in capsys.readouterr().out
    assert G.main(["g", "repo", "https://github.com/a/b.git"]) == 1
    assert "not found" in capsys.readouterr().out


def test_signals_reads_issue_threads(env, monkeypatch, capsys):
    issue = {"number": 1, "title": "Crash", "html_url": "https://github.com/a/b/issues/1", "user": {"login": "u1"},
             "body": "I installed it and it crashes", "created_at": "2026-11-01T00:00:00Z", "comments": 1,
             "reactions": {"total_count": 3}}
    comments = [{"id": 5, "user": {"login": "owner"}, "body": "Fixed in 0.2", "created_at": "2026-11-02T00:00:00Z",
                 "html_url": "https://github.com/a/b/issues/1#c5", "reactions": {"total_count": 1}}]
    fake = FakeGh({"repos/a/b/issues/1": (200, json.dumps(issue), {}),
                   "repos/a/b/issues/1/comments": (200, json.dumps(comments), {})})
    monkeypatch.setattr(G.subprocess, "run", fake)
    G.main(["g", "issue", "a/b", "1"])
    capsys.readouterr()
    out = R.run(env / "raw", {"B": ["crash"]}, [])
    m = out["sites"]["github"]["metrics"]
    assert m["items"] == 2 and m["authors"] == 2
    assert out["sites"]["github"]["entities"]["B"]["authors"] == 1
