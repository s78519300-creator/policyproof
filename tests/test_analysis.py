from conftest import make
from policyproof import analyze, load_policy


def codes(a):
    return [f.code for f in a.findings]


def test_detects_gap_with_exact_region():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lt": 100}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gt": 100}}, "then": "y"}]))
    a = analyze(p)
    gaps = [f for f in a.findings if f.code == "gap"]
    assert len(gaps) == 1
    assert gaps[0].region == "amount = 100"
    assert gaps[0].example["amount"] == 100


def test_detects_conflict_pair():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lte": 600}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gte": 500}}, "then": "y"}]))
    conflicts = [f for f in analyze(p).findings if f.code == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].rules == ["A", "B"]
    assert conflicts[0].region == "amount 500..600"


def test_same_decision_overlap_is_only_a_note():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lte": 600}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gte": 500}}, "then": "x"}]))
    a = analyze(p)
    assert not a.errors and "overlap" in codes(a)


def test_dead_and_shadowed_rules():
    rules = [{"id": "A", "when": {"flag": True}, "then": "x"},
             {"id": "B", "when": {"flag": True, "kind": "a"}, "then": "y"},
             {"id": "C", "when": {"amount": {"gt": 5, "lt": 3}}, "then": "y"},
             {"id": "D", "when": {"flag": False}, "then": "y"}]
    a = analyze(load_policy(make(rules, strategy="first_match")))
    by = {f.code: f for f in a.findings}
    assert by["shadowed-rule"].rules == ["B"]
    assert by["dead-rule"].rules == ["C"]
    assert not a.errors


def test_complete_policy_is_clean(examples):
    a = analyze(load_policy(examples / "refund_policy_fixed.json"))
    assert a.case_counts["gap"] == 0 and a.case_counts["conflict"] == 0
    assert not a.errors and not a.warnings
    assert abs(a.share["decided"] - 1.0) < 1e-9


def test_example_policy_findings(examples):
    a = analyze(load_policy(examples / "refund_policy.json"))
    pairs = {tuple(f.rules) for f in a.findings if f.code == "conflict"}
    assert ("R5", "R7") in pairs          # gold 45-day window vs no returns after 30 days
    assert ("R2", "R8") in pairs          # damaged furniture: refund or replace?
    regions = [f.region for f in a.findings if f.code == "gap"]
    assert any("days_since_delivery 8..30" in r and "item_damaged is true" in r for r in regions)
    assert [f.rules for f in a.findings if f.code == "dead-rule"] == [["R9"]]


def test_shares_sum_to_one(examples):
    a = analyze(load_policy(examples / "refund_policy.json"))
    assert abs(sum(a.share.values()) - 1.0) < 1e-9
    assert sum(a.case_counts.values()) == a.cells
