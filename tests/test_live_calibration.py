"""Live calibration (refits from the scan log), visibility and pooling."""

import json

import numpy as np
import pytest

from babel_surfer import benchmark as B, calibrate, classifier, nullmap, perceptual, visibility
from babel_surfer import scan as scan_mod
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import TESTS
from babel_surfer.storage import Store


def _log(n, rng):
    log = np.zeros(n, dtype=Store.score_log_dtype())
    log["location"] = np.arange(10**12, 10**12 + n)
    for t in TESTS:
        raw = rng.standard_normal((n, 20)).max(axis=1)
        log[f"raw_{t}"] = raw
        log[t] = raw
    return log


@pytest.fixture
def package(tmp_path, monkeypatch):
    """A package calibration where 'spot' is still simulated."""
    rng = np.random.default_rng(1)
    maps = {t: nullmap.fit(rng.standard_normal((3000, 20)).max(axis=1)) for t in TESTS}
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"tests": {}, "maps": maps,
                               "map_sources": {"spot": {"source": "simulated", "frames": 3000}}}))
    monkeypatch.setattr(classifier, "PACKAGE_CALIBRATION", pkg)
    monkeypatch.setattr(calibrate, "CALIBRATION_FILE", pkg)
    classifier.use_calibration(pkg)
    yield pkg
    classifier.use_calibration(classifier.Path(classifier.__file__).with_name("calibration.json"))


def test_refit_live_once_then_idle(tmp_path, package):
    data = tmp_path / "data"
    data.mkdir()
    np.save(data / "scores.npy", _log(6000, np.random.default_rng(2)))
    assert calibrate.refit_live(data) is True
    live = json.loads((data / "calibration.json").read_text())
    assert live["map_sources"]["spot"]["source"] == "real"
    assert classifier.live_calibration_path(data) == data / "calibration.json"
    assert calibrate.refit_live(data) is False                  # nothing left to refit
    package.write_text(package.read_text().replace('"frames": 3000', '"frames": 3001'))
    assert classifier.live_calibration_path(data) is None       # new package: live is stale


def test_refit_live_waits_for_enough_frames(tmp_path, package):
    data = tmp_path / "data"
    data.mkdir()
    np.save(data / "scores.npy", _log(1000, np.random.default_rng(3)))
    assert calibrate.refit_live(data) is False
    assert not (data / "calibration.json").exists()


def test_pool_workers_score_with_the_live_calibration(tmp_path, monkeypatch):
    """A live calibration that makes every frame a find: if the classifier
    processes ignored it, the scan would find nothing."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "calibration.json").write_text(json.dumps({
        "base": classifier.package_calibration_hash(), "maps": {},
        "tests": {t: {"mean": -100.0, "std": 1.0} for t in TESTS}}))
    frame = babel_static(np.random.default_rng(4))
    monkeypatch.setattr(scan_mod, "fetch", lambda loc: frame)
    monkeypatch.setattr(scan_mod.Store, "_write_assets",
                        lambda self, loc, img, res: {"maps": None, "mask": None, "description": "d"})
    try:
        run = scan_mod.scan(str(data), minutes=1, workers=1, start=0, max_frames=2, processes=1)
    finally:
        classifier.use_calibration(classifier.PACKAGE_CALIBRATION)
    assert run["scanned"] == 2 and run["finds"] == 2


def test_visibility_static_vs_obvious_picture():
    import io
    from PIL import Image
    load = lambda d: np.asarray(Image.open(io.BytesIO(d)).convert("RGB"), dtype=np.float64)
    static = visibility.visibility(load(babel_static(np.random.default_rng(5))))
    disc = visibility.visibility(load(B._blend(np.random.default_rng(6), *B.pic_disc(), 1.0)))
    assert static["label"] == "invisible" and static["ratio"] < visibility.FAINT
    assert disc["label"] == "likely" and disc["ratio"] > 10


@pytest.mark.parametrize("make", [
    lambda r: B._diagonal_line(r, 1.0),            # a 2 px line: barely moves any patch's mean colour
    lambda r: B._blend(r, *B.pic_line(), 1.0),     # a horizon line
    lambda r: B._grey_patch(r, 1.0),               # a flat grey square: same mean, no flicker
    lambda r: B._special(r, "greyscale static"),   # the whole frame loses its colour
])
def test_visibility_sees_lines_and_smooth_patches(make):
    """The old model compared only patch-average colour and rated all of these
    invisible (about 1.0), though a person sees each at a glance."""
    import io
    from PIL import Image
    rgb = np.asarray(Image.open(io.BytesIO(make(np.random.default_rng(3)))).convert("RGB"), dtype=np.float64)
    v = visibility.visibility(rgb)
    assert v["label"] == "likely", v


def test_visibility_points_at_the_line():
    import io
    from PIL import Image
    rgb = np.asarray(Image.open(io.BytesIO(B._diagonal_line(np.random.default_rng(4), 1.0))).convert("RGB"),
                     dtype=np.float64)
    v = visibility.visibility(rgb)
    assert v["cue"] == "line"
    # the line runs from (40, 400) to (600, 77): the marked point lies on it
    y_on_line = 400 + (v["x"] - 40) * (77 - 400) / (600 - 40)
    assert abs(v["y"] - y_on_line) < 6, v
    # direction (cos, sin) of the angle in image coordinates (y down): rising to the right is ~150
    assert v["angle"] in (135, 150, 165), v


@pytest.mark.parametrize("k,stride", [(8, 4), (16, 8), (16, 16), (64, 32)])
def test_vectorised_pool_matches_loop(k, stride):
    z = np.random.default_rng(k).standard_normal(((416 - k) // stride + 1, (640 - k) // stride + 1))
    assert np.array_equal(perceptual._pool(z, 16, stride, k, 26, 40),
                          perceptual._pool_loop(z, 16, stride, k, 26, 40))


def test_finds_over_the_cap_are_counted_once_and_not_written(tmp_path, monkeypatch):
    from babel_surfer import storage
    from babel_surfer.classifier import Classification
    writes = []
    monkeypatch.setattr(storage.Store, "_write_assets",
                        lambda self, loc, img, res: writes.append(loc) or {"maps": None})
    monkeypatch.setattr(storage, "MAX_FINDS", 2)
    monkeypatch.setattr(storage, "LEADERS_KEEP", 2)      # so frame 3 isn't a leader either
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    mk = lambda z: Classification(sigma=z, label="anomaly", is_interesting=True, reason="shape",
                                  tests={"shape": z})
    s.record("1", b"", mk(9.0), run)
    s.record("2", b"", mk(8.0), run)
    writes.clear()
    assert s.record("3", b"", mk(4.5), run) == "find"          # counted...
    assert "3" not in writes                                   # ...but no assets written
    total = s.stats["finds_total"]
    s.record("3", b"", mk(4.5), run, count=False)              # re-mined: not counted again
    assert s.stats["finds_total"] == total
