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
    instance: str = field(init=False)
    max_claims: int = field(init=False)
    gap: float = field(init=False)
    idle: float = field(init=False)
    max_loads: int = field(init=False)
    state: Path = field(init=False)

    def __post_init__(self):
        p = self.prefix
        self.instance = _env(p, "INSTANCE", self.default_instance)
        self.max_claims = _env(p, "MAX", 3)
        self.gap = _env(p, "GAP", self.default_gap)
        self.idle = _env(p, "IDLE", 1800.0)
        self.max_loads = _env(p, "MAX_LOADS", self.default_max_loads)
        name = p.split("_")[0].lower()
        self.state = _env(p, "STATE", Path(f"/tmp/chrome-agent-{name}"))

    # ---------------------------------------------------------------------
    # chrome-agent plumbing
    # ---------------------------------------------------------------------

    def ca(self, *args: str, timeout: float = 60) -> str:
        p = subprocess.run([BIN, *args], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
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

    def click(self, target: str, x: float, y: float) -> None:
        """A trusted click (press + release), as a person would make."""
        for t in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.cdp(target, "Input.dispatchMouseEvent",
                     {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})

    def move(self, target: str, x: float, y: float) -> None:
        self.cdp(target, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

    def wheel(self, target: str, dy: float, x: float = 400, y: float = 400) -> None:
        """Scroll with the mouse wheel, as a person would."""
        self.cdp(target, "Input.dispatchMouseEvent",
                 {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy})

    def rect(self, target: str, selector: str) -> dict | None:
        """Viewport rect of the first visible element matching selector (read-only)."""
        js = (f"(() => {{ const el = [...document.querySelectorAll({json.dumps(selector)})].find(e => {{"
              f" const r = e.getBoundingClientRect(), s = getComputedStyle(e);"
              f" return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; }});"
              f" if (!el) return null; const r = el.getBoundingClientRect();"
              f" return {{x: r.x, y: r.y, width: r.width, height: r.height, vh: innerHeight, vw: innerWidth}}; }})()")
        return self.evaluate(target, js)

    def scroll_into_view(self, target: str, selector: str, max_steps: int = 25) -> dict | None:
        """Wheel-scroll until the element is comfortably inside the viewport."""
        for _ in range(max_steps):
            r = self.rect(target, selector)
            if r is None:
                return None
            top, bottom = r["y"], r["y"] + min(r["height"], r["vh"] * 0.6)
            if top >= 60 and bottom <= r["vh"] - 20:
                return r
            step = max(-700, min(700, top - r["vh"] * 0.3))
            self.wheel(target, step, x=r["vw"] / 2, y=r["vh"] / 2)
            pause(0.35)
        return self.rect(target, selector)

    def scroll_to_top(self, target: str, max_steps: int = 40) -> None:
        for _ in range(max_steps):
            if (self.evaluate(target, "window.scrollY") or 0) <= 0:
                return
            self.wheel(target, -800)
            pause(0.25)

    def click_on(self, target: str, selector: str) -> bool:
        """Scroll an element into view and click its center with a trusted click."""
        r = self.scroll_into_view(target, selector)
        if r is None:
            return False
        self.move(target, r["x"] + r["width"] / 2 - 3, r["y"] + r["height"] / 2 - 2)
        pause(0.15)
        self.click(target, r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
        return True

    def screenshot(self, target: str, out: Path, clip: dict | None = None) -> Path:
        params: dict = {"format": "png"}
        if clip:
            params["clip"] = {**clip, "scale": clip.get("scale", 1)}
        data = self.cdp(target, "Page.captureScreenshot", params)["data"]
        out = Path(out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(data))
        return out

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
        """Block until `gap` seconds have passed since the last load by any agent."""
        with self._locked("pace.lock") as f:
            f.seek(0)
            try:
                last = float(f.read().strip() or 0)
            except ValueError:
                last = 0.0
            wait = last + self.gap * random.uniform(0.9, 1.1) - time.time()
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

    def open_window(self, wait_seconds: float = 0) -> dict:
        """Claim a new window. Restores the instance if it is not running.
        With wait_seconds, waits for a free slot."""
        deadline = time.time() + wait_seconds
        while True:
            with self._locked("claims.lock"):
                tabs = self.targets(restore=True)
                self._gc_locked({t["full_id"] for t in tabs})
                busy = len(self.claims())
                if busy < self.max_claims:
                    if not tabs:
                        raise Fail(f"{self.instance} has no open tab to issue commands through.")
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
        if self.on_open:
            self.on_open(self, full_id)
        return {"target": full_id, "slots_in_use": busy + 1, "max_slots": self.max_claims}

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
        loads = self._count_load(full_id)
        self._pace()
        self.cdp(full_id, "Page.navigate", {"url": url})
        self.wait_for(full_id, ready_js or self.ready_js, settle=self.settle)
        state = self.evaluate(full_id, self.page_state_js) or {}
        state["loads"] = loads
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
