"""Loads the routing policy and the model/price table.

The policy is plain JSON (data/policy.json) so it can be diffed and overridden
without touching code. Overrides are deep-merged, later wins:

  1. bundled data/policy.json
  2. ~/.config/gearshift/policy.json
  3. file in $GEARSHIFT_POLICY
  4. dict passed to load_policy()

"signals" and "agent_type_priors" merge by name/match, so you can swap one
entry without copying the whole list.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"

TIERS = ["haiku", "sonnet", "opus", "fable"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def tier_rank(tier: str) -> int:
    return TIERS.index(tier)


def effort_rank(effort: str) -> int:
    return EFFORTS.index(effort)


def _merge_named(base: List[dict], over: List[dict], key: str) -> List[dict]:
    out = [copy.deepcopy(x) for x in base]
    index = {x.get(key): i for i, x in enumerate(out)}
    for item in over:
        k = item.get(key)
        if k in index:
            out[index[k]] = copy.deepcopy(item)
        else:
            out.append(copy.deepcopy(item))
    return out


def deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k == "signals" and isinstance(v, list):
            out[k] = _merge_named(out.get(k, []), v, "name")
        elif k == "agent_type_priors" and isinstance(v, list):
            out[k] = _merge_named(out.get(k, []), v, "match")
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def user_config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "gearshift"


def load_policy_dict(overrides: Optional[dict] = None, *, use_user_files: bool = True) -> dict:
    with open(DATA_DIR / "policy.json", "r", encoding="utf-8") as f:
        policy = json.load(f)
    if use_user_files:
        user = _read_json(user_config_dir() / "policy.json")
        if user:
            policy = deep_merge(policy, user)
        env_path = os.environ.get("GEARSHIFT_POLICY")
        if env_path:
            env = _read_json(Path(env_path).expanduser())
            if env:
                policy = deep_merge(policy, env)
    if overrides:
        policy = deep_merge(policy, overrides)
    for name in policy.get("disable_signals", []) or []:
        policy["signals"] = [s for s in policy["signals"] if s.get("name") != name]
    return policy


def load_models() -> dict:
    with open(DATA_DIR / "models.json", "r", encoding="utf-8") as f:
        models = json.load(f)
    user = _read_json(user_config_dir() / "models.json")
    if user:
        models = deep_merge(models, user)
    return models


class Signal:
    __slots__ = ("name", "flag", "adds", "patterns", "raw", "quiet")

    def __init__(self, spec: dict):
        self.name: str = spec["name"]
        self.flag: Optional[str] = spec.get("flag")
        # raw = match with file paths left in (they are masked by default)
        self.raw: bool = bool(spec.get("raw"))
        # quiet = sets its flag but doesn't show up as evidence
        self.quiet: bool = bool(spec.get("quiet"))
        self.adds: Dict[str, float] = dict(spec.get("adds") or {})
        flags = re.IGNORECASE | re.MULTILINE
        pats: List[Pattern[str]] = []
        for p in spec.get("patterns", []):
            src = p if spec.get("no_boundary") else r"(?<![\w])(?:%s)(?![\w])" % p
            try:
                pats.append(re.compile(src, flags))
            except re.error:
                # bad regex in a user override: skip it, don't die
                continue
        self.patterns = pats


class Policy:
    """Compiled, validated routing policy."""

    def __init__(self, data: dict, models: Optional[dict] = None):
        self.data = data
        self.models = models or load_models()
        self.version: str = str(data.get("version", "0"))
        self.classes: Dict[str, dict] = data["classes"]
        self.lead_verbs: Dict[str, Dict[str, float]] = data["lead_verbs"]
        self.mutating_verbs = set(data.get("mutating_verbs", []))
        self.signals: List[Signal] = [Signal(s) for s in data.get("signals", [])]
        self.priors: List[Tuple[Pattern[str], dict]] = []
        for p in data.get("agent_type_priors", []):
            try:
                self.priors.append((re.compile(p["match"], re.IGNORECASE), p))
            except re.error:
                continue
        self.ladder: List[Tuple[str, str]] = [(t, e) for t, e in data["ladder"]]
        self.effort_caps: Dict[str, str] = data.get("effort_caps", {})
        self.thresholds: Dict[str, float] = data.get("thresholds", {})
        self.session_advice: Dict[str, List[str]] = data.get("session_advice", {})
        self._validate()

    def _validate(self) -> None:
        for name, c in self.classes.items():
            if c["tier"] not in TIERS:
                raise ValueError("policy class %r has unknown tier %r" % (name, c["tier"]))
            if c["effort"] not in EFFORTS:
                raise ValueError("policy class %r has unknown effort %r" % (name, c["effort"]))
        if "unsure" not in self.classes:
            raise ValueError("policy needs an 'unsure' class")
        for t, e in self.ladder:
            if t not in TIERS or e not in EFFORTS:
                raise ValueError("bad ladder rung %r" % ((t, e),))

    # -- model table helpers -------------------------------------------------

    def model_id(self, tier: str) -> str:
        return self.models["tiers"][tier]

    def model_info(self, tier_or_id: str) -> Optional[dict]:
        mid = self.models["tiers"].get(tier_or_id, tier_or_id)
        return self.models["models"].get(mid)

    def default_effort(self, tier: str, platform: str = "api") -> str:
        info = self.model_info(tier) or {}
        d = info.get("default_effort", {})
        key = "claude_code" if platform == "claude-code" else "api"
        return d.get(key, "high")

    def effort_cap(self, tier: str) -> str:
        return self.effort_caps.get(tier, "high")


_default: Optional[Policy] = None


def default_policy() -> Policy:
    global _default
    if _default is None:
        _default = Policy(load_policy_dict())
    return _default


def load_policy(overrides: Optional[dict] = None, *, use_user_files: bool = True) -> Policy:
    return Policy(load_policy_dict(overrides, use_user_files=use_user_files))
