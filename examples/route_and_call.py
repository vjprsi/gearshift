"""Route a task, then call the model the router picked.

    pip install anthropic
    export ANTHROPIC_API_KEY=...
    python examples/route_and_call.py "Classify this ticket as billing, login or other: 'I was charged twice'"
"""
import sys

import anthropic

from gearshift import route

task = " ".join(sys.argv[1:]) or "Classify this ticket as billing, login or other: 'I was charged twice'"
decision = route(task)
print(decision.explain(), "\n")

client = anthropic.Anthropic()
with client.messages.stream(
    max_tokens=16000,
    messages=[{"role": "user", "content": task}],
    **decision.api_params(),   # {"model": ..., "output_config": {"effort": ...}}
) as stream:
    message = stream.get_final_message()

print("".join(b.text for b in message.content if b.type == "text"))
