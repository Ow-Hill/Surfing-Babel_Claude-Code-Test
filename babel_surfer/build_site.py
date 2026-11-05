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
from datetime import date, datetime, timezone
from pathlib import Path

from . import alerts, benchmark, demo_kit, demo_slot, seeded

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PKG = Path(__file__).resolve().parent


def bust_cache(out: Path) -> None:
    """Give every local script and stylesheet link a content fingerprint
    (app.js -> app.js?v=1a2b3c4d). Browsers cache these files, so without it
    a new page can run yesterday's code until the cache expires."""
    import re
    for page in out.glob("*.html"):
        html = page.read_text()

        def stamp(m):
            name = m.group(2)
            f = out / name
            if not f.exists():
                return m.group(0)
            v = hashlib.sha256(f.read_bytes()).hexdigest()[:10]
            return f'{m.group(1)}{name}?v={v}"'
        html = re.sub(r'((?:src|href)=")([\w./-]+\.(?:js|css))(?:\?v=\w+)?"', stamp, html)
        page.write_text(html)


def detector_fingerprint() -> str:
    h = hashlib.sha256()
    for name in ("classifier.py", "calibration.json", "benchmark.py", "calibrate.py"):
        h.update((PKG / name).read_bytes())
    return h.hexdigest()[:16]


def build(out: Path, trials: int = 8, null_frames: int = 500, force_bench: bool = False,
          network: bool = True) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for f in SITE.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    (out / ".nojekyll").touch()
    bust_cache(out)
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

    # live-coding demo slot (site/agent.html shows the spec until it's implemented)
    (out / "agent").mkdir(exist_ok=True)
    (out / "agent" / "find_of_the_day.json").write_text(json.dumps(demo_slot_result(finds)))
    # the spec as a live checklist (passed / failed / waiting per requirement)
    spec = demo_kit.spec_results()
    spec["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "agent" / "spec_results.json").write_text(json.dumps(spec, ensure_ascii=False))
    print(f"demo slot: {spec['variant']}, spec {spec['status']} "
          f"({spec.get('passed', 0)}/{spec.get('total', 0)})")


def demo_slot_result(finds: list[dict], day: date | None = None) -> dict:
    """Run the demo slot safely: whatever the agent wrote, the build goes on."""
    day = day or datetime.now(timezone.utc).date()
    try:
        pick = demo_slot.find_of_the_day(finds, day)
    except NotImplementedError:
        return {"status": "not_implemented", "day": day.isoformat()}
    except Exception as e:                       # a broken implementation
        return {"status": "error", "day": day.isoformat(), "message": f"{type(e).__name__}: {e}"[:300]}
    if pick is None:
        return {"status": "no_finds", "day": day.isoformat()}
    keep = {k: pick.get(k) for k in ("location", "sigma", "tier", "reason", "caption")}
    match = next((f for f in finds if f["location"] == keep["location"]), {})
    keep["image"] = match.get("image")
    return {"status": "ok", "day": day.isoformat(), "pick": keep}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("public"))
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--null", type=int, default=500)
    ap.add_argument("--force-bench", action="store_true")
    ap.add_argument("--offline", action="store_true", help="don't fetch seeded demos")
    args = ap.parse_args(argv)
    build(args.out, args.trials, args.null, args.force_bench, network=not args.offline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
