"""Build examples/agent_decisions.jsonl: a SYNTHETIC decision log for the grading demo.

No language model was run. The "agent" below follows the fixed refund policy but
makes three mistakes that are typical when a model reads a policy written in prose:

1. Off-by-one on the gold window: treats "45 days" as "under 45 days".
2. Reads "INR 50,000 or more" as "more than INR 50,000".
3. Forgets the abuse check when the item is damaged.

Run:  python examples/simulate_agent.py
"""

import json
import random
from pathlib import Path

from policyproof import generate, load_policy

HERE = Path(__file__).parent
policy = load_policy(HERE / "refund_policy_fixed.json")


def buggy_agent(x):
    if x["refunds_last_90_days"] >= 3 and not x["item_damaged"]:      # mistake 3
        return "escalate"
    if x["category"] == "furniture" and x["item_damaged"] and x["days_since_delivery"] <= 3:
        return "replace"
    if not x["item_damaged"] and x["days_since_delivery"] <= 30 and x["order_value"] > 50000:  # mistake 2
        return "escalate"
    if x["category"] == "grocery" and not x["item_damaged"]:
        return "reject"
    if x["category"] == "furniture" and not x["item_damaged"] and x["order_value"] < 50000:
        return "reject"
    if x["customer_tier"] == "gold" and not x["item_damaged"] and x["days_since_delivery"] < 45:  # mistake 1
        return "refund"
    if x["days_since_delivery"] > 30:
        return "reject"
    if x["item_damaged"]:
        return "refund" if x["days_since_delivery"] <= 7 else "escalate"
    return "refund"


rng = random.Random(7)
inputs = [c.input for c in generate(policy) if c.expected is not None]
fields = policy.fields
while len(inputs) < 80:
    inputs.append({
        "order_value": rng.choice([799, 2499, 12999, 49999, 50000, 64999, 149999]),
        "days_since_delivery": rng.choice([0, 2, 5, 7, 8, 15, 30, 31, 44, 45, 46, 90]),
        "category": rng.choice(fields["category"].values),
        "item_damaged": rng.random() < 0.35,
        "customer_tier": rng.choice(["regular", "regular", "gold"]),
        "refunds_last_90_days": rng.choice([0, 0, 1, 2, 3, 5]),
    })

with open(HERE / "agent_decisions.jsonl", "w", encoding="utf-8") as fh:
    for i, x in enumerate(inputs, 1):
        fh.write(json.dumps({"id": f"ticket-{i:03d}", "input": x, "decision": buggy_agent(x)}) + "\n")
print(f"wrote {len(inputs)} synthetic decisions")
