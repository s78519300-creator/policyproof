"""Exhaustive analysis of a policy over its whole input space."""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .model import Policy, Rule

MAX_CELLS = 5_000_000
Cell = Tuple[int, ...]
Region = Tuple[FrozenSet[int], ...]


class TooManyCells(ValueError):
    pass


@dataclass
class Finding:
    severity: str  # error | warning | info
    code: str
    title: str
    detail: str = ""
    rules: List[str] = field(default_factory=list)
    region: str = ""
    example: Optional[Dict] = None
    share: float = 0.0  # fraction of the input space affected

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", [])}


@dataclass
class Analysis:
    policy: Policy
    cells: int
    share: Dict[str, float]  # decided / default / gap / conflict
    case_counts: Dict[str, int]
    rule_stats: Dict[str, Dict[str, float]]
    findings: List[Finding]

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def to_dict(self) -> dict:
        return {
            "policy": self.policy.name,
            "strategy": self.policy.strategy,
            "cells": self.cells,
            "case_counts": self.case_counts,
            "share": {k: round(v, 6) for k, v in self.share.items()},
            "rules": self.rule_stats,
            "findings": [f.to_dict() for f in self.findings],
        }


class Evaluator:
    """Fast cell evaluation with per-field rule bitmasks."""

    def __init__(self, policy: Policy):
        self.policy = policy
        self.fields = policy.field_list
        self.rules = policy.rules
        all_mask = (1 << len(self.rules)) - 1
        self.masks: List[List[int]] = []
        for f in self.fields:
            per_atom = []
            for a in range(f.atom_count()):
                rep = f.atom_rep(a)
                m = 0
                for i, r in enumerate(self.rules):
                    conds = [c for c in r.conditions if c.field == f.name]
                    if all(c.holds(f, rep) for c in conds):
                        m |= 1 << i
                per_atom.append(m & all_mask)
            self.masks.append(per_atom)
        self.domain = [f.domain_size() for f in self.fields]

    def cell_count(self) -> int:
        n = 1
        for f in self.fields:
            n *= f.atom_count()
        return n

    def cells(self) -> Iterable[Cell]:
        return itertools.product(*(range(f.atom_count()) for f in self.fields))

    def matching(self, cell: Cell) -> List[Rule]:
        m = -1
        for fi, a in enumerate(cell):
            m &= self.masks[fi][a]
            if not m:
                return []
        return [r for i, r in enumerate(self.rules) if m >> i & 1]

    def resolve(self, cell: Cell):
        return self.policy._resolve(self.matching(cell))

    def weight(self, cell: Cell) -> Fraction:
        w = Fraction(1)
        for fi, a in enumerate(cell):
            w *= Fraction(self.fields[fi].atom_size(a), self.domain[fi])
        return w

    def example(self, cell: Cell) -> Dict:
        return {f.name: f.atom_rep(a) for f, a in zip(self.fields, cell)}

    def outcome_key(self, cell: Cell) -> Tuple[str, Tuple[str, ...]]:
        status, decisions, _, _ = self.resolve(cell)
        return status, tuple(decisions)


def merge_regions(cells: Iterable[Cell], nfields: int) -> List[Region]:
    """Merge cells into a small set of boxes (products of atom sets) covering exactly them."""
    regions = {tuple(frozenset([a]) for a in c) for c in cells}
    changed = True
    while changed:
        changed = False
        for fi in range(nfields):
            groups: Dict[tuple, set] = defaultdict(set)
            for r in regions:
                groups[r[:fi] + r[fi + 1:]] |= r[fi]
            merged = {k[:fi] + (frozenset(v),) + k[fi:] for k, v in groups.items()}
            if len(merged) < len(regions):
                changed = True
            regions = merged
    return sorted(regions, key=lambda r: tuple(sorted(s) for s in r))


def describe_region(policy: Policy, region: Region) -> str:
    parts = [f.describe(atoms) for f, atoms in zip(policy.field_list, region)]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else "every input"


def region_share(ev: Evaluator, region: Region) -> Fraction:
    w = Fraction(1)
    for fi, atoms in enumerate(region):
        f = ev.fields[fi]
        w *= Fraction(sum(f.atom_size(a) for a in atoms), ev.domain[fi])
    return w


def typical_value(f, atom: int):
    """Middle of a numeric atom (a realistic example), or the enum/bool value."""
    if not f.numeric:
        return f.values[atom]
    lo, hi = f.atom_bounds(atom)
    return f.value_of((lo + hi) // 2)


def region_example(ev: Evaluator, region: Region) -> Dict:
    return {f.name: typical_value(f, min(atoms)) for f, atoms in zip(ev.fields, region)}


def analyze(policy: Policy, max_regions: int = 25) -> Analysis:
    ev = Evaluator(policy)
    total = ev.cell_count()
    if total > MAX_CELLS:
        raise TooManyCells(
            f"the policy splits into {total:,} cells (limit {MAX_CELLS:,}); split it into smaller policies "
            f"or coarsen numeric steps")

    share = defaultdict(Fraction)
    matched = defaultdict(int)
    won = defaultdict(Fraction)
    gaps: List[Cell] = []
    conflicts: Dict[Tuple[Rule, Rule], List[Cell]] = defaultdict(list)
    counts = defaultdict(int)
    redundant: Dict[Tuple[str, ...], List[Cell]] = defaultdict(list)
    defaults: List[Cell] = []
    produced = set()

    for cell in ev.cells():
        w = ev.weight(cell)
        status, decisions, winners, matching = ev.resolve(cell)
        share[status] += w
        counts[status] += 1
        for r in matching:
            matched[r.id] += 1
        if status == "decided":
            produced.update(decisions)
            for r in winners:
                won[r.id] += w / len(winners)
            if policy.strategy != "first_match" and len(winners) > 1:
                redundant[tuple(r.id for r in winners)].append(cell)
        elif status == "default":
            produced.update(decisions)
            defaults.append(cell)
        elif status == "gap":
            gaps.append(cell)
        else:
            for a, b in itertools.combinations(winners, 2):
                if a.decision != b.decision:
                    conflicts[(a, b)].append(cell)

    findings: List[Finding] = []
    n = len(ev.fields)

    def add_regions(cells, severity, code, title_fn, detail, rules=()):
        regions = merge_regions(cells, n)
        regions.sort(key=lambda r: -region_share(ev, r))
        for r in regions[:max_regions]:
            findings.append(Finding(
                severity, code, title_fn(r), detail, list(rules),
                describe_region(policy, r), region_example(ev, r), float(region_share(ev, r))))
        if len(regions) > max_regions:
            rest = sum(region_share(ev, r) for r in regions[max_regions:])
            findings.append(Finding(severity, code, f"... and {len(regions) - max_regions} more regions",
                                    detail, list(rules), share=float(rest)))

    if gaps:
        add_regions(gaps, "error", "gap", lambda r: "No rule decides these inputs",
                    "The policy is silent here, so an agent has to guess. Add a rule or a default.")
    if defaults:
        add_regions(defaults, "info", "default", lambda r: f"Only the default '{policy.default}' applies",
                    "No rule matches; the catch-all default decides. Check this is intended.")
    for (ra, rb), cells in sorted(conflicts.items(), key=lambda kv: (kv[0][0].order, kv[0][1].order)):
        add_regions(cells, "error", "conflict",
                    lambda r, a=ra, b=rb: f"{a.id} says {a.decision}, {b.id} says {b.decision}",
                    "Both rules apply here. Decide which one wins (add a priority or narrow a condition).",
                    (ra.id, rb.id))
    for rule_ids, cells in sorted(redundant.items()):
        cov = sum((ev.weight(c) for c in cells), Fraction(0))
        findings.append(Finding("info", "overlap", f"Rules {', '.join(rule_ids)} overlap with the same decision",
                                "Harmless today, but an edit to one of them will silently create a conflict.",
                                list(rule_ids), share=float(cov)))

    for r in policy.rules:
        if matched[r.id] == 0:
            findings.append(Finding("warning", "dead-rule", f"Rule {r.id} can never apply",
                                    "Its conditions contradict each other or the field ranges.", [r.id],
                                    r.text()))
        elif won[r.id] == 0 and policy.strategy in ("first_match", "priority"):
            findings.append(Finding("warning", "shadowed-rule", f"Rule {r.id} is always overridden",
                                    "Every input it matches is decided by an earlier or higher-priority rule.",
                                    [r.id], r.text()))
    used = {c.field for r in policy.rules for c in r.conditions}
    for f in policy.field_list:
        if f.name not in used:
            findings.append(Finding("info", "unused-field", f"Field '{f.name}' is never used by any rule"))
    for d in policy.decisions:
        if d not in produced:
            findings.append(Finding("info", "unused-decision", f"Decision '{d}' is never the outcome"))

    order = {"error": 0, "warning": 1, "info": 2}
    code_order = {"gap": 0, "conflict": 1}
    findings.sort(key=lambda f: (order[f.severity], code_order.get(f.code, 2), -f.share))
    rule_stats = {r.id: {"cells_matched": matched[r.id], "share_decided": round(float(won[r.id]), 6)}
                  for r in policy.rules}
    shares = {k: float(share.get(k, 0)) for k in ("decided", "default", "gap", "conflict")}
    case_counts = {k: counts.get(k, 0) for k in ("decided", "default", "gap", "conflict")}
    return Analysis(policy, total, shares, case_counts, rule_stats, findings)
