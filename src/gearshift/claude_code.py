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
VERIFY_TIMEOUT = 25      # seconds; the hook itself is cut off at 30

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
    "If a subagent's reply starts with ESCALATE:, the task was beyond it: send the same task again "
    "without a model and the hook starts it one step up. "
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
        if not agent_type.startswith("gearshift:"):
            return "unknown", {}
        # read our own agent file so the ledger knows which model it is pinned to
        own = Path(__file__).resolve().parents[2] / "agents" / (agent_type.split(":", 1)[1] + ".md")
        root = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if root and not own.is_file():
            own = Path(root) / "agents" / own.name
        return "pinned", (_frontmatter(own) if own.is_file() else {})
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


def _can_set_effort(cfg: Mapping[str, Any], version: Optional[str]) -> bool:
    if os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        return False   # env var wins over per-call effort anyway
    v = _parse_version(version)
    return (cfg["set_effort"] == "always"
            or (cfg["set_effort"] == "auto" and v is not None and v >= EFFORT_PARAM_MIN_VERSION))


def decide_agent_call(tool_input: Mapping[str, Any], *, cfg: Mapping[str, Any],
                      session_tier: Optional[str], version: Optional[str],
                      project_dir: Optional[str], router: Optional[Router] = None,
                      retry_from: Optional[Tuple[str, str]] = None, probe: bool = False) -> Dict[str, Any]:
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
        "mutating": d.task_class in ("edit", "build") or "mutating" in d.flags,
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
        # where it will really run: the agent's own definition, not the session model
        if fm.get("model"):
            rec["pinned_model"] = fm["model"]
        if fm.get("effort"):
            rec["pinned_effort"] = fm["effort"]
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
        elif floor != bumped[0]:
            # the step up is above the ceiling. Found live: the retry then fell back to the
            # home rung, BELOW the one that had just failed. Stay where it failed instead.
            rec["at_ceiling"] = True
            if (d.tier == retry_from[0] and retry_from[1] in EFFORTS
                    and EFFORTS.index(d.effort) < EFFORTS.index(retry_from[1])):
                force_effort = retry_from[1]
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

    if (probe and not bumped and have_model is None and have_effort is None and cfg["mode"] != "strict"
            and outcomes.probe_ok(d.task_class, d.flags)):
        # deliberately one step cheaper than the table, to learn whether the table is too generous
        down = outcomes.prev_rung(want_tier, want_effort, router.policy)
        if down and (down[0] != want_tier or _can_set_effort(cfg, version)):
            rec["probe_from"] = "%s@%s" % (want_tier, want_effort)
            want_tier, want_effort = down

    strict = cfg["mode"] == "strict"
    new_model = have_model
    if have_model is None or (strict and have_model != want_tier):
        # same tier as the session: leave model unset so it inherits the
        # exact session model and not whatever the alias resolves to
        if cfg.get("ceiling") == "session" and session_tier == want_tier and have_model is None:
            new_model = None
        else:
            new_model = want_tier

    can_set_effort = _can_set_effort(cfg, version)
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
                            version=facts["version"], project_dir=project_dir, retry_from=retry_from,
                            probe=prior is None and outcomes.sampled(str(inp.get("tool_use_id") or desc), cfg["probe"]))
    rec.pop("decision")
    updated = rec.pop("updated")
    if updated is not None and cfg.get("hint") and (rec["action"] in ("filled", "overrode") or rec.get("probe_from")) \
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
    ran_model = rec["model"] or rec.get("pinned_model")
    ran_tier = ran_model if ran_model in TIERS else (tier_of(str(ran_model)) if ran_model else facts["tier"])
    # subagents are often launched in parallel, so several of these hooks run
    # at once. re-read right before writing to keep the window small.
    st = load_session(sid)
    tasks = st.get("tasks") if isinstance(st.get("tasks"), list) else []
    tasks.append({"id": inp.get("tool_use_id"), "fp": fp, "desc": desc[:80], "tier": ran_tier,
                  "effort": rec["effort"] or rec.get("pinned_effort") or rec.get("want_effort"), "class": rec["class"],
                  "mut": bool(rec.get("mutating")), "attempt": event.get("attempt", 1), "short": False})
    st["tasks"] = tasks[-outcomes.MAX_TASKS:]
    save_session(sid, st)
    ledger.append_event(state_dir(), event)
    _debug("route %s" % json.dumps(event, default=str))
    if updated is None:
        return None
    label = "%s%s" % (rec["model"] or (rec.get("session_tier") or "session model"),
                      ("@" + rec["effort"]) if rec["effort"] else "")
    why = "gearshift: %s -> %s (%s, confidence %.2f)" % (rec["agent_type"], label, rec["class"], rec["confidence"])
    if rec.get("probe_from"):
        why = "gearshift: %s -> %s (probe: one step below %s for %s, to test the table)" % (
            rec["agent_type"], label, rec["probe_from"], rec["class"])
    if rec.get("bumped_from"):
        why = "gearshift: %s -> %s (same task fell short on %s, so one step up)" % (
            rec["agent_type"], label, rec["bumped_from"])
        if rec.get("at_ceiling"):
            why = ("gearshift: %s -> %s (same task fell short on %s and there is no step above it within "
                   "your ceiling; if it falls short again, take it in the main session)" % (
                       rec["agent_type"], label, rec["bumped_from"]))
    out: Dict[str, Any] = {"hookEventName": "PreToolUse", "updatedInput": updated,
                           "additionalContext": why + ". Pass model explicitly on an Agent call to override."}
    if cfg["permission"] in ("allow", "ask"):
        out["permissionDecision"] = cfg["permission"]
        out["permissionDecisionReason"] = why
    result: Dict[str, Any] = {"hookSpecificOutput": out}
    if cfg["announce"]:
        result["systemMessage"] = why
    return result


def _run_verify(command: str, cwd: Optional[str]) -> Tuple[Optional[bool], str]:
    """Run the user's check. (True/False, tail of output), or (None, "") if it could not finish."""
    import subprocess
    try:
        p = subprocess.run(command, shell=True, cwd=cwd or None, capture_output=True, text=True,
                           timeout=VERIFY_TIMEOUT, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, GEARSHIFT_MODE="off"))
    except (subprocess.SubprocessError, OSError):
        return None, ""
    tail = ((p.stderr or "") + (p.stdout or "")).strip()[-600:]
    return p.returncode == 0, tail


def on_post_agent(inp: Mapping[str, Any]) -> Optional[dict]:
    if inp.get("tool_name") not in AGENT_TOOLS:
        return None
    resp = inp.get("tool_response")
    if not isinstance(resp, dict):
        return None
    sid = str(inp.get("session_id") or "")
    if str(resp.get("status") or "") == "async_launched":
        # a background agent: this is the launch, not the result. Found out the hard way
        # (first live session logged 19 launches as "finished"). The real ending arrives
        # at SubagentStop, so tie the agent id to the task and wait for it there.
        st = load_session(sid)
        for t in st.get("tasks") or []:
            if t.get("id") == inp.get("tool_use_id"):
                t["agent"] = resp.get("agentId")
        save_session(sid, st)
        ledger.append_event(state_dir(), {"ev": "launched", "session": inp.get("session_id"),
                                          "tool_use_id": inp.get("tool_use_id"), "agent_id": resp.get("agentId")})
        return None
    reason = outcomes.escalation_in(resp)
    failed = outcomes.failed_status(resp)
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd")
    cfg = load_config(project_dir)
    st = load_session(sid)
    task = next((t for t in st.get("tasks") or [] if t.get("id") == inp.get("tool_use_id")), None)

    # the failure nobody announces: the subagent says "done" and the work is wrong.
    # if the user gave us a check and this subagent changed code, run it.
    check: Tuple[Optional[bool], str] = (None, "")
    if (cfg["mode"] in ("fill", "strict") and cfg.get("verify") and task and task.get("mut")
            and not reason and not failed):
        check = _run_verify(cfg["verify"], project_dir)
    bad_check = check[0] is False

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
    if check[0] is not None:
        event["verify"] = "pass" if check[0] else "fail"
    ledger.append_event(state_dir(), event)
    if not (reason or failed or bad_check) or task is None:
        return None

    # remember it, so the same task sent again starts one rung up
    st = load_session(sid)
    for t in st.get("tasks") or []:
        if t.get("id") == inp.get("tool_use_id"):
            t["short"] = True
    save_session(sid, st)
    if cfg["mode"] in ("off", "shadow") or not task.get("tier"):
        return None
    if not bad_check and not (reason and cfg["advise"]):
        return None
    ran = "%s@%s" % (task["tier"], task.get("effort") or "default effort")
    cap = effective_ceiling(cfg, _session_facts(inp)["tier"])
    up = outcomes.next_rung(task["tier"], task.get("effort") or "medium", load_policy(cfg.get("policy") or None), cap)
    if up:
        advice = "Re-run it one step up: model=%s, effort=%s." % up
    else:
        advice = "There is no step above it within your ceiling, so take this one in the main session."
    if bad_check:
        text = ("gearshift: this subagent ran on %s and reported the work done, but your check failed "
                "(`%s`):\n%s\nDo not build on this result. %s" % (ran, cfg["verify"], check[1], advice))
    else:
        text = "gearshift: this subagent ran on %s and asked to escalate (%s). %s" % (ran, reason, advice)
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}


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
    return _background_outcome(inp, str(path))


def _background_outcome(inp: Mapping[str, Any], transcript: str) -> Optional[dict]:
    """A background agent just finished. This is the only place its real result is visible."""
    sid = str(inp.get("session_id") or "")
    st = load_session(sid)
    task = next((t for t in st.get("tasks") or []
                 if t.get("agent") and t.get("agent") == inp.get("agent_id") and not t.get("done")), None)
    if task is None:
        return None     # a foreground agent: PostToolUse sees its result and handles it
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or inp.get("cwd")
    cfg = load_config(project_dir)
    text = inp.get("last_assistant_message")
    if not isinstance(text, str) or not text.strip():
        text = ledger.last_assistant_text(transcript)
    reason = outcomes.escalation_in(text)
    retrying = bool(inp.get("stop_hook_active"))
    check: Tuple[Optional[bool], str] = (None, "")
    if cfg["mode"] in ("fill", "strict") and cfg.get("verify") and task.get("mut") and not reason:
        check = _run_verify(cfg["verify"], project_dir)
    bad_check = check[0] is False
    if bad_check and not retrying and cfg["mode"] != "off":
        # don't let it leave yet: hand the failure back to the subagent once
        ledger.append_event(state_dir(), {"ev": "check", "session": inp.get("session_id"),
                                          "tool_use_id": task.get("id"), "agent_id": inp.get("agent_id"),
                                          "verify": "fail", "sent_back": True})
        # the whole command, not a cut one: in the first live run the subagent could not
        # run the check again because the message stopped at 120 characters
        return {"decision": "block", "reason": (
            "gearshift: you are not finished. The project's check failed (`%s`):\n%s\n"
            "Fix it and run the check again. If you cannot make it pass, stop and reply with a first "
            "line that starts with ESCALATE: and one sentence on why. Either way, end with your full "
            "report of what you changed: your earlier reply was not delivered." % (cfg["verify"], check[1]))}
    event = {"ev": "done", "session": inp.get("session_id"), "tool_use_id": task.get("id"),
             "agent_id": inp.get("agent_id"), "status": "completed", "background": True}
    if reason:
        event["escalated"], event["escalate_reason"] = True, reason
    if check[0] is not None:
        event["verify"] = "pass" if check[0] else "fail"
    ledger.append_event(state_dir(), event)
    st = load_session(sid)
    for t in st.get("tasks") or []:
        if t.get("id") == task.get("id"):
            t["done"] = True
            if reason or bad_check:
                t["short"] = True      # sent again, it starts one rung up
    save_session(sid, st)
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
