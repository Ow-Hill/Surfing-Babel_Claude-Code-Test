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

MAX_PER_RUN = 20               # frames scored per run
MAX_PER_AUTHOR = 3              # per person per run, so one account can't crowd others out
MAX_FAILURES = 3                # fetch failures before a flag is closed as not planned
FETCH_TIMEOUT, FETCH_RETRIES = 40.0, 1
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

    def open_flags(self, pages: int = 5) -> list[dict]:
        out = []
        for page in range(1, pages + 1):
            issues = self.call("GET", f"/issues?state=open&per_page=100&sort=created&direction=asc&page={page}") or []
            out += [i for i in issues if "pull_request" not in i and parse_location(i.get("title", ""))]
            if len(issues) < 100:
                break
        return out

    def comment(self, number: int, text: str) -> None:
        self.call("POST", f"/issues/{number}/comments", {"body": text})

    def close(self, number: int, completed: bool = True) -> None:
        self.call("PATCH", f"/issues/{number}",
                  {"state": "closed", "state_reason": "completed" if completed else "not_planned"})

    def reply_and_close(self, number: int, text: str, completed: bool = True) -> None:
        self.comment(number, text)
        self.close(number, completed)


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
            f"_Closed automatically. To vote for this frame again, open a new flag for it; "
            f"reactions on closed issues are not counted._")


def group_flags(issues: list[dict], limit: int = MAX_PER_RUN,
                per_author: int = MAX_PER_AUTHOR) -> list[tuple[str, list[dict]]]:
    """Open flag issues grouped by location, oldest first: votes for the same
    frame add up across issues, and nobody gets more than ``per_author``
    frames scored per run."""
    by_loc: dict[str, list[dict]] = {}
    authors: dict[str, int] = {}
    for issue in issues:
        loc = parse_location(issue.get("title", ""))
        if loc is None:
            continue
        if loc not in by_loc:
            who = ((issue.get("user") or {}).get("login") or "?").lower()
            if authors.get(who, 0) >= per_author or len(by_loc) >= limit:
                continue
            authors[who] = authors.get(who, 0) + 1
            by_loc[loc] = []
        by_loc[loc].append(issue)
    return list(by_loc.items())


def process(store: Store, gh: GitHub, fetch, threshold: float = INTERESTING_SIGMA,
            limit: int = MAX_PER_RUN) -> int:
    """Score open flag issues; returns how many frames were handled. One bad
    flag never blocks the others; a frame Babelia keeps failing on is closed
    after MAX_FAILURES tries."""
    groups = group_flags(gh.open_flags(), limit)
    run = {"started_at": time.time(), "trigger": "flags", "scanned": 0, "finds": 0, "errors": 0}
    failures = store.stats.setdefault("flag_failures", {})
    done = 0
    for loc, issues in groups:
        try:
            data = fetch(loc)
            result = classify(data, threshold=threshold)
        except Exception as e:                       # Babelia down or a bad frame
            log.warning("could not fetch or score flagged %s: %s", loc, e)
            failures[loc] = failures.get(loc, 0) + 1
            if failures[loc] >= MAX_FAILURES:
                for issue in issues:
                    try:
                        gh.comment(issue["number"], f"Babelia didn't return a usable frame for **{loc}** "
                                                    f"after {MAX_FAILURES} tries, so this flag is closed.")
                        gh.close(issue["number"], completed=False)
                    except Exception as e2:
                        log.warning("could not close #%s: %s", issue["number"], e2)
                failures.pop(loc, None)
            continue
        failures.pop(loc, None)
        votes = sum(1 + int((i.get("reactions") or {}).get("+1", 0)) for i in issues)
        try:
            entry = store.add_flag(loc, data, result, votes, note_from(issues[0].get("body")),
                                   issues[0].get("html_url"), run)
        except Exception as e:
            log.warning("could not keep flagged %s: %s", loc, e)
            store.error(run)
            continue
        for issue in issues:
            try:
                gh.reply_and_close(issue["number"], reply_text(loc, result, entry))
            except Exception as e:
                log.warning("could not reply on #%s: %s", issue["number"], e)
        done += 1
    if done or failures:
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
    import functools
    from .scan import fetch
    quick = functools.partial(fetch, timeout=FETCH_TIMEOUT, retries=FETCH_RETRIES)
    n = process(Store(args.data), GitHub(token, repo), quick)
    print(f"handled {n} flag(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
