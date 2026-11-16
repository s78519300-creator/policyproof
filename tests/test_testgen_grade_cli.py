import json

from conftest import make
from policyproof import generate, grade, load_log, load_policy
from policyproof.cli import main


def test_boundary_tests_straddle_threshold():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lt": 100}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gte": 100}}, "then": "y"}]))
    cases = generate(p)
    bounds = sorted((c.input["amount"], c.expected) for c in cases if c.kind == "boundary")
    assert (99, "x") in bounds and (100, "y") in bounds
    assert {c.rules[0] for c in cases if c.kind == "rule"} == {"A", "B"}


def test_issue_examples_marked(examples):
    cases = generate(load_policy(examples / "refund_policy.json"))
    gaps = [c for c in cases if c.kind == "gap"]
    conflicts = [c for c in cases if c.kind == "conflict"]
    assert gaps and all(c.expected is None for c in gaps)
    assert conflicts and all(isinstance(c.expected, list) for c in conflicts)
    assert not [c for c in generate(load_policy(examples / "refund_policy.json"), include_issues=False)
                if c.kind in ("gap", "conflict")]


def test_generated_tests_agree_with_policy(examples):
    p = load_policy(examples / "refund_policy_fixed.json")
    for c in generate(p, level="exhaustive"):
        status, decisions, _, _ = p.decide(c.input)
        assert c.expected == decisions[0]


def test_grade_verdicts():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lt": 100}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gt": 100}}, "then": "y"},
                          {"id": "C", "when": {"amount": {"gt": 900}}, "then": "z"}]))
    base = {"kind": "a", "flag": True}
    recs = [
        {"id": "ok", "input": {**base, "amount": 5}, "decision": "X"},     # case-insensitive
        {"id": "bad", "input": {**base, "amount": 5}, "decision": "y"},
        {"id": "gap", "input": {**base, "amount": 100}, "decision": "x"},
        {"id": "amb", "input": {**base, "amount": 950}, "decision": "z"},
        {"id": "amb-wrong", "input": {**base, "amount": 950}, "decision": "x"},
        {"id": "inv", "input": {**base, "amount": 5000}, "decision": "x"},
        {"amount": 7, "kind": "b", "flag": False, "action": "x"},        # flat record
    ]
    g = grade(p, recs)
    verdict = {r.id: r.verdict for r in g.records}
    assert verdict == {"ok": "correct", "bad": "wrong", "gap": "silent", "amb": "ambiguous",
                       "amb-wrong": "wrong", "inv": "invalid", "#7": "correct"}
    assert g.accuracy == 0.5
    assert g.confusion() == {"x -> y": 1, "['y', 'z'] -> x": 1}


def test_example_log_grading(examples):
    p = load_policy(examples / "refund_policy_fixed.json")
    g = grade(p, load_log(str(examples / "agent_decisions.jsonl")))
    assert g.counts["wrong"] > 0
    weak = {rid for rid, c in g.by_rule().items() if c["wrong"]}
    assert weak == {"R4", "R6", "R7"}  # exactly the three planted mistakes
    assert g.boundaries_exercised == g.boundaries_total


def test_cli_lint_exit_codes(examples, capsys):
    assert main(["lint", str(examples / "refund_policy.json")]) == 1
    assert "R5 says reject, R7 says refund" in capsys.readouterr().out
    assert main(["lint", str(examples / "refund_policy_fixed.json")]) == 0


def test_cli_lint_formats(examples, tmp_path):
    md, js = tmp_path / "r.md", tmp_path / "r.json"
    main(["lint", str(examples / "refund_policy.json"), "--format", "md", "-o", str(md)])
    main(["lint", str(examples / "refund_policy.json"), "--format", "json", "-o", str(js)])
    assert md.read_text().startswith("# Policy check")
    assert json.loads(js.read_text())["case_counts"]["gap"] > 0


def test_cli_tests_and_grade(examples, tmp_path):
    out = tmp_path / "tests.jsonl"
    assert main(["tests", str(examples / "refund_policy_fixed.json"), "-o", str(out)]) == 0
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    log = tmp_path / "log.jsonl"
    log.write_text("".join(json.dumps({"input": r["input"], "decision": r["expected"]}) + "\n" for r in rows))
    assert main(["grade", str(examples / "refund_policy_fixed.json"), str(log), "--min-accuracy", "1"]) == 0
    csv_out = tmp_path / "t.csv"
    main(["tests", str(examples / "refund_policy_fixed.json"), "--format", "csv", "-o", str(csv_out)])
    assert csv_out.read_text().startswith("id,kind,order_value")


def test_cli_grade_gate_and_explain(examples, capsys):
    assert main(["grade", str(examples / "refund_policy_fixed.json"), str(examples / "agent_decisions.jsonl"),
                 "--min-accuracy", "0.95"]) == 1
    capsys.readouterr()
    inp = '{"order_value": 1000, "days_since_delivery": 40, "category": "fashion", "item_damaged": false, ' \
          '"customer_tier": "gold", "refunds_last_90_days": 0}'
    assert main(["explain", str(examples / "refund_policy.json"), "--input", inp]) == 0
    assert "AMBIGUOUS" in capsys.readouterr().out


def test_cli_invalid_policy(tmp_path, capsys):
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps({"fields": {}, "rules": []}))
    assert main(["lint", str(bad)]) == 2
    assert "Policy is invalid" in capsys.readouterr().err
