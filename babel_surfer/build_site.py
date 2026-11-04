"""Assemble the static website into an output folder (e.g. ``public/``).

Copies ``site/`` (HTML/CSS/JS) next to the scan data in ``<out>/data`` and
(re)generates ``<out>/benchmark`` whenever the detector or its calibration
has changed since the benchmark was last run.

    python -m babel_surfer.build_site --out public
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from . import alerts, benchmark, seeded

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PKG = Path(__file__).resolve().parent


def detector_fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("classifier.py", "calibration.json", "benchmark.py", "calibrate.py"):
        h.update((PKG / name).read_bytes())
    return h.hexdigest()[:16]


def build(out: Path, trials: int = 8, null_frames: int = 1000, force_bench: bool = False,
          network: bool = True) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for f in SITE.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    (out / ".nojekyll").touch()
    (out / "data").mkdir(exist_ok=True)

    bench_dir = out / "benchmark"
    fp = detector_fingerprint()
    current = None
    try:
        current = json.loads((bench_dir / "benchmark.json").read_text()).get("fingerprint")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if force_bench or current != fp:
        print(f"running benchmark (detector {fp}, was {current})")
        if bench_dir.exists():
            shutil.rmtree(bench_dir)
        result = benchmark.run(trials, null_frames, bench_dir)
        result["fingerprint"] = fp
        (bench_dir / "benchmark.json").write_text(json.dumps(result, indent=2))
    else:
        print("benchmark up to date")

    # seeded demos (fetched from Babelia once, re-scored every build)
    demos = seeded.ensure(out / "demo", network=network)
    print(f"{len(demos)} seeded demo(s)")

    # Atom feed of finds
    finds_file = out / "data" / "finds.json"
    finds = json.loads(finds_file.read_text()) if finds_file.exists() else []
    (out / "feed.xml").write_text(alerts.build_feed(finds))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("public"))
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--null", type=int, default=1000)
    ap.add_argument("--force-bench", action="store_true")
    ap.add_argument("--offline", action="store_true", help="don't fetch seeded demos")
    args = ap.parse_args(argv)
    build(args.out, args.trials, args.null, args.force_bench, network=not args.offline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
