"""Policy model: typed input fields, rule conditions and exact atom partitions.

Every numeric field is put on a grid (its ``step``) and values are handled as
exact fractions, so a threshold like ``order_value < 5000`` is never off by a
floating-point hair. Each field is split into *atoms*: the coarsest pieces of
its domain on which every condition in the policy is constant. The product of
atoms over all fields gives *cells*, and every cell is decided the same way by
the policy, which is what makes exhaustive analysis possible.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

NUMERIC_OPS = {"eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "between"}
DISCRETE_OPS = {"eq", "ne", "in", "not_in"}
STRATEGIES = ("unique", "first_match", "priority")


class PolicyError(ValueError):
    """The policy file is malformed. ``problems`` lists every issue found."""

    def __init__(self, problems: List[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _frac(x: Any) -> Fraction:
    if isinstance(x, bool):
        raise TypeError("boolean is not a number")
    return Fraction(str(x))


def fmt_number(x: Fraction) -> str:
    if x.denominator == 1:
        return str(x.numerator)
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s


# --------------------------------------------------------------------------- fields


@dataclass
class Field:
    name: str
    type: str  # number | integer | enum | bool
    values: Tuple[Any, ...] = ()
    min: Optional[Fraction] = None
    max: Optional[Fraction] = None
    step: Fraction = Fraction(1)
    unit: str = ""
    # filled by Policy.build_atoms()
    starts: List[int] = field(default_factory=list)  # numeric: first grid unit of each atom

    @property
    def numeric(self) -> bool:
        return self.type in ("number", "integer")

    # grid helpers -------------------------------------------------------
    @property
    def umin(self) -> int:
        return math.ceil(self.min / self.step)

    @property
    def umax(self) -> int:
        return math.floor(self.max / self.step)

    def to_units(self, value: Any) -> Fraction:
        return _frac(value) / self.step

    def from_units(self, u: int) -> Fraction:
        return u * self.step

    def value_of(self, u: int) -> Union[int, float]:
        v = self.from_units(u)
        return int(v) if v.denominator == 1 else float(v)

    # atoms --------------------------------------------------------------
    def atom_count(self) -> int:
        return len(self.starts) if self.numeric else len(self.values)

    def atom_bounds(self, i: int) -> Tuple[int, int]:
        lo = self.starts[i]
        hi = self.starts[i + 1] - 1 if i + 1 < len(self.starts) else self.umax
        return lo, hi

    def atom_size(self, i: int) -> int:
        if not self.numeric:
            return 1
        lo, hi = self.atom_bounds(i)
        return hi - lo + 1

    def domain_size(self) -> int:
        return (self.umax - self.umin + 1) if self.numeric else len(self.values)

    def atom_rep(self, i: int) -> Any:
        """A representative value for atom ``i`` (its lower bound)."""
        if self.numeric:
            return self.value_of(self.atom_bounds(i)[0])
        return self.values[i]

    def atom_of(self, value: Any) -> int:
        """Index of the atom that contains ``value``. Raises ValueError if out of domain."""
        if self.numeric:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"{self.name}: expected a number, got {value!r}")
            u = self.to_units(value)
            if u.denominator != 1:
                raise ValueError(f"{self.name}: {value} is not a multiple of step {fmt_number(self.step)}")
            u = int(u)
            if not self.umin <= u <= self.umax:
                raise ValueError(f"{self.name}: {value} is outside {fmt_number(self.min)}..{fmt_number(self.max)}")
            lo, hi = 0, len(self.starts) - 1
            while lo < hi:  # last start <= u
                mid = (lo + hi + 1) // 2
                if self.starts[mid] <= u:
                    lo = mid
                else:
                    hi = mid - 1
            return lo
        if value not in self.values:
            raise ValueError(f"{self.name}: {value!r} is not one of {list(self.values)}")
        return self.values.index(value)

    def describe(self, atoms: Sequence[int]) -> Optional[str]:
        """Human description of a set of atoms, or None if it is the whole domain."""
        atoms = sorted(set(atoms))
        if len(atoms) == self.atom_count():
            return None
        if not self.numeric:
            vals = [self.values[i] for i in atoms]
            if self.type == "bool":
                return f"{self.name} is {str(vals[0]).lower()}" if len(vals) == 1 else None
            if len(vals) == 1:
                return f"{self.name} = {vals[0]}"
            return f"{self.name} in {{{', '.join(map(str, vals))}}}"
        ranges = []
        run = [atoms[0], atoms[0]]
        for a in atoms[1:]:
            if a == run[1] + 1:
                run[1] = a
            else:
                ranges.append(tuple(run))
                run = [a, a]
        ranges.append(tuple(run))
        parts = []
        unit = f" {self.unit}" if self.unit else ""
        for a, b in ranges:
            lo = self.atom_bounds(a)[0]
            hi = self.atom_bounds(b)[1]
            lo_v, hi_v = fmt_number(self.from_units(lo)), fmt_number(self.from_units(hi))
            if lo == hi:
                parts.append(f"{self.name} = {lo_v}{unit}")
            elif lo == self.umin:
                parts.append(f"{self.name} <= {hi_v}{unit}")
            elif hi == self.umax:
                parts.append(f"{self.name} >= {lo_v}{unit}")
            else:
                parts.append(f"{self.name} {lo_v}..{hi_v}{unit}")
        return " or ".join(parts)


# --------------------------------------------------------------------------- conditions


@dataclass
class Condition:
    field: str
    op: str
    arg: Any

    def text(self) -> str:
        sym = {"eq": "=", "ne": "!=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">=",
               "in": "in", "not_in": "not in", "between": "between"}[self.op]
        arg = self.arg
        if isinstance(arg, bool):
            arg = str(arg).lower()
        return f"{self.field} {sym} {arg}"

    def holds(self, f: Field, value: Any) -> bool:
        op, arg = self.op, self.arg
        if f.numeric:
            v = _frac(value)
            if op == "eq":
                return v == _frac(arg)
            if op == "ne":
                return v != _frac(arg)
            if op == "in":
                return any(v == _frac(a) for a in arg)
            if op == "not_in":
                return all(v != _frac(a) for a in arg)
            if op == "lt":
                return v < _frac(arg)
            if op == "lte":
                return v <= _frac(arg)
            if op == "gt":
                return v > _frac(arg)
            if op == "gte":
                return v >= _frac(arg)
            if op == "between":
                return _frac(arg[0]) <= v <= _frac(arg[1])
        if op == "eq":
            return value == arg
        if op == "ne":
            return value != arg
        if op == "in":
            return value in arg
        if op == "not_in":
            return value not in arg
        raise ValueError(op)

    def cut_starts(self, f: Field) -> List[int]:
        """Grid units at which this condition can change truth value."""
        def ceil_u(x):
            return math.ceil(f.to_units(x))

        def floor_u(x):
            return math.floor(f.to_units(x))

        op, arg = self.op, self.arg
        if op in ("eq", "ne", "in", "not_in"):
            out = []
            for a in (arg if isinstance(arg, list) else [arg]):
                u = f.to_units(a)
                if u.denominator == 1:
                    out += [int(u), int(u) + 1]
            return out
        if op == "lt":
            return [ceil_u(arg)]
        if op == "lte":
            return [floor_u(arg) + 1]
        if op == "gt":
            return [floor_u(arg) + 1]
        if op == "gte":
            return [ceil_u(arg)]
        if op == "between":
            return [ceil_u(arg[0]), floor_u(arg[1]) + 1]
        return []


@dataclass(eq=False)
class Rule:
    id: str
    conditions: List[Condition]
    decision: str
    priority: int = 0
    source: str = ""
    order: int = 0

    def text(self) -> str:
        cond = " and ".join(c.text() for c in self.conditions) or "always"
        return f"{self.id}: if {cond} then {self.decision}"


# --------------------------------------------------------------------------- policy


@dataclass
class Policy:
    name: str
    fields: Dict[str, Field]
    rules: List[Rule]
    decisions: List[str]
    strategy: str = "unique"
    default: Optional[str] = None
    path: Optional[str] = None

    @property
    def field_list(self) -> List[Field]:
        return list(self.fields.values())

    def matches(self, rule: Rule, inputs: Dict[str, Any]) -> bool:
        return all(c.holds(self.fields[c.field], inputs[c.field]) for c in rule.conditions)

    def failed_conditions(self, rule: Rule, inputs: Dict[str, Any]) -> List[Condition]:
        return [c for c in rule.conditions if not c.holds(self.fields[c.field], inputs[c.field])]

    def decide(self, inputs: Dict[str, Any]):
        """Return ``(status, decisions, winning_rules, matching_rules)``.

        status is ``decided`` (one decision), ``default`` (no rule, default used),
        ``gap`` (no rule, no default) or ``conflict`` (rules disagree).
        """
        matching = [r for r in self.rules if self.matches(r, inputs)]
        return self._resolve(matching)

    def _resolve(self, matching: List[Rule]):
        if not matching:
            if self.default is not None:
                return "default", [self.default], [], matching
            return "gap", [], [], matching
        if self.strategy == "first_match":
            win = min(matching, key=lambda r: r.order)
            return "decided", [win.decision], [win], matching
        if self.strategy == "priority":
            top = max(r.priority for r in matching)
            winners = [r for r in matching if r.priority == top]
        else:
            winners = matching
        decisions = sorted({r.decision for r in winners})
        if len(decisions) > 1:
            return "conflict", decisions, winners, matching
        return "decided", decisions, winners, matching

    def build_atoms(self) -> None:
        for f in self.fields.values():
            if not f.numeric:
                continue
            cuts = {f.umin}
            for r in self.rules:
                for c in r.conditions:
                    if c.field == f.name:
                        cuts.update(u for u in c.cut_starts(f) if f.umin < u <= f.umax)
            f.starts = sorted(cuts)

    def validate_input(self, inputs: Dict[str, Any]) -> List[str]:
        problems = []
        for name, f in self.fields.items():
            if name not in inputs:
                problems.append(f"missing field {name}")
                continue
            try:
                f.atom_of(inputs[name])
            except ValueError as exc:
                problems.append(str(exc))
        return problems


def _parse_condition(field_name: str, spec: Any, f: Optional[Field], where: str, problems: List[str]) -> List[Condition]:
    if f is None:
        problems.append(f"{where}: unknown field '{field_name}'")
        return []
    if not isinstance(spec, dict):
        spec = {"in": spec} if isinstance(spec, list) else {"eq": spec}
    out = []
    for op, arg in spec.items():
        allowed = NUMERIC_OPS if f.numeric else DISCRETE_OPS
        if op not in allowed:
            problems.append(f"{where}: operator '{op}' not allowed on {f.type} field '{field_name}'")
            continue
        if op in ("in", "not_in") and not isinstance(arg, list):
            problems.append(f"{where}: '{op}' on '{field_name}' needs a list")
            continue
        if op == "between" and not (isinstance(arg, list) and len(arg) == 2):
            problems.append(f"{where}: 'between' on '{field_name}' needs [low, high]")
            continue
        vals = arg if isinstance(arg, list) else [arg]
        if f.numeric:
            bad = [v for v in vals if isinstance(v, bool) or not isinstance(v, (int, float))]
            if bad:
                problems.append(f"{where}: '{field_name}' compares against non-number {bad[0]!r}")
                continue
        else:
            bad = [v for v in vals if v not in f.values]
            if bad:
                problems.append(f"{where}: '{field_name}' has no value {bad[0]!r} (allowed: {list(f.values)})")
                continue
        out.append(Condition(field_name, op, arg))
    return out


def load_policy(source: Union[str, Path, dict]) -> Policy:
    """Load a policy from a JSON/TOML file path or an already-parsed dict."""
    path = None
    if isinstance(source, dict):
        data = source
    else:
        path = str(source)
        text = Path(source).read_text(encoding="utf-8")
        if path.endswith(".toml"):
            try:
                import tomllib
            except ImportError as exc:  # pragma: no cover - Python < 3.11
                raise PolicyError(["TOML policies need Python 3.11+; use JSON instead"]) from exc
            data = tomllib.loads(text)
        else:
            data = json.loads(text)

    problems: List[str] = []
    fields: Dict[str, Field] = {}
    for name, spec in (data.get("fields") or {}).items():
        ftype = spec.get("type", "")
        if ftype == "bool":
            fields[name] = Field(name, "bool", values=(False, True))
        elif ftype == "enum":
            vals = tuple(spec.get("values") or ())
            if not vals:
                problems.append(f"field '{name}': enum needs a non-empty 'values' list")
            fields[name] = Field(name, "enum", values=vals)
        elif ftype in ("number", "integer"):
            if "min" not in spec or "max" not in spec:
                problems.append(f"field '{name}': numeric fields need 'min' and 'max'")
                continue
            step = _frac(spec.get("step", 1 if ftype == "integer" else "0.01"))
            f = Field(name, ftype, min=_frac(spec["min"]), max=_frac(spec["max"]), step=step,
                      unit=spec.get("unit", ""))
            if f.min > f.max:
                problems.append(f"field '{name}': min is greater than max")
            fields[name] = f
        else:
            problems.append(f"field '{name}': unknown type '{ftype}' (use number, integer, enum or bool)")
    if not fields:
        problems.append("policy declares no fields")

    decisions = list(data.get("decisions") or [])
    strategy = data.get("strategy", "unique")
    if strategy not in STRATEGIES:
        problems.append(f"unknown strategy '{strategy}' (use one of {', '.join(STRATEGIES)})")
    default = data.get("default")

    rules: List[Rule] = []
    seen = set()
    for i, spec in enumerate(data.get("rules") or []):
        rid = str(spec.get("id") or f"R{i + 1}")
        where = f"rule {rid}"
        if rid in seen:
            problems.append(f"{where}: duplicate rule id")
        seen.add(rid)
        decision = spec.get("then")
        if decision is None:
            problems.append(f"{where}: missing 'then'")
            continue
        if decisions and decision not in decisions:
            problems.append(f"{where}: decision '{decision}' is not in the declared decisions {decisions}")
        conds: List[Condition] = []
        for fname, cspec in (spec.get("when") or {}).items():
            conds += _parse_condition(fname, cspec, fields.get(fname), where, problems)
        rules.append(Rule(rid, conds, decision, int(spec.get("priority", 0)), spec.get("source", ""), i))
    if not rules:
        problems.append("policy declares no rules")
    if not decisions:
        decisions = sorted({r.decision for r in rules} | ({default} if default else set()))
    if default is not None and default not in decisions:
        problems.append(f"default decision '{default}' is not in the declared decisions")
    if problems:
        raise PolicyError(problems)

    policy = Policy(data.get("name", Path(path).stem if path else "policy"), fields, rules, decisions,
                    strategy, default, path)
    policy.build_atoms()
    return policy
