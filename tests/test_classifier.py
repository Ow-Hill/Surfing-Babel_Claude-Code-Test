import io

import numpy as np
import pytest
from PIL import Image

from babel_surfer import benchmark
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import PICTURE_SIGMA, classify, tier_for


@pytest.fixture
def rng():
    return np.random.default_rng(99)


def test_simulated_babel_static_is_static(rng):
    for _ in range(25):
        c = classify(babel_static(rng))
        assert c.sigma < PICTURE_SIGMA - 2, c


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
    assert tier_for(1) == "static" and tier_for(3.5) == "near miss"
    assert tier_for(5) == "anomaly"
    assert tier_for(9) == "faint" and tier_for(20) == "clear" and tier_for(50) == "striking"


def test_maps_locate_the_object_and_describe_it(rng):
    from babel_surfer import analysis
    picture, mask = benchmark.SIGNALS["small square (24px)"][0]()
    frame = benchmark._blend(rng, picture, mask, 1.0)
    maps = analysis.analyze(frame)
    gw, gh = maps["grid"]
    m = np.abs(np.asarray(maps["mean"])).reshape(gh, gw)
    y, x = np.unravel_index(m.argmax(), m.shape)
    assert m.max() > 15 and 17 <= x <= 21 and 11 <= y <= 14     # square at (300-324, 196-220)
    c = classify(frame)
    text = analysis.describe(c.tests, maps, c.sigma)
    assert "darker" in text and "centre" in text


@pytest.mark.parametrize("maker,alpha,reason", [
    ("_grey_patch", 1.0, "contrast"), ("_diagonal_line", 1.0, "angled"),
    ("_tiled", 0.4, "repetition"), ("_mirrored", 0.4, "symmetry")])
def test_new_tests_catch_their_signal(rng, maker, alpha, reason):
    c = classify(getattr(benchmark, maker)(rng, alpha))
    assert c.is_interesting and c.tests[reason] >= 8, c


def test_angled_line_geometry(rng):
    from babel_surfer import analysis
    maps = analysis.analyze(benchmark._diagonal_line(rng, 1.0))
    assert maps["angled"]["line"] and abs(analysis.line_angle(maps["angled"]["line"]) - 30) <= 8
