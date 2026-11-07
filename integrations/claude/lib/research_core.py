"""Shared core for polite, read-only research in a shared chrome-agent browser.

A `Site` describes one research target (Reddit, YouTube, ...): the browser
instance holding its signed-in session, the hosts it may load, its pacing, and
how to tell a healthy page from a blocked one. The core gives every agent its
own window in that browser (a "claim"), paces page loads across all agents with
a cross-process lock, caps loads per window, and reports login/block state on
every load so the agent stops instead of retrying.

Site modules (reddit_tab.py, youtube_tab.py) build on this; the CLI and the MCP
server both call the site modules, so they behave identically. Stdlib only.
"""
from __future__ import annotations

import base64
import fcntl
import json
import math
import os
import random
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

BIN = shutil.which("chrome-agent") or str(Path.home() / ".local/bin/chrome-agent")


def pause(seconds: float, fuzz: float = 0.1) -> None:
    """Sleep for about `seconds`, varied by +/- `fuzz` (10%) so timing isn't mechanical."""
    if seconds > 0:
        time.sleep(seconds * random.uniform(1 - fuzz, 1 + fuzz))


def pace_delay(gap: float, long_p: float = 0.125, long_range: tuple[float, float] = (20.0, 60.0),
               rng: random.Random | None = None) -> float:
    """Seconds to leave between page loads (B5): skewed, not uniform.

    Mostly short: the base `gap` (+/-10%) plus a log-normal extra whose median is about 0.35 x gap and
    which is rarely more than 2 x gap. With probability `long_p` (default 1 in 8 loads) the gap is
    instead a long idle drawn uniformly from `long_range` (20-60 s), like a person stopping to read
    or think. Expected cost for a 10-load run at gap 4-6 s: about 1-2 minutes of extra waiting, most
    of which overlaps the agent's own think time (the wait counts from the previous load).
    """
    rng = rng or random
    if long_p > 0 and rng.random() < long_p:
        lo, hi = long_range
        return rng.uniform(lo, hi)
    extra = min(rng.lognormvariate(-1.05, 0.6), 3.0)
    return gap * rng.uniform(0.9, 1.1) + gap * extra


def _parse_range(raw: str, default: tuple[float, float]) -> tuple[float, float]:
    try:
        lo, hi = (float(x) for x in raw.replace(" ", "").split("-", 1))
        return (min(lo, hi), max(lo, hi))
    except (ValueError, AttributeError):
        return default


def intersect(a: dict, b: dict) -> dict | None:
    """Intersection of two {x, y, width, height} rects, or None when they don't overlap."""
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1 = min(a["x"] + a["width"], b["x"] + b["width"])
    y1 = min(a["y"] + a["height"], b["y"] + b["height"])
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def page_clip(rect: dict, scroll_x: float, scroll_y: float, viewport: dict | None = None,
              area: dict | None = None, pad: float = 0) -> dict | None:
    """Turn a viewport-coordinate rect (getBoundingClientRect) into a Page.captureScreenshot clip, which
    is in page coordinates: clamp it to what is visible (the viewport, and `area` if given, e.g. the
    content column without the top bar), pad it a little, then add the scroll offset. Clips are never
    taken beyond the viewport (that would make Chrome resize the page, which a page can observe)."""
    r = {"x": rect["x"] - pad, "y": rect["y"] - pad, "width": rect["width"] + 2 * pad,
         "height": rect["height"] + 2 * pad}
    for bound in (viewport, area):
        if bound is not None:
            r = intersect(r, bound)
            if r is None:
                return None
    return {"x": r["x"] + scroll_x, "y": r["y"] + scroll_y, "width": r["width"], "height": r["height"]}


def _view_js(selector: str | None, area_js: str | None, ad_selectors: tuple[str, ...] | list[str]) -> str:
    """JS returning the element rect, content area, viewport, scroll offset and visible ads (read-only)."""
    sel = json.dumps(selector) if selector else "null"
    area = f"({area_js.strip()})" if area_js else "null"
    return f"""(() => {{
  const vis = e => {{ const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' ? r : null; }};
  const box = r => r && {{x: r.x, y: r.y, width: r.width, height: r.height}};
  const sel = {sel};
  const el = sel ? [...document.querySelectorAll(sel)].find(vis) : null;
  const vp = {{x: 0, y: 0, width: innerWidth, height: innerHeight}};
  const ads = [];
  for (const s of {json.dumps(list(ad_selectors))}) {{
    for (const e of document.querySelectorAll(s)) {{
      const r = vis(e);
      if (!r || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
      ads.push({{selector: s, label: (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
                rect: box(r)}});
    }}
  }}
  const area = {area};
  return {{rect: el ? box(el.getBoundingClientRect()) : null, found: !!el, area: area || null, viewport: vp,
          scrollX, scrollY, dpr: devicePixelRatio, ads}};
}})()"""


class Fail(Exception):
    """An error the agent should read and act on (usually: stop and ask the user)."""


def _env(prefix: str, key: str, default):
    raw = os.environ.get(f"{prefix}_{key}")
    if raw is None:
        return default
    return type(default)(raw) if not isinstance(default, Path) else Path(raw)


@dataclass
class Site:
    prefix: str                      # env var prefix, e.g. "REDDIT_TAB"
    default_instance: str
    hosts: tuple[str, ...]           # allowed host suffixes
    ready_js: str                    # truthy when the page has rendered enough to read
    page_state_js: str               # returns {url, title, loggedIn, blocked, ...}
    stop_reason: Callable[[dict, "Site"], str | None]
    forbidden: tuple[str, ...] = ()  # URL substrings that must never be loaded
    default_gap: float = 4.0
    default_max_loads: int = 40
    settle: float = 2.5              # extra wait after ready, for late-rendering components
    on_open: Callable[["Site", str], None] | None = None
    content_js: str | None = None    # returns the reading column's viewport rect {x, y, width, height}
    ad_selectors: tuple[str, ...] = ()  # CSS selectors of ad units, for ads_in_view in captures
    instance: str = field(init=False)
    max_claims: int = field(init=False)
    gap: float = field(init=False)
    idle: float = field(init=False)
    max_loads: int = field(init=False)
    state: Path = field(init=False)
    long_idle_p: float = field(init=False)
    long_idle: tuple[float, float] = field(init=False)

    def __post_init__(self):
        p = self.prefix
        self.instance = _env(p, "INSTANCE", self.default_instance)
        self.max_claims = _env(p, "MAX", 3)
        self.gap = _env(p, "GAP", self.default_gap)
        self.idle = _env(p, "IDLE", 1800.0)
        self.max_loads = _env(p, "MAX_LOADS", self.default_max_loads)
        # Pacing (B5): chance of a long idle before a load, and its range in seconds.
        self.long_idle_p = _env(p, "LONG_IDLE_P", 0.125)
        self.long_idle = _parse_range(os.environ.get(f"{p}_LONG_IDLE", ""), (20.0, 60.0))
        name = p.split("_")[0].lower()
        self.state = _env(p, "STATE", Path(f"/tmp/chrome-agent-{name}"))

    # ---------------------------------------------------------------------
    # chrome-agent plumbing
    # ---------------------------------------------------------------------

    def ca(self, *args: str, timeout: float = 60) -> str:
        try:
            p = subprocess.run([BIN, *args], capture_output=True, text=True,
                               timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise Fail(f"chrome-agent {' '.join(args[:4])[:120]} timed out after {timeout:.0f} s")
        if p.returncode != 0:
            raise Fail((p.stderr or p.stdout).strip())
        return p.stdout

    def cdp(self, target: str, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        out = self.ca(self.instance, "--target-id", target, method, json.dumps(params or {}),
                      timeout=timeout)
        return json.loads(out) if out.strip() else {}

    def evaluate(self, target: str, expr: str, timeout: float = 30):
        res = self.cdp(target, "Runtime.evaluate",
                       {"expression": expr, "returnByValue": True, "awaitPromise": True},
                       timeout=timeout)
        if "exceptionDetails" in res:
            raise Fail(json.dumps(res["exceptionDetails"])[:500])
        return res.get("result", {}).get("value")

    # ---------------------------------------------------------------------
    # Human-like pointer: curved paths, uneven speed, occasional overshoot.
    # The last position is kept per window (in its claim) so paths start where
    # the pointer actually is.
    # ---------------------------------------------------------------------

    def _mouse_pos(self, target: str) -> tuple[float, float]:
        c = self.claims().get(target) or {}
        if "mouse" in c:
            return tuple(c["mouse"])
        vp = self.evaluate(target, "[innerWidth, innerHeight]") or [1200, 800]
        return (vp[0] * random.uniform(0.3, 0.7), vp[1] * random.uniform(0.3, 0.7))

    def _save_mouse(self, target: str, x: float, y: float) -> None:
        c = self.claims().get(target)
        if c is not None:
            c["mouse"] = [x, y]
            self._write_claim(target, c)

    def _path(self, x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
        dx, dy = x1 - x0, y1 - y0
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < 3:
            return [(x1, y1)]
        steps = int(min(26, max(6, dist / 30))) + random.randint(0, 3)
        # A bowed cubic Bezier: control points pushed off the straight line.
        nx, ny = -dy / dist, dx / dist
        bow = random.uniform(0.05, 0.25) * dist * random.choice((-1, 1))
        c1 = (x0 + dx * random.uniform(0.2, 0.4) + nx * bow, y0 + dy * random.uniform(0.2, 0.4) + ny * bow)
        c2 = (x0 + dx * random.uniform(0.6, 0.8) + nx * bow * 0.5, y0 + dy * random.uniform(0.6, 0.8) + ny * bow * 0.5)
        pts = []
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)  # ease in and out: slow start, fast middle, slow finish
            mt = 1 - t
            px = mt**3 * x0 + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t**3 * x1
            py = mt**3 * y0 + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t**3 * y1
            pts.append((px + random.uniform(-0.8, 0.8), py + random.uniform(-0.8, 0.8)))
        if dist > 120 and random.random() < 0.35:
            # Overshoot a little, then settle back onto the target.
            over = random.uniform(4, 10)
            pts[-1] = (x1 + dx / dist * over, y1 + dy / dist * over)
            pts += [(x1 + dx / dist * over * 0.4, y1 + dy / dist * over * 0.4), (x1, y1)]
        else:
            pts[-1] = (x1, y1)
        return pts

    def move(self, target: str, x: float, y: float) -> None:
        """Move the pointer to (x, y) along a human-like path."""
        x0, y0 = self._mouse_pos(target)
        for px, py in self._path(x0, y0, x, y):
            self.cdp(target, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
            if random.random() < 0.15:
                time.sleep(random.uniform(0.02, 0.09))
        self._save_mouse(target, x, y)

    def click(self, target: str, x: float, y: float) -> None:
        """Move there like a person, hover a moment, then press and release (a trusted click)."""
        self.move(target, x, y)
        pause(random.uniform(0.12, 0.35), fuzz=0)
        self.cdp(target, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        time.sleep(random.uniform(0.06, 0.14))
        self.cdp(target, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    def content_area(self, target: str) -> dict | None:
        """The site's reading column (viewport rect), or None when the site doesn't define one."""
        if not self.content_js:
            return None
        try:
            return self.evaluate(target, self.content_js)
        except Fail:
            return None

    def pointer_into(self, target: str, area: dict | None = None) -> tuple[float, float]:
        """Make sure the pointer rests inside `area` (default: the middle of the viewport), moving it
        there along a human path if it isn't (or if no position is known yet). Returns the position."""
        c = self.claims().get(target) or {}
        pos = tuple(c["mouse"]) if "mouse" in c else None
        if area is None:
            if pos and pos[0] >= 40 and pos[1] >= 80:
                return pos  # already somewhere in the page body, not parked in a corner or on the top bar
            vp = self.evaluate(target, "[innerWidth, innerHeight]") or [1200, 800]
            area = {"x": vp[0] * 0.3, "y": vp[1] * 0.3, "width": vp[0] * 0.4, "height": vp[1] * 0.4}
        elif pos and area["x"] + 8 <= pos[0] <= area["x"] + area["width"] - 8 \
                and area["y"] + 8 <= pos[1] <= area["y"] + area["height"] - 8:
            return pos
        x = area["x"] + area["width"] * random.uniform(0.3, 0.7)
        y = area["y"] + area["height"] * random.uniform(0.35, 0.65)
        self.move(target, x, y)
        pause(random.uniform(0.15, 0.4), fuzz=0)
        return (x, y)

    def wheel(self, target: str, dy: float, x: float | None = None, y: float | None = None,
              area: dict | None = None) -> None:
        """Scroll with the mouse wheel at the pointer's current (persisted) position, as a person would.
        With `area`, the pointer is first moved into it if it is outside. With an explicit x/y the
        pointer travels there first, so a wheel event never arrives where the mouse isn't."""
        if x is not None and y is not None:
            px, py = self._mouse_pos(target)
            if abs(px - x) > 3 or abs(py - y) > 3 or "mouse" not in (self.claims().get(target) or {}):
                self.move(target, x, y)
        else:
            x, y = self.pointer_into(target, area)
        try:
            self.cdp(target, "Input.dispatchMouseEvent",
                     {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy}, timeout=12)
        except Fail as e:
            if "timed out" not in str(e):
                raise
            # Chrome only acknowledges wheel events in a window it is drawing.
            vis = None
            try:
                vis = self.evaluate(target, "document.visibilityState", timeout=10)
            except Fail:
                pass
            raise Fail("mouse-wheel input timed out" + (" because the research window is hidden" if vis == "hidden"
                       else "") + " (minimized, on another desktop/workspace, or fully covered). Chrome only "
                       "delivers wheel scrolls to a visible window. Ask the user to bring the research browser "
                       "window into view, then retry.")

    # ---- Optional window-manager placement (AeroSpace) ---------------------------------------
    # With RESEARCH_AEROSPACE_WORKSPACE=<name> set, this instance's browser windows are moved to
    # that workspace, and the workspace is brought on screen if it isn't (then focus returns to the
    # workspace the user was on, so a workspace on another monitor stays visible without taking
    # focus). Only windows of this instance's own Chrome process are touched, never the user's.
    def _aerospace(self, *args: str) -> str | None:
        exe = shutil.which("aerospace")
        if not exe:
            return None
        try:
            p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout if p.returncode == 0 else None

    def _browser_pid(self) -> int | None:
        inst = self._status() or {}
        if inst.get("pid"):
            return int(inst["pid"])
        port = inst.get("port")
        if not port:
            return None
        try:
            out = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=10).stdout.split()
        except (OSError, subprocess.SubprocessError):
            return None
        return int(out[0]) if out else None

    def place_windows(self) -> dict | None:
        ws = os.environ.get("RESEARCH_AEROSPACE_WORKSPACE", "").strip()
        if not ws or not shutil.which("aerospace"):
            return None
        pid = self._browser_pid()
        if not pid:
            return {"workspace": ws, "error": "browser process not found"}
        listing = self._aerospace("list-windows", "--all", "--format", "%{window-id}|%{app-pid}|%{workspace}") or ""
        moved = 0
        for line in listing.splitlines():
            parts = line.split("|")
            if len(parts) == 3 and parts[1].strip() == str(pid) and parts[2].strip() != ws:
                if self._aerospace("move-node-to-workspace", "--window-id", parts[0].strip(), ws) is not None:
                    moved += 1
        visible = (self._aerospace("list-workspaces", "--monitor", "all", "--visible") or "").split()
        shown = False
        if ws not in visible:
            prev = (self._aerospace("list-workspaces", "--focused") or "").strip()
            self._aerospace("workspace", ws)
            if prev and prev != ws:
                self._aerospace("workspace", prev)  # give focus back; ws stays shown on its own monitor
            shown = True
        if moved or shown:
            pause(0.8)  # let Chrome notice it is visible again
        return {"workspace": ws, "moved": moved, "shown": shown}

    SCREEN_MSG = ("the screen is {why}, so Chrome treats every research window as hidden. Ask the user to "
                  "wake and unlock the computer (with the research monitor on), then retry.")

    @staticmethod
    def screen_state() -> str | None:
        """macOS preflight: 'locked' when the login session's screen is locked, 'asleep' when every
        display is asleep, else None (also None off macOS or when the check itself fails)."""
        if not shutil.which("ioreg"):
            return None
        try:
            import plistlib
            out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True,
                                 timeout=5).stdout
            root = plistlib.loads(out)
            root = root[0] if isinstance(root, list) else root
            for user in root.get("IOConsoleUsers") or []:
                if user.get("kCGSSessionOnConsoleKey") and user.get("CGSSessionScreenIsLocked"):
                    return "locked"
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError, IndexError):
            pass
        try:
            import ctypes
            import ctypes.util
            cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics") or "")
            ids, n = (ctypes.c_uint32 * 16)(), ctypes.c_uint32()
            if cg.CGGetOnlineDisplayList(16, ids, ctypes.byref(n)) == 0 and n.value:
                if all(cg.CGDisplayIsAsleep(ids[i]) for i in range(n.value)):
                    return "asleep"
        except (OSError, AttributeError):
            pass
        return None

    def preflight_screen(self) -> None:
        why = self.screen_state()
        if why:
            raise Fail(self.SCREEN_MSG.format(why=why))

    HIDDEN_MSG = ("the research window is hidden (minimized, on another desktop/workspace, or fully covered). "
                  "Chrome barely draws hidden windows, so scrolling stalls and pages load very slowly. Ask the "
                  "user to bring the research browser window into view, then retry.")

    def ensure_visible(self, target: str, grace: float = 3.0) -> None:
        """Fail fast when the window is hidden, instead of stalling later on wheel input (read-only
        check of document.visibilityState; re-checked once after a short grace period)."""
        def vis():
            try:
                return self.evaluate(target, "document.visibilityState", timeout=10)
            except Fail:
                return None
        if vis() == "hidden":
            self.preflight_screen()
            self.place_windows()
            pause(grace)
            if vis() == "hidden":
                self.preflight_screen()
                raise Fail(self.HIDDEN_MSG)

    def drift(self, target: str, max_px: float = 40, area: dict | None = None) -> None:
        """A small, slow idle movement of the pointer (B6), the way a hand rests on a mouse while reading.
        Stays inside `area` (or the viewport) when given."""
        x0, y0 = self._mouse_pos(target)
        dist = random.uniform(max_px * 0.2, max_px)
        ang = random.uniform(0, 6.2832)
        x, y = x0 + dist * math.cos(ang), y0 + dist * math.sin(ang) * 0.6
        if area is not None:
            x = min(max(x, area["x"] + 5), area["x"] + area["width"] - 5)
            y = min(max(y, area["y"] + 5), area["y"] + area["height"] - 5)
        x, y = max(2.0, x), max(2.0, y)
        for px, py in self._path(x0, y0, x, y)[:8] + [(x, y)]:
            self.cdp(target, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": px, "y": py})
            time.sleep(random.uniform(0.03, 0.12))
        self._save_mouse(target, x, y)

    def read_pause(self, target: str, seconds: float, drift_p: float = 0.35, area: dict | None = None) -> None:
        """Pause about `seconds` (+/-10%) as if reading; sometimes drift the pointer a little midway."""
        total = seconds * random.uniform(0.9, 1.1)
        if total > 1.2 and random.random() < drift_p:
            first = total * random.uniform(0.3, 0.7)
            time.sleep(first)
            t0 = time.time()
            try:
                self.drift(target, area=area)
            except Fail:
                pass
            time.sleep(max(0.0, total - first - (time.time() - t0)))
        else:
            time.sleep(total)

    def rect(self, target: str, selector: str) -> dict | None:
        """Viewport rect of the first visible element matching selector (read-only)."""
        js = (f"(() => {{ const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => {{"
              f" const r = e.getBoundingClientRect(), s = getComputedStyle(e);"
              f" return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; }});"
              f" if (!el) return null; const r = el.getBoundingClientRect();"
              f" return {{x: r.x, y: r.y, width: r.width, height: r.height, vh: innerHeight, vw: innerWidth}}; }})()")
        return self.evaluate(target, js)

    def scroll_into_view(self, target: str, selector: str, max_steps: int = 25,
                         area: dict | None = None) -> dict | None:
        """Wheel-scroll (at the pointer, inside `area` if given) until the element is comfortably inside
        the viewport."""
        for _ in range(max_steps):
            r = self.rect(target, selector)
            if r is None:
                return None
            top, bottom = r["y"], r["y"] + min(r["height"], r["vh"] * 0.6)
            if top >= 60 and bottom <= r["vh"] - 20:
                return r
            step = max(-700, min(700, top - r["vh"] * 0.3))
            self.wheel(target, step, area=area)
            pause(0.35)
        return self.rect(target, selector)

    def scroll_to_top(self, target: str, max_steps: int = 40, area: dict | None = None) -> None:
        for _ in range(max_steps):
            if (self.evaluate(target, "window.scrollY") or 0) <= 0:
                return
            self.wheel(target, -800, area=area)
            pause(0.25)

    def click_on(self, target: str, selector: str, look: float = 0) -> bool:
        """Scroll an element into view, take `look` seconds to find it, and click a point
        inside it (not dead center) with a human-like pointer path."""
        r = self.scroll_into_view(target, selector)
        if r is None:
            return False
        if look:
            pause(look)
        x = r["x"] + r["width"] * random.uniform(0.3, 0.7)
        y = r["y"] + r["height"] * random.uniform(0.35, 0.65)
        self.click(target, x, y)
        return True

    def screenshot(self, target: str, out: Path, clip: dict | None = None,
                   max_width: int | None = None, quality: int = 85) -> Path:
        """Capture the viewport (or `clip`). JPEG when `out` ends in .jpg, scaled down so the
        image is at most `max_width` pixels wide; PNG at full resolution otherwise."""
        out = Path(out).expanduser()
        jpeg = out.suffix.lower() in (".jpg", ".jpeg")
        params: dict = {"format": "jpeg", "quality": quality} if jpeg else {"format": "png"}
        if clip is None and max_width:
            # Clips are in page coordinates: the visible viewport starts at the scroll offset.
            clip = self.evaluate(target, "({x: scrollX, y: scrollY, width: innerWidth, height: innerHeight})")
        if clip:
            scale = clip.get("scale", 1)
            if max_width:
                dpr = self.evaluate(target, "devicePixelRatio") or 1
                scale = min(1.0, max_width / (clip["width"] * dpr))
            params["clip"] = {"x": clip["x"], "y": clip["y"], "width": clip["width"],
                              "height": clip["height"], "scale": scale}
        data = self.cdp(target, "Page.captureScreenshot", params)["data"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data))
        return out

    def capture(self, target: str, out: Path, selector: str | None = None, area_js: str | None = None,
                ad_selectors: tuple[str, ...] | list[str] | None = None, max_width: int | None = None,
                pad: float = 8, scroll: bool = True) -> dict:
        """Screenshot of the visible content (S3), shared by the sites.

        - `area_js`: JS returning the content area's viewport rect (top bar and nav excluded);
          default: the site's `content_js`, else the whole viewport.
        - `selector`: capture just that element, after wheel-scrolling it into view the way a person
          would (`scroll=False` captures it where it is). Elements taller than the view are cut to the
          visible part (`truncated: true`); clips never extend beyond the viewport.
        - `ad_selectors`: the site's ad units (default: `Site.ad_selectors`); the ones overlapping the
          image are returned as `ads_in_view` so the agent can caption them, or scroll and retake.
        Clips are computed in viewport coordinates and converted to page coordinates (scrollX/scrollY).
        Returns {path, clip, ads_in_view, [target_found, truncated]}."""
        ads = tuple(self.ad_selectors if ad_selectors is None else ad_selectors)
        area_js = area_js or self.content_js
        if selector and scroll:
            area = self.evaluate(target, area_js) if area_js else None
            self.scroll_into_view(target, selector, area=area)
            pause(0.4)
        v = self.evaluate(target, _view_js(selector, area_js, ads)) or {}
        vp = v.get("viewport") or {"x": 0, "y": 0, "width": 1200, "height": 800}
        res: dict = {}
        if selector:
            res["target_found"] = bool(v.get("found"))
            if not v.get("found"):
                raise Fail(f"nothing visible matches {selector!r} on this page")
            clip = page_clip(v["rect"], v["scrollX"], v["scrollY"], vp, v.get("area"), pad=pad)
            if clip is None:
                raise Fail(f"{selector!r} is not inside the visible content area")
            res["truncated"] = clip["height"] + 1 < v["rect"]["height"] or clip["width"] + 1 < v["rect"]["width"]
        else:
            clip = page_clip(v.get("area") or vp, v.get("scrollX", 0), v.get("scrollY", 0), vp)
        view_clip = {**clip, "x": clip["x"] - v.get("scrollX", 0), "y": clip["y"] - v.get("scrollY", 0)}
        res["ads_in_view"] = [a for a in v.get("ads", []) if intersect(a["rect"], view_clip)]
        path = self.screenshot(target, out, clip=clip, max_width=max_width)
        return {"path": str(path), "clip": {k: round(val, 1) for k, val in clip.items()}, **res}

    def _status(self) -> dict | None:
        try:
            data = json.loads(self.ca("status", self.instance))
        except Fail:
            return None
        inst = data[0] if isinstance(data, list) else data
        return inst if inst.get("alive") else None

    def targets(self, restore: bool = False) -> list[dict]:
        inst = self._status()
        if inst is None and restore:
            try:
                self.ca("restore", self.instance, "--any-port", timeout=120)
            except Fail as e:
                raise Fail(f"{self.instance} is not running and could not be restored ({e}). "
                           "Stop and ask the user.")
            inst = self._status()
        if inst is None:
            raise Fail(f"{self.instance} is not running. Stop and ask the user "
                       f"(it can be restored with: chrome-agent restore {self.instance} --any-port).")
        if not inst.get("targets") and restore:
            # A browser whose last window was closed keeps running with no tabs
            # (macOS). Open one through the DevTools HTTP endpoint so there is a
            # page target to issue commands through.
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{inst['port']}/json/new?about:blank",
                                         method="PUT")
            urllib.request.urlopen(req, timeout=10).read()
            pause(1)
            inst = self._status() or inst
        return inst.get("targets", [])

    # ---------------------------------------------------------------------
    # Locks and claims
    # ---------------------------------------------------------------------

    def _locked(self, name: str) -> "_Lock":
        return _Lock(self.state / name)

    @property
    def _claims_dir(self) -> Path:
        return self.state / "claims"

    def claims(self) -> dict[str, dict]:
        self._claims_dir.mkdir(parents=True, exist_ok=True)
        out = {}
        for f in self._claims_dir.glob("*.json"):
            try:
                out[f.stem] = json.loads(f.read_text())
            except (OSError, ValueError):
                pass
        return out

    def _write_claim(self, full_id: str, claim: dict) -> None:
        self._claims_dir.mkdir(parents=True, exist_ok=True)
        (self._claims_dir / f"{full_id}.json").write_text(json.dumps(claim))

    def resolve(self, target: str) -> str:
        hits = [k for k in self.claims() if k.upper().startswith(target.upper())]
        if len(hits) != 1:
            raise Fail(f"no window of yours matches {target!r}. Claim one with open "
                       "(or run list to see active claims).")
        return hits[0]

    def touch(self, full_id: str) -> None:
        c = self.claims().get(full_id)
        if c is not None:
            c["last_used"] = time.time()
            self._write_claim(full_id, c)

    def _gc_locked(self, live_ids: set[str]) -> list[str]:
        dropped, now = [], time.time()
        for full_id, c in self.claims().items():
            gone = full_id not in live_ids
            idle = now - c.get("last_used", 0) > self.idle
            if gone or idle:
                if idle and not gone:
                    try:
                        self.ca("stop", self.instance, "--target-id", full_id)
                    except Fail:
                        pass
                (self._claims_dir / f"{full_id}.json").unlink(missing_ok=True)
                dropped.append(full_id[:8])
        return dropped

    def _count_load(self, full_id: str) -> int:
        c = self.claims().get(full_id)
        if c is None:
            raise Fail(f"window {full_id[:8]} is no longer claimed (closed, or reclaimed after idling). "
                       "Claim a new one with open.")
        if c.get("loads", 0) >= self.max_loads:
            raise Fail(f"page-load cap reached ({self.max_loads}) for this window. "
                       "Stop and ask the user before loading more.")
        c["loads"] = c.get("loads", 0) + 1
        c["last_used"] = time.time()
        self._write_claim(full_id, c)
        return c["loads"]

    def _pace(self) -> None:
        """Block until a skewed gap (see pace_delay) has passed since the last load by any agent.
        Tune with {PREFIX}_GAP, {PREFIX}_LONG_IDLE_P (0 disables long idles) and {PREFIX}_LONG_IDLE ("20-60")."""
        with self._locked("pace.lock") as f:
            f.seek(0)
            try:
                last = float(f.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = last + pace_delay(self.gap, self.long_idle_p, self.long_idle) - time.time()
            if wait > 0:
                time.sleep(wait)
            f.seek(0)
            f.truncate()
            f.write(str(time.time()))
            f.flush()

    def wait_for(self, target: str, expr: str, timeout: float = 15, settle: float = 0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.evaluate(target, expr, timeout=10):
                    if settle:
                        pause(settle)
                    return True
            except Fail:
                pass
            pause(1)
        return False

    # ---------------------------------------------------------------------
    # Public operations
    # ---------------------------------------------------------------------

    def _window_of(self, full_id: str) -> int | None:
        try:
            return self.cdp(full_id, "Browser.getWindowForTarget", {"targetId": full_id}).get("windowId")
        except Fail:
            return None

    def _lone_tab(self, candidates: list[dict], tabs: list[dict]) -> str | None:
        """The first candidate that is the only tab in its window. A tab sharing a window may be a
        background tab, which Chrome treats as hidden."""
        windows = {t["full_id"]: self._window_of(t["full_id"]) for t in tabs}
        for c in candidates:
            w = windows.get(c["full_id"])
            if w is not None and sum(1 for v in windows.values() if v == w) == 1:
                return c["full_id"]
        return None

    def open_window(self, wait_seconds: float = 0) -> dict:
        """Claim a new window. Restores the instance if it is not running.
        With wait_seconds, waits for a free slot."""
        self.preflight_screen()
        deadline = time.time() + wait_seconds
        while True:
            with self._locked("claims.lock"):
                tabs = self.targets(restore=True)
                self._gc_locked({t["full_id"] for t in tabs})
                busy = len(self.claims())
                if busy < self.max_claims:
                    if not tabs:
                        raise Fail(f"{self.instance} has no open tab to issue commands through.")
                    # Reuse an unclaimed blank tab (the one kept or created so commands have a page to
                    # go through) instead of leaving it as an extra empty window next to a new one.
                    claimed = set(self.claims())
                    blank = [t for t in tabs if t["full_id"] not in claimed
                             and (t.get("url") or "") in ("about:blank", "")]
                    full_id = self._lone_tab(blank, tabs) if blank else None
                    if not full_id:
                        res = self.cdp(tabs[0]["full_id"], "Target.createTarget",
                                       {"url": "about:blank", "newWindow": True, "background": True})
                        full_id = res["targetId"]
                    now = time.time()
                    self._write_claim(full_id, {"created": now, "last_used": now, "loads": 0})
                    break
            if time.time() >= deadline:
                raise Fail(f"all {self.max_claims} research windows are in use. Wait and retry "
                           "(open --wait), or ask the user.")
            pause(min(15, max(1, deadline - time.time())))
        placed = self.place_windows()
        if self.on_open:
            self.on_open(self, full_id)
        try:
            self.ensure_visible(full_id)
        except Fail:
            self.close(full_id)  # release the claim; nothing useful can run in a hidden window
            raise
        res = {"target": full_id, "slots_in_use": busy + 1, "max_slots": self.max_claims}
        if placed:
            res["placement"] = placed
        return res

    def check_url(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not any(host == h or host.endswith("." + h) for h in self.hosts):
            raise Fail(f"only {', '.join(self.hosts)} pages may be loaded with this tool.")
        if any(f in url for f in self.forbidden):
            raise Fail("that URL is an API/internal endpoint and is off-limits; load the normal page instead.")

    def nav(self, target: str, url: str, ready_js: str | None = None) -> dict:
        """Paced, counted page load. Returns {url, title, loggedIn, blocked, loads, stop?}."""
        full_id = self.resolve(target)
        self.check_url(url)
        self.ensure_visible(full_id)  # before counting a load: a hidden window would only stall
        loads = self._count_load(full_id)
        self._pace()
        self.cdp(full_id, "Page.navigate", {"url": url})
        try:
            self.wait_for(full_id, ready_js or self.ready_js, settle=self.settle)
        except Fail:
            self.ensure_visible(full_id, grace=0)  # a hidden window explains a stalled load: say so
            raise
        state = self.evaluate(full_id, self.page_state_js) or {}
        state["loads"] = loads
        if state.get("visibility") == "hidden" or self.evaluate(full_id, "document.visibilityState") == "hidden":
            state["warning"] = self.HIDDEN_MSG
        reason = self.stop_reason(state, self)
        if reason:
            state["stop"] = reason
        return state

    def run_js(self, target: str, js: str):
        full_id = self.resolve(target)
        if js.startswith("@"):
            js = Path(js[1:]).expanduser().read_text()
        self.touch(full_id)
        return self.evaluate(full_id, js)

    def close(self, target: str) -> dict:
        full_id = self.resolve(target)
        with self._locked("claims.lock"):
            try:
                self.ca("stop", self.instance, "--target-id", full_id)
            except Fail:
                pass
            (self._claims_dir / f"{full_id}.json").unlink(missing_ok=True)
            # Don't leave empty windows on screen: close unclaimed blank tabs too. On macOS Chrome
            # keeps running without windows, and open creates a fresh tab when there is none.
            try:
                claimed = set(self.claims())
                for t in self.targets():
                    if t["full_id"] not in claimed and (t.get("url") or "") == "about:blank":
                        self.ca("stop", self.instance, "--target-id", t["full_id"])
            except Fail:
                pass
        return {"closed": full_id}

    def list_claims(self) -> list[dict]:
        now = time.time()
        return [{"target": k, "age_s": int(now - c["created"]), "idle_s": int(now - c["last_used"]),
                 "loads": c.get("loads", 0)} for k, c in self.claims().items()]

    def gc(self) -> dict:
        with self._locked("claims.lock"):
            return {"released": self._gc_locked({t["full_id"] for t in self.targets()})}


class _Lock:
    """Exclusive cross-process lock on a file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a+")

    def __enter__(self):
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self.f

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


# -------------------------------------------------------------------------
# CLI helpers
# -------------------------------------------------------------------------

def opt(args: list[str], name: str, default=None):
    """Pop `--name value` (or bare `--name` -> True) from args."""
    if name in args:
        i = args.index(name)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            val = args[i + 1]
            del args[i:i + 2]
            return val
        del args[i]
        return True
    return default


def common_cli(site: Site, cmd: str, args: list[str]):
    """Commands every site shares. Returns NotImplemented if cmd isn't one of them."""
    if cmd == "open":
        w = opt(args, "--wait", 0)
        return site.open_window(600 if w is True else float(w))
    if cmd == "nav" and len(args) == 2:
        return site.nav(*args)
    if cmd == "eval" and len(args) == 2:
        return site.run_js(*args)
    if cmd == "close" and len(args) == 1:
        return site.close(args[0])
    if cmd == "list":
        return site.list_claims()
    if cmd == "gc":
        return site.gc()
    return NotImplemented


def run_cli(doc: str, dispatch: Callable[[str, list[str]], object], argv: list[str]) -> int:
    import sys
    if len(argv) < 2:
        print(doc.strip(), file=sys.stderr)
        return 2
    if argv[1] in ("-h", "--help", "help"):
        print(doc.strip())
        return 0
    try:
        result = dispatch(argv[1], argv[2:])
        if result is NotImplemented:
            raise Fail(doc.strip())
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Fail as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
