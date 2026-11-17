"""The weekly calibration audit (audit.py): it must judge a calibration only
on frames scanned after it was fitted, pool the archived snapshots without
counting a frame twice, stay quiet on static, and raise an alert on a
surplus of high scores."""

import json
import math

import numpy as np
import pytest

from babel_surfer import audit, calibrate
from babel_surfer.classifier import TESTS
from test_nullmap_remine import legacy_log

LEGACY = {t: {"mean": 1.5, "std": 1.0} for t in TESTS}


def make_log(seed, n, offset):
    log, _ = legacy_log(np.random.default_rng(seed), n, LEGACY)
    log["location"] += offset                     # distinct frames in each snapshot
    return log


@pytest.fixture
def fitted(tmp_path):
    """A calibration fitted on a synthetic log whose newest frame was scan 100,000."""
    np.save(tmp_path / "fit.npy", make_log(1, 60_000, 0))     # (fit on 20,000, 3 sigma is too noisy)
    cal = tmp_path / "cal.json"
    cal.write_text(json.dumps({"tests": LEGACY}))
    calibrate.calibrate_from_log(tmp_path / "fit.npy", cal, scanned_through=100_000)
    assert json.loads(cal.read_text())["maps_source"]["scanned_through"] == 100_000
    return cal


@pytest.fixture
def small(monkeypatch):
    """Snapshots of 20,000 frames, judged from 30,000."""
    monkeypatch.setattr(audit, "SCORE_LOG_KEEP", 20_000)
    monkeypatch.setattr(audit, "MIN_FRAMES", 30_000)


def test_poisson_tail():
    assert audit.poisson_sf(0, 3.2) == 1.0
    assert audit.poisson_sf(9, 3.167) == pytest.approx(0.00536, abs=1e-4)  # palette, 2026-11-17
    assert audit.poisson_sf(3, 3.0) == pytest.approx(1 - 8.5 * math.exp(-3), rel=1e-9)
    # large expectations (3-sigma counts over weeks of frames): no underflow to "no surplus"
    assert 1e-11 < audit.poisson_sf(1200, 1000) < 1e-8
    assert audit.poisson_sf(950, 1000) == pytest.approx(0.94, abs=0.01)


def test_only_a_clear_and_large_surplus_is_worth_an_issue():
    assert not audit.surplus(15, 6.3)       # colours over two snapshots: 2.4x but p 0.002, not yet...
    assert audit.surplus(23, 9.5)           # ...over three it is (p 0.0001)
    assert not audit.surplus(1200, 1000)    # 1.2x: significant on a big pool, but small
    assert audit.surplus(1600, 1000)        # 1.6x on a big pool
    assert not audit.surplus(4, 1.3)        # 3x, but four frames prove nothing


def test_archive_adds_only_snapshots_that_share_no_frames_and_keeps_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "SCORE_LOG_KEEP", 1000)
    scores, stats, d = tmp_path / "scores.npy", tmp_path / "stats.json", tmp_path / "arch"
    np.save(scores, make_log(2, 100, 0))

    def at(scanned):
        stats.write_text(json.dumps({"scanned": scanned}))
        return audit.archive(scores, stats, d, keep=2)
    assert at(5000).name == "scores-5000.npz"
    assert at(5500) is None                       # its frames 4,500-5,500 were already archived
    assert at(6000).name == "scores-6000.npz"
    assert at(7000) is not None and [s for s, _ in audit.snapshots(d)] == [6000, 7000]
    assert np.array_equal(np.load(d / "scores-7000.npz")["log"], np.load(scores))


def test_the_audit_judges_only_frames_scanned_after_the_fit(tmp_path, fitted, small):
    d = tmp_path / "arch"
    d.mkdir()
    np.savez_compressed(d / "scores-110000.npz", log=make_log(3, 20_000, 10**9))    # 90,000-110,000
    r = audit.audit(d, fitted)
    assert r["status"].startswith("no snapshot") and r["in_sample"] == [110000]
    np.savez_compressed(d / "scores-130000.npz", log=make_log(4, 20_000, 2 * 10**9))
    r = audit.audit(d, fitted)
    assert r["snapshots"] == [130000] and "only 20,000" in r["status"]
    np.savez_compressed(d / "scores-150000.npz", log=make_log(5, 20_000, 3 * 10**9))
    np.savez_compressed(d / "scores-170000.npz", log=make_log(5, 20_000, 3 * 10**9))  # same frames again
    r = audit.audit(d, fitted)
    assert r["status"] == "ok" and r["frames"] == 40_000 and r["flagged"] == []     # counted once
    assert r["headline"]["p_4"] > audit.P_ALERT and audit.alert_text(r) is None


def test_a_surplus_of_high_scores_raises_an_alert(tmp_path, fitted, small):
    d = tmp_path / "arch"
    d.mkdir()
    bad = make_log(6, 20_000, 2 * 10**9)
    bad["colours"][:40] = 6.5                       # 40 frames far out in one test's tail
    np.savez_compressed(d / "scores-130000.npz", log=bad)
    np.savez_compressed(d / "scores-150000.npz", log=make_log(7, 20_000, 3 * 10**9))
    r = audit.audit(d, fitted)
    assert r["status"] == "flagged" and "colours" in r["flagged"]
    assert [t for t in r["flagged"] if t != "headline"] == ["colours"]
    body = audit.alert_text(r)
    assert "| colours |" in body and "40,000 frames" in body


def test_command_line_archives_audits_and_writes_the_alert(tmp_path, fitted, small, monkeypatch):
    from babel_surfer import classifier
    monkeypatch.setattr(classifier, "CALIBRATION_FILE", fitted)
    site, d = tmp_path / "site", tmp_path / "arch"
    site.mkdir()
    d.mkdir()
    np.savez_compressed(d / "scores-130000.npz", log=make_log(8, 20_000, 2 * 10**9))
    bad = make_log(9, 20_000, 3 * 10**9)
    bad["palette"][:40] = 6.5
    np.save(site / "scores.npy", bad)
    (site / "stats.json").write_text(json.dumps({"scanned": 150_000}))
    alert = tmp_path / "audit-alert.md"
    assert audit.main(["--archive", str(d), "--scores", str(site / "scores.npy"), "--stats",
                       str(site / "stats.json"), "--audit", str(d), "--alert", str(alert)]) == 0
    assert (d / "scores-150000.npz").exists()
    rep = json.loads((d / "audit.json").read_text())
    assert rep["status"] == "flagged" and "palette" in rep["flagged"] and alert.exists()


def test_the_combined_score_is_audited_too(tmp_path, fitted, small):
    d = tmp_path / "arch"
    d.mkdir()
    for k, s in enumerate((130_000, 150_000)):
        np.savez_compressed(d / f"scores-{s}.npz", log=make_log(20 + k, 20_000, (k + 2) * 10**9))
    r = audit.audit(d, fitted)
    assert "combined" in r["tests"] and r["tests"]["combined"]["n"] == 40_000


def test_a_refit_is_proposed_from_snapshots_and_judged_on_one_it_never_saw(tmp_path, fitted, small, monkeypatch):
    monkeypatch.setattr(audit, "MIN_FRAMES", 10_000)       # judge the one 20,000-frame check snapshot
    d = tmp_path / "arch"
    d.mkdir()
    for k, s in enumerate((130_000, 150_000, 170_000)):
        np.savez_compressed(d / f"scores-{s}.npz", log=make_log(30 + k, 20_000, (k + 2) * 10**9))
    out = tmp_path / "candidate.json"
    prop = audit.propose_refit(d, out, fitted)
    assert prop["fit_snapshots"] == [130_000, 150_000] and prop["check_snapshot"] == 170_000
    assert prop["ok"] and prop["check"]["frames"] == 20_000
    cand = json.loads(out.read_text())
    assert cand["maps_source"]["scanned_through"] == 150_000 and cand["maps_source"]["frames"] == 40_000
    assert "refit-proposal" in audit.proposal_text(prop, "o/r")
    (d / "scores-170000.npz").unlink()
    assert audit.propose_refit(d, out, fitted) is None          # two snapshots: not enough


def test_command_line_proposes_on_request(tmp_path, fitted, small, monkeypatch):
    monkeypatch.setattr(audit, "MIN_FRAMES", 10_000)       # judge the one 20,000-frame check snapshot
    from babel_surfer import classifier
    monkeypatch.setattr(classifier, "CALIBRATION_FILE", fitted)
    d = tmp_path / "arch"
    d.mkdir()
    for k, s in enumerate((130_000, 150_000, 170_000)):
        np.savez_compressed(d / f"scores-{s}.npz", log=make_log(40 + k, 20_000, (k + 2) * 10**9))
    alert, cand = tmp_path / "alert.md", tmp_path / "candidate.json"
    audit.main(["--audit", str(d), "--alert", str(alert), "--propose", str(cand), "--always", "--repo", "o/r"])
    assert cand.exists() and (d / "proposal.json").exists() and "refit-proposal" in alert.read_text()
