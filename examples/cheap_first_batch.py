"""Cheap-first retry over a checkable batch: everything at low effort, then
only the failures again at high.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python examples/cheap_first_batch.py
"""
from gearshift import run_batch
from gearshift.anthropic_adapter import make_call

LABELS = ("billing", "login", "shipping", "other")
TICKETS = [
    "I was charged twice for my subscription this month",
    "The reset password email never arrives",
    "My package says delivered but nothing is here",
    "Do you have an API for exporting my data?",
]


def prompt(ticket: str) -> str:
    return "Classify this support ticket as one of: %s. Reply with the label only.\n\n%s" % (", ".join(LABELS), ticket)


def verify(ticket: str, output: str):
    label = output.strip().lower()
    return label in LABELS, "reply with exactly one of: %s" % ", ".join(LABELS)


batch = run_batch(TICKETS, make_call(), verify, first=("haiku", "low"), retry=("haiku", "high"),
                  prompt=prompt, max_workers=4)
for ticket, r in zip(TICKETS, batch.results):
    print("%-10s %d attempt(s)  %s" % (r.output.strip(), len(r.attempts), ticket))
print(batch.summary())
