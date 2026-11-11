"""gearshift - model + effort routing for Claude.

    from gearshift import route
    d = route("Classify these 5,000 support tickets by product area, as JSON")
    d.tier, d.effort      # ('haiku', 'low')
    d.api_params()        # {'model': 'claude-haiku-5-5', 'output_config': {'effort': 'low'}}
"""

from .classify import Classification, classify
from .execute import Attempt, BatchResult, CallResult, RunResult, run, run_batch
from .policy import EFFORTS, TIERS, Policy, default_policy, load_policy
from .pricing import cost_usd, resolve_model, tier_of
from .router import Decision, Router, route

__version__ = "1.4.0"

__all__ = [
    "Attempt", "BatchResult", "CallResult", "Classification", "Decision", "EFFORTS",
    "Policy", "Router", "RunResult", "TIERS", "classify", "cost_usd", "default_policy",
    "load_policy", "resolve_model", "route", "run", "run_batch", "tier_of", "__version__",
]
