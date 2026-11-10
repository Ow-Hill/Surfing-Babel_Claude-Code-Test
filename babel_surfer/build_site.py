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
    from . import classifier
    h = hashlib.sha256()
    for name in ("classifier.py", "perceptual.py", "nullmap.py", "visibility.py", "benchmark.py",
                 "calibrate.py"):
        h.update((PKG / name).read_bytes())
    h.update(classifier.CALIBRATION_FILE.read_bytes())       # package or live calibration
    return h.hexdigest()[:16]


def run_benchmark(out: Path, trials: int, null_frames: int, force: bool = False) -> bool:
    """(Re)run the benchmark if the detector changed. It is built beside
    ``out`` and swapped in at the end, so a publish meanwhile ships the old
    one whole, never a half-written one. Returns True if it ran."""
    bench_dir = out / "benchmark"
    fp = detector_fingerprint()
    current = None
    try:
        current = json.loads((bench_dir / "benchmark.json").read_text()).get("fingerprint")
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    if not force and current == fp:
        print("benchmark up to date")
        return False
    print(f"running benchmark (detector {fp}, was {current})")
    tmp, old = out.parent / f".{out.name}-bench-new", out.parent / f".{out.name}-bench-old"
    for d in (tmp, old):
        if d.exists():
            shutil.rmtree(d)
    result = benchmark.run(trials, null_frames, tmp)
    result["fingerprint"] = fp
    (tmp / "benchmark.json").write_text(json.dumps(result, indent=2))
    if bench_dir.exists():
        bench_dir.rename(old)
    tmp.rename(bench_dir)
    shutil.rmtree(old, ignore_errors=True)
    return True


def build(out: Path, trials: int = 8, null_frames: int = 500, force_bench: bool = False,
          network: bool = True, bench: str = "auto") -> None:
    """``bench``: "auto" re-runs a stale benchmark now; "skip" keeps
    whatever benchmark is there (the scan workflow runs it in the
    background with ``--bench only`` so scanning never waits for it)."""
    out.mkdir(parents=True, exist_ok=True)
    from . import classifier
    classifier.activate_live_calibration(out / "data")    # same calibration as the scan
    if bench == "only":
        run_benchmark(out, trials, null_frames, force_bench)
        return
    for f in SITE.iterdir():
        if f.is_file():
            shutil.copy2(f, out / f.name)
    (out / ".nojekyll").touch()
    bust_cache(out)
    (out / "data").mkdir(exist_ok=True)

    if bench == "auto":
        run_benchmark(out, trials, null_frames, force_bench)

    # the blind-spot probe's latest results (its own workflow keeps them on
    # the probe-data branch, so it never races the scan's pushes to gh-pages)
    probe = probe_results() if network else None
    if probe:
        (out / "data" / "sensitivity.json").write_text(probe)

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


def probe_results() -> str | None:
    """sensitivity.json from the probe-data branch, or None."""
    import subprocess
    try:
        subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "probe-data"],
                       check=True, capture_output=True, timeout=60)
        text = subprocess.run(["git", "show", "FETCH_HEAD:sensitivity.json"], check=True,
                              capture_output=True, text=True, timeout=30).stdout
        json.loads(text)
        return text
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


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
    ap.add_argument("--bench", choices=("auto", "skip", "only"), default="auto",
                    help="auto: re-run a stale benchmark first; skip: keep the current one; "
                         "only: just (re)run the benchmark, nothing else")
    args = ap.parse_args(argv)
    build(args.out, args.trials, args.null, args.force_bench, network=not args.offline, bench=args.bench)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
