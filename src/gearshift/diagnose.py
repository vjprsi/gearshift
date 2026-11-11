"""What `doctor` needs to know about the install, and the bundle you paste into an issue.

Two things bit me on my own machine: the plugin was at 1.0.0 while the pip-installed CLI said
1.4.1, and I only found out by opening the cached plugin.json. And a `verify` command I had
never run by hand. Both are checked here.

The bundle is meant to be pasted in public, so it carries no paths, no prompts, no task
descriptions, no session ids and not the text of your check command.
"""

from __future__ import annotations

import collections
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import __version__

PLUGIN_KEY = "gearshift@"
UPDATE_PLUGIN = "claude plugin marketplace update gearshift && claude plugin update gearshift@gearshift"
UPDATE_CLI = "pip install --user --upgrade git+https://github.com/vjprsi/gearshift"
_OUR_AGENT_TYPES = ("general-purpose", "claude", "Explore", "Plan")


def _claude_dir(home: Optional[str] = None) -> Path:
    if home is None and os.environ.get("CLAUDE_CONFIG_DIR"):
        return Path(os.environ["CLAUDE_CONFIG_DIR"])
    return Path(home or os.path.expanduser("~")) / ".claude"


def _vtuple(v: str) -> tuple:
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def pip_version() -> Optional[str]:
    """Version of a pip-installed gearshift in this Python, if there is one."""
    try:
        from importlib import metadata
        return metadata.version("gearshift")
    except Exception:
        return None


def plugin_versions(home: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every installed copy of the plugin Claude Code knows about: version and scope."""
    out: List[Dict[str, Any]] = []
    try:
        with open(_claude_dir(home) / "plugins" / "installed_plugins.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        plugins = data.get("plugins") if isinstance(data, dict) else None
        for key, entries in (plugins or {}).items():
            if not str(key).startswith(PLUGIN_KEY):
                continue
            for e in entries if isinstance(entries, list) else [entries]:
                if isinstance(e, dict) and e.get("version"):
                    out.append({"version": str(e["version"]), "scope": str(e.get("scope") or "?")})
    except (OSError, ValueError):
        pass
    return out


def running_from_plugin() -> Optional[str]:
    """When this code is the plugin's own copy, the version in its plugin.json."""
    try:
        with open(Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json", "r", encoding="utf-8") as f:
            return str(json.load(f).get("version") or "") or None
    except (OSError, ValueError):
        return None


def versions(home: Optional[str] = None) -> Dict[str, Any]:
    return {"running": __version__, "pip": pip_version(), "plugins": plugin_versions(home),
            "plugin_here": running_from_plugin()}


def version_lines(v: Mapping[str, Any]) -> List[str]:
    """Doctor lines about versions. A mismatch is a warning with the command that fixes it."""
    plugins = v.get("plugins") or []
    cli = v.get("running")
    lines = []
    if v.get("pip") and cli and v["pip"] != cli:
        lines.append("  [!!] a pip-installed gearshift (%s) is not the copy answering now (%s). The `gearshift` "
                     "command on your PATH may be the other one. Update: %s" % (v["pip"], cli, UPDATE_CLI))
    if not plugins:
        return lines + ["  note: the Claude Code plugin is not installed (fine for library or CLI use; "
                        "`claude --plugin-dir` sessions do not show up here)"]
    seen = sorted({p["version"] for p in plugins}, key=_vtuple)
    lines.append("  plugin: %s" % ", ".join("%s (%s)" % (p["version"], p["scope"]) for p in plugins))
    newest = seen[-1]
    if len(seen) > 1:
        lines.append("  [!!] the plugin is installed at different versions in different scopes. Update: %s" % UPDATE_PLUGIN)
    if cli and _vtuple(newest) < _vtuple(cli):
        lines.append("  [!!] the plugin (%s) is older than this CLI (%s), so the hooks in your sessions are the old "
                     "ones. Update: %s" % (newest, cli, UPDATE_PLUGIN))
    elif cli and _vtuple(newest) > _vtuple(cli):
        lines.append("  [!!] this CLI (%s) is older than the plugin (%s), so `gearshift report` here may not know "
                     "what the hooks write. Update: %s" % (cli, newest, UPDATE_CLI))
    return lines


def verify_lines(cfg: Mapping[str, Any]) -> List[str]:
    if not cfg.get("verify"):
        return []
    return ["  verify: on, limit %ds. Run it once by hand in your project folder before you rely on it:" % cfg["verify_timeout"],
            "      %s" % cfg["verify"],
            "    It must exit 0 on a good tree and finish inside the limit. If it fails there, every subagent that "
            "edits is sent back; if it is too slow, results go unchecked (raise verify_timeout, up to 120)."]


def _safe_agent(name: Any) -> str:
    name = str(name or "")
    if name in _OUR_AGENT_TYPES or name.startswith("gearshift:"):
        return name
    return "other-plugin" if ":" in name else "custom"


def build_bundle(cfg: Mapping[str, Any], state: Path, claude_version: Optional[str] = None,
                 home: Optional[str] = None, days: float = 7, last: int = 12) -> Dict[str, Any]:
    from . import ledger
    v = versions(home)
    events = ledger.read_events(state, since=time.time() - days * 86400)
    counts = collections.Counter(str(e.get("ev")) for e in events)
    actions = collections.Counter(str(e.get("action")) for e in events if e.get("ev") == "route")
    checks = collections.Counter(str(e.get("verify")) for e in events if e.get("ev") in ("done", "check") and e.get("verify"))
    tail = []
    for e in events[-last:]:
        row: Dict[str, Any] = {"ev": e.get("ev")}
        if e.get("ev") == "route":
            row.update(action=e.get("action"), cls=e.get("class"), agent=_safe_agent(e.get("agent_type")),
                       ran="%s@%s" % (e.get("model") or e.get("pinned_model") or "session",
                                      e.get("effort") or e.get("pinned_effort") or "-"))
        for k in ("status", "verify", "background", "continued", "escalated", "failed", "sent_back", "nudge"):
            if e.get(k):
                row[k] = e[k]
        tail.append(row)
    return {
        "gearshift": v["running"], "pip": v["pip"], "plugins": v["plugins"], "running_from_plugin": bool(v["plugin_here"]),
        "version_warnings": [l.strip() for l in version_lines(v) if "[!!]" in l],
        "python": sys.version.split()[0], "os": platform.system(), "claude_code": claude_version,
        "settings": {
            "mode": cfg["mode"], "ceiling": cfg["ceiling"], "project_cap": cfg.get("project_cap"),
            "set_effort": cfg["set_effort"], "announce": cfg["announce"], "advise": cfg["advise"],
            "brief": cfg["brief"], "explore": cfg["explore"], "permission": cfg["permission"],
            "hint": cfg["hint"], "probe": cfg["probe"], "judge": cfg["judge"],
            "verify": "set" if cfg.get("verify") else "not set", "verify_timeout": cfg["verify_timeout"],
            "policy_override": bool(cfg.get("policy")),
        },
        "env": sorted(k for k in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL",
                                  "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "GEARSHIFT_HOME", "GEARSHIFT_DEBUG")
                      if os.environ.get(k)),
        "api_key": bool(os.environ.get("GEARSHIFT_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")),
        "ledger_days": days, "events": dict(counts), "route_actions": dict(actions), "checks": dict(checks),
        "last_events": tail,
    }


def format_bundle(b: Mapping[str, Any]) -> str:
    """Compact text for an issue. Nothing in it identifies the machine, the project or the work."""
    def kv(d: Mapping[str, Any]) -> str:
        return " ".join("%s=%s" % (k, d[k]) for k in d) or "none"

    lines = ["```", "gearshift %s  pip=%s  plugin=%s%s" % (
        b["gearshift"], b["pip"] or "-",
        ",".join("%s(%s)" % (p["version"], p["scope"]) for p in b["plugins"]) or "-",
        "  (running from the plugin)" if b["running_from_plugin"] else "")]
    lines.append("python %s  os %s  claude-code %s" % (b["python"], b["os"], b["claude_code"] or "?"))
    lines.extend(b["version_warnings"])
    lines.append("settings: " + kv(b["settings"]))
    lines.append("env set: %s  api key: %s" % (", ".join(b["env"]) or "none", "yes" if b["api_key"] else "no"))
    lines.append("last %g days: %s" % (b["ledger_days"], kv(b["events"])))
    lines.append("route actions: " + kv(b["route_actions"]))
    lines.append("checks: " + kv(b["checks"]))
    lines.append("last events:")
    for row in b["last_events"]:
        lines.append("  " + kv(row))
    lines.append("```")
    return "\n".join(lines)
