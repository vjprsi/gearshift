"""LLM judge: a small fast model classifies the tasks the keyword rules can't.

The keyword classifier is instant and free but only knows the words it was
given. The judge is the second opinion: one Haiku call at low effort that
reads the task in any language and answers with a class plus a few facts
(does it change code? high stakes? hard bug? long run?).

Order of things, on purpose:
  1. keywords first. confident -> done, no model call.
  2. not confident -> ask the judge.
  3. safety rules run on top of the judge's answer. The model does not get to
     send a code change to Haiku, whatever it says.

Three ways to reach the model:
  make_judge(call)        any callable, e.g. anthropic_adapter.make_call()
  api_transport()         plain HTTPS with an API key, no SDK needed
  claude_cli_transport()  headless `claude -p`, uses your Claude Code login

    router = Router(judge=make_judge(make_call()))
    router = Router(judge=judge_from_config("auto"))

NOTE: tested with fake transports. api and claude transports follow the
documented request formats but I have not measured judge accuracy on real
traffic yet - run `gearshift eval <cases> --judge api` and see for yourself.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .policy import Policy, default_policy

JUDGE_MODEL = "claude-haiku-5-5"

PROMPT = """You route software and knowledge-work tasks to the right AI model. Read the task below (it can be in any language) and reply with ONE line of JSON and nothing else:

{{"class": "<{names}>", "changes_code": true|false, "high_stakes": true|false, "hard_bug": true|false, "long_run": true|false}}

Classes:
{classes}

How to decide:
- changes_code: true if the task edits, creates or deletes code, config or data. Read-only work is false.
- high_stakes: production systems, money, auth, security, or anything irreversible.
- hard_bug: a bug that is intermittent, concurrent, or cannot be reproduced.
- long_run: meant to run unattended for a long time.
- Hunting for a bug is review, not lookup. A task that changes code is never lookup or extract.
- If you cannot tell, use "unsure".

<task>
{task}
</task>"""

_FLAG_MAP = {"changes_code": "mutating", "high_stakes": "stakes", "hard_bug": "tricky", "long_run": "long_horizon"}


def parse_verdict(text: str, policy: Policy) -> Optional[Dict[str, Any]]:
    """Pull {"class": ..., flags...} out of the model's reply. None if unusable."""
    if not text:
        return None
    names = [c for c in policy.classes]
    data: Dict[str, Any] = {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            got = json.loads(m.group(0))
            if isinstance(got, dict):
                data = got
        except ValueError:
            data = {}
    cls = str(data.get("class", "")).strip().lower()
    if cls not in names:
        # model ignored the format: accept a bare class name if there is exactly one
        found = {w.lower() for w in re.findall(r"[a-z]+", text.lower())} & set(names)
        if len(found) != 1:
            return None
        cls = found.pop()
    if cls == "unsure":
        return None
    flags = sorted(flag for key, flag in _FLAG_MAP.items() if data.get(key) is True)
    return apply_safety({"class": cls, "flags": flags})


def apply_safety(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """Rules the judge is not allowed to break."""
    cls, flags = verdict["class"], set(verdict.get("flags") or [])
    notes = []
    if "mutating" in flags and cls in ("lookup", "extract", "chat"):
        notes.append("judge said %s but the task changes code: treated as an edit" % cls)
        cls = "edit"
    if "tricky" in flags and "mutating" in flags and cls == "edit":
        notes.append("hard-to-reproduce bug with a fix: treated as build")
        cls = "build"
    return {"class": cls, "flags": sorted(flags), "notes": notes}


class _Cache:
    """Tiny on-disk memo so the same subagent prompt is not judged twice."""

    def __init__(self, path: Optional[Path], limit: int = 400):
        self.path, self.limit, self.mem = path, limit, {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.mem = data
            except (OSError, ValueError):
                pass

    @staticmethod
    def key(task: str, version: str) -> str:
        return hashlib.sha1((version + "\n" + task).encode("utf-8", "replace")).hexdigest()[:20]

    def get(self, k: str) -> Optional[dict]:
        v = self.mem.get(k)
        return v if isinstance(v, dict) else None

    def put(self, k: str, v: dict) -> None:
        self.mem[k] = v
        if not self.path:
            return
        try:
            if len(self.mem) > self.limit:
                for old in list(self.mem)[: len(self.mem) - self.limit]:
                    self.mem.pop(old, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp.%d" % os.getpid())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.mem, f)
            os.replace(tmp, self.path)
        except OSError:
            pass


def make_judge(call: Callable[..., object], *, model: str = JUDGE_MODEL, effort: str = "low",
               policy: Optional[Policy] = None, max_chars: int = 1500,
               cache_path: Optional[Path] = None) -> Callable[[str], Optional[Dict[str, Any]]]:
    """Wrap a `call` into a judge: task text in, {"class", "flags", "notes"} out."""
    pol = policy or default_policy()
    names = [c for c in pol.classes if c != "unsure"]
    listing = "\n".join("- %s: %s" % (n, pol.classes[n]["what"]) for n in names)
    cache = _Cache(cache_path)

    def judge(task: str) -> Optional[Dict[str, Any]]:
        text = (task or "").strip()[:max_chars]
        if not text:
            return None
        key = cache.key(text, pol.version)
        hit = cache.get(key)
        if hit:
            return hit
        res = call(model=model, effort=effort, feedback=None,
                   prompt=PROMPT.format(names=" | ".join(names + ["unsure"]), classes=listing, task=text))
        reply = getattr(res, "text", None)
        if reply is None:
            reply = res[0] if isinstance(res, tuple) else str(res)
        verdict = parse_verdict(reply or "", pol)
        if verdict:
            cache.put(key, verdict)
        return verdict

    return judge


# -- transports ----------------------------------------------------------------

def _api_key() -> Optional[str]:
    # GEARSHIFT_API_KEY first: exporting ANTHROPIC_API_KEY also moves Claude Code
    # itself from your subscription to API billing, which you may not want.
    return os.environ.get("GEARSHIFT_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or None


def api_transport(api_key: Optional[str] = None, timeout: float = 6.0) -> Callable[..., str]:
    """Direct HTTPS to the Messages API. Standard library only."""
    key = api_key or _api_key()
    if not key:
        raise RuntimeError("no API key: set GEARSHIFT_API_KEY or ANTHROPIC_API_KEY")
    base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")

    def call(model: str, effort: str, prompt: str, feedback: Optional[str] = None) -> str:
        body = json.dumps({
            "model": model, "max_tokens": 1024,
            "output_config": {"effort": effort},
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(base + "/v1/messages", data=body, method="POST", headers={
            "x-api-key": key, "content-type": "application/json",
            "anthropic-version": os.environ.get("GEARSHIFT_ANTHROPIC_VERSION", "2023-06-01"),
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    return call


def claude_cli_transport(timeout: float = 20.0, binary: Optional[str] = None) -> Callable[..., str]:
    """Headless Claude Code (`claude -p`). No API key: it uses your login.

    Slower than the API (it starts a whole session), so it is opt-in. The
    child runs with GEARSHIFT_INSIDE_JUDGE=1 so our own hooks stay out of it.
    """
    exe = binary or shutil.which("claude")
    if not exe:
        raise RuntimeError("`claude` is not on PATH")

    def call(model: str, effort: str, prompt: str, feedback: Optional[str] = None) -> str:
        env = dict(os.environ, GEARSHIFT_INSIDE_JUDGE="1", GEARSHIFT_MODE="off")
        # prompt goes in on stdin: quoting a multi-line argument for claude.cmd
        # on Windows is not something I want to debug
        p = subprocess.run([exe, "-p", "--model", "haiku"], input=prompt, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return p.stdout or ""

    return call


def judge_from_config(kind: str, *, policy: Optional[Policy] = None,
                      cache_path: Optional[Path] = None) -> Optional[Callable[[str], Optional[dict]]]:
    """kind: off | auto | api | claude. auto = api when a key is set, else off."""
    kind = (kind or "auto").lower()
    if os.environ.get("GEARSHIFT_INSIDE_JUDGE"):
        return None
    if kind == "auto":
        kind = "api" if _api_key() else "off"
    try:
        if kind == "api":
            return make_judge(api_transport(), policy=policy, cache_path=cache_path)
        if kind == "claude":
            return make_judge(claude_cli_transport(), policy=policy, cache_path=cache_path)
    except RuntimeError:
        return None
    return None
