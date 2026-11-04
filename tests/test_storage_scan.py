import json

import numpy as np

from babel_surfer import scan as scan_mod
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import Classification
from babel_surfer import storage
from babel_surfer.storage import NEAR_MISS_KEEP, Store, hist_bin

FRAME = babel_static(np.random.default_rng(5))     # a real ~460 KB Babel-like JPEG


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
    imgs = {p.name for p in (tmp_path / "images").glob("*.jpg")}
    assert imgs == {f"{n['location']}.jpg" for n in s.near} | {"42.jpg"}
    heats = {p.name for p in (tmp_path / "images").glob("*.heat.png")}
    assert heats == {f"{n['location']}.heat.png" for n in s.near} | {"42.heat.png"}

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
    assert {p.stem for p in (tmp_path / "images").glob("*.jpg")} == {"101", "103", "104"}
    assert len(list((tmp_path / "images").glob("*.heat.png"))) == 3
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
    assert (tmp_path / "images" / "1.heat.png").exists() and s.finds[0]["heat"]
    assert not (tmp_path / "images" / "orphan.jpg").exists()
    assert s.stats["finds_total"] == 1


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
