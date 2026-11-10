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
                if c.get("by_level"):
                    print("    median latency by requests at once: " + ", ".join(
                        f"{k}: {v[1]}s ({v[0]})" for k, v in c["by_level"].items()))
        if kinds:
            print("  fetch errors by kind: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])))
        print(f"  guards: {guards['rejected']} frames not genuine Babelia frames, "
              f"{guards['unconfirmed']} finds not confirmed by a second download"
              + ("; SCANNING HALTED: " + s["halted"]["reason"] if s.get("halted") else ""))
    print("  re-mined for the current calibration: "
          + ("yes" if s.get("log_calibration") else "not yet (new code not live, or re-mine pending)"))
except (ValueError, json.JSONDecodeError):
    print("  could not read data/stats.json")

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
