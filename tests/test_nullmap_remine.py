"""Empirical calibration (nullmap), log-based recalibration and re-mining."""

import json
from statistics import NormalDist

import numpy as np

from babel_surfer import calibrate, classifier, nullmap, remine, storage
from babel_surfer.classifier import TESTS, Classification
from babel_surfer.storage import Store


def max_type(rng, n, k=50):
    """A max-of-k-Gaussians statistic, like the detector's scan tests."""
    return rng.standard_normal((n, k)).max(axis=1)


def test_map_makes_max_statistics_standard_normal():
    rng = np.random.default_rng(1)
    m = nullmap.fit(max_type(rng, 20000))
    z = nullmap.sigma_array(m, max_type(rng, 20000))
    assert abs(z.mean()) < 0.05 and abs(z.std() - 1) < 0.05
    expected = 20000 * (1 - NormalDist().cdf(3))            # ~27
    assert abs((z > 3).sum() - expected) < 4 * np.sqrt(expected)


def test_map_is_monotone_and_keeps_pictures_far_above_threshold():
    rng = np.random.default_rng(2)
    m = nullmap.fit(max_type(rng, 5000))
    xs = np.linspace(-2, 400, 4000)
    z = nullmap.sigma_array(m, xs)
    assert np.all(np.diff(z) >= -1e-9)
    assert nullmap.sigma(m, 300.0) > 290            # far tail: about one-for-one
    assert np.allclose(z[::97], [nullmap.sigma(m, float(x)) for x in xs[::97]])


def test_point_mass_at_zero_gets_mid_probability():
    rng = np.random.default_rng(3)
    v = np.maximum(0, rng.standard_normal(10000))   # half the mass exactly at 0
    m = nullmap.fit(v)
    assert m["atom"] and abs(m["atom"]["p"] - 0.5) < 0.02
    assert abs(nullmap.sigma(m, 0.0) - NormalDist().inv_cdf(0.25)) < 0.05


def test_classify_falls_back_to_mean_std_without_maps(monkeypatch):
    monkeypatch.setattr(classifier, "_calibration_cache", {
        "tests": {t: {"mean": 1.0, "std": 0.5} for t in TESTS}, "maps": {},
        "tone_expected": None})
    assert classifier.calibrate_scores({"shape": 2.0})["shape"] == 2.0


def legacy_log(rng, n, legacy):
    """A score log in the old format: z = (raw - mean) / std, float16."""
    dtype = [("location", "<u8")] + [(c, "<f2") for c in Store.SCORE_COLUMNS[1:]]
    log = np.zeros(n, dtype=dtype)
    log["location"] = np.arange(10**12, 10**12 + n)
    raw = {t: max_type(rng, n, 20) for t in TESTS}
    for t in TESTS:
        log[t] = (raw[t] - legacy[t]["mean"]) / legacy[t]["std"]
    return log, raw


def test_old_logs_upgrade_and_raw_round_trips():
    legacy = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}
    log, raw = legacy_log(np.random.default_rng(4), 2000, legacy)
    up = Store.upgrade_score_log(log)
    assert "raw_shape" in up.dtype.names and np.isnan(up["raw_shape"]).all()
    assert np.array_equal(up["location"], log["location"])
    back = calibrate.raw_from_log(log, legacy)
    for t in TESTS:                                    # float16 precision
        assert np.allclose(back[t], raw[t], atol=4e-3)


def test_calibrate_from_log_and_remine(tmp_path, monkeypatch):
    legacy = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}
    rng = np.random.default_rng(5)
    log, _ = legacy_log(rng, 6000, legacy)
    # one frame with a strong but not picture-like shape signal
    log["shape"][123] = 6.0
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"tests": legacy}))
    np.save(tmp_path / "log.npy", log)
    rep = calibrate.calibrate_from_log(tmp_path / "log.npy", cal)
    assert abs(rep["validation"]["blob"]["sd"] - 1) < 0.1
    monkeypatch.setattr(classifier, "CALIBRATION_FILE", cal)
    monkeypatch.setattr(classifier, "_calibration_cache", None)
    monkeypatch.setattr(classifier, "load_calibration",
                        lambda path=cal, _f=classifier.load_calibration: _f(path))

    data = tmp_path / "data"
    data.mkdir()
    np.save(data / "scores.npy", log)
    monkeypatch.setattr(storage.analysis, "analyze", lambda img: {"stub": True})
    monkeypatch.setattr(storage.analysis, "describe", lambda tests, maps, sigma: "desc")
    monkeypatch.setattr(storage.analysis, "tone_mask_png", lambda img, maps: b"png")
    store = Store(data)
    scanned_before = store.stats["scanned"]
    target = str(int(log["location"][123]))
    fetched = []

    def fake_fetch(loc):
        fetched.append(loc)
        return calibrate.babel_static(np.random.default_rng(int(loc) % 1000))

    def fake_classify(data, threshold):
        z = 6.0 if fetched[-1] == target else 1.0
        return Classification(sigma=z, label="anomaly" if z >= 4 else "static",
                              is_interesting=z >= threshold, reason="shape",
                              tests={"shape": z})

    run = remine.mine(store, 4.0, workers=1, fetch=fake_fetch, classify=fake_classify)
    assert run and target in fetched and len(fetched) <= remine.MAX_REFETCH
    assert [f["location"] for f in store.finds] == [target]
    assert store.stats["scanned"] == scanned_before          # nothing double-counted
    assert sum(store.stats["hist_current"]["counts"]) == 6000
    new = np.load(data / "scores.npy")
    assert "raw_shape" in new.dtype.names and np.isfinite(new["raw_shape"]).all()
    # second run: the log already matches the calibration
    assert remine.mine(store, 4.0, fetch=fake_fetch, classify=fake_classify) is None
    # ...and stays a no-op after a scan logs more frames: nothing re-fetched, totals stable
    total, n_fetched = store.stats["finds_total"], len(fetched)
    store._score_log.append(tuple([10**13] + [0.5] * (len(Store.SCORE_COLUMNS) - 1)
                                  + [1.0] * (len(Store.SCORE_COLUMNS) - 3)))
    store.save()
    assert remine.mine(store, 4.0, fetch=fake_fetch, classify=fake_classify) is None
    assert len(fetched) == n_fetched and store.stats["finds_total"] == total


def test_tail_fit_ignores_a_real_picture_in_the_log():
    rng = np.random.default_rng(7)
    v = max_type(rng, 20000)
    clean = nullmap.fit(v)
    dirty = nullmap.fit(np.concatenate([v, [500.0, 1e6]]))   # a picture and a flat frame
    probe = float(np.quantile(v, 0.9999))
    assert abs(nullmap.sigma(dirty, probe) - nullmap.sigma(clean, probe)) < 0.3


def test_scores_stay_finite():
    m = nullmap.fit(max_type(np.random.default_rng(8), 5000))
    out = nullmap.sigma_array(m, np.array([np.inf, -np.inf, np.nan, 1e9]))
    assert out[0] == nullmap.Z_MAX and out[1] == nullmap.Z_FLOOR and np.isnan(out[2])
    assert out[3] <= nullmap.Z_MAX and np.isfinite(np.float16(out[3]))


def test_steep_tail_is_not_inflated_within_the_data():
    """A statistic whose tail is steeper than one-for-one (like the colours
    test's robust z of counts) must read its empirical rarity, not more."""
    rng = np.random.default_rng(9)
    v = np.round(rng.standard_normal(20000) * 1.5, 1)      # raw outpaces sigma, with ties
    m = nullmap.fit(v)
    probe = float(np.quantile(v, 1 - 5 / 20000))          # 1 in 4,000
    expected = -NormalDist().inv_cdf(5 / 20000)            # about 3.5
    assert abs(nullmap.sigma(m, probe) - expected) < 0.35
    held_out = nullmap.sigma_array(m, np.round(rng.standard_normal(200000) * 1.5, 1))
    assert (held_out >= 4).sum() < 4 * 200000 * (1 - NormalDist().cdf(4)) + 10


def test_refit_live_refits_when_the_log_doubles(tmp_path, monkeypatch):
    legacy = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}
    log, _ = legacy_log(np.random.default_rng(10), 6000, legacy)
    (tmp_path / "pkg.json").write_text(json.dumps({"tests": legacy}))
    monkeypatch.setattr(calibrate, "CALIBRATION_FILE", tmp_path / "pkg.json")
    monkeypatch.setattr(classifier, "package_calibration_hash", lambda: "pkg")
    monkeypatch.setattr(classifier, "live_calibration_path",
                        lambda d: (d / "calibration.json") if (d / "calibration.json").exists() else None)
    np.save(tmp_path / "scores.npy", log)
    assert calibrate.refit_live(tmp_path)                  # first fit: simulated -> real
    assert not calibrate.refit_live(tmp_path)              # nothing new
    bigger, _ = legacy_log(np.random.default_rng(11), 12000, legacy)
    np.save(tmp_path / "scores.npy", bigger)
    assert calibrate.refit_live(tmp_path)                  # the log doubled
    src = json.loads((tmp_path / "calibration.json").read_text())["map_sources"]["shape"]
    assert src["frames"] == 12000
