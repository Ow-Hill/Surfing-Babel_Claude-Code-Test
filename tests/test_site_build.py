"""The site build (build_site.py), which the scan workflow runs after every
30-minute chunk to produce what GitHub Pages serves. A break here means the
site keeps showing old data (the workflow publishes the data with the
previous site), so it is worth catching before it ships."""

import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from babel_surfer import build_site, demo_kit

SITE = Path(__file__).resolve().parents[1] / "site"
GENERATED = ("feed.xml", "data/", "benchmark/", "demo/", "agent/")     # written by the build


def local_refs(html: str) -> set[str]:
    refs = set(re.findall(r'(?:src|href)="([^"#?]+)', html))
    return {r for r in refs if not r.startswith(("http:", "https:", "mailto:", "./", "data:"))}


@pytest.mark.parametrize("page", sorted(p.name for p in SITE.glob("*.html")))
def test_every_local_file_a_page_links_to_exists(page):
    """A renamed or deleted script would leave a page half-working with no
    error anywhere but the visitor's console."""
    missing = [r for r in local_refs((SITE / page).read_text())
               if not r.startswith(GENERATED) and not (SITE / r).exists()]
    assert not missing, f"{page} links to missing files: {missing}"


@pytest.fixture
def built(tmp_path, monkeypatch):
    """An offline build into tmp_path/public, with one find in the data."""
    monkeypatch.setattr(demo_kit, "spec_results", lambda: {
        "variant": "stub", "status": "waiting", "passed": 0, "total": 1, "requirements": []})
    out = tmp_path / "public"
    (out / "data").mkdir(parents=True)
    find = {"location": "123", "sigma": 5.2, "tier": "anomaly", "reason": "tone",
            "found_at": 1_700_000_000, "image": "images/123.jpg", "description": "A brighter band."}
    (out / "data" / "finds.json").write_text(json.dumps([find]))
    build_site.build(out, network=False, bench="skip")
    return out


def test_offline_build_produces_a_complete_site(built):
    for name in ["index.html", "app.js", "search-demo.js", "style.css", ".nojekyll", "feed.xml",
                 "agent/find_of_the_day.json", "agent/spec_results.json"]:
        assert (built / name).exists(), name
    # every page's own scripts are there, and fingerprinted so browsers drop stale copies
    html = (built / "index.html").read_text()
    for script in re.findall(r'src="([\w.-]+\.js)\?v=\w+"', html):
        assert (built / script).exists()
    assert re.search(r'src="app\.js\?v=[0-9a-f]+"', html)
    # the feed is valid Atom and carries the find
    feed = ET.fromstring((built / "feed.xml").read_text())
    entries = feed.findall("{http://www.w3.org/2005/Atom}entry")
    assert len(entries) == 1 and "123" in entries[0].findtext("{http://www.w3.org/2005/Atom}title")
    # the demo slot result is safe JSON even while the slot is unimplemented
    slot = json.loads((built / "agent" / "find_of_the_day.json").read_text())
    assert slot["status"] in ("not_implemented", "no_finds", "ok", "error")
    assert json.loads((built / "agent" / "spec_results.json").read_text())["built_at"]


def test_a_rebuild_keeps_the_fingerprint_until_a_file_changes(built):
    first = re.search(r'app\.js\?v=(\w+)', (built / "index.html").read_text()).group(1)
    build_site.build(built, network=False, bench="skip")
    assert re.search(r'app\.js\?v=(\w+)', (built / "index.html").read_text()).group(1) == first


def test_probe_results_falls_back_when_the_branch_is_missing_or_broken(monkeypatch):
    """The probe's results come from another branch; if it is missing or
    holds invalid JSON, the build carries on without them."""
    def run(cmd, **k):
        if cmd[:2] == ["git", "fetch"]:
            raise subprocess.CalledProcessError(128, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="{}")
    monkeypatch.setattr(subprocess, "run", run)
    assert build_site.probe_results() is None

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout="not json"))
    assert build_site.probe_results() is None

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, stdout='{"summary": {}}'))
    assert json.loads(build_site.probe_results()) == {"summary": {}}


def test_command_line_runs_an_offline_build(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_kit, "spec_results", lambda: {
        "variant": "stub", "status": "waiting", "requirements": []})
    out = tmp_path / "site"
    assert build_site.main(["--out", str(out), "--offline", "--bench", "skip"]) == 0
    assert (out / "index.html").exists() and (out / "feed.xml").exists()
