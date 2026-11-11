"""Settings for the Claude Code hooks.

Order, later wins: defaults -> ~/.config/gearshift/config.json -> plugin
options (CLAUDE_PLUGIN_OPTION_*) -> GEARSHIFT_* env vars -> <project>/.gearshift.json

The project file is special: it can only make things MORE conservative (lower
the ceiling, shadow/off). I don't want a repo I just cloned to be able to
make my subagents more expensive.

  mode        off | shadow | fill (default) | strict
              shadow = log only. fill = set model/effort only where the Agent
              call left them empty. strict = also replace the model Claude chose.
  ceiling     session (default) | haiku | sonnet | opus | fable
              highest tier the hook may assign. "session" = never above the
              model you are running, i.e. the hook only routes down.
  set_effort  auto (default) | always | never
              auto = only on Claude Code versions that accept effort per call (2.1.292+)
  announce    one line per routed subagent (default on)
  advise      session model mismatch / model switch notes (default on)
  brief       short routing brief in context at session start (default on)
  explore     route (default) | leave - whether to touch the built-in Explore agent
  permission  allow (default) | ask | none - permissionDecision sent with a rewritten call
  hint        true (default) | false - when the hook moves a call to a cheaper model, add one
              line to its prompt telling it to answer ESCALATE: if the task is beyond it
  verify      a shell command (for example your test suite). When a subagent that changed code
              comes back, the hook runs it in the project folder; if it fails, Claude is told
              not to build on that result and to redo it one step up. Limit 25 seconds.
              Not settable from a project file: it runs a command on your machine.
  probe       0 (default) to 0.5 - share of safe, read-only calls sent one step CHEAPER than
              the table says, to find out whether the table is too generous for your work.
              Off unless you turn it on. Not settable from a project file.
  judge       auto (default) | off | api | claude
              LLM second opinion when the keyword rules aren't confident.
              auto = api if GEARSHIFT_API_KEY or ANTHROPIC_API_KEY is set, otherwise off.
              claude = headless `claude -p` on your login (slower, no key).
              Not settable from a project file: it can cost money.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .policy import TIERS, tier_rank, user_config_dir

DEFAULTS: Dict[str, Any] = {
    "mode": "fill",
    "ceiling": "session",
    "set_effort": "auto",
    "announce": True,
    "advise": True,
    "brief": True,
    "explore": "route",
    "permission": "allow",
    "judge": "auto",
    "hint": True,
    "probe": 0.0,
    "verify": "",
    "project_cap": None,
    "policy": None,
}

_CHOICES = {
    "mode": ("off", "shadow", "fill", "strict"),
    "ceiling": ("session",) + tuple(TIERS),
    "set_effort": ("auto", "always", "never"),
    "explore": ("route", "leave"),
    "permission": ("allow", "ask", "none"),
    "judge": ("auto", "off", "api", "claude"),
}
_BOOLS = ("announce", "advise", "brief", "hint")


def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off"):
            return False
    return None


def _apply(cfg: Dict[str, Any], src: Mapping[str, Any]) -> None:
    for key, choices in _CHOICES.items():
        v = src.get(key)
        if isinstance(v, str) and v.strip().lower() in choices:
            cfg[key] = v.strip().lower()
    for key in _BOOLS:
        b = _as_bool(src.get(key))
        if b is not None:
            cfg[key] = b
    if isinstance(src.get("verify"), str):
        cfg["verify"] = src["verify"].strip()[:500]
    if src.get("probe") is not None:
        try:
            cfg["probe"] = min(0.5, max(0.0, float(src["probe"])))   # never more than half the calls
        except (TypeError, ValueError):
            pass


def _read(path: Path) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config(project_dir: Optional[str] = None,
                env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    cfg = dict(DEFAULTS)
    _apply(cfg, _read(user_config_dir() / "config.json"))
    _apply(cfg, {k: env.get("CLAUDE_PLUGIN_OPTION_" + k.upper()) for k in DEFAULTS})
    _apply(cfg, {k: env.get("GEARSHIFT_" + k.upper()) for k in DEFAULTS})

    if project_dir:
        proj = _read(Path(project_dir) / ".gearshift.json")
        if proj:
            # project file can only tighten things: off/shadow, lower cap,
            # policy tweaks (which still can't go above the ceiling)
            if proj.get("mode") in ("off", "shadow"):
                cfg["mode"] = proj["mode"]
            cap = proj.get("ceiling")
            if cap in TIERS:
                cfg["project_cap"] = cap
            for key in _BOOLS:
                if _as_bool(proj.get(key)) is False:
                    cfg[key] = False
            if proj.get("explore") == "leave":
                cfg["explore"] = "leave"
            if isinstance(proj.get("policy"), dict):
                cfg["policy"] = proj["policy"]
    return cfg


def effective_ceiling(cfg: Mapping[str, Any], session_tier: Optional[str]) -> Optional[str]:
    """Highest tier the hook may assign, or None when unknown.

    With ceiling=session and an unknown session model we assume Sonnet:
    the hook may still route down to Haiku/Sonnet but never guesses upward.
    """
    c = cfg.get("ceiling", "session")
    cap = (session_tier or "sonnet") if c == "session" else c
    pc = cfg.get("project_cap")
    if pc in TIERS and tier_rank(pc) < tier_rank(cap):
        cap = pc
    return cap


def state_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    """Where session state and the ledger live: $GEARSHIFT_HOME or
    ~/.claude/gearshift. Deliberately not the plugin data directory, so
    the hooks and a pip-installed gearshift report read the same files."""
    env = os.environ if env is None else env
    v = env.get("GEARSHIFT_HOME")
    if v:
        return Path(v).expanduser()
    return Path(os.path.expanduser("~")) / ".claude" / "gearshift"
