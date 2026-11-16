"""Grade an agent's decision log against the policy."""

from __future__ import annotations

import itertools
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .analysis import Evaluator
from .model import Policy, fmt_number

DECISION_KEYS = ("decision", "action", "output", "agent_decision")


@dataclass
class GradedRecord:
    id: str
    input: Dict[str, Any]
    decision: Optional[str]
    expected: Any
    verdict: str  # correct | wrong | silent | ambiguous | invalid
    rules: List[str] = field(default_factory=list)
    problem: str = ""


@dataclass
class GradeReport:
    records: List[GradedRecord]
    rules_total: int
    rules_exercised: List[str]
    boundaries_total: int
    boundaries_exercised: int

    @property
    def counts(self) -> Dict[str, int]:
        c = Counter(r.verdict for r in self.records)
        return {k: c.get(k, 0) for k in ("correct", "wrong", "ambiguous", "silent", "invalid")}

    @property
    def accuracy(self) -> float:
        c = self.counts
        graded = c["correct"] + c["wrong"]
        return c["correct"] / graded if graded else 0.0

    def confusion(self) -> Dict[str, int]:
        c = Counter(f"{r.expected} -> {r.decision}" for r in self.records if r.verdict == "wrong")
        return dict(c.most_common())

    def by_rule(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0})
        for r in self.records:
            if r.verdict in ("correct", "wrong"):
                for rid in r.rules or ["(default)"]:
                    out[rid][r.verdict] += 1
        return dict(out)

    def to_dict(self) -> dict:
        return {
            "counts": self.counts,
            "accuracy": round(self.accuracy, 4),
            "confusion": self.confusion(),
            "by_rule": self.by_rule(),
            "coverage": {
                "rules_exercised": len(self.rules_exercised),
                "rules_total": self.rules_total,
                "boundaries_exercised": self.boundaries_exercised,
                "boundaries_total": self.boundaries_total,
            },
            "records": [r.__dict__ for r in self.records if r.verdict != "correct"],
        }


def load_log(path: str) -> List[dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, list) else data.get("records", [])
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _split(record: dict, policy: Policy) -> Tuple[Dict[str, Any], Optional[str]]:
    inputs = record.get("input")
    if not isinstance(inputs, dict):
        inputs = {k: v for k, v in record.items() if k in policy.fields}
    decision = next((record[k] for k in DECISION_KEYS if k in record), None)
    return inputs, (str(decision).strip() if decision is not None else None)


def decision_boundaries(policy: Policy) -> List[Tuple[str, int, int]]:
    """Numeric thresholds where the decision changes in at least one context: (field, upper, lower) units."""
    ev = Evaluator(policy)
    out = []
    for fi, f in enumerate(ev.fields):
        if not f.numeric:
            continue
        for i in range(f.atom_count() - 1):
            others = [range(g.atom_count()) if j != fi else [i] for j, g in enumerate(ev.fields)]
            for cell in itertools.product(*others):
                nxt = cell[:fi] + (i + 1,) + cell[fi + 1:]
                if ev.outcome_key(cell) != ev.outcome_key(nxt):
                    out.append((f.name, f.atom_bounds(i)[1], f.atom_bounds(i + 1)[0]))
                    break
    return out


def grade(policy: Policy, records: List[dict]) -> GradeReport:
    lookup = {d.lower(): d for d in policy.decisions}
    graded: List[GradedRecord] = []
    exercised = set()
    for i, rec in enumerate(records, 1):
        rid = str(rec.get("id", f"#{i}"))
        inputs, decision = _split(rec, policy)
        problems = policy.validate_input(inputs)
        if decision is None:
            problems.append("no decision field (use one of: " + ", ".join(DECISION_KEYS) + ")")
        if problems:
            graded.append(GradedRecord(rid, inputs, decision, None, "invalid", problem="; ".join(problems)))
            continue
        decision = lookup.get(decision.lower(), decision)
        status, decisions, winners, _ = policy.decide(inputs)
        rules = [r.id for r in winners]
        exercised.update(rules)
        if status in ("decided", "default"):
            verdict = "correct" if decision == decisions[0] else "wrong"
            expected: Any = decisions[0]
        elif status == "gap":
            verdict, expected = "silent", None
        else:
            verdict = "ambiguous" if decision in decisions else "wrong"
            expected = decisions
        graded.append(GradedRecord(rid, inputs, decision, expected, verdict, rules))

    bounds = decision_boundaries(policy)
    hit = 0
    for name, upper, lower in bounds:
        f = policy.fields[name]
        vals = {f.from_units(upper), f.from_units(lower)}
        if any(r.verdict != "invalid" and f.to_units(r.input[name]) * f.step in vals for r in graded):
            hit += 1
    return GradeReport(graded, len(policy.rules), sorted(exercised), len(bounds), hit)


def describe_boundary(policy: Policy, b: Tuple[str, int, int]) -> str:
    f = policy.fields[b[0]]
    return f"{f.name} {fmt_number(f.from_units(b[1]))}|{fmt_number(f.from_units(b[2]))}"
