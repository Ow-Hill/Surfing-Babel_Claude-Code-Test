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


def test_demos_offline_rescore(tmp_path):
    rng = np.random.default_rng(1)
    (tmp_path / "landscape.jpg").write_bytes(babel_static(rng))
    (tmp_path / "landscape.location.txt").write_text("12345678901234567890123456789")
    entries = seeded.ensure(tmp_path, network=False)        # faint-disc missing: skipped
    assert [e["slug"] for e in entries] == ["landscape"]
    assert entries[0]["location_digits"] == 29 and (tmp_path / "landscape.heat.png").exists()
    assert json.loads((tmp_path / "demo.json").read_text())[0]["tier"] in ("static", "near miss", "anomaly")
