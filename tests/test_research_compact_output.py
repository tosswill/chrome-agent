"""Helper output stays under Claude Code's spill size; the full result is left in raw_file."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import research_core as core  # noqa: E402


def big_result():
    return {"page": {"url": "https://example.com"}, "count": 500,
            "items": [{"id": i, "text": "x" * 2000, "tags": list(range(50))} for i in range(500)]}


def test_small_output_unchanged():
    r = {"a": 1, "b": "short"}
    assert json.loads(core.compact_output(r, "001_x.json")) == r


def test_large_output_is_cut_with_pointer():
    r = big_result()
    out = core.compact_output(r, "reddit/001_x.json", limit=20000, indent=2)
    assert len(out) <= 20000
    d = json.loads(out)
    assert d["output_cut"]["raw_file"] == "reddit/001_x.json" and d["output_cut"]["full_chars"] > 20000
    assert d["count"] == 500 and d["page"]["url"] == "https://example.com"
    assert len(d["items"]) < 500 and str(d["items"][-1]).startswith("…")


def test_unsaved_large_output_printed_in_full():
    r = big_result()
    assert len(core.compact_output(r, None, limit=20000)) > 20000


def test_pathological_output_falls_back_to_keys():
    r = {f"k{i}": "y" * 50 for i in range(5000)}
    d = json.loads(core.compact_output(r, "001_x.json", limit=2000))
    assert "output_cut" in d and len(json.dumps(d)) <= 2000 + 200
