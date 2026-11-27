#!/usr/bin/env python3
"""Clock time, tokens and activity for every research agent that worked on one report folder.

    research_run_stats.py <report folder> [--projects-dir DIR] [--md FILE] [--json FILE] [--no-ledger]

Finds the Claude Code subagent transcripts (~/.claude/projects/<project>/<session>/subagents/agent-*.jsonl) whose
task prompt names the report folder, and the helpers' saved results (<report>/raw/<site>/index.jsonl), then
reports per agent:

  - agent type, description, model, start/end, wall-clock minutes
  - tokens: fresh input, cache writes, cache reads, output (thinking included), deduplicated per API message
  - turns, tool calls by tool, tool errors, guard blocks, web searches/fetches
  - yield: how many of each per-site report's URLs the final report (*_report_*.md) cites
and per site: helper commands run (ok / failed / stops) and the GitHub call counter.

Writes <report>/raw/run_stats.json and run_stats.md (or --json/--md), and keeps one line per report folder in
<reports root>/_runs.jsonl (a ledger for comparing runs over time; --no-ledger skips it). Read-only on transcripts.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def _ts(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _first_prompt(rows: list[dict]) -> str:
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "user" and m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def agent_stats(path: Path) -> dict:
    rows = []
    for line in path.open():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    meta = {}
    mp = path.with_suffix("").with_suffix(".meta.json") if path.name.endswith(".jsonl") else None
    mp = path.parent / (path.name[:-len(".jsonl")] + ".meta.json")
    if mp.exists():
        try:
            meta = json.loads(mp.read_text())
        except ValueError:
            pass
    tok = Counter()
    seen = set()
    tools = Counter()
    errors = blocks = turns = 0
    wrote: list[str] = []
    model = None
    times = [t for t in (_ts(r.get("timestamp")) for r in rows) if t]
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "assistant":
            mid = m.get("id")
            u = m.get("usage")
            if u and mid not in seen:
                seen.add(mid)
                turns += 1
                model = m.get("model") or model
                tok["input"] += u.get("input_tokens") or 0
                tok["cache_write"] += u.get("cache_creation_input_tokens") or 0
                tok["cache_read"] += u.get("cache_read_input_tokens") or 0
                tok["output"] += u.get("output_tokens") or 0
                tok["thinking"] += (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0
                st = u.get("server_tool_use") or {}
                tok["web_search"] += st.get("web_search_requests") or 0
                tok["web_fetch"] += st.get("web_fetch_requests") or 0
            for b in m.get("content") or [] if isinstance(m.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools[b.get("name") or "?"] += 1
                    fp = (b.get("input") or {}).get("file_path")
                    if b.get("name") in ("Write", "Edit") and isinstance(fp, str) and fp.endswith(".md") \
                            and "/raw/" not in fp:
                        wrote.append(fp)
        elif r.get("type") == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    text = json.dumps(b.get("content"))
                    if "Blocked by research_guard" in text:
                        blocks += 1
                    elif b.get("is_error"):
                        errors += 1
    start, end = (min(times), max(times)) if times else (None, None)
    return {"agent_id": meta.get("agentId") or path.stem.removeprefix("agent-"),
            "agent_type": meta.get("agentType"), "description": meta.get("description"), "model": model,
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "wall_min": round((end - start).total_seconds() / 60, 1) if start and end else None,
            "turns": turns, "tokens": dict(tok), "tool_calls": sum(tools.values()), "tools": dict(tools.most_common()),
            "tool_errors": errors, "guard_blocks": blocks, "finished": meta.get("finished"),
            "wrote": list(dict.fromkeys(wrote)),
            "transcript": str(path)}


def site_activity(raw: Path) -> dict:
    out = {}
    for idx in sorted(raw.glob("*/index.jsonl")) + sorted(raw.glob("verify/*/index.jsonl")):
        site = str(idx.parent.relative_to(raw))
        c, ok, fail, stop = Counter(), 0, 0, 0
        for line in idx.open():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c[e.get("cmd") or "?"] += 1
            if e.get("ok"):
                ok += 1
            else:
                fail += 1
            if "stop" in json.dumps(e.get("error") or ""):
                stop += 1
        calls = idx.parent / ".calls"
        out[site] = {"commands": sum(c.values()), "ok": ok, "failed": fail, "by_cmd": dict(c.most_common()),
                     **({"api_calls": int(calls.read_text().strip() or 0)} if calls.exists() else {})}
    return out


URL_RE = re.compile(r"https?://[^\s)\]>\"'`|]+")


def _urls(text: str) -> set[str]:
    """Evidence keys: normalized URLs plus short forms reports use instead of links (GitHub `#N` issue/PR
    numbers, HN item ids, YouTube video ids, Reddit comment/thread ids), so `#2253` in the final report matches
    github.com/obra/superpowers/issues/2253 in a per-site report."""
    out = set()
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?*_")
        u = re.sub(r"[?&](utm_[^&]+|si=[^&]+)", "", u).rstrip("/")
        out.add(u.lower())
        if m := re.search(r"github\.com/[^/]+/[^/]+/(?:issues|pull)/(\d+)", u):
            out.add(f"gh#{m.group(1)}")
        if m := re.search(r"news\.ycombinator\.com/item\?id=(\d+)", u):
            out.add(f"hn:{m.group(1)}")
        if m := re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", u):
            out.add(f"yt:{m.group(1)}")
        if m := re.search(r"reddit\.com/r/\w+/comments/(\w+)(?:/comment/(\w+))?", u):
            out.add(f"rd:{m.group(2) or m.group(1)}")
    for n in re.findall(r"(?<![\w&/])#(\d{2,6})\b", text):
        out.add(f"gh#{n}")
    return out


SRC_TAG = re.compile(r"\(src:\s*([a-z0-9_, -]+)\)", re.I)


def yield_stats(report: Path, agents: list[dict]) -> dict:
    """How much of each per-site report the final report uses: the per-site report's distinct URLs that also
    appear in the final report (and whether the final report links the per-site file at all)."""
    finals = sorted(report.glob("*_report_*.md"))
    if not finals:
        return {"final_report": None, "sites": {}}
    final = finals[-1]
    ftext = final.read_text()
    furls = _urls(ftext)
    owners = {}
    for a in agents:
        for w in a.get("wrote") or []:
            owners.setdefault(Path(w).name, a["description"] or a["agent_id"])
    sites = {}
    skip = ("_brief_", "_report_", "_cross-check", "_verify", "_verification")
    for f in sorted(report.glob("20*.md")):
        if any(k in f.name for k in skip):
            continue
        urls = _urls(f.read_text())
        cited = urls & furls
        sites[f.name] = {"agent": owners.get(f.name), "urls": len(urls), "urls_cited": len(cited),
                         "yield_pct": round(100 * len(cited) / len(urls), 1) if urls else None,
                         "linked_from_report": f.name in ftext,
                         "share_of_report_urls_pct": round(100 * len(cited) / len(furls), 1) if furls else None}
    only = furls - set().union(*[_urls(f.read_text()) for f in report.glob("20*.md") if f != final]) \
        if sites else furls
    tags = Counter(t.strip().lower() for g in SRC_TAG.findall(ftext) for t in g.split(","))
    for name, v in sites.items():
        site = re.sub(r"^\d{4}-\d{2}-\d{2}_|(-\d+)?\.md$", "", name)
        v["src_tags"] = tags.get(site, 0) + tags.get(name.removesuffix(".md"), 0)
    return {"final_report": final.name, "report_urls": len(furls), "report_urls_not_in_site_reports": len(only),
            "src_tags": dict(tags), "sites": sites}


def find_transcripts(report: Path, projects: Path) -> list[Path]:
    needle = str(report)
    hits = []
    for p in projects.glob("*/*/subagents/agent-*.jsonl"):
        try:
            with p.open() as f:
                head = f.read(20000)
        except OSError:
            continue
        if needle in head:
            first = json.loads(head.splitlines()[0]) if head else {}
            if needle in _first_prompt([first]):
                hits.append(p)
    return hits


def render_md(report: Path, agents: list[dict], sites: dict, total: dict, yld: dict) -> str:
    L = [f"# Run stats: {report.name}", "",
         f"Agents: {total['agents']} · wall-clock span {total['span_min']} min · agent-minutes {total['agent_min']} · "
         f"output tokens {total['output']:,} · cache reads {total['cache_read']:,} · tool calls {total['tool_calls']}", "",
         "| Agent | Type | Model | Start (UTC) | Min | Turns | Fresh in | Cache write | Cache read | Output | "
         "Tool calls | Errors | Guard blocks |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for a in sorted(agents, key=lambda a: a["start"] or ""):
        t = a["tokens"]
        L.append(f"| {a['description'] or a['agent_id']} | {a['agent_type']} | {a['model']} | "
                 f"{(a['start'] or '')[11:16]} | {a['wall_min']} | {a['turns']} | {t.get('input', 0):,} | "
                 f"{t.get('cache_write', 0):,} | {t.get('cache_read', 0):,} | {t.get('output', 0):,} | "
                 f"{a['tool_calls']} | {a['tool_errors']} | {a['guard_blocks']} |")
    L += ["", "| Site | Helper commands | OK | Failed | API calls | By command |", "|---|---|---|---|---|---|"]
    for s, v in sites.items():
        L.append(f"| {s} | {v['commands']} | {v['ok']} | {v['failed']} | {v.get('api_calls', '')} | "
                 + ", ".join(f"{k} {n}" for k, n in v["by_cmd"].items()) + " |")
    if yld.get("final_report"):
        L += ["", f"## Yield (URLs from each per-site report that {yld['final_report']} cites)", "",
              f"Final report cites {yld['report_urls']} distinct URLs; {yld['report_urls_not_in_site_reports']} "
              "of them appear in no per-site report.", "",
              "| Per-site report | Agent | Evidence keys | Cited in final | Yield | Share of final's keys | `src:` tags | Linked |",
              "|---|---|---|---|---|---|---|---|"]
        for f, v in sorted(yld["sites"].items(), key=lambda kv: -(kv[1]["yield_pct"] or 0)):
            L.append(f"| {f} | {v['agent'] or ''} | {v['urls']} | {v['urls_cited']} | "
                     f"{'' if v['yield_pct'] is None else str(v['yield_pct']) + '%'} | "
                     f"{'' if v['share_of_report_urls_pct'] is None else str(v['share_of_report_urls_pct']) + '%'} | "
                     f"{v.get('src_tags', 0)} | {'yes' if v['linked_from_report'] else 'no'} |")
        L += ["", "Evidence keys are URLs plus GitHub `#N`, HN item, YouTube video and Reddit ids. `src:` tags count "
              "findings the final report attributes to a site with `(src: reddit, hn)`; reports written before the "
              "tag convention show 0. Both are proxies for how much of a pass reached the answer."]
    L += ["", "Tokens are summed once per API message. Fresh in = uncached input. Times are transcript timestamps."]
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if args else 2

    def take(flag, default=None):
        if flag in args:
            i = args.index(flag)
            v = args[i + 1]
            del args[i:i + 2]
            return v
        return default
    projects = Path(os.path.expanduser(take("--projects-dir", "~/.claude/projects")))
    md_out, json_out = take("--md"), take("--json")
    ledger = "--no-ledger" not in args
    args = [a for a in args if a != "--no-ledger"]
    report = Path(os.path.expanduser(args[0])).resolve()
    raw = report / "raw"
    agents = [agent_stats(p) for p in find_transcripts(report, projects)]
    sites = site_activity(raw) if raw.exists() else {}
    starts = [_ts(a["start"]) for a in agents if a["start"]]
    ends = [_ts(a["end"]) for a in agents if a["end"]]
    sum_tok = lambda k: sum(a["tokens"].get(k, 0) for a in agents)  # noqa: E731
    total = {"report": report.name, "agents": len(agents),
             "span_min": round((max(ends) - min(starts)).total_seconds() / 60, 1) if starts and ends else 0,
             "agent_min": round(sum(a["wall_min"] or 0 for a in agents), 1),
             "input": sum_tok("input"), "cache_write": sum_tok("cache_write"), "cache_read": sum_tok("cache_read"),
             "output": sum_tok("output"), "thinking": sum_tok("thinking"),
             "tool_calls": sum(a["tool_calls"] for a in agents),
             "guard_blocks": sum(a["guard_blocks"] for a in agents),
             "by_type": {t: len([a for a in agents if a["agent_type"] == t]) for t in sorted({a["agent_type"] or "?" for a in agents})},
             "helper_commands": sum(v["commands"] for v in sites.values())}
    yld = yield_stats(report, agents)
    total["yield_overall_pct"] = (round(100 * sum(v["urls_cited"] for v in yld["sites"].values())
                                        / max(1, sum(v["urls"] for v in yld["sites"].values())), 1)
                                  if yld.get("sites") else None)
    data = {"total": total, "agents": agents, "sites": sites, "yield": yld}
    jp = Path(json_out) if json_out else raw / "run_stats.json"
    mp = Path(md_out) if md_out else raw / "run_stats.md"
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(data, indent=1))
    mp.write_text(render_md(report, agents, sites, total, yld))
    if ledger:
        lf = report.parent / "_runs.jsonl"
        lines = [l for l in (lf.read_text().splitlines() if lf.exists() else [])
                 if l.strip() and json.loads(l).get("report") != report.name]
        lines.append(json.dumps({**total, "updated": datetime.now().isoformat(timespec="seconds")}))
        lf.write_text("\n".join(lines) + "\n")
    print(json.dumps({"total": total, "json": str(jp), "md": str(mp)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
