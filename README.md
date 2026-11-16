# policyproof

**Find the holes in the policy your AI agent follows, before your customers do.**

Companies are handing refunds, claims, credit limits and returns to AI agents, and every one of those agents follows a written business policy. When the agent makes a strange call, the policy itself is often to blame: it says nothing about the case, or two of its rules contradict each other. Then the agent has to guess, and nobody can say whether it was "wrong".

`policyproof` turns the policy into rules and checks **every possible case**:

- **Lint** finds every gap (inputs no rule decides), every conflict (rules that give different answers for the same input), and every rule that is dead or always overridden. Each finding comes with the exact region where it happens and a ready-to-use example.
- **Tests** generates scenarios that cover every rule and both sides of every threshold where the decision flips (49,999 vs 50,000; day 45 vs day 46). This is where agents slip.
- **Grade** scores an agent's decision log against the policy: accuracy, the rules it misreads, decisions made where the policy is silent, and which thresholds the log never tested.
- **Explain** shows why one input gets its decision, and why every other rule did not apply.

The analysis is exact, not sampled. Every numeric field is split at the thresholds the rules use, so a policy with millions of possible inputs comes down to a few hundred distinct cases, and all of them are checked. Values are exact fractions, so `0.1` is never off by a floating-point hair. Pure Python standard library, no model, no API key.

## The example: a refund policy that looks fine

[`examples/refund_policy.json`](examples/refund_policy.json) is a nine-rule e-commerce refund policy, the kind a support team writes in a doc ("damaged items within 7 days get a refund", "gold members get 45 days", "3+ refunds in 90 days is possible abuse").

```bash
pip install -e ".[dev]"
policyproof lint examples/refund_policy.json --quiet
```

```text
Refund policy v3 (as written by Support Ops): 9 rules, 6 fields, strategy 'unique'
  320 distinct cases: 139 decided, 19 silent, 162 conflicting
  share of the declared input space: 17.2% decided, 0.5% silent, 82.4% conflicting
  13 error(s), 1 warning(s), 4 note(s)

ERROR   gap             No rule decides these inputs  [0.4% of inputs]
                        where: days_since_delivery 8..30, item_damaged is true, refunds_last_90_days <= 2
                        e.g.   order_value=24999, days_since_delivery=19, category="electronics", item_damaged=true, ...
ERROR   gap             No rule decides these inputs  [<0.1% of inputs]
                        where: order_value <= 49999 INR, days_since_delivery <= 30, category = furniture, item_damaged is false, ...
ERROR   conflict        R5 says reject, R6 says escalate  [78.5% of inputs]
                        where: days_since_delivery >= 31, refunds_last_90_days >= 3
ERROR   conflict        R5 says reject, R7 says refund  [1.0% of inputs]
                        where: days_since_delivery 31..45, item_damaged is false, customer_tier = gold
ERROR   conflict        R2 says refund, R8 says replace  [0.1% of inputs]
                        where: days_since_delivery <= 3, category = furniture, item_damaged is true
...
WARNING dead-rule       Rule R9 can never apply
                        where: R9: if order_value < 0 then reject
```

In plain words, policyproof found:

- **A damaged item reported on day 8 to 30 has no answer.** The damage rule stops at day 7 and every other rule needs an undamaged item.
- **Undamaged furniture under INR 50,000 has no answer.** Nobody wrote a rule for it.
- **Gold members on day 31 to 45 get both "refund" and "reject".** The 45-day perk and the 30-day limit were never reconciled.
- **Damaged furniture in the first 3 days gets both "refund" and "replace".**
- **The gift-card rule is dead.** It checks `order_value < 0`, which can never happen.

An agent following this policy has to guess in 181 of the 320 distinct situations.

[`examples/refund_policy_fixed.json`](examples/refund_policy_fixed.json) is the same policy after review: rule priorities plus two new rules. It lints clean: 320 of 320 cases decided, no conflicts.

## Generate tests that hit the thresholds

```bash
policyproof tests examples/refund_policy_fixed.json -o tests.jsonl
# 46 test cases: 10 rule, 36 boundary
```

```json
{"id": "T0011", "kind": "boundary", "input": {"order_value": 49999, "days_since_delivery": 1, "category": "electronics", ...}, "expected": "refund", "rules": ["R3"], "note": "order_value 49999 -> 50000 changes refund to escalate"}
{"id": "T0012", "kind": "boundary", "input": {"order_value": 50000, "days_since_delivery": 1, "category": "electronics", ...}, "expected": "escalate", "rules": ["R4"], "note": "order_value 49999 -> 50000 changes refund to escalate"}
```

Feed these to your agent or eval harness. `--level exhaustive` adds one case for every cell, and `--format csv` gives a sheet the policy owner can read. On a policy that still has problems, the gap and conflict examples are included with `expected: null` or a list of the competing answers, so they can go straight back to the policy owner.

## Grade what your agent actually decided

[`examples/agent_decisions.jsonl`](examples/agent_decisions.jsonl) is a **synthetic** log of 80 decisions, made by [`simulate_agent.py`](examples/simulate_agent.py) with three planted mistakes of the kind agents make when they read a policy written in prose. No language model was run.

```bash
policyproof grade examples/refund_policy_fixed.json examples/agent_decisions.jsonl --min-accuracy 0.95
```

```text
Refund policy v4 (after policyproof review): graded 80 agent decisions
  accuracy 83.8% on 80 decisions the policy defines (67 correct, 13 wrong)
  0 made where the policy is silent  |  0 where rules conflict  |  0 invalid records
  coverage: 10/10 rules, 6/6 decision thresholds tested

Rules the agent gets wrong (correct/total): R4 4/10, R7 3/7, R6 13/16

Mistakes (expected -> agent):
  escalate -> refund: 7
  refund -> reject: 4
  escalate -> reject: 2
```

It points to the three misread rules: R4 (INR 50,000 "or more"), R7 (the 45-day window) and R6 (the abuse check). The exit code is 1 because accuracy is under the 0.95 gate, so this can block a deployment in CI.

A log record is `{"id": ..., "input": {...}, "decision": "..."}`, or the input fields at the top level. `action` or `output` work as well as `decision`.

## Explain one decision

```bash
policyproof explain examples/refund_policy.json --input '{"order_value": 64999, "days_since_delivery": 40, "category": "electronics", "item_damaged": false, "customer_tier": "gold", "refunds_last_90_days": 1}'
```

```text
Decision: AMBIGUOUS. R5, R7 give refund vs reject

  -      R1 does not apply: fails category = grocery
  -      R4 does not apply: fails days_since_delivery <= 30
  CLASH  R5: if days_since_delivery > 30 then reject
  CLASH  R7: if customer_tier = gold and item_damaged = false and days_since_delivery <= 45 then refund
  ...
```

## Writing a policy

JSON (or TOML on Python 3.11+):

```json
{
  "name": "Refund policy",
  "strategy": "priority",
  "decisions": ["refund", "replace", "reject", "escalate"],
  "default": "escalate",
  "fields": {
    "order_value": {"type": "integer", "min": 0, "max": 200000, "unit": "INR"},
    "rate":        {"type": "number", "min": 0, "max": 1, "step": 0.01},
    "category":    {"type": "enum", "values": ["electronics", "fashion", "grocery"]},
    "item_damaged": {"type": "bool"}
  },
  "rules": [
    {"id": "R1", "priority": 10, "source": "2.1 of the policy doc",
     "when": {"category": "grocery", "item_damaged": false, "order_value": {"gte": 500, "lt": 5000}},
     "then": "reject"}
  ]
}
```

- **Conditions:** `eq`, `ne`, `in`, `not_in`, `lt`, `lte`, `gt`, `gte`, `between: [low, high]`. A bare value means `eq` and a list means `in`.
- **Strategies:** `unique` means overlapping rules must agree, which is the strictest and the default. `priority` means the highest priority wins and ties must agree. `first_match` means rule order wins.
- **`default`** is optional. Without it, uncovered inputs are reported as gaps. With it, they are listed as notes so you can confirm the fallback is intended.
- **`source`** links a rule back to the policy document clause it came from.

Exit codes: `0` clean, `1` errors found (or warnings with `--strict`, or accuracy below `--min-accuracy`), `2` invalid policy. All commands support `--format json` or `md`, and `-o file`.

## Use it from Python

```python
from policyproof import analyze, generate, grade, load_policy

policy = load_policy("refund_policy.json")
report = analyze(policy)
print(report.case_counts)                  # {'decided': 139, 'default': 0, 'gap': 19, 'conflict': 162}
cases = generate(policy, level="boundaries")
status, decisions, winners, _ = policy.decide(cases[0].input)
```

## How it works

1. Every numeric field is split into the coarsest pieces on which all rule conditions stay constant. For example, `days_since_delivery` becomes 0..3, 4..7, 8..30, 31..45 and 46..365.
2. The product of those pieces across fields gives the distinct cases. There are 320 for the example, against about 24.6 billion raw inputs, and every case is evaluated.
3. Findings are merged back into readable regions, and each region's share of the declared input space is reported. The shares assume every value in a field's declared range is equally likely, so read them as "how much of the rulebook" rather than "how many real tickets".

Research on agent evaluation has started to look at the same problem from the other side: when the policy is ambiguous, an agent can be marked wrong for a policy error. See, for example, *Policy Loopholes in Agent Evaluation* ([arXiv 2609.14400](https://arxiv.org/abs/2609.14400)). policyproof helps you fix the policy before you blame the agent.

## Limits

- Policies must be written as rules over typed fields. Turning a prose document into rules is still a human (or LLM-assisted) step, and the `source` field keeps that traceable.
- Conditions compare a field with constants. Comparing two fields (`refund_amount <= order_value`) is not supported yet.
- The analysis is exhaustive, so the number of distinct cases grows with the product of the pieces per field. The limit is 5 million; split very large policies by product line.

## License

MIT
