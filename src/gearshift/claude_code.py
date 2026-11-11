"""Claude Code hook handlers.

Rule number one: a hook must never break the session. Every handler returns
None when it has nothing to say, and main() swallows everything and exits 0.
Worst case gearshift behaves as if it wasn't installed.

  SessionStart       remember the session model, add a short routing brief
  PreToolUse/Agent   classify the subagent task, fill in model + effort
  PostToolUse/Agent  note which model actually ran
  SubagentStop       total tokens + cost from the subagent transcript
  UserPromptSubmit   one line if the session model clearly doesn't fit the work
  PreModelSwitch     show what a mid-session switch re-sends
  PostModelSwitch    remember the new model

Verified live with permissionDecision=allow (Claude Code 2.1.322, Windows).
TODO: check whether updatedInput also works without permissionDecision. The
docs only show it together with allow/ask, so that is what we send by default
(GEARSHIFT_PERMISSION=none to try without).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from . import ledger, outcomes
from .config import effective_ceiling, load_config, state_dir
from .policy import EFFORTS, TIERS, load_policy, tier_rank
from .pricing import price_table, tier_of
from .router import Router

AGENT_TOOLS = ("Agent", "Task")
# built-ins we route vs never touch. Plan stays on the session model: planning
# is exactly what you picked that model for.
ROUTABLE_BUILTINS = {"general-purpose", "claude", "Explore"}
NEVER_ROUTE = {"Plan", "statusline-setup", "claude-code-guide", "fork"}
EFFORT_PARAM_MIN_VERSION = (2, 1, 292)

_BRIEF = (
    "The gearshift plugin is active in this session. It routes delegated work by task type: "
    "lookups, log reading and test runs to Haiku; well-scoped edits, drafting and reviews to "
    "Sonnet; multi-file builds, hard bugs, design and deep research to Opus; Fable only for the "
    "hardest reasoning. Subagents pinned to a model and effort are available: "
    "gearshift:scout (haiku: find, grep, read logs, run tests and report), "
    "gearshift:sorter (haiku: classify and extract at volume), "
    "gearshift:fixer (sonnet: contained edits against a clear spec), "
    "gearshift:reviewer (sonnet: read-only review and investigation), "
    "gearshift:builder (opus: multi-file features and hard-to-reproduce bugs), "
    "gearshift:architect (opus: plans for migrations, refactors and architecture), "
    "gearshift:prover (fable: proofs, novel algorithms, problems smaller models failed). "
    "When an Agent call names no model, gearshift's hook fills one in from the task "
    "(mode: {mode}; it never assigns a tier above {cap}); a model passed explicitly is kept{strict}. "
    "The gearshift:routing skill has the full table, the escalation ladder and when not to delegate."
)


# -- small helpers -------------------------------------------------------------

def _debug(msg: str) -> None:
    if os.environ.get("GEARSHIFT_DEBUG"):
        try:
            d = state_dir()
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "debug.log", "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), msg))
        except OSError:
            pass


def _session_file(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", session_id or "unknown")[:120]
    return state_dir() / "sessions" / (safe + ".json")


def load_session(session_id: str) -> Dict[str, Any]:
    try:
        with open(_session_file(session_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_session(session_id: str, data: Dict[str, Any]) -> None:
    path = _session_file(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.%d" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _parse_version(v: Optional[str]) -> Optional[Tuple[int, ...]]:
    if not v:
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v)
    return tuple(int(x) for x in m.groups()) if m else None


def _session_facts(inp: Mapping[str, Any]) -> Dict[str, Optional[str]]:
    """Session model and Claude Code version from saved state, then transcript."""
    st = load_session(str(inp.get("session_id") or ""))
    model, version = st.get("model"), st.get("version")
    if not model or not version:
        tail = ledger.transcript_tail_info(inp.get("transcript_path"))
        model = model or tail["model"]
        version = version or tail["version"]
    return {"model": model, "tier": tier_of(model) if model else None, "version": version}


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
                m = re.match(r"^([A-Za-z][\w\-]*)\s*:\s*(.*?)\s*$", line)
                if m:
                    out[m.group(1)] = m.group(2).strip("\"'")
    except OSError:
        pass
    return out


def agent_definition(agent_type: str, project_dir: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Classify a subagent type.

    Returns (status, frontmatter) where status is one of:

    * builtin  - a built-in agent we may route
    * skip     - never routed (Plan, ...)
    * pinned   - its definition sets a model: the author's choice stands
    * unpinned - a user/project agent with no model (or inherit)
    * unknown  - definition not found (other plugins' agents): left alone
    """
    if agent_type in NEVER_ROUTE:
        return "skip", {}
    builtin = agent_type in ROUTABLE_BUILTINS
    if ":" in agent_type:
        # plugin agent. ours are pinned already, other plugins' we can't see -> hands off
        return ("pinned", {}) if agent_type.startswith("gearshift:") else ("unknown", {})
    roots: List[Path] = []
    if project_dir:
        roots.append(Path(project_dir) / ".claude" / "agents")
    roots.append(Path(os.path.expanduser("~")) / ".claude" / "agents")
    seen = 0
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            seen += 1
            if seen > 300:
                break
            fm = _frontmatter(path)
            if fm.get("name") == agent_type:
                model = fm.get("model", "").lower()
                if model and model != "inherit":
                    return "pinned", fm
                return "unpinned", fm
    return ("builtin", {}) if builtin else ("unknown", {})


# -- handlers ------------------------------------------------------------------

def _router(cfg: Mapping[str, Any], with_judge: bool = False) -> Router:
    pol = load_policy(cfg.get("policy") or None)
    judge = None
    if with_judge and cfg.get("judge", "auto") != "off":
        from .judge import judge_from_config
        judge = judge_from_config(cfg.get("judge", "auto"), policy=pol,
                                  cache_path=state_dir() / "judge_cache.json")
    return Router(pol, judge=judge)


def on_session_start(inp: Mapping[str, Any]) -> Optional[dict]:
    cfg = load_config(os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd"))
    sid = str(inp.get("session_id") or "")
    st = load_session(sid)
    if inp.get("model"):
        st["model"] = inp["model"]
    if inp.get("transcript_path"):
        st["transcript"] = inp["transcript_path"]
    st.setdefault("advised", {})
    st.setdefault("streak", 0)
    save_session(sid, st)
    ledger.append_event(state_dir(), {"ev": "session", "session": sid, "model": st.get("model"),
                                      "source": inp.get("source")})
    if cfg["mode"] == "off" or not cfg["brief"]:
        return None
    cap = cfg["ceiling"]
    cap_text = "the session model" if cap == "session" else cap.capitalize()
    brief = _BRIEF.format(mode=cfg["mode"], cap=cap_text,
                          strict="" if cfg["mode"] != "strict" else " unless it disagrees with the task type")
    if cfg["mode"] == "shadow":
        brief += " Shadow mode: the hook is logging decisions without changing any call."
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": brief}}


def decide_agent_call(tool_input: Mapping[str, Any], *, cfg: Mapping[str, Any],
                      session_tier: Optional[str], version: Optional[str],
                      project_dir: Optional[str], router: Optional[Router] = None,
                      retry_from: Optional[Tuple[str, str]] = None) -> Dict[str, Any]:
    """Pure decision logic for one Agent call. Returns a record with
    action and, when something changes, updated (the full new input).

    retry_from is the (tier, effort) an earlier run of the same task fell
    short on. When set, this run starts at least one rung above it."""
    router = router or _router(cfg, with_judge=True)
    agent_type = str(tool_input.get("subagent_type") or "general-purpose")
    desc = str(tool_input.get("description") or "")
    prompt = str(tool_input.get("prompt") or "")
    have_model = tool_input.get("model") or None
    have_effort = tool_input.get("effort") or None

    d = router.route(prompt, description=desc or None, agent_type=agent_type, platform="claude-code")
    rec: Dict[str, Any] = {
        "agent_type": agent_type, "class": d.task_class, "confidence": d.confidence,
        "judged": bool(d.classification and d.classification.judged),
        "want_tier": d.tier, "want_effort": d.effort, "had_model": have_model,
        "had_effort": have_effort, "session_tier": session_tier, "action": "kept",
        "model": have_model, "effort": have_effort, "updated": None, "decision": d,
    }
    status, fm = agent_definition(agent_type, project_dir)
    rec["agent_status"] = status
    if status == "skip" or (agent_type == "Explore" and cfg.get("explore") == "leave"):
        rec["action"] = "skipped"
        return rec
    if status in ("pinned", "unknown"):
        rec["action"] = "pinned"
        return rec

    bumped = None
    if retry_from and not have_model:
        bumped = outcomes.next_rung(retry_from[0], retry_from[1], router.policy)
    force_effort = None
    if bumped:
        # outcome beats guesswork: it already failed one rung down
        cap0 = effective_ceiling(cfg, session_tier)
        floor = bumped[0] if not cap0 or tier_rank(bumped[0]) <= tier_rank(cap0) else cap0
        d = router.route(prompt, description=desc or None, agent_type=agent_type,
                         platform="claude-code", floor=floor)
        if d.tier == bumped[0] and EFFORTS.index(d.effort) < EFFORTS.index(bumped[1]):
            force_effort = bumped[1]
        rec["bumped_from"] = "%s@%s" % retry_from
        rec["want_tier"], rec["want_effort"], rec["decision"] = d.tier, force_effort or d.effort, d

    act = router.policy.thresholds.get("act_confidence", 0.55)
    if not bumped and (d.task_class == "unsure" or d.confidence < act):
        rec["action"] = "low-confidence"
        return rec

    if cfg.get("ceiling") == "session" and session_tier is None and tier_rank(d.tier) > tier_rank("sonnet"):
        # don't know what the session runs on, so can't tell if opus/fable
        # would be a step up. leave it, it inherits.
        rec["action"] = "inherit"
        return rec
    cap = effective_ceiling(cfg, session_tier)
    if cap and tier_rank(d.tier) > tier_rank(cap):
        d = router.route(prompt, description=desc or None, agent_type=agent_type,
                         platform="claude-code", ceiling=cap)
        rec["capped_from"], rec["decision"] = rec["want_tier"], d
    want_tier, want_effort = d.tier, force_effort or d.effort

    strict = cfg["mode"] == "strict"
    new_model = have_model
    if have_model is None or (strict and have_model != want_tier):
        # same tier as the session: leave model unset so it inherits the
        # exact session model and not whatever the alias resolves to
        if cfg.get("ceiling") == "session" and session_tier == want_tier and have_model is None:
            new_model = None
        else:
            new_model = want_tier

    v = _parse_version(version)
    can_set_effort = (cfg["set_effort"] == "always"
                      or (cfg["set_effort"] == "auto" and v is not None and v >= EFFORT_PARAM_MIN_VERSION))
    if os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        can_set_effort = False   # env var wins over per-call effort anyway
    final_tier = new_model or session_tier
    new_effort = have_effort
    if can_set_effort and not fm.get("effort") and (have_effort is None or strict):
        if final_tier == want_tier:
            new_effort = want_effort

    model_changed = new_model != have_model
    effort_changed = new_effort != have_effort
    rec["model"], rec["effort"] = new_model, new_effort
    if not model_changed and not effort_changed:
        rec["action"] = "inherit" if have_model is None else "kept"
        return rec
    if cfg["mode"] == "shadow":
        rec["action"] = "shadow"
        return rec
    updated = dict(tool_input)
    if new_model is not None:
        updated["model"] = new_model
    if new_effort is not None:
        updated["effort"] = new_effort
    rec["updated"] = updated
    if model_changed:
        rec["action"] = "overrode" if have_model is not None else "filled"
    else:
        rec["action"] = "effort-only"
    return rec


def on_pre_agent(inp: Mapping[str, Any]) -> Optional[dict]:
    if inp.get("tool_name") not in AGENT_TOOLS:
        return None
    tool_input = inp.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd")
    cfg = load_config(project_dir)
    if cfg["mode"] == "off":
        return None
    facts = _session_facts(inp)
    sid = str(inp.get("session_id") or "")
    desc = str(tool_input.get("description") or "")
    fp = outcomes.fingerprint(desc + "\n" + str(tool_input.get("prompt") or ""))
    st = load_session(sid)
    tasks = st.get("tasks") if isinstance(st.get("tasks"), list) else []
    prior = outcomes.find_prior(tasks, fp, desc)
    retry_from = None
    if prior and prior.get("short") and prior.get("tier"):
        retry_from = (prior["tier"], prior.get("effort") or "medium")
    rec = decide_agent_call(tool_input, cfg=cfg, session_tier=facts["tier"],
                            version=facts["version"], project_dir=project_dir, retry_from=retry_from)
    rec.pop("decision")
    updated = rec.pop("updated")
    if updated is not None and cfg.get("hint") and rec["action"] in ("filled", "overrode") \
            and outcomes.HINT not in str(updated.get("prompt") or ""):
        # a cheaper model than the one that asked: give it a way to say "too hard for me"
        updated["prompt"] = str(updated.get("prompt") or "").rstrip() + "\n\n" + outcomes.HINT
        rec["hint"] = True
    event = {"ev": "route", "session": inp.get("session_id"), "tool_use_id": inp.get("tool_use_id"),
             "mode": cfg["mode"], "desc": desc[:80]}
    if prior:
        event["retry_of"] = prior.get("id")
        event["attempt"] = int(prior.get("attempt") or 1) + 1
    event.update(rec)
    ran_tier = rec["model"] if rec["model"] in TIERS else (tier_of(str(rec["model"])) if rec["model"] else facts["tier"])
    tasks.append({"id": inp.get("tool_use_id"), "fp": fp, "desc": desc[:80], "tier": ran_tier,
                  "effort": rec["effort"] or rec.get("want_effort"), "class": rec["class"],
                  "attempt": event.get("attempt", 1), "short": False})
    st["tasks"] = tasks[-outcomes.MAX_TASKS:]
    save_session(sid, st)
    ledger.append_event(state_dir(), event)
    _debug("route %s" % json.dumps(event, default=str))
    if updated is None:
        return None
    label = "%s%s" % (rec["model"] or (rec.get("session_tier") or "session model"),
                      ("@" + rec["effort"]) if rec["effort"] else "")
    why = "gearshift: %s -> %s (%s, confidence %.2f)" % (rec["agent_type"], label, rec["class"], rec["confidence"])
    if rec.get("bumped_from"):
        why = "gearshift: %s -> %s (same task fell short on %s, so one step up)" % (
            rec["agent_type"], label, rec["bumped_from"])
    out: Dict[str, Any] = {"hookEventName": "PreToolUse", "updatedInput": updated,
                           "additionalContext": why + ". Pass model explicitly on an Agent call to override."}
    if cfg["permission"] in ("allow", "ask"):
        out["permissionDecision"] = cfg["permission"]
        out["permissionDecisionReason"] = why
    result: Dict[str, Any] = {"hookSpecificOutput": out}
    if cfg["announce"]:
        result["systemMessage"] = why
    return result


def on_post_agent(inp: Mapping[str, Any]) -> Optional[dict]:
    if inp.get("tool_name") not in AGENT_TOOLS:
        return None
    resp = inp.get("tool_response")
    if not isinstance(resp, dict):
        return None
    reason = outcomes.escalation_in(resp)
    failed = outcomes.failed_status(resp)
    event = {
        "ev": "done", "session": inp.get("session_id"), "tool_use_id": inp.get("tool_use_id"),
        "agent_id": resp.get("agentId"), "status": resp.get("status"),
        "resolved_model": resp.get("resolvedModel"), "duration_ms": resp.get("totalDurationMs"),
        "tool_uses": resp.get("totalToolUseCount"),
    }
    if reason:
        event["escalated"], event["escalate_reason"] = True, reason
    if failed:
        event["failed"] = True
    ledger.append_event(state_dir(), event)
    if not (reason or failed):
        return None

    # remember it, so the same task sent again starts one rung up
    sid = str(inp.get("session_id") or "")
    st = load_session(sid)
    task = None
    for t in st.get("tasks") or []:
        if t.get("id") == inp.get("tool_use_id"):
            t["short"] = True
            task = t
    if task is None:
        return None
    save_session(sid, st)
    cfg = load_config(os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd"))
    if cfg["mode"] in ("off", "shadow") or not cfg["advise"] or not reason or not task.get("tier"):
        return None
    ran = "%s@%s" % (task["tier"], task.get("effort") or "default effort")
    cap = effective_ceiling(cfg, _session_facts(inp)["tier"])
    up = outcomes.next_rung(task["tier"], task.get("effort") or "medium", load_policy(cfg.get("policy") or None), cap)
    if up:
        advice = "Re-run it one step up: model=%s, effort=%s." % up
    else:
        advice = "There is no step above it within your ceiling, so take this one in the main session."
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext":
            "gearshift: this subagent ran on %s and asked to escalate (%s). %s" % (ran, reason, advice)}}


def on_subagent_stop(inp: Mapping[str, Any]) -> Optional[dict]:
    path = inp.get("agent_transcript_path")
    if not path:
        return None
    # transcript can lag a bit behind the hook, give it a moment
    time.sleep(float(os.environ.get("GEARSHIFT_STOP_DELAY", "0.4")))
    try:
        usage = ledger.transcript_usage(str(path))
    except OSError:
        return None
    facts = _session_facts(inp)
    counter = ledger.counterfactual_cost(usage["per_request"], facts["model"]) if facts["model"] else None
    ledger.append_event(state_dir(), {
        "ev": "usage", "session": inp.get("session_id"), "agent_id": inp.get("agent_id"),
        "agent_type": inp.get("agent_type"), "requests": usage["requests"],
        "by_model": usage["by_model"], "cost_by_model": usage["cost_by_model"],
        "main_model": usage["main_model"], "cost_usd": usage["cost_usd"],
        "unpriced": usage["unpriced"], "session_model": facts["model"],
        "session_rate_cost_usd": counter,
    })
    return None


def _ratio_text(small: str, big: str) -> str:
    t = price_table()
    try:
        a = t["models"][t["tiers"][small]]["input"]
        b = t["models"][t["tiers"][big]]["input"]
        return "about 1/%d the list price" % round(b / a) if b > a else "a lower price"
    except (KeyError, ZeroDivisionError):
        return "a lower price"


def on_user_prompt(inp: Mapping[str, Any]) -> Optional[dict]:
    prompt = str(inp.get("prompt") or "").strip()
    if len(prompt) < 20 or prompt.startswith("/"):
        return None
    cfg = load_config(os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd"))
    if cfg["mode"] == "off" or not cfg["advise"]:
        return None
    sid = str(inp.get("session_id") or "")
    st = load_session(sid)
    facts = _session_facts(inp)
    tier = facts["tier"]
    if tier is None:
        return None
    advised = st.setdefault("advised", {})
    adv = _router(cfg).advise_session(prompt, current_tier=tier)
    msg = None
    if adv["mismatch"] == "too_small" and not advised.get("too_small"):
        advised["too_small"] = True
        msg = ("gearshift: this reads as %s work and the session is on %s. Consider /model %s "
               "(effort %s) before starting; switching later re-sends the whole conversation uncached."
               % (adv["task_class"], tier, adv["tier"], adv["effort"]))
    small = adv["task_class"] in ("lookup", "extract", "edit", "write") and adv["confidence"] >= 0.6
    if tier in ("opus", "fable"):
        if small:
            st["streak"] = int(st.get("streak", 0)) + 1
        elif adv["task_class"] in ("build", "design", "research", "frontier"):
            st["streak"] = 0
        if st.get("streak", 0) >= 5 and not advised.get("too_big") and msg is None:
            advised["too_big"] = True
            msg = ("gearshift: the last %d requests were small, well-scoped work on %s. Sonnet handles "
                   "these at %s. Finish the task, then start the next one in a fresh session "
                   "with /model sonnet (switching mid-task drops the prompt cache)."
                   % (st["streak"], tier, _ratio_text("sonnet", tier)))
    save_session(sid, st)
    return {"systemMessage": msg} if msg else None


def on_pre_model_switch(inp: Mapping[str, Any]) -> Optional[dict]:
    cfg = load_config(os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd"))
    if cfg["mode"] == "off" or not cfg["advise"]:
        return None
    tokens = int(inp.get("context_tokens") or 0)
    if not inp.get("prompt_cache_warm") or tokens < 20000:
        return None
    usd = inp.get("estimated_cache_write_usd")
    cost = " (about $%.2f to re-cache)" % float(usd) if isinstance(usd, (int, float)) and usd > 0 else ""
    return {"systemMessage": (
        "gearshift: switching to %s re-sends about %s tokens of conversation uncached%s. "
        "If you need the other model for one piece of work, delegate that piece to a subagent "
        "instead; if you are changing for good, /compact first or start a fresh session with a short plan."
        % (inp.get("to_model") or "the new model", "{:,}".format(tokens), cost))}


def on_post_model_switch(inp: Mapping[str, Any]) -> Optional[dict]:
    sid = str(inp.get("session_id") or "")
    st = load_session(sid)
    if inp.get("to_model"):
        st["model"] = inp["to_model"]
        st["streak"] = 0
        save_session(sid, st)
        ledger.append_event(state_dir(), {"ev": "session", "session": sid, "model": st["model"],
                                          "source": "switch:%s" % inp.get("source")})
    return None


HANDLERS: Dict[str, Callable[[Mapping[str, Any]], Optional[dict]]] = {
    "session-start": on_session_start,
    "pre-agent": on_pre_agent,
    "post-agent": on_post_agent,
    "subagent-stop": on_subagent_stop,
    "user-prompt": on_user_prompt,
    "pre-model-switch": on_pre_model_switch,
    "post-model-switch": on_post_model_switch,
}


def main(argv: List[str]) -> int:
    """Hook entry point. Always returns 0; prints JSON only when it has a say."""
    try:
        if os.environ.get("GEARSHIFT_INSIDE_JUDGE"):
            return 0    # we are inside our own headless judge call: stay out of it
        handler = HANDLERS.get(argv[0] if argv else "")
        if handler is None:
            return 0
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
        if not isinstance(inp, dict):
            return 0
        out = handler(inp)
        if out:
            sys.stdout.write(json.dumps(out))
    except Exception as exc:  # never break the session
        _debug("hook error in %s: %r" % (argv[:1], exc))
    return 0
