# Live demo runbook: agentic coding on Surfing Babel

**Duration:** about 10 minutes on stage, about 5 of them waiting while the agent works.
**What the audience sees:** Claude Code implements a small, real feature from a spec. It runs
the tests, opens and merges a pull request, and the feature appears on the live website, which
updates by itself.

## The pieces

| piece | where | role |
|---|---|---|
| Showcase page | `site/agent.html` → `…/agent.html` | the project story, the demo slot and live repository activity |
| Demo slot | `babel_surfer/demo_slot.py` | `find_of_the_day()`: an unimplemented function whose docstring is the spec |
| Spec tests | `tests/test_demo_slot.py` | 6 tests; **skipped** until the function exists, then they run automatically |
| Safe build hook | `build_site.demo_slot_result()` | calls the slot on every build; a broken implementation shows "implementation error" on the page instead of breaking the site |
| Fast publish | push-triggered runs scan for 1 minute | a merged change is live in about 3–4 minutes |

The task is deliberately one function in one file, so it is quick, safe and repeatable.

## Before the demo (T−10 min)

1. Open `https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/agent.html?live=1` on the
   presenter screen. `?live=1` turns on live mode, which refreshes every 15 s.
2. Check the slot badge reads **"waiting for the agent"**. If it shows "implemented" from a
   previous demo, reset first (see below) and wait about 4 minutes for the site to update.
3. Make sure no long scan is running: the "Scans & deploys" column must not show
   "in progress" for a 330-minute batch. A merge during a long scan publishes only after the
   scan ends. If one is running, cancel it from the Actions tab.
   Scheduled 25-minute scans start at about :23 past every third hour UTC (00:23, 03:23, …),
   often 10–40 minutes late. Avoid those windows, or cancel the scheduled run if one is in
   progress.
4. Open a Claude Code session on this repository on the `main`-tracking working branch.

## On stage

Paste this prompt (it also says what to show and narrate):

> Implement `find_of_the_day` in `babel_surfer/demo_slot.py`. The module docstring and
> `tests/test_demo_slot.py` are the spec. Run the tests until they pass, run the full test suite,
> then commit, open a pull request to main, and merge it. Keep the change to that one file.

What to expect and narrate:

| step | typical time | what to point out |
|---|---|---|
| Reads the docstring and tests | ~30 s | it works out the requirements: deterministic, order-independent, weighted by score |
| Writes the function | ~1 min | typically hashes the day and the sorted locations into a seed, then makes a weighted pick |
| Runs `pytest tests/test_demo_slot.py` | ~10 s | the 6 tests go from *skipped* to *passed*; if one fails, watch it read the error and fix it |
| Runs the full suite | ~30 s | nothing else broke |
| Commit → PR → merge | ~1 min | the PR appears in the "Pull requests" column within ~15 s |
| Actions run, then Pages deploy | ~3–4 min | watch "Scans & deploys": queued → in progress → success → deploy |
| Page updates by itself | — | the badge turns **implemented** and the featured find appears |

**Fill the wait** by scrolling the timeline. The "moments" are where the agent caught its own
bugs, for example the 14σ calibration error and the Babelia fingerprint discovery.

## If something goes wrong

- **Tests fail and the agent keeps iterating:** that's a good moment to show; let it run.
- **The page shows "implementation error":** the build caught an exception from the new code.
  The rest of the site is unaffected. Ask the agent to read `agent/find_of_the_day.json` on the
  site, or the Actions log, and fix it.
- **The deploy is slow:** show the merged PR and the green test run instead. The page catches up
  in a few minutes.
- **GitHub rate limit on the page:** live mode uses conditional requests, which are normally free.
  If the status says the limit was reached, refresh on another network or wait for the time shown.

## Reset for the next demo

Restore the stub from the commit that introduced it, then publish:

```bash
git checkout "$(git log --diff-filter=A --format=%h -- babel_surfer/demo_slot.py | tail -1)" -- babel_surfer/demo_slot.py
```

```bash
git commit -am "Reset live demo slot" && git push
```

Merge that to `main` (or ask Claude Code to "reset the demo slot and merge"). The badge returns
to "waiting for the agent" after the next publish.

## Variations (to show other capabilities)

- **"…and make the caption use the find's description."** The agent reads `analysis.py` to learn
  the description format.
- **"…and add a test that a find featured yesterday isn't featured again today."** The agent
  extends the spec itself.
- **Start from a bug:** before the demo, commit an implementation that ignores `sigma`. Ask the
  agent why the strongest find never wins, and let it diagnose and fix the bug.
