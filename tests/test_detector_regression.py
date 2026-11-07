"""Speed-ups must not change what the detector measures: the raw statistics
on fixed frames must match the recorded reference, or the calibration (learned
on real frames) would no longer apply."""

import json
import os
import time
from pathlib import Path

import pytest

from babel_surfer import classifier as C
from reference_frames import reference_frames

REF = json.loads((Path(__file__).parent / "data" / "raw_reference.json").read_text())
FRAMES = reference_frames()


@pytest.mark.parametrize("name,data", FRAMES, ids=[n for n, _ in FRAMES])
def test_raw_statistics_unchanged(name, data):
    ex = {}
    raw = C.raw_scores(data, extras=ex)
    raw["fingerprint"] = ex["fingerprint"]
    for test, want in REF[name].items():
        assert raw[test] == pytest.approx(want, rel=2e-3, abs=2e-3), test


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="shared CI runners are too noisy to time")
def test_classify_is_fast():
    data = FRAMES[0][1]
    C.classify(data)                                 # warm caches
    t = time.perf_counter()
    for _ in range(3):
        C.classify(data)
    # all 16 tests in less time than the original 10 took before the speed-ups (0.60 s)
    assert (time.perf_counter() - t) / 3 < 0.6
