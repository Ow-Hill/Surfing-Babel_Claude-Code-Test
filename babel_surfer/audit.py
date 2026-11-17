"""Out-of-sample calibration audit, from archived score logs.

The site keeps only the latest ``SCORE_LOG_KEEP`` frames' scores (about two
days of scanning), so an independent sample to check the calibration on
vanishes quickly. A weekly workflow archives a snapshot of that log on the
``log-archive`` branch, never overlapping the previous one, and audits the
calibration on every snapshot scanned *after* the calibration was fitted:

* each snapshot is named by the scan total when it was taken
  (``scores-<scanned>.npz``), so it holds frames scanned in
  (scanned - SCORE_LOG_KEEP, scanned];
* the calibration records ``maps_source.scanned_through``, the scan total
  when the newest frame it was fitted on was logged;
* a snapshot is out of sample when scanned - SCORE_LOG_KEEP >= that.

Pooling snapshots builds evidence week by week: one 100,000-frame log can
show a test at 9 frames over 4 sigma against 3.2 expected by chance, while
a real fault (colours read 2.5x too often, 2026-11-17) stands out clearly
over a few weeks. Only a surplus raises an alert (it means false finds); a
deficit is reported but errs toward missing finds, never inventing them.

    python -m babel_surfer.audit --archive archive --scores scores.npy --stats stats.json
    python -m babel_surfer.audit --audit archive --alert audit-alert.md
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from statistics import NormalDist

import numpy as np

from . import calibrate, classifier
from .storage import SCORE_LOG_KEEP

P_ALERT = 0.001          # one-sided Poisson p for a surplus that raises an issue...
MIN_RATIO = 1.5          # ...and it must be this many times what static gives. The p-value
                         # covers the audit's own noise, not the fit's, so with weeks of
                         # pooled frames a harmless 1.2x tail error would turn "significant".
                         # (colours, the fault this audit exists for, read 2.4x.)
MIN_FRAMES = 50_000      # out-of-sample frames needed before judging
KEEP = 8                 # snapshots kept on the archive branch
NAME = re.compile(r"^scores-(\d+)\.npz$")


def poisson_sf(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), accurate for large lam too: terms in
    log space (exp(-1000) underflows to 0), and above the mean the tail is
    summed directly rather than as 1 - cdf, which cancels to 0."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0
    log_term = lambda i: -lam + i * math.log(lam) - math.lgamma(i + 1)
    if k > lam:
        total, i = 0.0, k
        while True:
            t = math.exp(log_term(i))
            total += t
            if t <= total * 1e-17 or i > k + 100_000:
                return min(1.0, total)
            i += 1
    return max(0.0, 1.0 - sum(math.exp(log_term(i)) for i in range(k)))


def surplus(obs: int, exp: float) -> bool:
    """A surplus worth an issue: clearly more than chance, and large."""
    return obs >= MIN_RATIO * exp and poisson_sf(obs, exp) < P_ALERT


def snapshots(d: Path) -> list[tuple[int, Path]]:
    """Archived logs in ``d``, oldest first, as (scan total, path)."""
    out = [(int(m.group(1)), p) for p in d.glob("scores-*.npz") if (m := NAME.match(p.name))]
    return sorted(out)


def archive(scores: Path, stats: Path, d: Path, keep: int = KEEP) -> Path | None:
    """Add the current log as a snapshot unless it overlaps the newest one;
    keep the newest ``keep``. Returns the new snapshot, or None."""
    d.mkdir(parents=True, exist_ok=True)
    scanned = int(json.loads(stats.read_text())["scanned"])
    have = snapshots(d)
    if have and scanned - SCORE_LOG_KEEP < have[-1][0]:
        return None                               # would share frames with the last one
    path = d / f"scores-{scanned}.npz"
    np.savez_compressed(path, log=np.load(scores))
    for _, old in snapshots(d)[:-keep]:
        old.unlink()
    return path


def audit(d: Path, cal_path: Path | None = None) -> dict:
    """Score every out-of-sample snapshot with the calibration and compare
    how often each test passes 3 and 4 sigma with what static gives."""
    cal_path = cal_path or classifier.CALIBRATION_FILE
    data = json.loads(cal_path.read_text())
    through = (data.get("maps_source") or {}).get("scanned_through")
    snaps = snapshots(d)
    used = [(s, p) for s, p in snaps if through is not None and s - SCORE_LOG_KEEP >= through]
    report = {"calibration": classifier.calibration_hash() if cal_path == classifier.CALIBRATION_FILE
              else cal_path.name, "scanned_through": through,
              "snapshots": [s for s, _ in used], "in_sample": [s for s, p in snaps if (s, p) not in used]}
    if through is None:
        report["status"] = "the calibration does not record where its fit ended"
        return report
    if not used:
        report.update(status="no snapshot scanned since the calibration was fitted", frames=0)
        return report
    log = np.concatenate([np.load(p)["log"] for _, p in used])
    _, first = np.unique(log["location"], return_index=True)
    log = log[np.sort(first)]
    report["frames"] = int(len(log))
    if len(log) < MIN_FRAMES:
        report["status"] = f"only {len(log):,} out-of-sample frames; judging from {MIN_FRAMES:,}"
        return report
    tests = list(data.get("combined_tests") or classifier.TESTS)
    raw = calibrate.raw_from_log(log, data.get("tests") or {})
    z, _ = calibrate.apply_maps(data["maps"], raw, tests)
    tail = lambda k: 1 - NormalDist().cdf(k)
    rows, flagged = {}, []
    for t in tests:
        zt = z[t][np.isfinite(z[t])]
        if not len(zt):
            continue
        row = {"n": int(len(zt))}
        for k in (3, 4):
            obs, exp = int((zt > k).sum()), len(zt) * tail(k)
            row[f"over_{k}"], row[f"expected_{k}"] = obs, round(exp, 1)
            row[f"p_{k}"] = round(poisson_sf(obs, exp), 6)
        rows[t] = row
        if any(surplus(row[f"over_{k}"], row[f"expected_{k}"]) for k in (3, 4)):
            flagged.append(t)
    peak = np.nanmax(np.stack([z[t] for t in rows]), axis=0)
    peak = peak[np.isfinite(peak)]
    exp4 = len(peak) * (1 - NormalDist().cdf(4) ** len(rows))
    head = {"over_4": int((peak > 4).sum()), "expected_4": round(exp4, 1)}
    head["p_4"] = round(poisson_sf(head["over_4"], exp4), 6)
    if surplus(head["over_4"], exp4):
        flagged.append("headline")
    report.update(status="flagged" if flagged else "ok", tests=rows, headline=head, flagged=flagged)
    return report


def alert_text(report: dict) -> str | None:
    if report.get("status") != "flagged":
        return None
    lines = [f"The weekly calibration audit, on {report['frames']:,} frames scanned after the "
             f"calibration was fitted, found tests reaching 3 or 4 sigma at least {MIN_RATIO:g} "
             f"times as often as static does (one-sided Poisson p < {P_ALERT}). Their finds are "
             f"likely false.", "",
             "| test | >3σ observed / expected | >4σ observed / expected |", "|---|---|---|"]
    for t in report["flagged"]:
        r = report["headline"] if t == "headline" else report["tests"][t]
        o3 = f"{r['over_3']} / {r['expected_3']}" if "over_3" in r else "–"
        lines.append(f"| {t} | {o3} | {r['over_4']} / {r['expected_4']} |")
    lines += ["", "To act on it: refit with `python -m babel_surfer.calibrate --from-log` on the "
                  "archived logs (branch `log-archive`), checking the fit on frames it was not fitted "
                  "on, as in PR #44. See the babel-detector skill."]
    return "\n".join(lines)


def format_report(report: dict) -> str:
    out = [f"audit: {report['status']} ({report.get('frames', 0):,} out-of-sample frames, "
           f"snapshots {report.get('snapshots')})"]
    for t, r in (report.get("tests") or {}).items():
        mark = "  <-- surplus" if t in report.get("flagged", []) else ""
        out.append(f"  {t:10s} >3σ {r['over_3']:5d}/{r['expected_3']:<7} >4σ {r['over_4']:3d}/{r['expected_4']:<5}"
                   f" p4 {r['p_4']:.3g}{mark}")
    if report.get("headline"):
        h = report["headline"]
        out.append(f"  headline   >4σ {h['over_4']}/{h['expected_4']} p {h['p_4']:.3g}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", type=Path, metavar="DIR", help="add the current log to this archive")
    ap.add_argument("--scores", type=Path, help="the site's data/scores.npy (with --archive)")
    ap.add_argument("--stats", type=Path, help="the site's data/stats.json (with --archive)")
    ap.add_argument("--audit", type=Path, metavar="DIR", help="audit the calibration on this archive")
    ap.add_argument("--data", type=Path, help="the site's data directory: audit its live calibration "
                                              "when it matches this code, as the scan does")
    ap.add_argument("--alert", type=Path, help="write an issue body here when the audit flags a surplus")
    a = ap.parse_args(argv)
    if a.archive:
        new = archive(a.scores, a.stats, a.archive)
        print(f"archived {new.name}" if new else "the current log overlaps the last snapshot: not archived")
    if a.audit:
        cal = (classifier.live_calibration_path(a.data) if a.data else None) or classifier.CALIBRATION_FILE
        rep = audit(a.audit, cal)
        (a.audit / "audit.json").write_text(json.dumps(rep, indent=1))
        print(format_report(rep))
        body = alert_text(rep)
        if a.alert and body:
            a.alert.write_text(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
