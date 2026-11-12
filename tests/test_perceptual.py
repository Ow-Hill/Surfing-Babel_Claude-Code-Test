"""The perceptual tests: quiet on static, loud on what a person would see,
and each signal is caught by the test built for it."""

import io

import numpy as np
import pytest
from PIL import Image

from babel_surfer import benchmark as B, classifier as C, perceptual as P
from babel_surfer.calibrate import babel_static


def rgb(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.float64)


def test_static_stays_quiet(static_classified):
    for c in static_classified:
        for t in C.PERCEPTUAL_TESTS:
            assert c.tests[t] < 4.0, (t, c.tests[t])
        assert c.sigma_global <= c.sigma


@pytest.mark.parametrize("maker,alpha,test", [
    (B._arc, 0.5, "curves"),
    (B._stroke, 0.8, "flat"),
    (B._face, 0.3, "face"),
    (B._posterised, 0.3, "colours"),
    (B._photo_like, 0.2, "spectrum"),
])
def test_signal_caught_by_its_test(maker, alpha, test):
    c = C.classify(maker(np.random.default_rng(11), alpha))
    assert c.tests[test] >= 4.0, c.tests
    assert c.is_interesting


def test_frame_sigma_accounts_for_tests():
    assert C.frame_sigma(4.0, 1) == pytest.approx(4.0, abs=0.01)
    assert C.frame_sigma(4.0, 16) == pytest.approx(3.24, abs=0.05)
    assert C.frame_sigma(40.0, 16) == pytest.approx(39.95, abs=0.1)


def test_pyramid_keeps_static_white():
    fr = P.Frame(rgb(babel_static(np.random.default_rng(3))))
    for k in (1, 2):
        g = fr.level(k).blur_z(3.0)
        assert abs(float(g.std()) - 1) < 0.1          # blurred z fields are unit variance


def test_local_maps_point_at_the_face():
    maps = P.local_maps(rgb(B._face(np.random.default_rng(5), 0.4)))
    f = maps["face"]
    assert 260 <= f["x"] <= 380 and 140 <= f["y"] <= 240, f
