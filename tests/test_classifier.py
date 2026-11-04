import io

import numpy as np
import pytest
from PIL import Image

from babel_surfer import benchmark
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import INTERESTING_SIGMA, classify, tier_for


@pytest.fixture
def rng():
    return np.random.default_rng(99)


def test_simulated_babel_static_is_static(rng):
    for _ in range(25):
        c = classify(babel_static(rng))
        assert not c.is_interesting, c
        assert c.sigma < INTERESTING_SIGMA - 2


@pytest.mark.parametrize("name", ["large disc", "landscape", "word 'BABEL'"])
def test_faint_pictures_in_static_are_found(rng, name):
    maker, _ = benchmark.SIGNALS[name]
    picture, mask = maker()
    c = classify(benchmark._blend(rng, picture, mask, 0.05))
    assert c.is_interesting, c


def test_small_object_and_line(rng):
    for name, alpha, reason in [("small square (24px)", 0.5, "blob"),
                                ("horizon line (2px)", 0.5, "lines")]:
        picture, mask = benchmark.SIGNALS[name][0]()
        c = classify(benchmark._blend(rng, picture, mask, alpha))
        assert c.is_interesting and c.reason == reason, (name, c)


@pytest.mark.parametrize("kind,reason", [("greyscale static", "palette"),
                                         ("dark static (half palette)", "tone")])
def test_special_cases(rng, kind, reason):
    c = classify(benchmark._special(rng, kind))
    assert c.is_interesting


def test_flat_image_and_tiers():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(buf, "PNG")
    assert classify(buf.getvalue()).label == "striking"
    assert tier_for(1) == "static" and tier_for(6) == "near miss"
    assert tier_for(9) == "faint" and tier_for(20) == "clear" and tier_for(50) == "striking"
