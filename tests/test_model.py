import pytest

from conftest import make
from policyproof import PolicyError, load_policy


def test_atoms_split_exactly_at_thresholds():
    p = load_policy(make([{"id": "A", "when": {"amount": {"lt": 100}}, "then": "x"},
                          {"id": "B", "when": {"amount": {"gte": 100, "lte": 500}}, "then": "y"}]))
    f = p.fields["amount"]
    assert [f.atom_bounds(i) for i in range(f.atom_count())] == [(0, 99), (100, 500), (501, 1000)]
    assert f.atom_of(99) == 0 and f.atom_of(100) == 1 and f.atom_of(1000) == 2


def test_decimal_steps_are_exact():
    fields = {"rate": {"type": "number", "min": 0, "max": 1, "step": 0.01}}
    p = load_policy(make([{"id": "A", "when": {"rate": {"lte": 0.1}}, "then": "x"}], fields))
    assert p.decide({"rate": 0.1})[1] == ["x"]  # 0.1 is exactly on the boundary, no float error
    assert p.decide({"rate": 0.11})[0] == "gap"
    with pytest.raises(ValueError):
        p.fields["rate"].atom_of(0.105)  # off the grid


def test_shorthands_scalar_list_and_bool():
    p = load_policy(make([{"id": "A", "when": {"kind": ["a"], "flag": True, "amount": 5}, "then": "x"}]))
    assert p.decide({"amount": 5, "kind": "a", "flag": True})[0] == "decided"
    assert p.decide({"amount": 6, "kind": "a", "flag": True})[0] == "gap"


def test_validation_collects_every_problem():
    bad = make([
        {"id": "A", "when": {"nope": 1}, "then": "x"},
        {"id": "B", "when": {"kind": "zzz"}, "then": "x"},
        {"id": "C", "when": {"amount": {"lt": "ten"}}, "then": "x"},
        {"id": "D", "when": {"kind": {"lt": 3}}, "then": "x"},
        {"id": "E", "when": {}},
    ], decisions=["x"])
    with pytest.raises(PolicyError) as err:
        load_policy(bad)
    text = " ".join(err.value.problems)
    for needle in ("unknown field 'nope'", "no value 'zzz'", "non-number", "not allowed", "missing 'then'"):
        assert needle in text


def test_numeric_field_needs_range():
    with pytest.raises(PolicyError):
        load_policy(make([{"id": "A", "then": "x"}], {"n": {"type": "integer"}}))


def test_strategies():
    rules = [{"id": "A", "when": {"flag": True}, "then": "x", "priority": 1},
             {"id": "B", "when": {"kind": "a"}, "then": "y", "priority": 2}]
    inp = {"amount": 1, "kind": "a", "flag": True}
    assert load_policy(make(rules)).decide(inp)[0] == "conflict"
    assert load_policy(make(rules, strategy="first_match")).decide(inp)[1] == ["x"]
    assert load_policy(make(rules, strategy="priority")).decide(inp)[1] == ["y"]


def test_default_fills_gaps():
    p = load_policy(make([{"id": "A", "when": {"flag": True}, "then": "x"}], default="manual", decisions=["x", "manual"]))
    assert p.decide({"amount": 1, "kind": "a", "flag": False})[:2] == ("default", ["manual"])


def test_examples_load(examples):
    for name in ("refund_policy.json", "refund_policy_fixed.json"):
        assert load_policy(examples / name).rules
