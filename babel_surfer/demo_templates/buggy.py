"""Live-coding demo slot: "Find of the day".

This module is deliberately left unimplemented. It is the task for the live
demo of agentic coding on the showcase page (site/agent.html): an AI coding agent
is asked to implement it on stage, from this docstring and the tests in
``tests/test_demo_slot.py`` alone. Until then the site shows the spec and
"waiting for the agent". See ``docs/live-demo.md`` for the runbook.

Scope is intentionally one function in one file, so the demo is quick, safe
and repeatable.

Spec
----
``find_of_the_day(finds, day)`` picks one find to feature on the site.

* ``finds`` is the list from ``data/finds.json``: dicts with at least
  ``location`` (str), ``sigma`` (float), ``tier`` (str), ``reason`` (str) and
  optionally ``description`` (str).
* ``day`` is a ``datetime.date``.
* Return ``None`` when there are no finds.
* Otherwise return a dict with keys ``location``, ``sigma``, ``tier``,
  ``reason`` (copied from the chosen find) and ``caption``.
* **Deterministic:** the same finds and day always give the same pick, no
  matter what order the finds are listed in. (No ``random`` without a seed
  derived from the day and the finds.)
* **Varies by day:** over 30 consecutive days with 5 or more finds, at least 3
  different finds are featured.
* **Favours stronger finds:** over a year, a find with a higher sigma is
  featured more often than one with a lower sigma.
* ``caption``: a single line of at most 140 characters that includes the
  score formatted like ``5.8σ``.
"""

from __future__ import annotations

from datetime import date


def find_of_the_day(finds: list[dict], day: date) -> dict | None:
    """Pick the find to feature on ``day`` (see the module docstring)."""
    # Debugging variant for the live demo: this "works", but quietly breaks
    # two requirements of the spec. The agent is asked to find out which and
    # why, from the failing tests, then fix them.
    if not finds:
        return None
    pick = finds[day.toordinal() % len(finds)]
    caption = f"Find of the day: {pick['sigma']:.1f}σ, {pick['tier']}"
    return {"location": pick["location"], "sigma": pick["sigma"], "tier": pick["tier"],
            "reason": pick["reason"], "caption": caption}
