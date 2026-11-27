#!/usr/bin/env python3
"""Read slices of a research run's saved raw results, without pipes or jq (read-only, no network).

    raw_query.py RAW_DIR index [--site S] [--cmd C] [--grep T] [--limit N]
                       the run's commands: n, site, cmd, args, file, ok, partial (from every index.jsonl)
    raw_query.py RAW_DIR FILE [--path P] [--fields a,b,c] [--grep T] [--where F=V] [--sort F [--desc]]
                       [--offset N] [--limit N] [--chars N]
                       one saved result. With no --path: an outline (keys, types, list lengths, string sizes).
    raw_query.py RAW_DIR FILE --path P --text [--from N] [--length N]
                       one long string (a README, a post body) in windows, with the next --from to continue
    raw_query.py RAW_DIR find T [--site S] [--fields a,b] [--limit N]
                       every list item in every saved result whose text contains T (case-insensitive)

RAW_DIR is a report's raw folder (or raw/verify). FILE is a `raw_file` as a result printed it
("004_211651_links.json" for the site the command ran on, or "github/004_211651_links.json"), a path relative to
RAW_DIR, or an absolute path inside it. Only files under the reports root are read (RAW_QUERY_REPORTS_ROOT,
default <chrome-bot>/reports).

--path is dotted with list indexes: `result.repos`, `result.thread.comments[3]`, `result.repos[*].full_name`.
Saved files wrap the command's output in {"cmd", "args", "saved_at", "result"}; a path that doesn't start with
one of those keys is looked up under `result`. --fields picks keys from each item of a list (dotted keys work:
`owner.login`). --grep keeps list items whose text contains T; --where F=V keeps items whose field equals V
(F!=V; F>N, F>=N, F<N, F<=N for numbers; quote the condition in the shell). Strings longer than --chars (default 400) are cut, with their full length noted.
Output is JSON; lists come back as {"total", "matched", "offset", "items"}.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ.get("RAW_QUERY_REPORTS_ROOT",
                           str(Path(__file__).resolve().parents[4] / "reports"))).resolve()
WRAP_KEYS = ("cmd", "args", "saved_at", "result")
MAX_OUT = 60_000


class QueryError(Exception):
    pass


def _inside(p: Path) -> Path:
    p = Path(os.path.realpath(p))
    if not p.is_relative_to(ROOT):
        raise QueryError(f"only files inside {ROOT} can be read")
    return p


def raw_root(raw_dir: str) -> Path:
    d = _inside(Path(os.path.expanduser(raw_dir)))
    if not d.is_dir():
        raise QueryError(f"not a folder: {d}")
    return d


def resolve_file(d: Path, name: str) -> Path:
    cands = [Path(os.path.expanduser(name))] if os.path.isabs(os.path.expanduser(name)) else \
        [d / name] + [s / name for s in sorted(d.iterdir()) if s.is_dir()]
    for c in cands:
        if c.is_file():
            return _inside(c)
    raise QueryError(f"no saved file {name!r} under {d} (try `index` to list them)")


def load(f: Path):
    text = f.read_text()
    if f.suffix == ".jsonl":
        return [json.loads(l) for l in text.splitlines() if l.strip()]
    try:
        return json.loads(text)
    except ValueError:
        return text  # markdown transcript, README, ...


_TOK = re.compile(r"([^.\[\]]+)|\[(\d+|\*)\]")


def select(obj, path: str | None):
    if not path:
        return obj
    toks = [(m.group(1), m.group(2)) for m in _TOK.finditer(path)]
    if isinstance(obj, dict) and "result" in obj and toks and toks[0][0] not in WRAP_KEYS:
        obj = obj["result"]

    def walk(o, i):
        if i == len(toks):
            return o
        key, idx = toks[i]
        if key is not None:
            if not isinstance(o, dict) or key not in o:
                have = ", ".join(list(o)[:30]) if isinstance(o, dict) else type(o).__name__
                raise QueryError(f"no key {key!r} at {path!r} (available: {have})")
            return walk(o[key], i + 1)
        if not isinstance(o, list):
            raise QueryError(f"{path!r}: [{idx}] on a {type(o).__name__}")
        if idx == "*":
            return [walk(x, i + 1) for x in o]
        n = int(idx)
        if n >= len(o):
            raise QueryError(f"{path!r}: index {n} out of range (length {len(o)})")
        return walk(o[n], i + 1)
    return walk(obj, 0)


def outline(o, depth: int = 0, max_depth: int = 3):
    if isinstance(o, dict):
        if depth >= max_depth:
            return f"dict({len(o)} keys)"
        return {k: outline(v, depth + 1, max_depth) for k, v in o.items()}
    if isinstance(o, list):
        if not o:
            return "list(0)"
        first = o[0]
        if isinstance(first, dict):
            keys = sorted({k for x in o[:50] if isinstance(x, dict) for k in x})
            return f"list({len(o)}) of dict: {', '.join(keys[:40])}"
        return f"list({len(o)}) of {type(first).__name__}"
    if isinstance(o, str):
        return f"str({len(o)}): {o[:80]!r}" if len(o) > 80 else o
    return o


def cut(o, chars: int):
    if isinstance(o, str):
        return o if len(o) <= chars else o[:chars] + f"… [{len(o)} chars; use --text --path to read on]"
    if isinstance(o, dict):
        return {k: cut(v, chars) for k, v in o.items()}
    if isinstance(o, list):
        return [cut(v, chars) for v in o]
    return o


def get_field(item, dotted: str):
    for part in dotted.split("."):
        if not isinstance(item, dict):
            return None
        item = item.get(part)
    return item


def text_of(o) -> str:
    return json.dumps(o, ensure_ascii=False).lower() if not isinstance(o, str) else o.lower()


_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}


def _where(items: list, cond: str) -> list:
    """FIELD=VALUE, FIELD!=VALUE (case-insensitive text), or FIELD>N, >=N, <N, <=N (numbers). Quote the condition
    in the shell ("sec>=780"): unquoted, `>` is a redirect."""
    m = re.match(r"^([\w.]+)\s*(!=|>=|<=|=|>|<)\s*(.*)$", cond)
    if not m:
        raise QueryError(f"--where needs FIELD=VALUE, FIELD!=VALUE or FIELD>N / >=N / <N / <=N (got {cond!r}; "
                         "quote it in the shell, e.g. --where \"sec>=780\")")
    f, op, v = m.groups()
    out = []
    for it in items:
        x = get_field(it, f)
        if op in ("=", "!="):
            if (str(x).lower() == v.lower()) == (op == "="):
                out.append(it)
            continue
        try:
            if _OPS[op](float(x), float(v)):
                out.append(it)
        except (TypeError, ValueError):
            pass
    return out


def shape_list(items: list, fields=None, grep=None, where=None, sort=None, desc=False, offset=0, limit=50,
               chars=400) -> dict:
    total = len(items)
    if grep:
        items = [x for x in items if grep.lower() in text_of(x)]
    if where:
        items = _where(items, where)
    if sort:
        items = sorted(items, key=lambda x: (get_field(x, sort) is None, get_field(x, sort) or 0), reverse=desc)
        if desc:  # keep missing values last
            items = [x for x in items if get_field(x, sort) is not None] + \
                    [x for x in items if get_field(x, sort) is None]
    matched = len(items)
    page = items[offset:offset + limit]
    if fields:
        page = [{f: get_field(x, f) for f in fields} if isinstance(x, dict) else x for x in page]
    return {"total": total, "matched": matched, "offset": offset, "returned": len(page),
            "next_offset": offset + len(page) if offset + len(page) < matched else None, "items": cut(page, chars)}


def index(d: Path, site=None, cmd=None, grep=None, limit=200) -> dict:
    rows = []
    for f in sorted(d.rglob("index.jsonl")):
        s = f.parent.relative_to(d).as_posix()
        for e in load(f):
            rows.append({"site": s, "n": e.get("n"), "cmd": e.get("cmd"), "args": e.get("args", [])[1:],
                         "file": f"{s}/{e['file']}" if e.get("file") else None, "ok": e.get("ok"),
                         "partial": e.get("partial") or None, "error": e.get("error")})
    if site:
        rows = [r for r in rows if r["site"] == site or r["site"].endswith("/" + site)]
    if cmd:
        rows = [r for r in rows if r["cmd"] == cmd]
    return shape_list(rows, grep=grep, limit=limit, chars=200)


def find(d: Path, term: str, site=None, fields=None, limit=50, chars=300) -> dict:
    hits = []
    for f in sorted(d.rglob("[0-9][0-9][0-9]_*.json")):
        s = f.parent.relative_to(d).as_posix()
        if site and not (s == site or s.endswith("/" + site)):
            continue
        rec = load(f)

        def walk(o, path):
            if isinstance(o, list):
                for i, x in enumerate(o):
                    if isinstance(x, dict) and term.lower() in text_of(x):
                        hits.append({"file": f"{s}/{f.name}", "path": f"{path}[{i}]",
                                     "item": {k: get_field(x, k) for k in fields} if fields else x})
                    elif isinstance(x, (dict, list)):
                        walk(x, f"{path}[{i}]")
            elif isinstance(o, dict):
                for k, v in o.items():
                    if isinstance(v, (dict, list)):
                        walk(v, f"{path}.{k}" if path else k)
        walk(rec.get("result", rec) if isinstance(rec, dict) else rec, "result")
    out = shape_list(hits, limit=limit, chars=chars)
    out["term"] = term
    return out


def query(raw_dir: str, target: str, path=None, fields=None, grep=None, where=None, sort=None, desc=False,
          offset=0, limit=50, chars=400, text=False, start=0, length=8000, site=None, cmd=None) -> dict:
    d = raw_root(raw_dir)
    if target == "index":
        return index(d, site, cmd, grep, limit)
    if target == "find":
        if not grep:
            raise QueryError("find needs a term: raw_query.py RAW_DIR find TERM")
        return find(d, grep, site, fields, limit, chars)
    f = resolve_file(d, target)
    obj = select(load(f), path)
    out = {"file": str(f.relative_to(d)), "path": path}
    if text:
        if not isinstance(obj, str):
            raise QueryError(f"--text needs a string at --path (got {type(obj).__name__})")
        chunk = obj[start:start + length]
        out.update({"chars": len(obj), "from": start, "next_from": start + len(chunk)
                    if start + len(chunk) < len(obj) else None, "text": chunk})
        return out
    if isinstance(obj, list) and (path or fields or grep or where or sort):
        out.update(shape_list(obj, fields, grep, where, sort, desc, offset, limit, chars))
    elif path is None and not fields:
        out["outline"] = outline(obj)
        out["hint"] = "add --path (e.g. result.<key>) to read a part; lists take --fields/--grep/--limit"
    else:
        out["value"] = cut(obj, chars)
    return out


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) < 2 or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if args and args[0] in ("-h", "--help") else 2

    def take(name, default=None, flag=False):
        if name in args:
            i = args.index(name)
            if flag:
                del args[i]
                return True
            if i + 1 >= len(args):
                raise QueryError(f"{name} needs a value")
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default
    try:
        kw = dict(path=take("--path"), grep=take("--grep"), where=take("--where"), sort=take("--sort"),
                  desc=take("--desc", flag=True), text=take("--text", flag=True), site=take("--site"),
                  cmd=take("--cmd"), offset=int(take("--offset", 0)), limit=int(take("--limit", 50)),
                  chars=int(take("--chars", 400)), start=int(take("--from", 0)), length=int(take("--length", 8000)))
        fields = take("--fields")
        kw["fields"] = [x.strip() for x in fields.split(",") if x.strip()] if fields else None
        raw_dir, target, *rest = args
        if target == "find" and rest and not kw["grep"]:
            kw["grep"] = " ".join(rest)
        elif rest:
            raise QueryError(f"unexpected arguments: {rest}")
        out = query(raw_dir, target, **kw)
    except (QueryError, ValueError) as e:
        msg = json.dumps({"error": str(e)})
        print(msg)
        print(msg, file=sys.stderr)  # still visible if stdout was redirected by mistake
        return 1
    s = json.dumps(out, ensure_ascii=False, indent=1)
    if len(s) > MAX_OUT:
        s = s[:MAX_OUT] + f"\n… [output cut at {MAX_OUT} chars: narrow with --fields, --limit or --chars]"
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
