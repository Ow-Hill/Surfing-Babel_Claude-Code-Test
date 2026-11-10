"""The site's "How the search works" demo (site/search-demo.js) must be an
honest model: pure static reads like static, and hidden signals are caught by
the test meant for them. Runs the demo's own maths in Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "site" / "search-demo.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def run(js: str):
    code = f"const D = require({json.dumps(str(DEMO))});\n{js}"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_pure_static_is_rarely_strange():
    r = run("""
      let over3 = 0, over4 = 0, best = 0, sum = 0;
      for (let i = 0; i < 1500; i++) {
        const s = D.score(D.makeFrame(1e9 + i, "none", 0));
        for (const t of s.tests) { sum += t.z; if (t.z >= 3) over3++; if (t.z >= 4) over4++; }
        best = Math.max(best, s.sigma);
      }
      console.log(JSON.stringify({over3, over4, best, mean: sum / 4500}));
    """)
    # frame-wide sigma of one test on static: P(>= 3) = 0.0027, P(>= 4) = 6.3e-5 (of 4,500 test scores)
    assert 2 <= r["over3"] <= 30 and r["over4"] <= 2
    assert 0.5 < r["mean"] < 1.1


def test_each_signal_is_caught_by_its_test_and_frames_are_deterministic():
    r = run("""
      const out = {};
      for (const sig of ["disc", "line", "patch"]) {
        const s = D.score(D.makeFrame(12345, sig, 0.6));
        out[sig] = [s.best.id, s.sigma];
      }
      const a = D.makeFrame(77, "disc", 0.3), b = D.makeFrame(77, "disc", 0.3);
      out.same = a.every((v, i) => v === b[i]);
      out.weak = D.score(D.makeFrame(12345, "line", 0.02)).sigma;
      console.log(JSON.stringify(out));
    """)
    assert r["disc"][0] == "bright" and r["disc"][1] >= 4
    assert r["line"][0] == "line" and r["line"][1] >= 4
    assert r["patch"][0] == "smooth" and r["patch"][1] >= 4
    assert r["same"] and r["weak"] < 4                      # a 2% line is lost in static
