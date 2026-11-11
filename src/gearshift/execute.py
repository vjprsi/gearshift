"""Run a task where the router says, climb the ladder when a check fails.

  run()        one task; escalates while verify() rejects the output
  run_batch()  many checkable tasks; cheap first, re-run only the failures

Both need a pass/fail signal. No verify -> run() does a single attempt,
because there is nothing to escalate on.

`call` is whatever talks to the model: call(model=, effort=, prompt=, feedback=)
returning a CallResult, a (text, usage) tuple or just a string. See
anthropic_adapter.make_call for the Anthropic SDK.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

from .pricing import cost_usd, usage_to_dict
from .router import Decision, Router


@dataclass
class CallResult:
    text: str
    usage: Any = None
    raw: Any = None


@dataclass
class Attempt:
    n: int
    tier: str
    model: str
    effort: str
    ok: bool
    output: str
    cost_usd: Optional[float]
    usage: Optional[dict]
    seconds: float
    feedback: Optional[str] = None


@dataclass
class RunResult:
    ok: bool
    output: str
    attempts: List[Attempt]
    cost_usd: float
    cost_known: bool
    decision: Optional[Decision] = None
    stopped: str = "passed"   # passed | unverified | exhausted | budget

    @property
    def final(self) -> Optional[Attempt]:
        return self.attempts[-1] if self.attempts else None


@dataclass
class BatchResult:
    results: List[RunResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def first_pass(self) -> int:
        return sum(1 for r in self.results if r.ok and len(r.attempts) == 1)

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    @property
    def cost_per_finished_task(self) -> Optional[float]:
        """Total spend (failures included) divided by tasks that passed."""
        return self.cost_usd / self.passed if self.passed else None

    def summary(self) -> dict:
        return {
            "tasks": self.total,
            "passed": self.passed,
            "pass_rate": round(self.passed / self.total, 4) if self.total else 0.0,
            "first_pass": self.first_pass,
            "cost_usd": round(self.cost_usd, 6),
            "cost_per_finished_task": (round(self.cost_per_finished_task, 6)
                                       if self.cost_per_finished_task is not None else None),
        }


Verify = Callable[[str], Union[bool, Tuple[bool, Optional[str]]]]
Call = Callable[..., Union[CallResult, Tuple[str, Any], str]]


def _normalize(res: Any) -> CallResult:
    if isinstance(res, CallResult):
        return res
    if isinstance(res, tuple) and len(res) == 2:
        return CallResult(text=str(res[0]), usage=res[1])
    return CallResult(text=str(res))


def _check(verify: Optional[Callable[..., Any]], *args: Any) -> Tuple[bool, Optional[str]]:
    if verify is None:
        return True, None
    out = verify(*args)
    if isinstance(out, tuple):
        return bool(out[0]), (str(out[1]) if len(out) > 1 and out[1] else None)
    return bool(out), None


def _attempt(call: Call, n: int, tier: str, model: str, effort: str, prompt: str,
             feedback: Optional[str]) -> Tuple[Attempt, CallResult]:
    t0 = time.monotonic()
    res = _normalize(call(model=model, effort=effort, prompt=prompt, feedback=feedback))
    cost = cost_usd(res.usage, model) if res.usage is not None else None
    usage = usage_to_dict(res.usage) if res.usage is not None else None
    return Attempt(n, tier, model, effort, False, res.text, cost, usage,
                   round(time.monotonic() - t0, 3)), res


def run(
    task: str,
    call: Call,
    verify: Optional[Verify] = None,
    *,
    router: Optional[Router] = None,
    decision: Optional[Decision] = None,
    max_attempts: int = 3,
    budget_usd: Optional[float] = None,
    on_attempt: Optional[Callable[[Attempt], None]] = None,
    **route_kwargs: Any,
) -> RunResult:
    """Route task, run it, and escalate while verify fails.

    verify(output) returns True/False or (ok, feedback); the
    feedback string is passed to the next attempt. budget_usd stops
    further attempts once spend reaches it (an attempt in flight is not cut
    short). Exceptions from call propagate: a transport error is not a
    reason to buy a bigger model.
    """
    router = router or Router()
    d = decision or router.route(task, **route_kwargs)
    rungs: List[Tuple[str, str]] = [(d.tier, d.effort)] + list(d.ladder)
    if verify is None:
        rungs = rungs[:1]
    attempts: List[Attempt] = []
    total, known, feedback, stopped = 0.0, True, None, "exhausted"
    for n, (tier, effort) in enumerate(rungs[:max(1, max_attempts)], 1):
        att, res = _attempt(call, n, tier, router.policy.model_id(tier), effort, task, feedback)
        ok, fb = _check(verify, res.text)
        att.ok, att.feedback = ok, fb
        attempts.append(att)
        if att.cost_usd is None:
            known = False
        else:
            total += att.cost_usd
        if on_attempt:
            on_attempt(att)
        if ok:
            stopped = "passed" if verify is not None else "unverified"
            return RunResult(True, res.text, attempts, total, known, d, stopped)
        feedback = fb
        if budget_usd is not None and total >= budget_usd:
            stopped = "budget"
            break
    return RunResult(False, attempts[-1].output if attempts else "", attempts, total, known, d, stopped)


def run_batch(
    items: Sequence[Any],
    call: Call,
    verify: Callable[[Any, str], Union[bool, Tuple[bool, Optional[str]]]],
    *,
    first: Optional[Tuple[str, str]] = ("opus", "low"),
    retry: Optional[Tuple[str, str]] = ("opus", "high"),
    prompt: Callable[[Any], str] = str,
    router: Optional[Router] = None,
    max_workers: int = 1,
) -> BatchResult:
    """Cheap-first retry over a checkable batch.

    Every item runs at first (tier, effort); items that fail
    verify(item, output) run once more at retry. With first=None
    each item is routed on its own and retried one rung up its ladder.

    This is the cheapest policy on Anthropic's published curve for checkable
    work, but it is a cost saving, not a quality gain, and each first-pass
    failure costs two runs of wall-clock time.
    """
    router = router or Router()
    pol = router.policy

    def one(item: Any) -> RunResult:
        text = prompt(item)
        d: Optional[Decision] = None
        if first is None:
            d = router.route(text)
            plan = [(d.tier, d.effort)] + (list(d.ladder[:1]) if retry is None else [retry])
        else:
            plan = [first] + ([retry] if retry else [])
        attempts: List[Attempt] = []
        total, known, feedback = 0.0, True, None
        for n, (tier, effort) in enumerate(plan, 1):
            att, res = _attempt(call, n, tier, pol.model_id(tier), effort, text, feedback)
            ok, fb = _check(verify, item, res.text)
            att.ok, att.feedback = ok, fb
            attempts.append(att)
            if att.cost_usd is None:
                known = False
            else:
                total += att.cost_usd
            if ok:
                return RunResult(True, res.text, attempts, total, known, d, "passed")
            feedback = fb
        return RunResult(False, attempts[-1].output, attempts, total, known, d, "exhausted")

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(one, items))
    else:
        results = [one(i) for i in items]
    return BatchResult(results)
