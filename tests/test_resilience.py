"""The paths that only run when something goes wrong in production: a
classifier process dies, a frame can't be scored or stored, Babelia keeps
failing, a confirming re-download fails, a re-mined frame is damaged, and the
alert bookkeeping between 30-minute chunks. Each must keep the scan going,
count the problem, and never turn it into a find or a duplicate alert."""

import io
import json
from concurrent.futures.process import BrokenProcessPool

import numpy as np
import pytest
from PIL import Image

from babel_surfer import alerts, calibrate, classifier, remine, storage
from babel_surfer import scan as scan_mod
from babel_surfer.calibrate import babel_static
from babel_surfer.classifier import TESTS, Classification
from babel_surfer.storage import Store

FRAME = babel_static(np.random.default_rng(5))


@pytest.fixture(autouse=True)
def fast_analysis(monkeypatch):
    monkeypatch.setattr(storage.analysis, "analyze", lambda img: {"stub": True})
    monkeypatch.setattr(storage.analysis, "describe", lambda tests, maps, sigma: "desc")
    monkeypatch.setattr(storage.analysis, "tone_mask_png", lambda img, maps: b"png")


def fake(sigma, interesting=False):
    return Classification(sigma=sigma, label="x", is_interesting=interesting,
                          reason="shape", tests={"shape": sigma})


def grey_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 416), (200, 200, 200)).save(buf, "JPEG")
    return buf.getvalue()


# --- the scan loop ------------------------------------------------------------

def test_a_frame_that_cannot_be_scored_or_stored_does_not_stop_the_scan(tmp_path, monkeypatch):
    """One frame's classifier crashes, another's storage write fails: both are
    counted as errors and every other frame is still scanned (the worker
    thread must not die with them)."""
    def classify(data, threshold=4.0):
        return fake(1.0)
    real_record = Store.record

    def record(self, loc, *a, **k):
        if loc == "4":
            raise OSError("disk full")
        return real_record(self, loc, *a, **k)

    seen = []

    def fetch(loc):
        seen.append(loc)
        return FRAME

    def classify_some(data, threshold=4.0):
        if seen and seen[-1] == "2":
            raise ValueError("classifier bug")
        return classify(data, threshold)
    monkeypatch.setattr(scan_mod, "fetch", fetch)
    monkeypatch.setattr(scan_mod, "classify", classify_some)
    monkeypatch.setattr(Store, "record", record)
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=1, start=0, max_frames=6, processes=0)
    assert sorted(seen) == ["1", "2", "3", "4", "5", "6"]
    assert run["scanned"] == 4 and run["errors"] == 2


def test_scoring_falls_back_to_the_fetch_threads_when_the_process_pool_breaks(tmp_path, monkeypatch):
    """A classifier process killed (say, out of memory) breaks the pool. The
    scan must keep scoring in its own threads, not throw every later frame
    away."""
    class BrokenPool:
        made, shut = 0, 0

        def __init__(self, *a, **k):
            BrokenPool.made += 1

        def submit(self, fn, *a, **k):
            class Future:
                def result(self):
                    raise BrokenProcessPool("a worker died")
            return Future()

        def shutdown(self, *a, **k):
            BrokenPool.shut += 1

    monkeypatch.setattr(scan_mod, "ProcessPoolExecutor", BrokenPool)
    monkeypatch.setattr(scan_mod, "fetch", lambda loc: FRAME)
    monkeypatch.setattr(scan_mod, "classify", lambda data, threshold=4.0: fake(1.0))
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=2, max_workers=2, start=0,
                        max_frames=8, processes=1)
    assert run["scanned"] == 8 and run["errors"] == 0
    assert BrokenPool.made == 1 and BrokenPool.shut == 1        # dropped once, never reused


def test_frames_babelia_never_serves_are_counted_by_kind_and_the_scan_goes_on(tmp_path, monkeypatch):
    def fetch(loc):
        if int(loc) % 2:
            raise RuntimeError(f"location {loc}: HTTP Error 500: Internal Server Error")
        return FRAME
    monkeypatch.setattr(scan_mod, "fetch", fetch)
    monkeypatch.setattr(scan_mod, "classify", lambda data, threshold=4.0: fake(1.0))
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=1, start=0, max_frames=6, processes=0)
    assert run["scanned"] == 3 and run["errors"] == 3
    assert run["error_kinds"] == {"HTTP 500": 3}


@pytest.mark.parametrize("second", ["raises", "not a Babelia frame"])
def test_a_find_whose_confirming_download_fails_is_not_kept(tmp_path, monkeypatch, second):
    """The first download scores as a find; the re-download that must match
    it byte for byte fails, or comes back as something that isn't a Babelia
    frame. Either way: unconfirmed, not a find."""
    calls = []

    def fetch(loc):
        calls.append(loc)
        if len(calls) == 1:
            return FRAME
        if second == "raises":
            raise RuntimeError(f"location {loc}: timed out")
        return grey_jpeg()
    monkeypatch.setattr(scan_mod, "fetch", fetch)
    monkeypatch.setattr(scan_mod, "classify", lambda data, threshold=4.0: fake(9.0, True))
    run = scan_mod.scan(str(tmp_path), minutes=1, workers=1, start=0, max_frames=1, processes=0)
    assert run["finds"] == 0 and run["unconfirmed"] == 1 and len(calls) == 2
    assert json.loads((tmp_path / "finds.json").read_text()) == []


def test_fetch_retries_a_reply_that_is_not_a_jpeg_then_gives_up(monkeypatch):
    """Babelia answering with an HTML error page: retried, then a clear
    error that error_kind files as 'not a JPEG'."""
    calls = []

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html>Service unavailable</html>"

    monkeypatch.setattr(scan_mod.urllib.request, "urlopen", lambda req, timeout: calls.append(1) or Resp())
    monkeypatch.setattr(scan_mod.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as e:
        scan_mod.fetch("12", retries=3)
    assert len(calls) == 3 and scan_mod.error_kind(e.value) == "not a JPEG"


# --- re-mining after a recalibration ------------------------------------------

def _remine_store(tmp_path, monkeypatch):
    """A score log of 6,000 static frames plus one strong shape frame, scored
    under a legacy calibration, so re-mining with a fitted one finds it."""
    from test_nullmap_remine import legacy_log
    legacy = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}
    log, _ = legacy_log(np.random.default_rng(5), 6000, legacy)
    log["shape"][123] = 6.0
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"tests": legacy}))
    np.save(tmp_path / "log.npy", log)
    calibrate.calibrate_from_log(tmp_path / "log.npy", cal)
    monkeypatch.setattr(classifier, "CALIBRATION_FILE", cal)
    monkeypatch.setattr(classifier, "_calibration_cache", None)
    monkeypatch.setattr(classifier, "load_calibration",
                        lambda path=cal, _f=classifier.load_calibration: _f(path))
    data = tmp_path / "data"
    data.mkdir()
    np.save(data / "scores.npy", log)
    return Store(data), str(int(log["location"][123]))


@pytest.mark.parametrize("trouble", ["not a Babelia frame", "two downloads differ", "storage fails"])
def test_remine_never_keeps_a_frame_it_cannot_trust(tmp_path, monkeypatch, trouble):
    """Re-mining fetches the frames a new calibration lifts over the
    threshold. The target frame's download is not a Babelia frame, or two
    downloads of it differ (damaged in transit), or it can't be stored: it
    must be counted as an error, never become a find, and the rest of the
    re-mine must still finish."""
    store, target = _remine_store(tmp_path, monkeypatch)
    n = {}

    def fetch(loc):
        n[loc] = n.get(loc, 0) + 1
        if loc == target and trouble == "not a Babelia frame":
            return grey_jpeg()
        if loc == target and trouble == "two downloads differ":
            return babel_static(np.random.default_rng(n[loc]))    # different every time
        return babel_static(np.random.default_rng(int(loc) % 1000))

    def classify(data, threshold):
        # the log says 6 sigma; a damaged copy reads 1 sigma, so it is re-fetched
        z = 6.0 if trouble != "two downloads differ" else 1.0
        return Classification(sigma=z, label="x", is_interesting=z >= threshold,
                              reason="shape", tests={"shape": z})
    if trouble == "storage fails":
        real = Store.record

        def record(self, loc, *a, **k):
            if loc == target:
                raise OSError("disk full")
            return real(self, loc, *a, **k)
        monkeypatch.setattr(Store, "record", record)
    run = remine.mine(store, 4.0, workers=1, fetch=fetch, classify=classify)
    assert run is not None and target in n
    assert target not in [f["location"] for f in store.finds]
    assert run["errors"] >= 1
    assert store.stats["log_calibration"] == classifier.calibration_hash()   # finished anyway


# --- alerts between chunks ------------------------------------------------------

def _write(data_dir, stats, finds=()):
    (data_dir / "stats.json").write_text(json.dumps(stats))
    (data_dir / "finds.json").write_text(json.dumps(list(finds)))


def test_alerts_fire_once_per_find_and_once_per_halt(tmp_path):
    """The workflow runs alerts after every 30-minute chunk and turns each
    alert file into a GitHub issue, which emails the watchers. A big find
    must be reported once, not again every chunk; the same for a halt."""
    d = tmp_path / "data"
    d.mkdir()
    feed, alert = tmp_path / "feed.xml", tmp_path / "alert.md"
    halt_file = alert.with_name("alert-halt.md")
    args = ["--data", str(d), "--feed", str(feed), "--alert", str(alert)]
    big = {"location": "77", "sigma": 25.0, "tier": "clear", "reason": "blob", "found_at": 100}
    run1 = {"started_at": 90, "finished_at": 100, "scanned": 900, "new_finds": [big]}
    _write(d, {"runs": [run1], "last_run": run1}, [big])

    alerts.main(args)
    assert alert.exists() and "#77" in alert.read_text() and feed.exists()
    alerts.main(args)                                     # next chunk: nothing new
    assert not alert.exists()

    run2 = {"started_at": 101, "finished_at": 130, "scanned": 900, "new_finds": []}
    _write(d, {"runs": [run1, run2], "last_run": run2}, [big])
    alerts.main(args)
    assert not alert.exists()                              # a quiet chunk stays quiet

    run3 = {"started_at": 131, "finished_at": 160, "scanned": 5, "new_finds": []}
    halted = {"at": 160.0, "reason": "9 finds in 20 frames"}
    _write(d, {"runs": [run1, run2, run3], "last_run": run3, "halted": halted,
               "quarantine": [{"location": "5", "sigma": 9.0, "reason": "shape"}]}, [big])
    alerts.main(args)
    assert halt_file.exists() and "9 finds in 20 frames" in halt_file.read_text()
    halt_file.unlink()                                     # the workflow files it as an issue
    alerts.main(args)
    assert not halt_file.exists()                          # same halt: no second issue
