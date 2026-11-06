"""research_core pure helpers: screenshot clip math and pacing. No browser."""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "claude" / "lib"))
import research_core as R  # noqa: E402

VP = {"x": 0, "y": 0, "width": 1200, "height": 800}


def test_intersect():
    assert R.intersect({"x": 0, "y": 0, "width": 10, "height": 10}, {"x": 5, "y": 5, "width": 10, "height": 10}) == \
        {"x": 5, "y": 5, "width": 5, "height": 5}
    assert R.intersect({"x": 0, "y": 0, "width": 10, "height": 10}, {"x": 20, "y": 0, "width": 5, "height": 5}) is None


def test_page_clip_adds_scroll_offset():
    clip = R.page_clip({"x": 100, "y": 50, "width": 300, "height": 200}, scroll_x=0, scroll_y=2400, viewport=VP)
    assert clip == {"x": 100, "y": 2450, "width": 300, "height": 200}


def test_page_clip_clamps_to_viewport_and_area():
    area = {"x": 250, "y": 64, "width": 950, "height": 736}  # no top bar, no left nav
    tall = {"x": 300, "y": -100, "width": 600, "height": 2000}
    clip = R.page_clip(tall, scroll_x=0, scroll_y=1000, viewport=VP, area=area, pad=8)
    assert clip == {"x": 292, "y": 1064, "width": 616, "height": 736}


def test_page_clip_offscreen_is_none():
    assert R.page_clip({"x": 0, "y": 900, "width": 100, "height": 100}, 0, 0, viewport=VP) is None


def test_view_js_embeds_selectors():
    js = R._view_js("shreddit-comment[thingid='t1_x']", None, ["shreddit-ad-post"])
    assert "shreddit-ad-post" in js and "t1_x" in js and "scrollY" in js


def test_pace_delay_is_skewed_and_bounded():
    rng = random.Random(7)
    d = [R.pace_delay(5.0, rng=rng) for _ in range(20000)]
    short = [x for x in d if x < 20]
    longs = [x for x in d if x >= 20]
    assert min(d) >= 4.5
    assert 0.10 < len(longs) / len(d) < 0.15  # about 1 in 8
    assert max(longs) <= 60 and max(short) <= 5 * 1.1 + 5 * 3.0
    assert statistics.median(short) < statistics.mean(short)  # right-skewed
    assert all(R.pace_delay(5.0, long_p=0, rng=rng) < 20 for _ in range(2000))


def test_parse_range():
    assert R._parse_range("10-30", (20.0, 60.0)) == (10.0, 30.0)
    assert R._parse_range("bad", (20.0, 60.0)) == (20.0, 60.0)
