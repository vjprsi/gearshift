"""What got routed where, and what it cost.

The hooks append JSON lines to <state>/events.jsonl:

  route    PreToolUse on Agent: the decision and what we changed
  done     PostToolUse on Agent: which model actually ran
  usage    SubagentStop: tokens + list-price cost, summed from the subagent's
           own transcript (all requests, not only the last one)
  session  session start / model switch

`gearshift report` joins them.

About the "same tokens at session-model rates" number: it is NOT savings. A
bigger model would not have used the same tokens. It only shows the weight of
routing down on those token counts. Don't quote it as money saved.

NOTE: the transcript format is not a public contract. Parsing here is
defensive on purpose; if Claude Code changes it we lose the usage numbers,
nothing else.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .pricing import add_usage, cost_usd, tier_of, usage_to_dict

_EMPTY = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
          "cache_write_5m_tokens": 0, "cache_write_1h_tokens": 0}


def append_event(state: Path, event: Dict[str, Any]) -> None:
    try:
        state.mkdir(parents=True, exist_ok=True)
        event = dict(event)
        event.setdefault("ts", round(time.time(), 3))
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(str(state / "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass  # the ledger is best-effort; never break a session over it


def read_events(state: Path, since: Optional[float] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(state / "events.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict) and (since is None or ev.get("ts", 0) >= since):
                    out.append(ev)
    except OSError:
        pass
    return out


# -- transcripts ---------------------------------------------------------------

def _tail_lines(path: str, max_bytes: int = 262144) -> List[str]:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        data = f.read().decode("utf-8", "replace")
    lines = data.split("\n")
    if start > 0:
        lines = lines[1:]  # first line may be cut in half
    return lines


def transcript_tail_info(path: Optional[str]) -> Dict[str, Optional[str]]:
    """Latest model and Claude Code version seen in a session transcript."""
    info: Dict[str, Optional[str]] = {"model": None, "version": None}
    if not path:
        return info
    try:
        lines = _tail_lines(os.path.expanduser(path))
    except OSError:
        return info
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        if info["version"] is None and isinstance(obj.get("version"), str):
            info["version"] = obj["version"]
        if info["model"] is None and obj.get("type") == "assistant" and not obj.get("isSidechain"):
            model = (obj.get("message") or {}).get("model")
            if isinstance(model, str) and model and not model.startswith("<"):
                info["model"] = model
        if info["model"] and info["version"]:
            break
    return info


def transcript_usage(path: str) -> Dict[str, Any]:
    """Sum usage over every model request in a transcript.

    Streaming writes several lines per response; they share message.id
    and the last one carries the final counts, so we keep the last per id.
    Returns {"requests": n, "by_model": {model: usage}, "cost_usd": x,
    "unpriced": [models]} with each request priced on its own.
    """
    last: Dict[str, Tuple[str, Dict[str, int]]] = {}
    order = 0
    with open(os.path.expanduser(path), "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict) or obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            model = msg.get("model")
            usage = msg.get("usage")
            if not isinstance(model, str) or model.startswith("<") or not isinstance(usage, dict):
                continue
            order += 1
            key = msg.get("id") or "anon-%d" % order
            last[key] = (model, usage_to_dict(usage))
    by_model: Dict[str, Dict[str, int]] = {}
    cost_by_model: Dict[str, float] = {}
    cost, unpriced = 0.0, set()
    for model, usage in last.values():
        by_model[model] = add_usage(by_model.get(model, dict(_EMPTY)), usage)
        c = cost_usd(usage, model)
        if c is None:
            unpriced.add(model)
        else:
            cost += c
            cost_by_model[model] = cost_by_model.get(model, 0.0) + c
    main = max(by_model, key=lambda m: _tokens(by_model[m])) if by_model else None
    return {"requests": len(last), "by_model": by_model, "main_model": main,
            "cost_by_model": {m: round(c, 6) for m, c in cost_by_model.items()},
            "cost_usd": round(cost, 6), "unpriced": sorted(unpriced),
            "per_request": list(last.values())}


def counterfactual_cost(per_request: Iterable[Tuple[str, Dict[str, int]]], model: str) -> Optional[float]:
    """Price the same per-request token counts at another model's rates."""
    total = 0.0
    for _, usage in per_request:
        c = cost_usd(usage, model)
        if c is None:
            return None
        total += c
    return round(total, 6)


# -- report --------------------------------------------------------------------

def _tokens(u: Dict[str, int]) -> int:
    return sum(int(v) for v in u.values())


def build_report(state: Path, days: Optional[float] = 7, session: Optional[str] = None) -> Dict[str, Any]:
    since = time.time() - days * 86400 if days else None
    events = read_events(state, since)
    if session:
        events = [e for e in events if e.get("session") == session]
    routes = [e for e in events if e.get("ev") == "route"]
    usages: Dict[str, Dict[str, Any]] = {}
    for e in events:
        if e.get("ev") == "usage" and e.get("agent_id"):
            usages[e["agent_id"]] = e   # latest wins (a resumed agent reports cumulative totals)

    actions: Dict[str, int] = {}
    classes: Dict[str, int] = {}
    for r in routes:
        actions[r.get("action", "?")] = actions.get(r.get("action", "?"), 0) + 1
        classes[r.get("class", "?")] = classes.get(r.get("class", "?"), 0) + 1

    tiers: Dict[str, Dict[str, Any]] = {}
    spend = 0.0
    counter = 0.0
    counter_known = True
    unpriced: set = set()
    for u in usages.values():
        spend += float(u.get("cost_usd") or 0.0)
        cf = u.get("session_rate_cost_usd")
        if cf is None:
            counter_known = False
        else:
            counter += float(cf)
        unpriced.update(u.get("unpriced") or [])
        costs = u.get("cost_by_model") or {}
        for model, usage in (u.get("by_model") or {}).items():
            tier = tier_of(model) or model
            t = tiers.setdefault(tier, {"runs": 0, "tokens": 0, "cost_usd": 0.0, "models": set()})
            t["tokens"] += _tokens(usage)
            t["cost_usd"] += float(costs.get(model) or 0.0)
            t["models"].add(model)
        main = u.get("main_model")
        if main:
            tier = tier_of(main) or main
            tiers.setdefault(tier, {"runs": 0, "tokens": 0, "cost_usd": 0.0, "models": set()})
            tiers[tier]["runs"] += 1
    for t in tiers.values():
        t["models"] = sorted(t["models"])
        t["cost_usd"] = round(t["cost_usd"], 4)
    return {
        "days": days,
        "session": session,
        "routed_calls": len(routes),
        "actions": actions,
        "classes": classes,
        "subagents_measured": len(usages),
        "tiers": tiers,
        "spend_usd": round(spend, 4),
        "same_tokens_at_session_rates_usd": round(counter, 4) if (counter_known and usages) else None,
        "unpriced_models": sorted(unpriced),
    }


_ACTION_LABELS = [
    ("filled", "model filled in by gearshift"),
    ("overrode", "model replaced (strict mode)"),
    ("effort-only", "effort set, model left alone"),
    ("kept", "orchestrator's own choice kept"),
    ("pinned", "agent pinned by its definition"),
    ("inherit", "left on the session model"),
    ("low-confidence", "not confident enough to act"),
    ("shadow", "shadow mode (logged only)"),
    ("skipped", "not routed (Plan and other excluded agents)"),
]


def format_report(rep: Dict[str, Any]) -> str:
    scope = "session %s" % rep["session"] if rep.get("session") else (
        "last %g days" % rep["days"] if rep.get("days") else "all time")
    lines = ["gearshift report - %s" % scope, ""]
    if not rep["routed_calls"] and not rep["subagents_measured"]:
        lines.append("No subagent calls recorded yet. The ledger fills in as Claude Code delegates work")
        lines.append("with the gearshift plugin enabled.")
        return "\n".join(lines)
    lines.append("Agent calls seen: %d" % rep["routed_calls"])
    for key, label in _ACTION_LABELS:
        if rep["actions"].get(key):
            lines.append("  %4d  %s" % (rep["actions"][key], label))
    if rep["classes"]:
        top = sorted(rep["classes"].items(), key=lambda kv: -kv[1])
        lines.append("  by task class: " + ", ".join("%s %d" % kv for kv in top))
    lines.append("")
    lines.append("Subagent runs measured: %d" % rep["subagents_measured"])
    for tier in ("haiku", "sonnet", "opus", "fable"):
        t = rep["tiers"].get(tier)
        if t:
            lines.append("  %-7s %3d runs  %9s tokens  $%.4f" % (
                tier, t["runs"], "{:,}".format(t["tokens"]), t["cost_usd"]))
    for tier, t in rep["tiers"].items():
        if tier not in ("haiku", "sonnet", "opus", "fable"):
            lines.append("  %-7s %3d runs  %9s tokens  (no price on file)" % (
                tier[:24], t["runs"], "{:,}".format(t["tokens"])))
    lines.append("")
    lines.append("Subagent spend at list price:            $%.4f" % rep["spend_usd"])
    cf = rep.get("same_tokens_at_session_rates_usd")
    if cf is not None:
        lines.append("Same tokens at your session model rates: $%.4f" % cf)
        if cf > 0 and rep["spend_usd"] <= cf:
            lines.append("Difference:                              $%.4f (%.0f%% lower)" % (
                cf - rep["spend_usd"], 100 * (1 - rep["spend_usd"] / cf)))
        lines.append("")
        lines.append("The second figure is a comparison aid, not a measurement: the session")
        lines.append("model would not have used exactly these tokens. Subscription plans bill")
        lines.append("by usage limits, not dollars; read the figures as relative weight.")
    if rep.get("unpriced_models"):
        lines.append("No list price on file for: " + ", ".join(rep["unpriced_models"]))
    return "\n".join(lines)
