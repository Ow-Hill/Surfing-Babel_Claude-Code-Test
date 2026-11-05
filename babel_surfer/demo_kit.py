"""Tools for the live-coding demo (see docs/live-demo.md).

* ``reset``: put ``babel_surfer/demo_slot.py`` back to a template:
  ``stub`` (unimplemented: the agent writes it) or ``buggy`` (runs, but
  breaks two requirements: the agent debugs it).
* ``spec_results()``: run the slot's spec tests and report each requirement
  as passed / failed / waiting, for the showcase page's live checklist.

    python -m babel_surfer.demo_kit reset --variant buggy
    python -m babel_surfer.demo_kit check
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PKG = Path(__file__).resolve().parent
ROOT = PKG.parent
SLOT = PKG / "demo_slot.py"
TEMPLATES = PKG / "demo_templates"
SPEC_TESTS = ROOT / "tests" / "test_demo_slot.py"
VARIANTS = ("stub", "buggy")

#: test function -> the requirement it checks, in the order shown on the page
REQUIREMENTS = {
    "test_none_when_no_finds": "Returns nothing when there are no finds",
    "test_result_shape": "Returns the chosen find's location, score, tier and reason, plus a caption",
    "test_deterministic_and_order_independent": "Same finds and day give the same pick, in any order",
    "test_varies_by_day": "Different days feature different finds (at least 3 over 30 days)",
    "test_favours_stronger_finds": "Stronger finds are featured more often",
    "test_caption": "One-line caption of at most 140 characters, including the score (e.g. 5.8σ)",
}


def reset(variant: str = "stub") -> Path:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    shutil.copyfile(TEMPLATES / f"{variant}.py", SLOT)
    return SLOT


def current_variant() -> str:
    """Which template the slot currently matches, or 'custom' (the agent's
    own implementation)."""
    text = SLOT.read_text()
    for v in VARIANTS:
        if (TEMPLATES / f"{v}.py").read_text() == text:
            return v
    return "custom"


def spec_results(timeout: int = 120) -> dict:
    """Run the spec tests in a separate process (a broken implementation
    can't take the build down) and summarise them per requirement."""
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(SPEC_TESTS),
                 f"--junitxml={xml_path}"],
                cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": f"spec tests took longer than {timeout} s",
                    "variant": current_variant(), "requirements": []}
        if not xml_path.exists():
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
            return {"status": "error", "message": " ".join(tail)[:300] or "pytest did not run",
                    "variant": current_variant(), "requirements": []}
        cases = {}
        for tc in ET.parse(xml_path).getroot().iter("testcase"):
            name = tc.get("name", "")
            failure = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
            if failure is not None:
                msg = (failure.get("message") or failure.text or "").strip().splitlines()
                cases[name] = ("failed", (msg[0] if msg else "")[:200])
            elif tc.find("skipped") is not None:
                cases[name] = ("waiting", "")
            else:
                cases[name] = ("passed", "")
    reqs = [{"test": t, "requirement": text, "status": cases.get(t, ("missing", ""))[0],
             "message": cases.get(t, ("", ""))[1]} for t, text in REQUIREMENTS.items()]
    statuses = {r["status"] for r in reqs}
    overall = ("waiting" if statuses == {"waiting"} else
               "passed" if statuses == {"passed"} else
               "failed" if "failed" in statuses or "missing" in statuses else "partial")
    return {"status": overall, "variant": current_variant(),
            "passed": sum(r["status"] == "passed" for r in reqs), "total": len(reqs),
            "requirements": reqs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reset", help="restore the demo slot from a template")
    r.add_argument("--variant", choices=VARIANTS, default="stub")
    sub.add_parser("check", help="run the spec tests and print the results")
    args = ap.parse_args(argv)
    if args.cmd == "reset":
        print(f"demo slot reset to '{args.variant}': {reset(args.variant)}")
        return 0
    res = spec_results()
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["status"] in ("passed", "waiting") else 1


if __name__ == "__main__":
    raise SystemExit(main())
