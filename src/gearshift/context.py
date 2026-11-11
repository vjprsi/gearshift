"""The cost that model routing does not touch: context.

In my first measured session the main model was 87% of the bill, and about
70% of that was re-reading and re-saving context, not thinking. Two things
were behind it: a long session that was never cleared, and ~300 installed
agents whose descriptions rode along on every request.

This module only measures and tells you. It changes nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import ledger

AGENT_TOKEN_WARN = 15000     # Claude Code itself warns about agent descriptions above this
CONTEXT_NUDGE = 120000       # tokens in the main context before the status line says so


def _frontmatter(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if f.readline().strip() != "---":
                return out
            for _ in range(60):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                if ":" in line and not line.startswith((" ", "\t")):
                    k, v = line.split(":", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def agent_footprint(project_dir: Optional[str] = None, home: Optional[str] = None, limit: int = 4000) -> Dict[str, Any]:
    """Installed subagents and a rough count of the description tokens they add
    to every request. ~4 characters per token, so treat it as an estimate."""
    home_dir = Path(home or os.path.expanduser("~")) / ".claude"
    roots = [("you", home_dir / "agents"), ("plugins", home_dir / "plugins")]
    if project_dir:
        roots.insert(0, ("project", Path(project_dir) / ".claude" / "agents"))
    seen, by_source, files = set(), {}, 0
    for label, root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.md"):
            files += 1
            if files > limit:
                break
            if label == "plugins" and path.parent.name != "agents":
                continue
            fm = _frontmatter(path)
            name, desc = fm.get("name"), fm.get("description", "")
            if not name:
                continue
            source = label
            if label == "plugins":
                parts = path.relative_to(root).parts
                # .../cache/<marketplace>/<plugin>/<version>/agents/x.md or .../<plugin>/agents/x.md
                source = parts[2] if len(parts) >= 5 and parts[0] == "cache" else (parts[-3] if len(parts) >= 3 else "plugin")
            key = (source, name)
            if key in seen:     # the same plugin cached in two versions
                continue
            seen.add(key)
            s = by_source.setdefault(source, {"agents": 0, "tokens": 0})
            s["agents"] += 1
            s["tokens"] += (len(name) + len(desc)) // 4 + 8
    total = sum(s["tokens"] for s in by_source.values())
    top = sorted(by_source.items(), key=lambda kv: -kv[1]["tokens"])
    return {"agents": sum(s["agents"] for s in by_source.values()), "tokens": total,
            "sources": [{"source": k, "agents": v["agents"], "tokens": v["tokens"]} for k, v in top]}


def footprint_lines(fp: Dict[str, Any]) -> List[str]:
    if not fp["agents"]:
        return []
    mark = "[!!]" if fp["tokens"] > AGENT_TOKEN_WARN else "[ok]"
    lines = ["  %s %d agents installed, roughly %s tokens of descriptions sent with every request (estimate)" % (
        mark, fp["agents"], "{:,}".format(fp["tokens"]))]
    if fp["tokens"] > AGENT_TOKEN_WARN:
        big = ", ".join("%s %s" % (s["source"], "{:,}".format(s["tokens"])) for s in fp["sources"][:4])
        lines.append("       biggest: %s" % big)
        lines.append("       every request pays for these, on your session model. Remove plugins you don't use (/plugin).")
    return lines


def main_context_tokens(transcript_path: Optional[str]) -> Optional[int]:
    """Size of the main session's context at its last request, from the transcript."""
    if not transcript_path:
        return None
    try:
        lines = ledger._tail_lines(str(transcript_path), 524288)
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant" or ev.get("isSidechain"):
            continue
        u = (ev.get("message") or {}).get("usage") or {}
        total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        if total:
            return total
    return None
