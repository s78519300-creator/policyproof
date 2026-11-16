from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def make(rules, fields=None, **extra):
    return {
        "name": "t",
        "fields": fields or {
            "amount": {"type": "integer", "min": 0, "max": 1000},
            "kind": {"type": "enum", "values": ["a", "b"]},
            "flag": {"type": "bool"},
        },
        "rules": rules,
        **extra,
    }


@pytest.fixture
def examples():
    return EXAMPLES
