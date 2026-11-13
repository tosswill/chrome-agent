"""Action log: events land per window, secrets are redacted, stops get recorded, `log` reads them back."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "integrations" / "claude"
sys.path.insert(0, str(ROOT / "lib"))
import research_core as rc  # noqa: E402


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_LOG", "1")
    monkeypatch.setattr(rc, "LOG_DIR", tmp_path)
    rc._PRUNED.clear()
    for k in ("pending", "sel", "site_obj"):
        setattr(rc._LOG, k, None)
    rc._LOG.stop_logged = rc._LOG.typing = False
    rc._LOG.site, rc._LOG.target, rc._LOG.cmd = "demo", "ABCDEF1234567890", "search"
    yield tmp_path
    rc._LOG.site = rc._LOG.target = rc._LOG.cmd = rc._LOG.pending = None


def _lines(d: Path) -> list[dict]:
    return [json.loads(x) for f in sorted(d.glob("demo/*.jsonl")) for x in f.read_text().splitlines()]


def test_event_goes_to_the_windows_file(logdir):
    rc.log_event("pace", waited_s=1.5)
    (f,) = list((logdir / "demo").glob("*.jsonl"))
    assert f.name.endswith("_ABCDEF12.jsonl")
    rec = _lines(logdir)[0]
    assert rec["event"] == "pace" and rec["target"] == "ABCDEF12" and rec["cmd"] == "search"
    assert rec["waited_s"] == 1.5


def test_off_switch(logdir, monkeypatch):
    monkeypatch.setenv("RESEARCH_LOG", "0")
    rc.log_event("pace")
    assert not list(logdir.glob("demo/*.jsonl"))


class _StubSite(rc.Site):
    pass


def _site():
    s = rc.Site(prefix="DEMO_TAB", default_instance="demo", hosts=("example.com",), ready_js="true",
                page_state_js="({})", stop_reason=lambda st, site: None)
    s.calls = []
    s.ca = lambda *a, **k: (s.calls.append(a), "{}")[1]
    return s


def test_inputs_are_logged_but_not_pointer_moves(logdir):
    s = _site()
    s.cdp("T1", "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 1, "y": 2})
    rc._LOG.sel = "a.goods-title"
    s.cdp("T1", "Input.dispatchMouseEvent", {"type": "mousePressed", "x": 10.4, "y": 20, "button": "left"})
    s.cdp("T1", "Input.dispatchMouseEvent", {"type": "mouseWheel", "x": 1, "y": 1, "deltaY": 120})
    s.cdp("T1", "Input.dispatchMouseEvent", {"type": "mousePressed", "x": 1, "y": 1, "button": "back"})
    s.cdp("T1", "Page.navigate", {"url": "https://example.com/x"})
    ev = [(r["event"], r.get("selector")) for r in _lines(logdir)]
    assert ev == [("click", "a.goods-title"), ("wheel", None), ("mouse_back", None), ("navigate", None)]


def test_typing_is_one_event_and_secrets_are_redacted(logdir, monkeypatch):
    s = _site()
    monkeypatch.setattr(rc.time, "sleep", lambda *_: None)
    s.type_text("T1", "ab c")
    s.type_text("T1", "hunter2", secret=True)
    recs = _lines(logdir)
    assert [r["event"] for r in recs] == ["type", "type"]  # no per-key events while typing
    assert recs[0]["text"] == "ab c" and recs[1]["text"] == "<redacted>" and recs[1]["chars"] == 7
    assert "hunter2" not in json.dumps(recs)


def test_command_result_and_stop_fallback(logdir, monkeypatch):
    s = _site()
    shots = []
    monkeypatch.setattr(s, "screenshot", lambda t, out, **k: (shots.append(out), out)[1])
    rc._LOG.site_obj = s
    rc.begin_command("product", ["T1", "https://example.com/p/1"])
    rc._LOG.target = "ABCDEF1234567890"
    res = {"page": {"url": "https://example.com/p/1", "blocked": True, "blocked_by": "challenge frame: x",
                    "stop": "bot check"}, "product": None}
    rc.end_command(res)
    ev = [r["event"] for r in _lines(logdir)]
    assert ev == ["command", "stop", "result"]
    stop = _lines(logdir)[1]
    assert stop["blocked_by"] == "challenge frame: x" and stop["shot"]
    assert res["page"]["stop_shot"] and len(shots) == 1
    rc.end_command(res)  # a stop is recorded once per command
    assert len(shots) == 1


def test_read_log_filters_and_formats(logdir):
    rc.log_event("load", n=1)
    rc.log_event("pace", waited_s=0.0, target="FFFF000011112222")
    out = rc.read_log("demo")
    assert len(out["events"]) == 2 and "load" in out["events"][0]
    only = rc.read_log("demo", target="ffff")
    assert len(only["events"]) == 1 and "waited_s=0.0" in only["events"][0]
    assert rc.read_log("demo", since="2h", raw=True)["events"][0]["event"] == "load"
    with pytest.raises(rc.Fail):
        rc.read_log("demo", since="soon")


def test_old_logs_are_pruned(logdir):
    d = logdir / "demo"
    d.mkdir()
    old = d / "2026-01-01_OLD.jsonl"
    old.write_text("{}\n")
    t = time.time() - 40 * 86400
    os.utime(old, (t, t))
    rc.log_event("load", n=1)
    assert not old.exists()


def test_block_check_needs_a_wall_shape():
    js = rc.BLOCK_CHECK_JS
    assert "phrase_ignored" in js and "short" in js and "challenge frame" in js
