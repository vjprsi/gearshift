"""One line for Claude Code's status bar: what gearshift did in this session.

    gearshift fill | 7 routed: haiku 4, sonnet 2, opus 1 | subagents $0.21 (session rates $1.94) | 1 escalated

Claude Code runs the status line command often and passes session JSON on
stdin, so this reads only the tail of the ledger and never raises. Every
field of the input is optional here: if the shape changes, the line gets
shorter, it does not break.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from . import ledger
from .config import load_config, state_dir

_ORDER = ("haiku", "sonnet", "opus", "fable")
_ROUTED = ("filled", "overrode", "effort-only")


def session_summary(session_id: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"calls": 0, "routed": 0, "tiers": {}, "spend": 0.0, "at_session_rates": 0.0,
                           "rates_known": True, "escalated": 0, "probes": 0, "shadow": 0}
    try:
        lines = ledger._tail_lines(str(state_dir() / "events.jsonl"), 524288)
    except OSError:
        return out
    usage: Dict[str, Mapping[str, Any]] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or (session_id and ev.get("session") != session_id):
            continue
        kind = ev.get("ev")
        if kind == "route":
            out["calls"] += 1
            if ev.get("action") in _ROUTED:
                out["routed"] += 1
                tier = ev.get("model") if ev.get("model") in _ORDER else ev.get("session_tier")
                if tier:
                    out["tiers"][tier] = out["tiers"].get(tier, 0) + 1
            elif ev.get("action") == "shadow":
                out["shadow"] += 1
            if ev.get("probe_from"):
                out["probes"] += 1
        elif kind == "done" and ev.get("escalated"):
            out["escalated"] += 1
        elif kind == "usage" and ev.get("agent_id"):
            usage[ev["agent_id"]] = ev      # latest wins, totals are cumulative
    for u in usage.values():
        out["spend"] += float(u.get("cost_usd") or 0.0)
        if u.get("session_rate_cost_usd") is None:
            out["rates_known"] = False
        else:
            out["at_session_rates"] += float(u["session_rate_cost_usd"])
    out["measured"] = len(usage)
    return out


def render(inp: Mapping[str, Any]) -> str:
    cfg = load_config((inp.get("workspace") or {}).get("project_dir") if isinstance(inp.get("workspace"), dict) else None)
    s = session_summary(str(inp.get("session_id") or "") or None)
    parts = ["gearshift %s" % cfg["mode"]]
    if cfg["mode"] == "off":
        return parts[0]
    if s["routed"]:
        tiers = ", ".join("%s %d" % (t, s["tiers"][t]) for t in _ORDER if s["tiers"].get(t))
        parts.append("%d routed: %s" % (s["routed"], tiers) if tiers else "%d routed" % s["routed"])
    elif s["shadow"]:
        parts.append("%d would be routed" % s["shadow"])
    elif s["calls"]:
        parts.append("%d subagent calls, none changed" % s["calls"])
    else:
        parts.append("no subagents yet")
    if s.get("measured"):
        money = "subagents $%.2f" % s["spend"] if s["spend"] >= 0.995 else "subagents $%.3f" % s["spend"]
        if s["rates_known"] and s["at_session_rates"] > s["spend"] > 0:
            money += " (session rates $%.2f)" % s["at_session_rates"]
        parts.append(money)
    if s["probes"]:
        parts.append("%d probes" % s["probes"])
    if s["escalated"]:
        parts.append("%d escalated" % s["escalated"])
    return " | ".join(parts)


def main(stdin_text: str) -> str:
    try:
        inp = json.loads(stdin_text) if stdin_text.strip() else {}
        return render(inp if isinstance(inp, dict) else {})
    except Exception:       # a status line must never take the terminal down with it
        return "gearshift"
