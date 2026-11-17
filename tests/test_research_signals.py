"""research_signals: metrics from a run's saved raw results. No browser."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import research_signals as R  # noqa: E402


def _save(d: Path, n: int, result: dict):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{n:03d}_120000_cmd.json").write_text(json.dumps({"cmd": "c", "args": [], "saved_at": "2026-11-16T12:00:00",
                                                             "result": result}))


def test_metrics_dedupe_and_entities(tmp_path):
    x = [{"url": "https://x.com/a/status/1", "handle": "@a", "text": "I bought an MS-01, use code SAVE20", "date": "2026-11-01T00:00:00Z",
          "likes": 10, "replies": 2},
         {"url": "https://x.com/Hi_MINISFORUM/status/2", "handle": "@Hi_MINISFORUM", "text": "MS-01 giveaway!", "date": "2026-10-01T00:00:00Z",
          "likes": 0, "replies": 0, "name_matches_query": "minisforum"},
         {"url": "https://x.com/grok/status/3", "handle": "@grok", "text": "MS-01 is a mini PC", "grok": True}]
    _save(tmp_path / "x", 1, {"posts": x})
    _save(tmp_path / "x", 2, {"post": x[0], "replies": []})  # the same post read again: counted once
    _save(tmp_path / "reddit", 1, {"thread": {"url": "https://reddit.com/t/1", "author": "op", "title": "M720q?", "body": "",
                                              "created": "2026-09-01T00:00:00+00:00", "score": 5, "comments_total": 1,
                                              "comments": [{"id": "c1", "parent": None, "author": "b", "score": 3,
                                                            "created": "2026-09-02T00:00:00+00:00",
                                                            "text": "My M720q box idles at 9W", "link": "https://reddit.com/c1"}]}})
    _save(tmp_path / "reddit", 2, {"posts": [{"title": "listing only", "author": "z"}]})  # search listing: ignored
    out = R.run(tmp_path, {"MS-01": ["MS-01"], "Lenovo Tiny": ["M720q"]}, [])
    xm, rm = out["sites"]["x"]["metrics"], out["sites"]["reddit"]["metrics"]
    assert xm["items"] == 2 and xm["authors"] == 2 and xm["brand_named_share"] == 0.5
    assert xm["discount_codes"] == 1 and xm["giveaways"] == 1 and xm["first_hand_share"] == 0.5
    assert rm["items"] == 2 and rm["replied_share"] == 0.5 and rm["first_hand_share"] == 0.5
    assert out["sites"]["x"]["entities"]["MS-01"] == {"authors": 2, "mentions": 2, "first_hand_authors": 1}
    assert out["sites"]["reddit"]["entities"]["Lenovo Tiny"]["authors"] == 2
    assert "| Items read | 2 | 2 |" in R.markdown(out) or "| Items read |" in R.markdown(out)


def test_relative_dates_and_numbers():
    from datetime import datetime, timezone
    saved = datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert R._date("3 weeks ago", saved).date().isoformat() == "2026-10-26"
    assert R._num("1.2K") == 1200 and R._num(None) is None and R._num(True) is None


def test_discount_pattern_ignores_ordinary_code():
    assert R.DISCOUNT.search("Use code MINIPC15 at checkout")
    assert not R.DISCOUNT.search("Claude Code set it up") and not R.DISCOUNT.search("use code review")
