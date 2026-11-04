import numpy as np
import pytest
from PIL import Image, ImageDraw

from babel_surfer.classifier import DEFAULT_THRESHOLD, classify

W, H = 640, 416


@pytest.fixture
def rng():
    return np.random.default_rng(1234)


def test_random_static_is_not_interesting(rng):
    for _ in range(50):
        img = Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8))
        c = classify(img)
        assert not c.is_interesting, c
        assert c.label == "static"
        assert c.score < DEFAULT_THRESHOLD / 2


def test_4096_colour_static_is_not_interesting(rng):
    a = (rng.integers(0, 16, (H, W, 3)) * 17).astype(np.uint8)
    assert not classify(Image.fromarray(a)).is_interesting


def test_clear_shape_is_structure():
    img = Image.new("RGB", (W, H), "white")
    ImageDraw.Draw(img).ellipse((100, 80, 400, 350), fill="red")
    c = classify(img)
    assert c.is_interesting and c.label == "structure"


def test_faint_shape_in_noise_is_detected(rng):
    a = rng.integers(0, 256, (H, W, 3)).astype(int)
    yy, xx = np.ogrid[:H, :W]
    mask = (xx - 320) ** 2 + (yy - 208) ** 2 < 120 ** 2
    a[mask] += 12
    c = classify(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    assert c.is_interesting


def test_flat_image_and_bytes_input():
    import io
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "gray").save(buf, "PNG")
    assert classify(buf.getvalue()).is_interesting
