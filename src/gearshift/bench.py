"""Compare configs on your own tasks. The number that matters is cost per
finished task, with failed attempts counted in.

Task file is JSONL:

    {"id": "t1", "prompt": "...", "check": {"type": "contains", "value": "42"}}

check types: contains, equals, regex, json_equals, python (the code block in
the answer is run with the asserts in "tests" appended), command (shell
command, gets the output on stdin, exit 0 = pass). python and command run
on your machine, so only use task files you trust.

`gearshift bench pack` runs the 31 tasks that ship with gearshift.

Configs:

    opus@medium          fixed model + effort, one attempt
    router               gearshift picks per task, one attempt
    cascade              gearshift picks, then climbs the ladder on failure
    cheap-first:opus     everything at low, failures again at high

--simulate uses a fake model so the harness can be tried without an API key.
Those numbers are made up.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .execute import BatchResult, CallResult, RunResult, run, run_batch
from .policy import DATA_DIR, EFFORTS, TIERS
from .pricing import tier_of
from .router import Router

SIM_BANNER = ("SIMULATED RUN: a toy model with invented pass rates and token counts. "
              "It demonstrates the harness; it says nothing about real models.")

# made-up numbers for the --simulate toy model. NOT measurements.
_SIM_SKILL = {"haiku": 0.45, "sonnet": 0.62, "opus": 0.78, "fable": 0.88}
_SIM_EFFORT = {"low": -0.08, "medium": 0.0, "high": 0.05, "xhigh": 0.07, "max": 0.08}
_SIM_OUTPUT = {"low": 300, "medium": 700, "high": 1400, "xhigh": 3000, "max": 5000}


PACK = "pack"     # `gearshift bench pack` = the task pack shipped in the package


def pack_path() -> str:
    return str(DATA_DIR / "bench_pack.jsonl")


def extract_code(output: str) -> str:
    """The longest fenced block in the answer, or the whole answer if there is none."""
    blocks = re.findall(r"```[a-zA-Z0-9_+\-]*[ \t]*\r?\n(.*?)```", output, re.DOTALL)
    return max(blocks, key=len) if blocks else output


def _run_python(code: str, tests: str, timeout: int) -> Tuple[bool, Optional[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "candidate.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code.rstrip() + "\n\n# --- checks ---\n" + tests + "\n")
        try:
            p = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True,
                               timeout=timeout, cwd=tmp, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, "timed out after %ds" % timeout
        except OSError as exc:
            return False, "could not run the check: %s" % exc
    if p.returncode == 0:
        return True, None
    return False, (p.stderr or p.stdout or "check failed").strip()[-800:]


def load_tasks(path: str, limit: Optional[int] = None) -> List[dict]:
    if path == PACK:
        path = pack_path()
    tasks = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            t = json.loads(line)
            t.setdefault("id", "task-%d" % (i + 1))
            tasks.append(t)
            if limit and len(tasks) >= limit:
                break
    return tasks


def check_output(task: dict, output: str) -> Tuple[bool, Optional[str]]:
    chk = task.get("check") or {}
    kind, value = chk.get("type"), chk.get("value")
    if kind == "contains":
        ok = str(value).lower() in output.lower()
        return ok, None if ok else "expected the answer to contain %r" % value
    if kind == "equals":
        ok = output.strip() == str(value).strip()
        return ok, None if ok else "expected exactly %r" % value
    if kind == "regex":
        ok = re.search(str(value), output, re.MULTILINE | re.DOTALL) is not None
        return ok, None if ok else "expected output matching /%s/" % value
    if kind == "json_equals":
        try:
            m = re.search(r"\{.*\}|\[.*\]", output, re.DOTALL)
            got = json.loads(m.group(0) if m else output)
        except ValueError:
            return False, "output was not valid JSON"
        ok = got == value
        return ok, None if ok else "JSON did not match the expected value"
    if kind == "python":
        return _run_python(extract_code(output), str(chk.get("tests") or value or ""), int(chk.get("timeout", 20)))
    if kind == "command":
        try:
            p = subprocess.run(str(value), shell=True, input=output, text=True,
                               capture_output=True, timeout=int(chk.get("timeout", 60)))
        except subprocess.SubprocessError as exc:
            return False, "check command failed to run: %s" % exc
        return p.returncode == 0, None if p.returncode == 0 else (p.stderr or p.stdout or "check failed")[-800:]
    raise ValueError("task %s: unknown check type %r" % (task.get("id"), kind))


def _sim_call(tasks_by_prompt: Dict[str, dict]) -> Callable[..., CallResult]:
    def call(model: str, effort: str, prompt: str, feedback: Optional[str] = None) -> CallResult:
        task = tasks_by_prompt[prompt]
        tier = tier_of(model) or "sonnet"
        difficulty = float(task.get("difficulty", 0.5))
        h = hashlib.sha256(("%s|%s|%s" % (task["id"], tier, effort)).encode()).digest()
        noise = (h[0] / 255.0 - 0.5) * 0.10
        skill = _SIM_SKILL[tier] + _SIM_EFFORT[effort] + (0.03 if feedback else 0.0)
        ok = difficulty + noise <= skill
        usage = {"input_tokens": 200 + len(prompt) // 4,
                 "output_tokens": int(_SIM_OUTPUT[effort] * (1.0 + difficulty))}
        return CallResult(text="SIM-PASS" if ok else "SIM-FAIL", usage=usage)
    return call


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _summarize(name: str, results: List[RunResult]) -> Dict[str, Any]:
    b = BatchResult(results)
    costs = [r.cost_usd for r in results]
    return {
        "config": name,
        "tasks": b.total,
        "passed": b.passed,
        "pass_rate": round(b.passed / b.total, 4) if b.total else 0.0,
        "total_cost_usd": round(b.cost_usd, 6),
        "cost_per_finished_task": (round(b.cost_per_finished_task, 6)
                                   if b.cost_per_finished_task is not None else None),
        "p90_task_cost_usd": round(_percentile(costs, 0.9), 6),
        "avg_attempts": round(sum(len(r.attempts) for r in results) / b.total, 2) if b.total else 0.0,
        "cost_known": all(r.cost_known for r in results),
    }


def run_bench(path: str, *, configs: Sequence[str], simulate: bool = False, limit: Optional[int] = None,
              max_workers: int = 4, call: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    tasks = load_tasks(path, limit)
    if not tasks:
        raise RuntimeError("no tasks in %s" % path)
    router = Router()
    by_prompt = {t["prompt"]: t for t in tasks}
    if simulate:
        call = _sim_call(by_prompt)

        def verify_item(task: dict, out: str) -> Tuple[bool, Optional[str]]:
            return out == "SIM-PASS", None if out == "SIM-PASS" else "simulated failure"
    else:
        if call is None:
            from .anthropic_adapter import make_call
            call = make_call()
        verify_item = check_output

    rows = []
    for cfg in configs:
        cfg = cfg.strip()
        if not cfg:
            continue
        if cfg in ("router", "cascade"):
            attempts = 1 if cfg == "router" else 3
            results = [run(t["prompt"], call, lambda out, t=t: verify_item(t, out),
                           router=router, max_attempts=attempts) for t in tasks]
        elif cfg.startswith("cheap-first:"):
            tier = cfg.split(":", 1)[1]
            if tier not in TIERS:
                raise RuntimeError("unknown tier in %r" % cfg)
            results = run_batch(tasks, call, verify_item, first=(tier, "low"), retry=(tier, "high"),
                                prompt=lambda t: t["prompt"], router=router,
                                max_workers=1 if simulate else max_workers).results
        elif "@" in cfg:
            tier, effort = cfg.split("@", 1)
            if tier not in TIERS or effort not in EFFORTS:
                raise RuntimeError("unknown configuration %r (use tier@effort)" % cfg)
            results = run_batch(tasks, call, verify_item, first=(tier, effort), retry=None,
                                prompt=lambda t: t["prompt"], router=router,
                                max_workers=1 if simulate else max_workers).results
        else:
            raise RuntimeError("unknown configuration %r" % cfg)
        rows.append(_summarize(cfg, results))
    return {"simulated": simulate, "tasks": len(tasks), "rows": rows,
            "source": "gearshift pack" if path == PACK else os.path.basename(path),
            "date": time.strftime("%Y-%m-%d")}


def format_markdown(result: Dict[str, Any]) -> str:
    """A results file you can commit. Says what ran, when, and on which models."""
    from .pricing import price_table
    tiers = price_table()["tiers"]
    lines = ["# gearshift bench results", ""]
    if result.get("simulated"):
        lines += ["> " + SIM_BANNER, ""]
    lines += ["- Date: %s" % result.get("date", "?"),
              "- Tasks: %d (%s)" % (result["tasks"], result.get("source", "?")),
              "- Models: " + ", ".join("%s = `%s`" % (t, tiers[t]) for t in TIERS if t in tiers),
              "- Prices: list prices in `models.json` on that date",
              "", "| Config | Passed | Cost per finished task | Total | p90 per task | Attempts |",
              "|---|---|---|---|---|---|"]
    for r in result["rows"]:
        cpf = "$%.4f" % r["cost_per_finished_task"] if r["cost_per_finished_task"] is not None else "n/a"
        lines.append("| `%s` | %.0f%% | %s | $%.4f | $%.4f | %.2f |" % (
            r["config"], 100 * r["pass_rate"], cpf, r["total_cost_usd"], r["p90_task_cost_usd"], r["avg_attempts"]))
    lines += ["", "Cost per finished task is total spend, failed attempts included, divided by the tasks that passed.",
              "One run. Model output varies between runs, so small differences here mean nothing."]
    return "\n".join(lines) + "\n"


def format_table(result: Dict[str, Any]) -> str:
    lines = []
    if result.get("simulated"):
        lines += [SIM_BANNER, ""]
    lines.append("%-20s %6s %15s %12s %12s %9s" % ("config", "pass", "cost/finished", "total", "p90/task", "attempts"))
    for r in result["rows"]:
        cpf = "$%.4f" % r["cost_per_finished_task"] if r["cost_per_finished_task"] is not None else "n/a"
        lines.append("%-20s %5.0f%% %15s %12s %12s %9.2f" % (
            r["config"], 100 * r["pass_rate"], cpf, "$%.4f" % r["total_cost_usd"],
            "$%.4f" % r["p90_task_cost_usd"], r["avg_attempts"]))
    lines.append("")
    lines.append("cost/finished = total spend, failures included, divided by tasks that passed.")
    if result.get("simulated"):
        lines.append("Run without --simulate (and with ANTHROPIC_API_KEY set) to measure real models.")
    return "\n".join(lines)
