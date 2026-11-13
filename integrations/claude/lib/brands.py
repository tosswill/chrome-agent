"""Topic brand files shared by the Reddit and YouTube helpers.

The built-in brand lists can't know every category, so a run records the products its landscape pass found
in one file per topic, and both sites read it. A window opted into a topic with `topic ID "TOPIC"` uses it
for every later command (brand detection, sponsor isolation, brand subreddits, tally aliases).

    ~/.local/share/chrome-agent-research/topics/<slug>.json   (RESEARCH_TOPICS overrides the folder)
    {"topic": "...", "brands": ["Apple TV", ...], "aliases": {"Fire TV": ["Firestick"]},
     "everyday": {"Bear Notes": ["Bear"]}, "common": ["widevine"], "updated": "YYYY-MM-DD"}

- brands: product or company names (as written, any case).
- aliases: other spellings of a brand.
- everyday: short names that are ordinary words; matched only in context (case-sensitive on YouTube,
  only when the thread or query names them on Reddit).
- common: category words that look like names but are never a brand ("Widevine", "HDR").
"""
import json
import os
import re
from datetime import date
from pathlib import Path

TOPICS = Path(os.environ.get("RESEARCH_TOPICS", "~/.local/share/chrome-agent-research/topics")).expanduser()


def slug(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")
    if not s:
        raise ValueError("a topic needs at least one letter or digit")
    return s[:80]


def path(topic: str) -> Path:
    return TOPICS / f"{slug(topic)}.json"


def empty(topic: str) -> dict:
    return {"topic": topic, "brands": [], "aliases": {}, "everyday": {}, "common": []}


def load(topic: str | None) -> dict:
    """The topic's file, or an empty one (never fails on a missing or broken file)."""
    if not topic:
        return {}
    try:
        data = json.loads(path(topic).read_text())
    except (OSError, ValueError):
        return empty(topic)
    out = empty(data.get("topic") or topic)
    out.update({k: data.get(k) or out[k] for k in ("brands", "aliases", "everyday", "common")})
    if data.get("updated"):
        out["updated"] = data["updated"]
    return out


def _uniq(items) -> list[str]:
    seen, out = set(), []
    for x in items:
        x = re.sub(r"\s+", " ", str(x or "")).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def parse_map(raw: str | dict | None) -> dict[str, list[str]]:
    """'Fire TV=Firestick,Fire Stick; Nvidia Shield=Shield' -> {"Fire TV": ["Firestick", "Fire Stick"], ...}"""
    if isinstance(raw, dict):
        return {k: _uniq(v if isinstance(v, list) else [v]) for k, v in raw.items()}
    out: dict[str, list[str]] = {}
    for part in (raw or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out.setdefault(k.strip(), []).extend(_uniq(v.split(",")))
    return out


def add(topic: str, brands=(), aliases=None, everyday=None, common=()) -> dict:
    """Merge names into the topic's file and save it. Returns the saved file plus `path`."""
    data = load(topic)
    brands = [brands] if isinstance(brands, str) else brands
    common = [common] if isinstance(common, str) else common
    data["brands"] = _uniq(list(data["brands"]) + list(brands or []))
    for key, new in (("aliases", parse_map(aliases)), ("everyday", parse_map(everyday))):
        cur = {k: list(v) for k, v in (data.get(key) or {}).items()}
        for k, v in new.items():
            cur[k] = _uniq(cur.get(k, []) + v)
            if key == "aliases" or k not in data["brands"]:
                data["brands"] = _uniq(data["brands"] + [k])
        data[key] = cur
    data["common"] = _uniq(list(data["common"]) + [c for x in common or () for c in str(x).split(",")])
    data["updated"] = date.today().isoformat()
    TOPICS.mkdir(parents=True, exist_ok=True)
    p = path(topic)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return {**data, "path": str(p)}


def remove(topic: str, names=()) -> dict:
    data = load(topic)
    drop = {n.lower() for n in names}
    data["brands"] = [b for b in data["brands"] if b.lower() not in drop]
    for key in ("aliases", "everyday"):
        data[key] = {k: v for k, v in data[key].items() if k.lower() not in drop}
    data["common"] = [c for c in data["common"] if c.lower() not in drop]
    data["updated"] = date.today().isoformat()
    TOPICS.mkdir(parents=True, exist_ok=True)
    path(topic).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return {**data, "path": str(path(topic))}


def listing() -> list[dict]:
    out = []
    for p in sorted(TOPICS.glob("*.json")) if TOPICS.exists() else []:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        out.append({"topic": d.get("topic"), "brands": len(d.get("brands") or []), "updated": d.get("updated"),
                    "path": str(p)})
    return out


def flat(name: str) -> str:
    """The joined lowercase token a name reduces to ("iA Writer" -> "iawriter", "Fire TV" -> "firetv")."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class Overlay:
    """Adds a topic's names to a module's brand collections in place, and takes them back out, so a
    long-lived process (the MCP server) can switch topics between calls."""

    def __init__(self):
        self.topic: str | None = None
        self._undo: list = []

    def reset(self) -> None:
        for fn in reversed(self._undo):
            fn()
        self._undo, self.topic = [], None

    def extend_list(self, lst: list, items) -> None:
        have = {x.lower() for x in lst}
        new = [x for x in items if x.lower() not in have]
        lst.extend(new)
        self._undo.append(lambda: [lst.remove(x) for x in new if x in lst])

    def extend_set(self, st: set, items) -> None:
        new = {x for x in items if x not in st}
        st.update(new)
        self._undo.append(lambda: st.difference_update(new))

    def extend_map(self, mp: dict, extra: dict) -> None:
        before = {k: list(mp[k]) if k in mp else None for k in extra}
        for k, v in extra.items():
            mp[k] = _uniq(list(mp.get(k) or []) + list(v))

        def undo():
            for k, v in before.items():
                if v is None:
                    mp.pop(k, None)
                else:
                    mp[k] = v
        self._undo.append(undo)


def tally_aliases(data: dict, terms, user_aliases: dict | None) -> dict:
    """The user's --alias groups plus the topic file's aliases for any tallied name the user didn't alias."""
    out = {k: list(v) for k, v in (user_aliases or {}).items()}
    have = {k.lower() for k in out}
    topic = {k.lower(): (k, v) for k, v in (data.get("aliases") or {}).items()}
    for t in terms:
        if t.lower() in topic and t.lower() not in have:
            out[t] = list(topic[t.lower()][1])
    return out


def command(action: str, topic: str | None = None, names=(), alias: str | None = None,
            everyday: str | None = None, common: str | None = None) -> dict:
    """`brands list | show --topic T | add --topic T NAME ... [--alias ...] [--everyday ...] [--common w,w]
    | remove --topic T NAME ...` (shared by both helpers' CLIs and MCP tools)."""
    if action == "list":
        return {"topics": listing(), "folder": str(TOPICS)}
    if not topic:
        raise ValueError(f"brands {action} needs --topic")
    if action == "show":
        return {**load(topic), "path": str(path(topic)), "exists": path(topic).exists()}
    if action == "add":
        if not (names or alias or everyday or common):
            raise ValueError("brands add needs names, --alias, --everyday or --common")
        return add(topic, names, alias, everyday, [common] if common else ())
    if action == "remove":
        return remove(topic, names)
    raise ValueError("usage: brands list | show --topic T | add --topic T NAME ... [--alias \"A=x,y\"] "
                     "[--everyday \"Bear Notes=Bear\"] [--common word,word] | remove --topic T NAME ...")
