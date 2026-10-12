#!/usr/bin/env python3
"""Run every gate and print what happened to each one.

A verification claim is an output like any other, and an output needs a
producer. Every gate is listed whether or not it can run here, and one that
cannot says SKIPPED with the reason, so the day a gate stops being runnable the
manifest says so in the place that would otherwise say nothing.

    python3 gates/run_all.py

Exit code is 0 only when nothing failed. Skips do not fail the run: a skip is a
stated absence, which is the point of printing it.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCAP = ROOT / "samples" / "queuing-retail-sample.mcap"
FOUNTAIN = ROOT / "samples" / "fountain-anchors.mcap"


def gates() -> list[dict]:
    try:
        import aiohttp        # noqa: F401
        import websockets     # noqa: F401
        web = True
    except ImportError:
        web = False
    try:
        import jsonschema     # noqa: F401
        have_js = True
    except ImportError:
        have_js = False
    no_fountain = None if FOUNTAIN.is_file() else (
        f"needs {FOUNTAIN.relative_to(ROOT)}; author it with "
        f"tools/author_anchors.py, which needs a specification checkout for "
        f"the IDL it embeds")
    no_mcap = None if MCAP.is_file() else (
        f"needs {MCAP.relative_to(ROOT)}, which is vendored in this "
        f"repository; see webjson/PROVENANCE")
    no_web = None if web else "needs aiohttp and websockets: pip install aiohttp websockets"
    no_js = None if have_js else "needs jsonschema: pip install jsonschema"
    # The examples gate verifies the browser snippets by running them, and a
    # snippet it cannot run it reports as a failure rather than a pass. So a
    # missing interpreter becomes a stated skip here instead of red there.
    no_node = None if shutil.which("node") else (
        "needs node to execute the browser console snippets in "
        "examples/browser.md; the gate fails rather than passes on an "
        "unverified example, so the absence is stated here")
    return [
        {"name": "wire_model", "what": "the recording's IDL and the published tables agree",
         "cmd": ["gates/wire_model.py"], "skip": no_mcap},
        {"name": "golden_json", "what": "the JSON projection matches the authored vectors",
         "cmd": ["gates/golden_json.py"], "skip": no_mcap},
        {"name": "decode_fidelity", "what": "every message decodes and the counts are the recording's",
         "cmd": ["gates/decode_fidelity.py"], "skip": no_mcap or no_js},
        {"name": "determinism", "what": "two builds serve byte-identical latched resources",
         "cmd": ["gates/determinism.py"], "skip": no_mcap or no_web},
        {"name": "replay_order", "what": "latched replay, exactly one end_of_replay, then live",
         "cmd": ["gates/replay_order.py"], "skip": no_mcap or no_web},
        {"name": "removal_parity", "what": "every removal is a terminal sample then a dispose",
         "cmd": ["gates/removal_parity.py"], "skip": no_mcap or no_web},
        {"name": "schema_fidelity", "what": "the endpoint publishes and obeys the generated schemas",
         "cmd": ["gates/schema_fidelity.py"], "skip": no_mcap or no_web or no_js},
        {"name": "access_classes", "what": "configuration is public, observation is bearer-gated",
         "cmd": ["gates/access_classes.py"], "skip": no_mcap or no_web},
        # The anchor-bearing recording. Authored rather than captured, and the
        # first artifact to exercise the content machinery end to end.
        {"name": "golden_anchor", "what": "one anchor field by field against the authoring input",
         "cmd": ["gates/golden_anchor.py"], "skip": no_fountain},
        {"name": "anchor_serve", "what": "anchors served unmodified, and discovery finds them",
         "cmd": ["gates/anchor_serve.py"], "skip": no_fountain or no_web or no_js},
        {"name": "content_resolution", "what": "a served reference ends in a real committed file",
         "cmd": ["gates/content_resolution.py"], "skip": no_fountain or no_web},
        # The storefront's own gate: an example that rots is worse than none.
        {"name": "examples", "what": "every example runs and prints what examples/ documents",
         "cmd": ["gates/examples.py"],
         "skip": no_mcap or no_fountain or no_web or no_node},
        # The front page as a sequence. `examples` checks each tier against
        # the recording its own marker names; this checks that the page's
        # serve line and its tiers agree, which is the defect that shipped.
        {"name": "quickstart", "what": "the README's quickstart runs when pasted verbatim",
         "cmd": ["gates/quickstart.py"],
         "skip": no_fountain or no_web or no_node},
    ]


def main() -> int:
    argparse.ArgumentParser().parse_args()
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    rows = []
    for g in gates():
        if g.get("skip"):
            rows.append((g["name"], "SKIPPED", g["skip"], g["what"]))
            continue
        r = subprocess.run([sys.executable] + g["cmd"], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        if r.returncode == 0:
            rows.append((g["name"], "PASS", "", g["what"]))
        else:
            lines = [l.strip() for l in (r.stdout or "").splitlines()
                     if l.strip()]
            verdicts = [l for l in lines
                        if l.startswith(("FAIL", "PASS")) or ", FAIL" in l]
            detail = (verdicts[-1] if verdicts else
                      lines[-1] if lines else
                      ((r.stderr or "").strip().splitlines() or ["no output"])[-1])
            rows.append((g["name"], "FAIL", detail[:96], g["what"]))

    w = max(len(r[0]) for r in rows)
    print(f"gate manifest, {ROOT.name}")
    print(f"  recordings: {MCAP.name if MCAP.is_file() else 'absent'}, "
          f"{FOUNTAIN.name if FOUNTAIN.is_file() else 'absent'}\n")
    for name, status, detail, what in rows:
        print(f"  {status:<8} {name:<{w}}  {what}")
        if detail:
            print(f"           {' ' * w}  {detail}")
    npass = sum(1 for r in rows if r[1] == "PASS")
    nfail = sum(1 for r in rows if r[1] == "FAIL")
    nskip = sum(1 for r in rows if r[1] == "SKIPPED")
    print(f"\n{len(rows)} gates: {npass} passed, {nfail} failed, {nskip} skipped")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
