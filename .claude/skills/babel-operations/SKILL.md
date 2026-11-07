---
name: babel-operations
description: Ship and run Surfing Babel. Covers the branch → PR → CI → merge routine, commit conventions, the scan-and-publish workflow (one pending run, hand-off, always-publish), and checking what's live without github.io access. Use for "is it live yet", "merge it", "make it appear on the site", "Scan now", scheduling scans, editing .github/workflows, or debugging deploys.
---

# Surfing Babel: operations

## Must
- Work on `claude/pensive-ritchie-hc4bfy`. If its last PR was merged, first run
  `git checkout -B claude/pensive-ritchie-hc4bfy origin/main`.
- Before pushing, run `python -m pytest -q` and `node --check site/*.js`. Then open a PR
  with the GitHub MCP tools, wait for the **Tests** check, and merge (the owner has
  authorised merging to main).
- Write plain commit and PR messages: **no Co-Authored-By, session or "Generated with"
  lines in commits** (the owner's request). Use tool-neutral wording such as "an AI coding
  agent".
- Before saying something is live, check it with `scripts/status.py`. If a deploy is
  queued, schedule a follow-up check (`send_later`) instead of polling.

## Never
- Put tokens or secrets in the repo or on the site. The Scan now token lives only in the
  visitor's localStorage. The external cron uses the owner's own token.
- Promise a quick deploy while a manual scan is running.
  - The workflow keeps **one pending run**; any newer trigger (push, Scan now, flag issue)
    cancels it.
  - The running scan hands over to new code only between its 30-minute chunks.
  - A run that started on older workflow code can't hand over at all, so it runs until it
    finishes or hits the 6-hour limit.
- Remove the `|| echo ::warning::` guards after the scan step. They make every chunk
  publish even if refit, re-mine, build or alerts fail.

## Recipes
- **What's live, what's queued, what the data says:**
  `python3 .claude/skills/babel-operations/scripts/status.py`
- **Local preview:**
  1. Copy the data: `git archive origin/gh-pages data | tar -x -C <scratch>`.
  2. Build: `python -m babel_surfer.build_site --out <scratch>/pub --offline`.
  3. Serve the folder with `python -m http.server`.
  4. Check it with Playwright (`require('playwright')`).
- **Test the workflow shell:**
  1. Extract the step's `run:` script with `yaml.safe_load`.
  2. Replace `${{ … }}` with a placeholder and run `bash -n`.
  3. Run it with stubbed `python`, `git` and `gh` on PATH.

## Reference
[reference.md](reference.md) covers:
- workflow triggers, minutes and chunks;
- the keep-alive step and the external cron;
- the gh-pages data layout and the `detector` / `assets` ids;
- flags, cache busting, pinned dependencies, and other lessons learned.
