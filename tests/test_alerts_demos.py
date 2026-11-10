import json
import xml.dom.minidom

import numpy as np

from babel_surfer import alerts, seeded
from babel_surfer.calibrate import babel_static


def test_feed_is_valid_atom():
    finds = [{"location": str(i), "sigma": 4.5 + i, "tier": "anomaly", "reason": "shape",
              "found_at": 1_800_000_000 + i} for i in range(3)]
    doc = xml.dom.minidom.parseString(alerts.build_feed(finds))
    entries = doc.getElementsByTagName("entry")
    assert len(entries) == 3
    assert entries[0].getElementsByTagName("link")[0].getAttribute("href").endswith("#2")


def test_alert_only_for_picture_level_finds():
    stats = {"last_run": {"finished_at": 0, "scanned": 900, "new_finds": [
        {"location": "1", "sigma": 5.0, "tier": "anomaly", "reason": "shape"}]}}
    assert alerts.build_alert(stats) is None
    stats["last_run"]["new_finds"].append(
        {"location": "77", "sigma": 25.0, "tier": "clear", "reason": "blob"})
    body = alerts.build_alert(stats)
    assert "25.0σ" in body and "#77" in body and "location=77" in body and "#1)" not in body


def test_alert_checks_every_run_since_the_last_alert():
    find = {"location": "9", "sigma": 30.0, "tier": "clear", "reason": "spot"}
    stats = {"runs": [{"finished_at": 10, "scanned": 500, "new_finds": [find]},
                      {"finished_at": 20, "scanned": 0, "trigger": "remine", "new_finds": []}],
             "last_run": {"finished_at": 10, "scanned": 500, "new_finds": [find]}}
    body = alerts.build_alert(stats, since=5)       # the scan is not the latest run
    assert body and "#9" in body
    assert alerts.build_alert(stats, since=20) is None   # already alerted


def test_demos_offline_rescore(tmp_path):
    rng = np.random.default_rng(1)
    (tmp_path / "landscape.jpg").write_bytes(babel_static(rng))
    (tmp_path / "landscape.location.txt").write_text("12345678901234567890123456789")
    entries = seeded.ensure(tmp_path, network=False)        # faint-disc missing: skipped
    assert [e["slug"] for e in entries] == ["landscape"]
    assert entries[0]["location_digits"] == 29 and (tmp_path / "landscape.maps.json").exists()
    assert entries[0]["description"]
    assert json.loads((tmp_path / "demo.json").read_text())[0]["tier"] in ("static", "near miss", "anomaly")


def test_demo_slot_result_is_safe(monkeypatch):
    from babel_surfer import build_site, demo_slot
    assert build_site.demo_slot_result([])["status"] in ("not_implemented", "no_finds", "ok")
    monkeypatch.setattr(demo_slot, "find_of_the_day", lambda finds, day: 1 / 0)
    r = build_site.demo_slot_result([{"location": "1", "sigma": 5}])
    assert r["status"] == "error" and "ZeroDivisionError" in r["message"]
    monkeypatch.setattr(demo_slot, "find_of_the_day", lambda finds, day: {
        "location": "1", "sigma": 5.0, "tier": "anomaly", "reason": "tone", "caption": "c"})
    r = build_site.demo_slot_result([{"location": "1", "sigma": 5.0, "image": "images/1.jpg"}])
    assert r["status"] == "ok" and r["pick"]["image"] == "images/1.jpg"


def test_demo_kit_reset_and_spec(tmp_path, monkeypatch):
    from babel_surfer import demo_kit
    slot = tmp_path / "demo_slot.py"
    monkeypatch.setattr(demo_kit, "SLOT", slot)
    demo_kit.reset("buggy")
    assert demo_kit.current_variant() == "buggy"
    demo_kit.reset("stub")
    assert demo_kit.current_variant() == "stub"
    slot.write_text(slot.read_text() + "\n# edited\n")
    assert demo_kit.current_variant() == "custom"


def test_spec_results_are_consistent():
    from babel_surfer import demo_kit
    r = demo_kit.spec_results()
    # Works whatever is in the slot right now (during a live demo it is the
    # agent's own code), so this test never gets in the demo's way.
    assert r["total"] == 6 and [q["test"] for q in r["requirements"]] == list(demo_kit.REQUIREMENTS)
    if r["variant"] == "stub":
        assert r["status"] == "waiting"
    if r["variant"] == "buggy":
        assert r["status"] == "failed" and r["passed"] == 4


def test_bust_cache(tmp_path):
    from babel_surfer.build_site import bust_cache
    (tmp_path / "app.js").write_text("v1")
    (tmp_path / "index.html").write_text('<script src="app.js"></script><a href="https://x.org/y.js">')
    bust_cache(tmp_path)
    first = (tmp_path / "index.html").read_text()
    assert 'src="app.js?v=' in first and 'href="https://x.org/y.js"' in first
    (tmp_path / "app.js").write_text("v2")
    bust_cache(tmp_path)                        # re-stamped, not stacked
    second = (tmp_path / "index.html").read_text()
    assert second != first and second.count("?v=") == 1


def test_benchmark_runs_beside_and_swaps_in_whole(tmp_path, monkeypatch):
    """--bench only rebuilds a stale benchmark next to the site and swaps it
    in at the end; --bench skip never runs it (the scan doesn't wait)."""
    import json
    from babel_surfer import build_site
    calls = []

    def fake_run(trials, null_frames, out):
        calls.append(out)
        assert not (tmp_path / "public" / "benchmark" / "new.txt").exists()   # old one untouched meanwhile
        out.mkdir(parents=True)
        (out / "new.txt").write_text("x")
        return {"threshold_sigma": 4.0}
    monkeypatch.setattr(build_site.benchmark, "run", fake_run)
    monkeypatch.setattr(build_site, "detector_fingerprint", lambda: "fp2")
    site = tmp_path / "public"
    (site / "benchmark").mkdir(parents=True)
    (site / "benchmark" / "benchmark.json").write_text(json.dumps({"fingerprint": "fp1"}))
    assert build_site.run_benchmark(site, 1, 1) is True
    assert calls[0].parent == tmp_path and not calls[0].exists()          # built beside, then moved
    assert json.loads((site / "benchmark" / "benchmark.json").read_text())["fingerprint"] == "fp2"
    assert (site / "benchmark" / "new.txt").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["public"]         # no temp dirs left
    assert build_site.run_benchmark(site, 1, 1) is False                     # now up to date
