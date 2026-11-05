"""Spec for the live-coding demo slot (babel_surfer/demo_slot.py).

These tests are skipped while the slot is unimplemented, so the main suite
stays green. Once ``find_of_the_day`` is implemented they run automatically.
"""

from collections import Counter
from datetime import date, timedelta

import pytest

from babel_surfer.demo_slot import find_of_the_day


def _implemented() -> bool:
    try:
        find_of_the_day([], date(2026, 1, 1))
    except NotImplementedError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _implemented(),
                                reason="live demo slot not implemented yet (see docs/live-demo.md)")

FINDS = [
    {"location": str(1000 + i), "sigma": s, "tier": "anomaly", "reason": "tone",
     "description": f"find {i}"}
    for i, s in enumerate([4.1, 4.4, 4.9, 5.3, 7.5, 4.2])
]
DAY = date(2026, 11, 5)


def test_none_when_no_finds():
    assert find_of_the_day([], DAY) is None


def test_result_shape():
    r = find_of_the_day(FINDS, DAY)
    assert {"location", "sigma", "tier", "reason", "caption"} <= set(r)
    chosen = next(f for f in FINDS if f["location"] == r["location"])
    assert (r["sigma"], r["tier"], r["reason"]) == (chosen["sigma"], chosen["tier"], chosen["reason"])


def test_deterministic_and_order_independent():
    a = find_of_the_day(FINDS, DAY)
    b = find_of_the_day(list(reversed(FINDS)), DAY)
    assert a == b == find_of_the_day(FINDS, DAY)


def test_varies_by_day():
    picks = {find_of_the_day(FINDS, DAY + timedelta(days=i))["location"] for i in range(30)}
    assert len(picks) >= 3


def test_favours_stronger_finds():
    counts = Counter(find_of_the_day(FINDS, DAY + timedelta(days=i))["location"] for i in range(365))
    strongest = max(FINDS, key=lambda f: f["sigma"])["location"]
    weakest = min(FINDS, key=lambda f: f["sigma"])["location"]
    assert counts[strongest] > counts[weakest]


def test_caption():
    r = find_of_the_day(FINDS, DAY)
    assert "\n" not in r["caption"] and len(r["caption"]) <= 140
    assert f"{r['sigma']:.1f}σ" in r["caption"]
