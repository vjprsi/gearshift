"""List-price cost of one request from its usage block.

Each token kind has its own rate: input, 5m / 1h cache writes (1.25x / 2x
input), cache reads, output.

TODO: fast mode and the US-only inference multiplier are not handled, and
obviously neither are negotiated rates. Good enough for comparing configs.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .policy import load_models

_models: Optional[dict] = None


def _table() -> dict:
    global _models
    if _models is None:
        _models = load_models()
    return _models


def resolve_model(model: str) -> Optional[str]:
    """Map any spelling of a model (tier alias, dated ID, provider-prefixed
    ID, [1m] suffix) to a key in the price table."""
    if not model:
        return None
    t = _table()
    m = model.strip().lower()
    if m in t["tiers"]:
        return t["tiers"][m]
    m = m.replace("[1m]", "")
    if m in t["models"]:
        return m
    for key in sorted(t["models"], key=len, reverse=True):
        if key in m:
            return key
    return None


def tier_of(model: str) -> Optional[str]:
    """Tier (haiku/sonnet/opus/fable) for any model spelling."""
    if not model:
        return None
    key = resolve_model(model)
    if key:
        return _table()["models"][key]["tier"]
    m = model.lower()
    for tier in ("haiku", "sonnet", "opus", "fable"):
        if tier in m:
            return tier
    return "fable" if "mythos" in m else None


def _get(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    v = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
    return int(v or 0)


def usage_to_dict(usage: Any) -> Dict[str, int]:
    """Normalize an SDK Usage object or a raw dict to flat token counts."""
    cc = usage.get("cache_creation") if isinstance(usage, Mapping) else getattr(usage, "cache_creation", None)
    w5 = _get(cc, "ephemeral_5m_input_tokens")
    w1 = _get(cc, "ephemeral_1h_input_tokens")
    total_writes = _get(usage, "cache_creation_input_tokens")
    if w5 + w1 == 0 and total_writes:
        w5 = total_writes  # no TTL breakdown: assume the 5-minute default
    return {
        "input_tokens": _get(usage, "input_tokens"),
        "output_tokens": _get(usage, "output_tokens"),
        "cache_read_input_tokens": _get(usage, "cache_read_input_tokens"),
        "cache_write_5m_tokens": w5,
        "cache_write_1h_tokens": w1,
    }


def add_usage(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in set(a) | set(b)}


def cost_usd(usage: Any, model: str, *, batch: bool = False) -> Optional[float]:
    """List-price cost in USD of ONE request. None for an unknown model.

    Pass one request at a time: Haiku 5.5's long-prompt rate is decided per
    request, so summed usage across requests cannot be priced exactly.
    """
    key = resolve_model(model)
    if key is None:
        return None
    t = _table()
    info = t["models"][key]
    u = usage if "cache_write_5m_tokens" in (usage if isinstance(usage, Mapping) else {}) else usage_to_dict(usage)
    rate_in, rate_out, rate_read = info["input"], info["output"], info["cache_read"]
    lp = info.get("long_prompt")
    if lp:
        prompt_tokens = (u["input_tokens"] + u["cache_read_input_tokens"]
                         + u["cache_write_5m_tokens"] + u["cache_write_1h_tokens"])
        if prompt_tokens > lp["over_tokens"]:
            rate_in, rate_out, rate_read = lp["input"], lp["output"], lp["cache_read"]
    mult = t["cache_write_multipliers"]
    total = (
        u["input_tokens"] * rate_in
        + u["cache_write_5m_tokens"] * rate_in * mult["5m"]
        + u["cache_write_1h_tokens"] * rate_in * mult["1h"]
        + u["cache_read_input_tokens"] * rate_read
        + u["output_tokens"] * rate_out
    ) / 1_000_000
    if batch:
        total *= t["batch_discount"]
    return total


def price_table() -> dict:
    return _table()
