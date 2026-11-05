# Live demo runbook: agentic coding on Surfing Babel

**What the audience sees:** Claude Code takes a small, real task from a written spec. It
writes (or debugs) the code, runs the tests, opens a pull request, waits for CI to go green and
merges. The showcase page follows every step on its own: a six-step progress tracker, a
stopwatch, and the spec as a checklist that turns green as the requirements pass.

**Duration:** about 6–10 minutes from prompt to live page; the agent works for about 2–4 of them.

## The pieces

| piece | where | role |
|---|---|---|
| Showcase page | `…/agent.html` (`?present=1` for a big screen) | progress tracker, stopwatch, live checklist, the prompt, reset buttons, repo activity |
| Demo slot | `babel_surfer/demo_slot.py` | `find_of_the_day()`; its docstring is the spec |
| Spec tests | `tests/test_demo_slot.py` | 6 tests, one per requirement. Skipped while the slot is unimplemented. |
| Templates | `babel_surfer/demo_templates/{stub,buggy}.py` | **stub**: unimplemented; **buggy**: runs, but breaks two requirements |
| Demo kit | `python -m babel_surfer.demo_kit reset --variant stub\|buggy` / `check` | reset the slot; print the checklist |
| CI | `.github/workflows/tests.yml` | runs the spec and the full suite on every pull request; the PR shows a check |
| Reset workflow | `.github/workflows/demo-reset.yml` | resets the slot on `main` and republishes the site (about 3 min) |
| Fast publish | `scan-and-publish.yml` | a merge that changes only the demo slot, site, docs or tests publishes **without scanning** |
| Safe build | `build_site` | runs the spec in a separate process; a broken implementation shows failing checks, never a broken site |

## Two demo variants

| variant | prompt (shown on the page, with a Copy button) | shows off |
|---|---|---|
| **Unimplemented** (`stub`) | implement it from the docstring and tests, run the tests, PR, merge once green | writing code from a spec |
| **Buggy** (`buggy`) | some spec tests fail: explain which requirements are broken and why, fix, PR, merge | reading failures and diagnosing two subtle bugs: an order-dependent pick and a pick that ignores score |

## Before the demo (T−10 min)

1. **Reset the slot** to the variant you want. On the showcase page, open **Presenter tools**
   and click **Reset: unimplemented** or **Reset: buggy**. This needs the same fine-grained token
   as the scanner's Scan now button (Actions: Read and write), saved in that browser. Without a
   token, the button opens the **Reset live demo** workflow on GitHub; choose the variant and run
   it. About 3 minutes later the badge reads "waiting for the agent" (unimplemented) or
   "4 of 6 checks pass" (buggy).
2. Open `https://ow-hill.github.io/Surfing-Babel_Claude-Code-Test/agent.html?present=1` on the
   presenter screen. Presenter mode hides the story, enlarges the tracker and turns live mode on.
3. **Check what's running:** in the "Scans & deploys" column, an hourly 45-minute scan may be in
   progress. They start at about :23 past the hour. A merge waits behind it, so plan the demo
   to start just after a scan finishes, or cancel the running scan from the Actions tab.
4. Open a Claude Code session on the repository, starting from an up-to-date `main`.

## On stage

1. Click **Start the clock** on the page.
2. Click **Copy prompt** and paste it into Claude Code.
3. Narrate as the tracker advances:

| step | typical time | what to point out |
|---|---|---|
| 1 Prompt given | 0:00 | — |
| *(agent reads, writes and tests locally)* | 1–3 min | it reads the docstring and tests first; for **buggy** it explains each failure before touching code |
| 2 Pull request opened | ~2–3 min | appears in the tracker and the "Pull requests" column within 15 s |
| 3 Tests pass on the PR | ~1–2 min | GitHub's own CI, separate from the agent's local run; if it fails, the agent should read the log and fix it |
| 4 Merged to main | seconds | the agent merges only once the check is green |
| 5 Building and publishing | ~2–3 min | no scan (only the demo slot changed): build, then GitHub Pages deploy |
| 6 Live on this page | — | the checklist turns green, the Find of the day appears, the **clock stops** |

**Fill the wait** by scrolling to the timeline (outside presenter mode), where the "moments" show
the agent catching its own bugs.

## If something goes wrong

- **The agent's first attempt fails a test:** good. Let it iterate; it's the most convincing
  part.
- **CI fails on the PR:** the tracker turns step 3 red, with a link to the log. Ask the agent to
  fix it.
- **Pages deploy is slow or times out:** GitHub Pages occasionally stalls; this happened once
  during development. Show the merged PR and the green CI check. The next publish catches up.
- **The page says the GitHub limit was reached:** live mode uses conditional requests, which
  are normally free. Wait until the time shown, or use another network.
- **The checklist shows "checks couldn't run":** the published build couldn't run pytest. Check
  the Scan & publish log.

## After the demo

Reset the slot (Presenter tools, or the **Reset live demo** workflow), or from a terminal:

```bash
python -m babel_surfer.demo_kit reset --variant stub
```

```bash
git commit -am "Reset live demo slot" && git push
```

## Variations

- **"…and make the caption use the find's description."** The agent reads `analysis.py` to learn
  the description format.
- **"…and add a test that a find featured yesterday isn't featured again today."** The agent
  extends the spec itself; the new test appears in CI, but not in the page's checklist, which
  lists the six original requirements.
- **Two-act demo:** run **buggy** first (diagnosis), then reset to **stub** and run it again
  (creation), and compare the two times on the clock.
