#!/usr/bin/env python3
"""Read-only status of Surfing Babel: recent scan runs, the last Pages deploy,
what the published data says, and whether main is ahead of what's live.
Works from a sandbox that can't reach github.io (public GitHub API + git).

    python3 .claude/skills/babel-operations/scripts/status.py
"""
import datetime as dt
import json
import subprocess
import urllib.request

REPO = "Ow-Hill/Surfing-Babel_Claude-Code-Test"
API = f"https://api.github.com/repos/{REPO}"


def get(path):
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "surfing-babel-status"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout.strip()


def when(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "?"


print("== scan-and-publish runs (newest first)")
runs = get("/actions/workflows/scan-and-publish.yml/runs?per_page=5")["workflow_runs"]
for r in runs:
    print(f"  {r['event']:17s} {r['head_sha'][:7]} {r['status']:11s} {str(r['conclusion']):9s} {r['created_at']}")
if sum(r["status"] in ("queued", "pending", "waiting") for r in runs):
    print("  note: only one run can wait; a newer trigger cancels the waiting one")

print("== last Pages deploy")
pages = [r for r in get("/actions/runs?per_page=30")["workflow_runs"]
         if r["name"] == "pages build and deployment"]
print(f"  {pages[0]['status']} {pages[0]['conclusion']} {pages[0]['updated_at']}" if pages
      else "  none in the last 30 runs")

print("== published data (gh-pages)")
git("fetch", "-q", "origin", "gh-pages", "main")
try:
    s = json.loads(git("show", "origin/gh-pages:data/stats.json"))
    last = s.get("last_run") or {}
    print(f"  frames scanned {s.get('scanned', 0):,}  finds {s.get('finds_total', 0)}  best {s.get('best')}")
    print(f"  last scan {when(last.get('finished_at'))}: {last.get('scanned', 0)} frames, "
          f"{last.get('finds', 0)} finds")
    scans = [r for r in s.get("runs", []) if r.get("scanned") and r.get("finished_at")]
    if scans:
        end = scans[-1]["finished_at"]
        span = min(86400, end - scans[0]["started_at"])
        day = [r for r in scans if r["finished_at"] > end - span]
        busy = sum(r["finished_at"] - max(r["started_at"], end - span) for r in day)
        frames = sum(r["scanned"] for r in day)
        print(f"  last {span / 3600:.0f} h: {frames:,} frames ({frames * 3600 / span:,.0f}/h), "
              f"scanning {100 * busy / span:.0f}% of the time (the relay should keep this near 100%)")
        kinds, guards = {}, {"rejected": 0, "unconfirmed": 0}
        for r in day:
            for k, v in (r.get("error_kinds") or {}).items():
                kinds[k] = kinds.get(k, 0) + v
            for k in guards:
                guards[k] += r.get(k) or 0
        for r in scans[-3:]:
            c = r.get("concurrency") or {}
            if "latency_median_s" in c:
                print(f"  run {when(r['finished_at'])}: {r.get('frames_per_hour')}/h, latency median "
                      f"{c['latency_median_s']}s p90 {c['latency_p90_s']}s, {c.get('in_flight')} requests "
                      f"in flight on average (of up to {r.get('max_workers')})")
                if c.get("unpaid_steps"):
                    print(f"    {c['unpaid_steps']} step(s) up gave no more frames a second and were undone")
                if c.get("by_level"):
                    print("    median latency by requests at once: " + ", ".join(
                        f"{k}: {v[1]}s ({v[0]})" for k, v in c["by_level"].items()))
        # D2: per-run medians by level are skewed (low levels are only sampled at
        # the start and end of a run); pooled across runs, weighted by requests
        pool = {}
        for r in scans:
            for k, (n, med) in ((r.get("concurrency") or {}).get("by_level") or {}).items():
                w, total = pool.get(k, (0, 0.0))
                pool[k] = (w + n, total + n * med)
        if pool:
            print("  latency by requests at once, all kept runs: " + ", ".join(
                f"{k}: {t / w:.1f}s ({w:,})" for k, (w, t) in sorted(pool.items(), key=lambda kv: int(kv[0]))))
        if kinds:
            print("  fetch errors by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
        print(f"  guards: {guards['rejected']} frames not genuine Babelia frames, "
              f"{guards['unconfirmed']} finds not confirmed by a second download"
              + ("; SCANNING HALTED: " + s["halted"]["reason"] if s.get("halted") else ""))
    print("  re-mined for the current calibration: "
          + ("yes" if s.get("log_calibration") else "not yet (new code not live, or re-mine pending)"))
except (ValueError, json.JSONDecodeError):
    print("  could not read data/stats.json")

print("== Actions minutes, last 30 days (F5)")
try:
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    mins = {}
    for page in range(1, 11):
        runs = get(f"/actions/runs?per_page=100&page={page}&created=>={since}").get("workflow_runs", [])
        for r in runs:
            if r.get("run_started_at") and r.get("updated_at"):
                a0 = dt.datetime.fromisoformat(r["run_started_at"].replace("Z", "+00:00"))
                a1 = dt.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
                mins[r["name"]] = mins.get(r["name"], 0) + max(0, (a1 - a0).total_seconds()) / 60
        if len(runs) < 100:
            break
    for name, m in sorted(mins.items(), key=lambda kv: -kv[1]):
        print(f"  {name:32s} {m:8,.0f} min")
    print("  (public repositories run Actions free; this shows the load, not a bill)")
except Exception as e:
    print(f"  could not count: {e}")

print("== calibration audit (log-archive branch)")
if subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", "log-archive"],
                  capture_output=True).returncode == 0:
    try:
        a = json.loads(git("show", "FETCH_HEAD:audit.json"))
        snaps = git("ls-tree", "--name-only", "FETCH_HEAD").split()
        print(f"  {a.get('status')}: {a.get('frames', 0):,} out-of-sample frames from "
              f"{len(a.get('snapshots') or [])} snapshot(s); {sum(n.startswith('scores-') for n in snaps)} archived"
              + (f"; surplus in {', '.join(a['flagged'])}" if a.get("flagged") else ""))
    except (ValueError, json.JSONDecodeError):
        print("  no audit.json yet")
else:
    print("  no archive yet (the weekly audit workflow has not run)")

print("== code")
# A running relay scan publishes after every 30-minute chunk, so it counts once a
# Pages deploy has finished more than 25 minutes after it started.
iso = lambda t: dt.datetime.fromisoformat(t.replace("Z", "+00:00"))  # noqa: E731
deployed = iso(pages[0]["updated_at"]) if pages and pages[0]["conclusion"] == "success" else None
ok = [r for r in get("/actions/workflows/scan-and-publish.yml/runs?per_page=20")["workflow_runs"]
      if r["conclusion"] == "success" or (r["status"] == "in_progress" and deployed
                                          and deployed - iso(r["created_at"]) > dt.timedelta(minutes=25))]
main = git("rev-parse", "origin/main")
if not ok:
    print("  no successful publish found")
elif ok[0]["head_sha"] == main:
    print(f"  live site built from main ({main[:7]})")
else:
    behind = git("rev-list", "--count", f"{ok[0]['head_sha']}..{main}") or "?"
    print(f"  live site built from {ok[0]['head_sha'][:7]}; main is {main[:7]}: "
          f"{behind} commit(s) not live yet")
