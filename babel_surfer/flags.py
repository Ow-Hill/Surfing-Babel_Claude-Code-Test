"""Frames people flagged: "I see something".

The site's "I see something" button opens a pre-filled GitHub issue titled
``Flag: <location>``. At the start of every scan this module reads the open
flag issues, fetches each flagged frame from Babelia, scores it, keeps it in
``data/flags.json`` ("People noticed", ranked by votes), replies on the issue
with the scores and closes it. A 👍 on the issue counts as an extra vote.

Needs ``GH_TOKEN`` (the workflow's token) and ``GITHUB_REPOSITORY``; does
nothing without them.

    python -m babel_surfer.flags --data public/data
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
import urllib.request

from . import classifier
from .classifier import INTERESTING_SIGMA, classify
from .storage import Store

log = logging.getLogger("babel_surfer.flags")

MAX_PER_RUN = 20
MAX_DIGITS = 40                 # slideshow locations have at most 16 digits
TITLE = re.compile(r"^\s*flag\s*:?\s*(\d{1,%d})\s*$" % MAX_DIGITS, re.I)
SITE = "https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/"


def parse_location(title: str) -> str | None:
    """The location in a ``Flag: <digits>`` title, or None."""
    m = TITLE.match(title or "")
    if not m:
        return None
    loc = m.group(1).lstrip("0") or "0"
    return loc


def note_from(body: str | None) -> str:
    """The flagger's own words: the body minus the template's lines."""
    lines = [l.strip() for l in (body or "").splitlines()]
    keep = [l for l in lines if l and not l.startswith(("<!--", "Location:", "Seen on", "http"))]
    return " ".join(keep)[:280]


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token, self.repo = token, repo

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{self.repo}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "surfing-babel-flags"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        return json.loads(data) if data else None

    def open_flags(self) -> list[dict]:
        issues = self.call("GET", "/issues?state=open&per_page=100&sort=created&direction=asc") or []
        return [i for i in issues if "pull_request" not in i and parse_location(i.get("title", ""))]

    def reply_and_close(self, number: int, text: str, completed: bool = True) -> None:
        self.call("POST", f"/issues/{number}/comments", {"body": text})
        self.call("PATCH", f"/issues/{number}",
                  {"state": "closed", "state_reason": "completed" if completed else "not_planned"})


def reply_text(location: str, result, entry: dict) -> str:
    vis = entry.get("visibility") or {}
    seen = {"likely": "likely visible at a glance", "faint": "faint", "invisible": "not visible"}
    top = sorted(result.tests.items(), key=lambda kv: kv[1], reverse=True)[:3]
    tests = ", ".join(f"{t} {z:.1f}σ" for t, z in top)
    ratio = f" ({vis['ratio']}× static's own flicker)" if vis.get("ratio") else ""
    verdict = ("a **find**" if result.is_interesting else
               "below the find threshold, but kept in *People noticed*")
    return (f"Thanks for flagging **{location}**. Scored by the scan workflow:\n\n"
            f"- strongest test: **{result.reason} {result.sigma:.1f}σ** ({result.label}); "
            f"{result.sigma_global:.1f}σ for the whole frame after allowing for every test\n"
            f"- top tests: {tests}\n"
            f"- to the eye (contrast model): {seen.get(vis.get('label'), 'n/a')}{ratio}\n"
            f"- {entry.get('description') or ''}\n\n"
            f"That makes it {verdict}. See it on the site: {SITE}#{location}\n\n"
            f"_Closed automatically; a 👍 on this issue still counts as a vote next time it is flagged._")


def process(store: Store, gh: GitHub, fetch, threshold: float = INTERESTING_SIGMA,
            limit: int = MAX_PER_RUN) -> int:
    """Score open flag issues; returns how many were handled."""
    issues = gh.open_flags()[:limit]
    run = {"started_at": time.time(), "trigger": "flags", "scanned": 0, "finds": 0, "errors": 0}
    done = 0
    for issue in issues:
        loc = parse_location(issue["title"])
        try:
            data = fetch(loc)
        except Exception as e:                       # Babelia down: try again next run
            log.warning("could not fetch flagged %s: %s", loc, e)
            continue
        result = classify(data, threshold=threshold)
        votes = 1 + int((issue.get("reactions") or {}).get("+1", 0))
        entry = store.add_flag(loc, data, result, votes, note_from(issue.get("body")),
                               issue.get("html_url"), run)
        try:
            gh.reply_and_close(issue["number"], reply_text(loc, result, entry))
        except Exception as e:
            log.warning("could not reply on #%s: %s", issue["number"], e)
        done += 1
    if done:
        run["finished_at"] = time.time()
        run["flags"] = done
        store.add_run(run, primary=False)
        store.save()
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="public/data")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("no GH_TOKEN / GITHUB_REPOSITORY: skipping flags")
        return 0
    classifier.activate_live_calibration(args.data)
    from .scan import fetch
    n = process(Store(args.data), GitHub(token, repo), fetch)
    print(f"handled {n} flag(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
