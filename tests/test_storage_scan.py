import json

import numpy as np

from babel_surfer import scan as scan_mod
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import Classification
from babel_surfer.storage import NEAR_MISS_KEEP, Store, hist_bin


def fake(sigma, interesting=False):
    return Classification(sigma=sigma, label="x", is_interesting=interesting,
                          reason="shape", tests={"shape": sigma})


def test_store_finds_near_misses_and_persistence(tmp_path):
    s = Store(tmp_path)
    run = {"scanned": 0, "finds": 0, "errors": 0}
    for i in range(NEAR_MISS_KEEP + 5):
        s.record(str(1000 + i), b"\xff\xd8img", fake(float(i)), run)
    assert s.record("42", b"\xff\xd8find", fake(99, True), run) == "find"
    assert s.record("42", b"\xff\xd8find", fake(99, True), run) is None   # dedupe
    s.add_run(run)
    s.save()

    assert len(s.near) == NEAR_MISS_KEEP
    assert s.near[0]["sigma"] == NEAR_MISS_KEEP + 4
    imgs = {p.name for p in (tmp_path / "images").iterdir()}
    assert imgs == {f"{n['location']}.jpg" for n in s.near} | {"42.jpg"}

    s2 = Store(tmp_path)
    assert s2.stats["scanned"] == NEAR_MISS_KEEP + 7
    assert [f["location"] for f in s2.finds] == ["42"]
    assert json.loads((tmp_path / "stats.json").read_text())["best"]["location"] == "42"


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
