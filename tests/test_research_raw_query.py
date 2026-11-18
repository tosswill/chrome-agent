"""raw_query: read-only slices of a run's saved raw results."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import raw_query as Q  # noqa: E402


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "ROOT", tmp_path.resolve())
    d = tmp_path / "2026-11-17_t" / "raw"
    (d / "reddit").mkdir(parents=True)
    rec = {"cmd": "thread", "args": ["ID", "u"], "saved_at": "2026-11-17T00:00:00",
           "result": {"thread": {"title": "G2 apps", "body": "x" * 1000, "comments": [
               {"author": "a", "score": 9, "text": "GlanceCode works on my Mac"},
               {"author": "b", "score": 2, "text": "no Windows build"},
               {"author": "c", "score": None, "text": "meh"}]}}}
    (d / "reddit" / "001_000000_thread.json").write_text(json.dumps(rec))
    (d / "reddit" / "index.jsonl").write_text(json.dumps(
        {"n": 1, "cmd": "thread", "args": ["ID", "u"], "file": "001_000000_thread.json", "ok": True}) + "\n")
    return d


def test_outline_and_path_defaults_to_result(raw):
    out = Q.query(str(raw), "001_000000_thread.json")
    assert out["file"] == "reddit/001_000000_thread.json"
    assert out["outline"]["result"]["thread"]["comments"].startswith("list(3) of dict")
    assert Q.query(str(raw), "reddit/001_000000_thread.json", path="thread.title")["value"] == "G2 apps"


def test_list_fields_sort_where_grep_paging(raw):
    out = Q.query(str(raw), "001_000000_thread.json", path="thread.comments", fields=["author", "score"],
                  sort="score", desc=True, limit=2)
    assert [i["author"] for i in out["items"]] == ["a", "b"] and out["next_offset"] == 2
    assert Q.query(str(raw), "001_000000_thread.json", path="thread.comments", where="score>5")["matched"] == 1
    assert Q.query(str(raw), "001_000000_thread.json", path="thread.comments", grep="windows")["matched"] == 1
    assert Q.query(str(raw), "001_000000_thread.json", path="thread.comments[*].author")["items"] == ["a", "b", "c"]


def test_long_strings_cut_and_text_windows(raw):
    v = Q.query(str(raw), "001_000000_thread.json", path="thread", chars=50)["value"]
    assert "[1000 chars" in v["body"]
    t = Q.query(str(raw), "001_000000_thread.json", path="thread.body", text=True, start=990, length=50)
    assert t["text"] == "x" * 10 and t["next_from"] is None and t["chars"] == 1000


def test_index_and_find(raw):
    idx = Q.query(str(raw), "index")
    assert idx["items"][0]["file"] == "reddit/001_000000_thread.json" and idx["items"][0]["args"] == ["u"]
    hits = Q.query(str(raw), "find", grep="glancecode", fields=["author"])
    assert hits["matched"] == 1 and hits["items"][0]["item"] == {"author": "a"}
    assert hits["items"][0]["path"] == "result.thread.comments[0]"


def test_refuses_outside_reports(raw, tmp_path):
    with pytest.raises(Q.QueryError):
        Q.query("/etc", "index")
    with pytest.raises(Q.QueryError):
        Q.query(str(raw), "/etc/passwd")
    outside = tmp_path.parent / "secret.json"
    outside.write_text("{}")
    (raw / "link.json").symlink_to(outside)
    with pytest.raises(Q.QueryError):
        Q.query(str(raw), "link.json")
    with pytest.raises(Q.QueryError):
        Q.query(str(raw), "001_000000_thread.json", path="thread.nope")
