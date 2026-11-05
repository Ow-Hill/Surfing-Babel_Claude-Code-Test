import json

import pytest

import numpy as np

from babel_surfer import scan as scan_mod
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import Classification
from babel_surfer import storage
from babel_surfer.storage import NEAR_MISS_KEEP, Store, hist_bin

FRAME = babel_static(np.random.default_rng(5))     # a real ~460 KB Babel-like JPEG


@pytest.fixture(autouse=True)
def fast_analysis(request, monkeypatch):
    """The real analysis takes ~1.5 s per frame; most storage tests only
    need its outputs to exist."""
    if "real_analysis" in request.keywords:
        return
    monkeypatch.setattr(storage.analysis, "analyze", lambda img: {"stub": True})
    monkeypatch.setattr(storage.analysis, "describe", lambda tests, maps, sigma: "desc")
    monkeypatch.setattr(storage.analysis, "tone_mask_png", lambda img, maps: b"png")


def fake(sigma, interesting=False):
    return Classification(sigma=sigma, label="x", is_interesting=interesting,
                          reason="shape", tests={"shape": sigma})


def test_store_finds_near_misses_and_persistence(tmp_path):
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    for i in range(NEAR_MISS_KEEP + 5):
        s.record(str(1000 + i), FRAME, fake(float(i)), run)
    assert s.record("42", FRAME, fake(99, True), run) == "find"
    assert s.record("42", FRAME, fake(99, True), run) is None   # dedupe
    s.add_run(run)
    s.save()

    assert len(s.near) == NEAR_MISS_KEEP
    assert s.near[0]["sigma"] == NEAR_MISS_KEEP + 4
    imgs = {p.stem for p in (tmp_path / "images").glob("*.jpg")}
    refs = s._referenced()
    assert imgs == refs and {n["location"] for n in s.near} | {"42"} <= refs
    maps = {p.name.split(".")[0] for p in (tmp_path / "images").glob("*.maps.json")}
    assert maps == refs
    assert all(e["description"] == "desc" for e in s.near + s.finds)

    s2 = Store(tmp_path)
    assert s2.stats["scanned"] == NEAR_MISS_KEEP + 7
    assert [f["location"] for f in s2.finds] == ["42"]
    assert json.loads((tmp_path / "stats.json").read_text())["best"]["location"] == "42"


def test_promote_when_threshold_lowered(tmp_path):
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    s.record("7", FRAME, fake(5.0), run)
    s.record("8", FRAME, fake(2.0), run)
    assert s.promote(4.0) == 1
    assert [f["location"] for f in s.finds] == ["7"] and s.finds[0]["tier"] == "anomaly"
    assert [n["location"] for n in s.near] == ["8"]


def test_images_are_small_previews(tmp_path):
    s = Store(tmp_path)
    s.record("9", FRAME, fake(9, True), {"scanned": 0, "finds": 0, "errors": 0})
    size = (tmp_path / "images" / "9.jpg").stat().st_size
    assert len(FRAME) > 300_000 and size < 60_000


def test_finds_capped_to_top_n(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "MAX_FINDS", 3)
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    for i, sig in enumerate([5, 9, 4.5, 7, 6]):
        s.record(str(100 + i), FRAME, fake(sig, True), run)
    s.save()
    assert sorted(f["sigma"] for f in s.finds) == [6, 7, 9]
    assert s.stats["finds_total"] == 5 and run["finds"] == 5
    imgs = {p.stem for p in (tmp_path / "images").glob("*.jpg")}
    assert {"101", "103", "104"} <= imgs and imgs == s._referenced()
    assert [f["location"] for f in run["new_finds"]] == ["100", "101", "102", "103", "104"]


def test_migrates_full_size_images(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "1.jpg").write_bytes(FRAME)
    (tmp_path / "images" / "orphan.jpg").write_bytes(FRAME)
    (tmp_path / "finds.json").write_text(json.dumps([{
        "location": "1", "sigma": 9, "tier": "faint", "reason": "shape", "tests": {},
        "found_at": 0, "image": "images/1.jpg"}]))
    s = Store(tmp_path)
    assert (tmp_path / "images" / "1.jpg").stat().st_size < 60_000
    assert s.missing_assets() == s.finds and s.finds[0]["maps"] is None
    assert not (tmp_path / "images" / "orphan.jpg").exists()
    assert s.stats["finds_total"] == 1


def test_leaderboards_and_score_log(tmp_path):
    import numpy as np
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    for i in range(8):
        c = Classification(sigma=1.0, label="static", is_interesting=False, reason="shape",
                           tests={"shape": float(i), "tone": float(-i)}, combined=float(i % 3),
                           fingerprint=14.0 + i)
        s.record(str(500 + i), FRAME, c, run)
    s.save()
    assert [e["location"] for e in s.leaders["shape"]] == ["507", "506", "505"]
    assert [e["location"] for e in s.leaders["tone"]] == ["500", "501", "502"]
    assert {p.stem for p in (tmp_path / "images").glob("*.jpg")} == s._referenced()
    log = np.load(tmp_path / "scores.npy")
    assert len(log) == 8 and int(log["location"][0]) == 500 and float(log["fingerprint"][-1]) == 21.0
    fp = s.stats["fingerprint"]
    assert fp["n"] == 8 and fp["min"]["location"] == "500"
    assert sum(s.stats["test_hist"]["counts"]["shape"]) == 8
    s.add_run(dict(run, new_finds=[{"location": "1", "sigma": 5, "tier": "anomaly", "reason": "tone",
                                    "tests": {"x": 1}, "maps": "m"}]))
    assert s.stats["runs"][-1]["new_finds"][0].keys() == {"location", "sigma", "tier", "reason", "description"}


def test_evicted_while_writing_assets_leaves_no_files(tmp_path, monkeypatch):
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    real = s._write_assets

    def slow(location, image, result):          # another frame pushes this one out meanwhile
        out = real(location, image, result)
        s.near.clear()
        s.leaders.clear()
        return out
    monkeypatch.setattr(s, "_write_assets", slow)
    s.record("77", FRAME, fake(3.5), run)
    assert not list((tmp_path / "images").glob("77.*"))


def test_hist_bin():
    assert hist_bin(-5) == 0 and hist_bin(0.5) == 3 and hist_bin(1000) == 13


def test_scan_end_to_end_with_stubbed_network(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    seen = []

    def fake_fetch(loc):
        seen.append(int(loc))
        return babel_static(rng)

    monkeypatch.setattr(scan_mod, "fetch", fake_fetch)
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=2, start=500, max_frames=6)
    assert run["scanned"] >= 6 and run["finds"] == 0
    assert sorted(seen)[:6] == list(range(501, 507))     # sequential, like the slideshow
    assert json.loads((tmp_path / "stats.json").read_text())["scanned"] == run["scanned"]


@pytest.mark.real_analysis
def test_real_assets_and_enrich(tmp_path):
    from babel_surfer.classifier import classify
    s = Store(tmp_path)
    c = classify(FRAME)
    entry = {"location": "5", "sigma": 0, "tier": "static", "reason": "shape", "tests": {},
             "found_at": 0, "image": "images/5.jpg", "maps": None}
    s.near.append(entry)
    s.enrich(entry, FRAME, c)
    assert entry["maps"] == "images/5.maps.json" and entry["description"].startswith("Statistically")
    maps = json.loads((tmp_path / "images" / "5.maps.json").read_text())
    assert len(maps["mean"]) == 40 * 26 and len(maps["tone"]) == 3 and entry["sigma"] == c.sigma


def test_max_frames_is_exact_with_many_workers(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    frame = babel_static(rng)
    calls = []
    monkeypatch.setattr(scan_mod, "fetch", lambda loc: calls.append(loc) or frame)
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=4, start=0, max_frames=9, max_workers=6)
    assert len(calls) == 9 and run["scanned"] == 9


def test_politeness_backs_off_and_recovers():
    import threading
    p = scan_mod.Politeness(start=3, min_workers=1, max_workers=5)
    stop = threading.Event()
    for _ in range(12):                      # fast, steady server: opens up
        assert p.acquire(stop)
        p.release(1.0)
    assert p.limit > 3
    high = p.limit
    for _ in range(12):                      # server slows to 3x baseline: backs off
        p.acquire(stop)
        p.release(3.0)
    assert p.limit < high
    before = p.limit
    p.acquire(stop)
    p.release(None)                          # an error: immediate step down
    assert p.limit == max(1, before - 1)


def test_reconcile_after_rescoring(tmp_path):
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    s.record("1", FRAME, fake(5.0, True), run)      # find
    s.record("2", FRAME, fake(3.5), run)            # near miss
    s.finds[0]["sigma"] = 3.2                        # re-scored below the threshold
    s.near[[n["location"] for n in s.near].index("2")]["sigma"] = 4.4   # re-scored above it
    dropped = s.reconcile(4.0)
    assert dropped == 1
    assert [f["location"] for f in s.finds] == ["2"] and s.finds[0]["tier"] == "anomaly"
    assert all(n["sigma"] < 4.0 for n in s.near)
    assert {p.stem for p in (tmp_path / "images").glob("*.jpg")} == s._referenced()
