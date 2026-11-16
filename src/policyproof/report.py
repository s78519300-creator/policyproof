"""Text, Markdown and JSON renderers."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .analysis import Analysis
from .grade import GradeReport
from .model import Policy


def _pct(x: float) -> str:
    if x == 0:
        return "0%"
    if x < 0.001:
        return "<0.1%"
    return f"{x * 100:.1f}%"


def _example(d: Dict[str, Any]) -> str:
    return ", ".join(f"{k}={json.dumps(v)}" for k, v in d.items())


def _counts(a: Analysis) -> str:
    info = len(a.findings) - len(a.errors) - len(a.warnings)
    return f"{len(a.errors)} error(s), {len(a.warnings)} warning(s), {info} note(s)"


def lint_text(a: Analysis, show_info: bool = True) -> str:
    p = a.policy
    s = a.share
    c = a.case_counts
    out = [
        f"{p.name}: {len(p.rules)} rules, {len(p.fields)} fields, strategy '{p.strategy}'",
        f"  {a.cells:,} distinct cases: {c['decided']} decided, {c['gap']} silent, {c['conflict']} conflicting"
        + (f", {c['default']} default only" if c["default"] else ""),
        f"  share of the declared input space: {_pct(s['decided'])} decided, {_pct(s['gap'])} silent, "
        f"{_pct(s['conflict'])} conflicting" + (f", {_pct(s['default'])} default" if s["default"] else ""),
        f"  {_counts(a)}",
        "",
    ]
    for f in a.findings:
        if f.severity == "info" and not show_info:
            continue
        share = f"  [{_pct(f.share)} of inputs]" if f.share else ""
        out.append(f"{f.severity.upper():<8}{f.code:<16}{f.title}{share}")
        if f.region:
            out.append(f"{'':<24}where: {f.region}")
        if f.example:
            out.append(f"{'':<24}e.g.   {_example(f.example)}")
        if f.detail and f.severity != "info":
            out.append(f"{'':<24}{f.detail}")
    return "\n".join(out) + "\n"


def lint_markdown(a: Analysis) -> str:
    p = a.policy
    s = a.share
    c = a.case_counts
    out = [
        f"# Policy check: {p.name}",
        "",
        f"Of {a.cells:,} distinct cases, the policy decides **{c['decided']}**, is **silent on {c['gap']}** "
        f"and **conflicting on {c['conflict']}**"
        + (f", and leaves {c['default']} to the default." if c["default"] else ".")
        + f" By share of the declared input space: {_pct(s['decided'])} decided, {_pct(s['gap'])} silent, "
          f"{_pct(s['conflict'])} conflicting.",
        "",
        f"{len(p.rules)} rules, {len(p.fields)} fields, strategy `{p.strategy}`, {a.cells:,} distinct cases. "
        f"{_counts(a)}.",
        "",
        "| Severity | Finding | Where | Share of inputs | Example |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in a.findings:
        ex = f"`{_example(f.example)}`" if f.example else ""
        out.append(f"| {f.severity} | {f.title} | {f.region or '-'} | {_pct(f.share) if f.share else '-'} | {ex} |")
    out += ["", "| Rule | Share of inputs it decides | Source |", "| --- | --- | --- |"]
    for r in p.rules:
        out.append(f"| {r.id} | {_pct(a.rule_stats[r.id]['share_decided'])} | {r.source or '-'} |")
    return "\n".join(out) + "\n"


def lint_json(a: Analysis) -> str:
    return json.dumps(a.to_dict(), indent=2) + "\n"


def explain_text(policy: Policy, inputs: Dict[str, Any]) -> str:
    problems = policy.validate_input(inputs)
    if problems:
        return "Invalid input:\n" + "\n".join(f"  - {x}" for x in problems) + "\n"
    status, decisions, winners, matching = policy.decide(inputs)
    head = {
        "decided": f"Decision: {decisions[0] if decisions else ''} (by {', '.join(r.id for r in winners)})",
        "default": f"Decision: {decisions[0]} (no rule matched; default)",
        "gap": "Decision: none. No rule covers this input and there is no default.",
        "conflict": f"Decision: AMBIGUOUS. {', '.join(r.id for r in winners)} give {' vs '.join(decisions)}",
    }[status]
    out = [head, ""]
    win_ids = {r.id for r in winners}
    for r in policy.rules:
        if r in matching:
            tag = ("CLASH" if status == "conflict" else "WINS ") if r.id in win_ids else "match"
            why = "overridden" if r.id not in win_ids else ""
            out.append(f"  {tag}  {r.text()} {why}".rstrip())
        else:
            failed = "; ".join(c.text() for c in policy.failed_conditions(r, inputs))
            out.append(f"  -      {r.id} does not apply: fails {failed}")
    return "\n".join(out) + "\n"


def grade_text(policy: Policy, g: GradeReport, limit: int = 15) -> str:
    c = g.counts
    out = [
        f"{policy.name}: graded {len(g.records)} agent decisions",
        f"  accuracy {g.accuracy * 100:.1f}% on {c['correct'] + c['wrong']} decisions the policy defines "
        f"({c['correct']} correct, {c['wrong']} wrong)",
        f"  {c['silent']} made where the policy is silent  |  {c['ambiguous']} where rules conflict  |  "
        f"{c['invalid']} invalid records",
        f"  coverage: {len(g.rules_exercised)}/{g.rules_total} rules, "
        f"{g.boundaries_exercised}/{g.boundaries_total} decision thresholds tested",
        "",
    ]
    weak = []
    for rid, c2 in sorted(g.by_rule().items()):
        tot = c2["correct"] + c2["wrong"]
        if c2["wrong"]:
            weak.append((c2["correct"] / tot, f"{rid} {c2['correct']}/{tot}"))
    if weak:
        out.append("Rules the agent gets wrong (correct/total): " + ", ".join(t for _, t in sorted(weak)))
        out.append("")
    conf = g.confusion()
    if conf:
        out.append("Mistakes (expected -> agent):")
        out += [f"  {k}: {v}" for k, v in conf.items()]
        out.append("")
    shown = 0
    for r in g.records:
        if r.verdict == "correct" or shown >= limit:
            continue
        shown += 1
        exp = r.expected if r.expected is not None else "(policy silent)"
        line = f"  {r.verdict.upper():<10}{r.id}: agent {r.decision!r}, policy {exp}"
        if r.rules:
            line += f" via {', '.join(r.rules)}"
        if r.problem:
            line += f" - {r.problem}"
        out.append(line)
    untested = [r.id for r in policy.rules if r.id not in g.rules_exercised]
    if untested:
        out += ["", f"Rules no logged case exercised: {', '.join(untested)}"]
    return "\n".join(out) + "\n"


def grade_markdown(policy: Policy, g: GradeReport) -> str:
    c = g.counts
    out = [
        f"# Agent decisions vs {policy.name}",
        "",
        f"**Accuracy {g.accuracy * 100:.1f}%** on {c['correct'] + c['wrong']} decisions the policy defines. "
        f"{c['silent']} decisions were made where the policy is silent and {c['ambiguous']} where its rules conflict.",
        "",
        f"Coverage: {len(g.rules_exercised)}/{g.rules_total} rules and "
        f"{g.boundaries_exercised}/{g.boundaries_total} decision thresholds exercised.",
        "",
        "| Record | Verdict | Agent | Policy | Rules |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in g.records:
        if r.verdict != "correct":
            out.append(f"| {r.id} | {r.verdict} | {r.decision} | {r.expected} | {', '.join(r.rules) or '-'} |")
    return "\n".join(out) + "\n"
