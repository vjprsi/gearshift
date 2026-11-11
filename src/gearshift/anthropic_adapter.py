"""Glue for the official Anthropic Python SDK (optional: pip install anthropic).

    call = make_call(anthropic.Anthropic())
    result = run("Extract the invoice total as JSON: ...", call, verify=my_check)

Always streams. With a big max_tokens at high effort a non-streaming request
can run past what the SDK allows.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .execute import CallResult
from .policy import EFFORTS, effort_rank
from .pricing import price_table, resolve_model

# max_tokens caps thinking + reply together, so give more room at higher effort
DEFAULT_MAX_TOKENS: Dict[str, int] = {"low": 8000, "medium": 16000, "high": 32000, "xhigh": 64000, "max": 64000}

FEEDBACK_TEMPLATE = (
    "{prompt}\n\nA previous attempt at this task was rejected by an automatic check. "
    "The check reported:\n{feedback}\n\nProduce a corrected answer."
)


def supported_effort(model: str, effort: str) -> Optional[str]:
    """The effort to send for model: the requested level if supported,
    else the highest supported level below it, else None (omit the
    parameter; the model predates effort)."""
    key = resolve_model(model)
    if key is None:
        return effort
    levels = price_table()["models"][key].get("efforts", EFFORTS)
    if not levels:
        return None
    if effort in levels:
        return effort
    lower = [e for e in levels if effort_rank(e) < effort_rank(effort)]
    return lower[-1] if lower else levels[0]


def build_request(model: str, effort: str, prompt: str, feedback: Optional[str] = None, *,
                  system: Optional[str] = None, max_tokens: Optional[int] = None,
                  extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Keyword arguments for client.messages.stream / .create."""
    content = FEEDBACK_TEMPLATE.format(prompt=prompt, feedback=feedback) if feedback else prompt
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS.get(effort, 16000),
        "messages": [{"role": "user", "content": content}],
    }
    eff = supported_effort(model, effort)
    if eff is not None:
        kwargs["output_config"] = {"effort": eff}
    if system:
        kwargs["system"] = system
    if extra:
        kwargs.update(extra)
    return kwargs


def make_call(client: Any = None, *, system: Optional[str] = None, max_tokens: Optional[int] = None,
              extra: Optional[Dict[str, Any]] = None) -> Callable[..., CallResult]:
    """Build a call for run() / run_batch()."""
    if client is None:
        try:
            import anthropic  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError("the Anthropic SDK is not installed: pip install anthropic") from exc
        client = anthropic.Anthropic()

    def call(model: str, effort: str, prompt: str, feedback: Optional[str] = None) -> CallResult:
        kwargs = build_request(model, effort, prompt, feedback, system=system,
                               max_tokens=max_tokens, extra=extra)
        with client.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text")
        return CallResult(text=text, usage=message.usage, raw=message)

    return call
