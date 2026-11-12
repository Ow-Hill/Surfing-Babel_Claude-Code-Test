"""The tools around the scan: the calibration command line (the workflow
runs --refit-live after every chunk), fitting a new test on simulated static,
the blind-spot probe end to end (its workflow runs daily), the image search
behind the demos, and the benchmark file the site reads."""

import json

import numpy as np
import pytest

from babel_surfer import benchmark, calibrate, classifier, seeded, sensitivity
from babel_surfer.classifier import TESTS, Classification


# --- calibration -------------------------------------------------------------

def test_calibrate_command_line_dispatches_each_mode(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(calibrate, "refit_live", lambda d: calls.append(("refit", d)) or True)
    monkeypatch.setattr(calibrate, "calibrate_simulated",
                        lambda out, n: calls.append(("sim", out, n)) or {})
    assert calibrate.main(["--refit-live", str(tmp_path)]) == 0
    assert calibrate.main(["--simulate-missing", "40", "--out", str(tmp_path / "c.json")]) == 0
    assert calls == [("refit", tmp_path), ("sim", tmp_path / "c.json", 40)]


def test_calibrate_from_a_log_on_the_command_line_writes_maps_and_a_report(tmp_path, capsys):
    from test_nullmap_remine import legacy_log
    legacy = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}
    log, _ = legacy_log(np.random.default_rng(5), 6000, legacy)
    np.save(tmp_path / "log.npy", log)
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"tests": legacy}))
    assert calibrate.main(["--from-log", str(tmp_path / "log.npy"), "--out", str(cal)]) == 0
    maps = json.loads(cal.read_text())["maps"]
    assert "shape" in maps and "tone" in maps
    report = capsys.readouterr().out
    assert report.splitlines()[0].split()[:2] == ["test", "sd"] and "shape" in report


def _fast_raw(seed, tests):
    """Stands in for simulating a frame: one standard-normal statistic per test."""
    return {t: float(np.random.default_rng(seed).normal()) for t in tests}


def test_a_new_test_gets_a_map_from_simulated_static_and_nothing_else_changes(tmp_path, monkeypatch):
    """When a test is added it has no real frames yet: calibrate_simulated
    fits it on simulated static and must leave every other map untouched.
    (The simulation itself is stubbed: 1,000+ real frames take minutes.)"""
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setattr(calibrate, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(calibrate, "_simulated_raw", _fast_raw)
    cal = tmp_path / "calibration.json"
    data = json.loads(classifier.CALIBRATION_FILE.read_text())
    before = {t: m for t, m in data["maps"].items() if t != "marks"}
    del data["maps"]["marks"]
    cal.write_text(json.dumps(data))
    monkeypatch.setattr(classifier, "_calibration_cache", None)
    rep = calibrate.calibrate_simulated(cal, frames=1500, seed=11)
    assert list(rep) == ["marks"] and abs(rep["marks"]["sd"] - 1) < 0.15
    after = json.loads(cal.read_text())
    assert after["map_sources"]["marks"] == {"source": "simulated", "frames": 1500}
    assert {t: m for t, m in after["maps"].items() if t != "marks"} == before
    assert calibrate.calibrate_simulated(cal, frames=1500) == {}       # nothing missing now


# --- the blind-spot probe --------------------------------------------------------

def test_probe_records_a_blind_spot_rechecks_it_and_moves_it_to_fixed(tmp_path, monkeypatch):
    """The daily probe's bookkeeping through its results file: a random
    variant a person sees but the detector misses is recorded; the next run
    re-checks it, and once it is caught it moves to "fixed". Scoring is
    stubbed (the real detector on these signals is tested elsewhere): the
    family is caught from strength 0.3, everything is seen from 0.4, and
    variants are missed until the detector "improves"."""
    from concurrent.futures import ThreadPoolExecutor
    improved = {"yes": False}

    def make(job):
        name, _, strength = job
        caught = strength >= (0.3 if name in sensitivity.FAMILIES or improved["yes"] else 2.0)
        return ({"marks"} if caught else set()), (9.0 if caught else 1.0)
    monkeypatch.setattr(sensitivity, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(sensitivity, "_make", make)
    monkeypatch.setattr(sensitivity, "_eye", lambda job: job[2] * 5)     # 2.0 (seen) at 0.4
    out = tmp_path / "sensitivity.json"
    args = ["--trials", "2", "--steps", "6", "--explore", "1", "--family", "small dot (8px)",
            "--out", str(out)]
    assert sensitivity.main(args + ["--seed", "3"]) == 0
    first = json.loads(out.read_text())
    fam = first["families"][0]
    assert fam["signal"] == "small dot (8px)" and not fam["blind"] and fam["by"] == ["marks"]
    assert 0.3 <= fam["detect"] < 0.4 and 0.4 <= fam["eye"] < 0.5
    assert [m["id"] for m in first["misses"]] == [sensitivity.V2 + 3000] and first["misses"][0]["blind"]

    improved["yes"] = True
    assert sensitivity.main(args + ["--seed", "4"]) == 0
    second = json.loads(out.read_text())
    assert sensitivity.V2 + 3000 in second["checked"]                     # the old miss was re-checked
    assert [m["id"] for m in second["fixed"]] == [sensitivity.V2 + 3000]
    assert second["misses"] == [] and len(second["history"]) == 2


# --- the demos' image search ---------------------------------------------------

def test_image_search_reads_the_location_babelia_returns(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append(req)

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'<input type="text" id="location" value="98765432109876543210">'
        return Resp()
    monkeypatch.setattr(seeded.urllib.request, "urlopen", urlopen)
    assert seeded.search_location(b"\x89PNG fake", "x.png") == "98765432109876543210"
    req = sent[0]
    assert req.get_method() == "POST" and b'filename="x.png"' in req.data and b"\x89PNG fake" in req.data
    assert "boundary=" in req.get_header("Content-type") and "SurfingBabel" in req.get_header("User-agent")


def test_image_search_without_a_location_is_an_error(monkeypatch):
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html>Too many requests</html>"
    monkeypatch.setattr(seeded.urllib.request, "urlopen", lambda req, timeout: Resp())
    with pytest.raises(RuntimeError, match="no location"):
        seeded.search_location(b"png", "x.png")


# --- the benchmark the site shows ----------------------------------------------

def test_benchmark_file_has_everything_the_site_reads(tmp_path, monkeypatch):
    """site/app.js reads these fields from benchmark/benchmark.json; a
    renamed field would leave the benchmark section blank, silently."""
    def fake_classify_many(frames):
        return [Classification(sigma=float(i % 7), label="static", is_interesting=i % 7 >= 4,
                               reason="tone", tests={"tone": float(i % 7)}) for i in range(len(frames))]
    monkeypatch.setattr(benchmark, "_classify_many", fake_classify_many)
    monkeypatch.setattr(benchmark, "_visibility", lambda data: {"ratio": 1.2, "label": "invisible"})
    out = tmp_path / "benchmark"
    benchmark.run(trials=1, null_frames=4, out=out)
    b = json.loads((out / "benchmark.json").read_text())
    assert isinstance(b["threshold_sigma"], (int, float))
    assert {"frames", "false_positives", "max_sigma", "hist", "hist_edges"} <= set(b["null"])
    assert len(b["null"]["hist"]) == len(b["null"]["hist_edges"]) + 1
    for r in b["signals"]:
        assert {"signal", "strength", "detected", "trials", "min_sigma", "reason",
                "visible", "visibility"} <= set(r)
    assert b["examples"]
    for e in b["examples"]:
        assert {"label", "file", "thumb", "sigma", "tier"} <= set(e)
        assert (out / e["file"]).exists() and (out / e["thumb"]).exists()
        assert (out / e["thumb"]).stat().st_size < (out / e["file"]).stat().st_size
