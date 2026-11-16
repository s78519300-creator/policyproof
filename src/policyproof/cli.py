"""policyproof command line."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .analysis import TooManyCells, analyze
from .grade import grade, load_log
from .model import PolicyError, load_policy
from .report import explain_text, grade_markdown, grade_text, lint_json, lint_markdown, lint_text
from .testgen import LEVELS, generate


def _emit(text: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
        print(f"written to {output}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="policyproof",
                                description="Find gaps and conflicts in the business policy your AI agent follows, "
                                            "generate boundary tests, and grade agent decisions.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("lint", help="find gaps, conflicts, dead and shadowed rules")
    s.add_argument("policy")
    s.add_argument("--format", choices=["text", "md", "json"], default="text")
    s.add_argument("--output", "-o")
    s.add_argument("--quiet", action="store_true", help="hide notes, show errors and warnings only")
    s.add_argument("--strict", action="store_true", help="exit 1 on warnings too")

    t = sub.add_parser("tests", help="generate test scenarios (JSONL or CSV)")
    t.add_argument("policy")
    t.add_argument("--level", choices=LEVELS, default="boundaries")
    t.add_argument("--per-boundary", type=int, default=3, help="contexts per decision threshold (default 3)")
    t.add_argument("--no-issues", action="store_true", help="leave out gap and conflict examples")
    t.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    t.add_argument("--output", "-o")

    g = sub.add_parser("grade", help="grade an agent decision log (JSONL) against the policy")
    g.add_argument("policy")
    g.add_argument("log")
    g.add_argument("--format", choices=["text", "md", "json"], default="text")
    g.add_argument("--output", "-o")
    g.add_argument("--min-accuracy", type=float, default=None, help="exit 1 below this accuracy, e.g. 0.95")

    e = sub.add_parser("explain", help="show which rule decides one input, and why the others do not")
    e.add_argument("policy")
    e.add_argument("--input", required=True, help="JSON object, or @file.json")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = load_policy(args.policy)
    except PolicyError as exc:
        print("Policy is invalid:", file=sys.stderr)
        for prob in exc.problems:
            print(f"  - {prob}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "lint":
            a = analyze(policy)
            text = {"text": lambda: lint_text(a, not args.quiet), "md": lambda: lint_markdown(a),
                    "json": lambda: lint_json(a)}[args.format]()
            _emit(text, args.output)
            return 1 if a.errors or (args.strict and a.warnings) else 0

        if args.command == "tests":
            cases = generate(policy, args.level, args.per_boundary, not args.no_issues)
            if args.format == "jsonl":
                text = "".join(json.dumps(c.to_dict()) + "\n" for c in cases)
            else:
                buf = io.StringIO()
                names = list(policy.fields)
                w = csv.writer(buf)
                w.writerow(["id", "kind", *names, "expected", "rules", "note"])
                for c in cases:
                    exp = c.expected if not isinstance(c.expected, list) else " | ".join(c.expected)
                    w.writerow([c.id, c.kind, *[c.input[n] for n in names], exp if exp is not None else "",
                                " ".join(c.rules), c.note])
                text = buf.getvalue()
            _emit(text, args.output)
            kinds = {}
            for c in cases:
                kinds[c.kind] = kinds.get(c.kind, 0) + 1
            print(f"{len(cases)} test cases: " + ", ".join(f"{v} {k}" for k, v in kinds.items()), file=sys.stderr)
            return 0

        if args.command == "grade":
            report = grade(policy, load_log(args.log))
            text = {"text": lambda: grade_text(policy, report), "md": lambda: grade_markdown(policy, report),
                    "json": lambda: json.dumps(report.to_dict(), indent=2) + "\n"}[args.format]()
            _emit(text, args.output)
            if args.min_accuracy is not None and report.accuracy < args.min_accuracy:
                return 1
            return 0

        raw = args.input
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8")
        sys.stdout.write(explain_text(policy, json.loads(raw)))
        return 0
    except TooManyCells as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
