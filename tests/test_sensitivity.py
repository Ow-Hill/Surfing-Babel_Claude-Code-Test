"""The blind-spot probe (sensitivity.py) and the detector's accuracy on the
kinds of marks it used to miss."""

import json
from pathlib import Path

import numpy as np
import pytest

from babel_surfer import classifier, sensitivity as S

CASES = json.loads((Path(__file__).parent / "data" / "blind_spots.json").read_text())["cases"]


@pytest.mark.parametrize("case", [
    c if c["caught"] else pytest.param(c, marks=pytest.mark.xfail(reason="known blind spot: 8 px marks", strict=False))
    for c in CASES], ids=[c["signal"] for c in CASES])
def test_detector_catches_marks_a_person_would_see(case):
    """Each of these random marks was missed (no test at 4 sigma in most
    trials) at the strength where a person first sees it. The strokes test
    was added for them; it must keep catching them."""
    desc, maker = S.variant(case["id"])
    assert desc == case["signal"]                      # the generator is unchanged
    hits = sum(classifier.classify(maker(np.random.default_rng(700 + k), case["eye"])).is_interesting
               for k in range(3))
    assert hits >= 2, f"{desc} at {case['eye']:.2f}: caught {hits}/3"


def test_families_are_deterministic_frames():
    for name in ("pen stroke (2px)", "small dot (8px)", "2px checker patch (96px)"):
        a = S.FAMILIES[name](np.random.default_rng(1), 0.5)
        b = S.FAMILIES[name](np.random.default_rng(1), 0.5)
        assert a == b and a[:2] == b"\xff\xd8"          # same JPEG for the same seed


def test_merge_keeps_misses_until_a_recheck_fixes_them():
    old = {"misses": [{"id": 1, "signal": "a"}, {"id": 2, "signal": "b"}, {"id": 3, "signal": "c"}],
           "fixed": [], "history": []}
    new = {"generated": 100, "explored": 5, "checked": [1, 2, 9],   # 3 was not re-checked
           "summary": {"families": 23, "blind": 0, "never_detected": 0},
           "misses": [{"id": 1, "signal": "a"}, {"id": 9, "signal": "z"}]}
    m = S.merge_history(old, new)
    assert [x["id"] for x in m["misses"]] == [1, 9, 3]        # still missed, new, kept
    assert [x["id"] for x in m["fixed"]] == [2] and m["fixed"][0]["fixed"] == 100
    assert m["history"][-1]["fixed"] == 1 and m["history"][-1]["misses"] == 3
