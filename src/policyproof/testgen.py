"""Generate test scenarios from a policy: rule coverage plus decision boundaries."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .analysis import Evaluator, describe_region, merge_regions, region_example, typical_value
from .model import Field, Policy, fmt_number

LEVELS = ("rules", "boundaries", "exhaustive")


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class

    id: str
    kind: str  # rule | boundary | cell | gap | conflict
    input: Dict[str, Any]
    expected: Any  # str, None (policy silent) or list (conflict)
    rules: List[str]
    note: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "input": self.input, "expected": self.expected,
                "rules": self.rules, "note": self.note}


def _typical(f: Field, atom: int) -> Any:
    return typical_value(f, atom)


def _expected(status: str, decisions: List[str]):
    if status in ("decided", "default"):
        return decisions[0]
    if status == "gap":
        return None
    return decisions


def generate(policy: Policy, level: str = "boundaries", per_boundary: int = 3,
             include_issues: bool = True) -> List[TestCase]:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    ev = Evaluator(policy)
    fields = ev.fields
    cases: List[TestCase] = []
    seen = set()

    def add(kind, inputs, note=""):
        key = tuple(inputs[f.name] for f in fields)
        if key in seen:
            return
        seen.add(key)
        status, decisions, winners, _ = policy.decide(inputs)
        if kind in ("rule", "boundary", "cell") and status in ("gap", "conflict") and not include_issues:
            return
        cases.append(TestCase(f"T{len(cases) + 1:04d}", kind, dict(inputs), _expected(status, decisions),
                              [r.id for r in winners], note))

    def typical_inputs(cell):
        return {f.name: _typical(f, a) for f, a in zip(fields, cell)}

    all_cells = list(ev.cells())

    # 1. one representative per winning rule
    for rule in policy.rules:
        for cell in all_cells:
            status, _, winners, _ = ev.resolve(cell)
            if status == "decided" and rule in winners:
                add("rule", typical_inputs(cell), f"exercises {rule.id}")
                break

    # 2. decision boundaries on numeric fields
    if level in ("boundaries", "exhaustive"):
        for fi, f in enumerate(fields):
            if not f.numeric:
                continue
            for i in range(f.atom_count() - 1):
                upper = f.atom_bounds(i)[1]
                lower = f.atom_bounds(i + 1)[0]
                found = 0
                others = [range(g.atom_count()) if j != fi else [i] for j, g in enumerate(fields)]
                for cell in itertools.product(*others):
                    nxt = cell[:fi] + (i + 1,) + cell[fi + 1:]
                    a, b = ev.outcome_key(cell), ev.outcome_key(nxt)
                    if a == b:
                        continue
                    base = typical_inputs(cell)
                    left, right = dict(base), dict(base)
                    left[f.name], right[f.name] = f.value_of(upper), f.value_of(lower)
                    note = (f"{f.name} {fmt_number(f.from_units(upper))} -> {fmt_number(f.from_units(lower))} "
                            f"changes {'/'.join(a[1]) or a[0]} to {'/'.join(b[1]) or b[0]}")
                    add("boundary", left, note)
                    add("boundary", right, note)
                    found += 1
                    if found >= per_boundary:
                        break

    # 3. every cell
    if level == "exhaustive":
        for cell in all_cells:
            add("cell", typical_inputs(cell))

    # 4. policy issues, one example per region, for the policy owner
    if include_issues:
        n = len(fields)
        gap_cells = [c for c in all_cells if ev.resolve(c)[0] == "gap"]
        for region in merge_regions(gap_cells, n):
            add("gap", region_example(ev, region), "policy is silent: " + describe_region(policy, region))
        conflict_cells = [c for c in all_cells if ev.resolve(c)[0] == "conflict"]
        for region in merge_regions(conflict_cells, n):
            add("conflict", region_example(ev, region), "rules disagree: " + describe_region(policy, region))

    return cases
