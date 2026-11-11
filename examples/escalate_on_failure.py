"""Start cheap, climb the ladder only when a check fails.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python examples/escalate_on_failure.py
"""
import json

from gearshift import run
from gearshift.anthropic_adapter import make_call

TASK = (
    "Extract the invoice number, total and currency from the text below. "
    "Reply with JSON only, with keys invoice_number, total (a number) and currency.\n\n"
    "FACTURA No. FV-20391 ... Total a pagar: $ 1.284.500,00 COP"
)


def verify(output: str):
    """Return (ok, feedback). The feedback goes to the next attempt."""
    try:
        data = json.loads(output[output.index("{"): output.rindex("}") + 1])
    except ValueError:
        return False, "the reply was not valid JSON"
    if data.get("invoice_number") != "FV-20391":
        return False, "invoice_number is wrong"
    if data.get("total") != 1284500:
        return False, "total should be the number 1284500 (the text uses . for thousands and , for decimals)"
    return data.get("currency") == "COP", "currency should be COP"


result = run(TASK, make_call(), verify, max_attempts=3, budget_usd=0.25)

for a in result.attempts:
    print("attempt %d  %s@%s  %s  $%.5f" % (a.n, a.tier, a.effort, "pass" if a.ok else "fail: %s" % a.feedback, a.cost_usd or 0))
print("\nfinished:", result.ok, "  total: $%.5f" % result.cost_usd)
print(result.output)
