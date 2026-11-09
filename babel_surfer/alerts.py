"""Alerts: an Atom feed of finds, and a GitHub-issue body for big ones.

* ``feed.xml``: every find on the site, newest first. Subscribe in any feed
  reader; no account or token needed.
* ``alert.md``: written only when the latest scan found something at or
  above ``PICTURE_SIGMA`` (a genuine pattern, not an anomaly). The workflow
  turns it into a GitHub issue, which emails the repository's watchers.

    python -m babel_surfer.alerts --data public/data --feed public/feed.xml --alert alert.md
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from xml.sax.saxutils import escape

from .classifier import PICTURE_SIGMA
from .scan import image_url

SITE_URL = "https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/"
FEED_ITEMS = 50


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def build_feed(finds: list[dict], site_url: str = SITE_URL) -> str:
    finds = sorted(finds, key=lambda f: f["found_at"], reverse=True)[:FEED_ITEMS]
    updated = _iso(finds[0]["found_at"] if finds else time.time())
    items = []
    for f in finds:
        link = f"{site_url}#{f['location']}"
        title = f"{f['tier']} · {f['sigma']:.1f}σ · location {f['location']}"
        summary = (f"{f.get('description') or 'Strongest signal: ' + f['reason'] + '.'} "
                   f"Full image: {image_url(f['location'])}")
        items.append(f"""  <entry>
    <id>{escape(link)}</id>
    <title>{escape(title)}</title>
    <link href="{escape(link)}"/>
    <updated>{_iso(f['found_at'])}</updated>
    <summary>{escape(summary)}</summary>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>{escape(site_url)}</id>
  <title>Surfing Babel: finds</title>
  <subtitle>Frames from the Babel Image Archive that scored as more than static.</subtitle>
  <link href="{escape(site_url)}"/>
  <link rel="self" href="{escape(site_url)}feed.xml"/>
  <updated>{updated}</updated>
{chr(10).join(items)}
</feed>
"""


def build_alert(stats: dict, site_url: str = SITE_URL, since: float | None = None) -> str | None:
    """Issue body for finds at or above PICTURE_SIGMA in every run that
    finished after ``since`` (default: only the latest scan). Several runs
    can be recorded between alerts (a scan, then a re-mine), so checking
    only the last one could miss a find."""
    runs = stats.get("runs") or []
    if since is None:
        last = stats.get("last_run") or {}
        runs = [last] if last else []
    else:
        runs = [r for r in runs if (r.get("finished_at") or 0) > since]
    seen, big = set(), []
    for run in runs:
        for f in run.get("new_finds", []):
            if f["sigma"] >= PICTURE_SIGMA and f["location"] not in seen:
                seen.add(f["location"])
                big.append(f)
    if not big:
        return None
    big.sort(key=lambda f: f["sigma"], reverse=True)
    end = max((r.get("finished_at") or 0) for r in runs) or time.time()
    lines = [f"Scans up to {_iso(end)} found "
             f"{len(big)} frame(s) at or above {PICTURE_SIGMA:g}σ. Pure static has never reached that "
             f"score in calibration, so these are worth a look.", ""]
    for f in big:
        lines.append(f"- **{f['tier']} · {f['sigma']:.1f}σ** (strongest signal: {f['reason']}): "
                     f"[on the site]({site_url}#{f['location']}) · "
                     f"[full image]({image_url(f['location'])}) · location `{f['location']}`")
        if f.get("description"):
            lines.append(f"  {f['description']}")
    lines += ["", f"Frames scanned in these runs: {sum(r.get('scanned', 0) for r in runs):,}."]
    return "\n".join(lines)


def build_halt_alert(stats: dict, since_halt: float | None) -> str | None:
    """Issue body when scanning halted on an implausible find rate (once per
    halt), or None."""
    h = stats.get("halted")
    if not h or (since_halt is not None and h["at"] <= since_halt):
        return None
    q = stats.get("quarantine") or []
    lines = [f"Scanning halted at {_iso(h['at'])}: {h['reason']}.", "",
             "That many finds can't be the tail of static, so something upstream is wrong: most likely "
             "a calibration that no longer fits real frames, or Babelia changing how it renders. The "
             f"run's finds were taken off the site and out of the totals ({len(q)} frame(s) are listed "
             "in stats.json under `quarantine`).", "",
             "Once it is understood, resume from the Actions tab: run **Scan Babel and publish site** "
             "with **resume** set to 1."]
    for f in q[-10:]:
        lines.append(f"- `{f['location']}` {f['sigma']:.1f}σ ({f['reason']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("public/data"))
    ap.add_argument("--feed", type=Path, default=Path("public/feed.xml"))
    ap.add_argument("--alert", type=Path, default=Path("alert.md"))
    args = ap.parse_args(argv)
    finds = json.loads((args.data / "finds.json").read_text()) if (args.data / "finds.json").exists() else []
    stats = json.loads((args.data / "stats.json").read_text()) if (args.data / "stats.json").exists() else {}
    args.feed.write_text(build_feed(finds))
    # remember how far alerts have looked, so every run is checked exactly once
    mark = args.data / "alerted.json"
    try:
        since = float(json.loads(mark.read_text())["until"])
    except (OSError, ValueError, KeyError, TypeError):
        last = stats.get("last_run") or {}
        since = (last.get("started_at") or time.time()) - 1      # first time: the latest scan
    try:
        halt_seen = json.loads(mark.read_text()).get("halt")
    except (OSError, ValueError, AttributeError):
        halt_seen = None
    halt = build_halt_alert(stats, halt_seen)
    if halt:
        args.alert.with_name("alert-halt.md").write_text(halt)
        print("halt alert written")
    body = build_alert(stats, since=since)
    newest = max([since] + [r.get("finished_at") or 0 for r in stats.get("runs") or []])
    mark.write_text(json.dumps({"until": newest,
                                "halt": (stats.get("halted") or {}).get("at", halt_seen)}))
    if body:
        args.alert.write_text(body)
        print(f"alert written to {args.alert}")
    else:
        args.alert.unlink(missing_ok=True)
        print("no alert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
