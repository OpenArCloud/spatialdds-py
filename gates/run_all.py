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
# The distribution in the checkout, which carries the gateway and the
# vendored artifacts. Subprocesses get it on PYTHONPATH so that an
# example or a serve line runs against this tree and never an install.
LIB = str(ROOT / "lib" / "spatialdds")
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
    try:
        import requests       # noqa: F401
        have_requests = True
    except ImportError:
        have_requests = False
    no_fountain = None if FOUNTAIN.is_file() else (
        f"needs {FOUNTAIN.relative_to(ROOT)}; author it with "
        f"tools/author_anchors.py, which needs a specification checkout for "
        f"the IDL it embeds")
    no_mcap = None if MCAP.is_file() else (
        f"needs {MCAP.relative_to(ROOT)}, which is vendored in this "
        f"repository; see webjson/PROVENANCE")
    try:
        import yaml                    # noqa: F401
        import openapi_spec_validator  # noqa: F401
        no_openapi = None
    except ImportError:
        no_openapi = ("needs PyYAML and openapi-spec-validator: pip install "
                      "pyyaml openapi-spec-validator")
    no_web = None if web else "needs aiohttp and websockets: pip install aiohttp websockets"
    no_js = None if have_js else "needs jsonschema: pip install jsonschema"
    # The client package's own dependency, not the gateway's. A checkout that
    # has not installed it cannot run the library gate, and the manifest says
    # so rather than passing a gate that did not execute.
    no_requests = None if have_requests else (
        "needs requests, which lib/spatialdds depends on: pip install requests")
    # The examples gate verifies the browser snippets by running them, and a
    # snippet it cannot run it reports as a failure rather than a pass. So a
    # missing interpreter becomes a stated skip here instead of red there.
    pub_input = ROOT / "samples" / "published-records.input.json"
    no_input = None if pub_input.is_file() else (
        f"needs {pub_input.relative_to(ROOT)}, the authored records the "
        f"publish gates take their expected values from")
    duck_input = ROOT / "samples" / "duck.input.json"
    duck_asset = ROOT / "samples" / "assets" / "duck.gltf"
    no_duck = None if duck_input.is_file() and duck_asset.is_file() else (
        f"needs {duck_input.relative_to(ROOT)} and the committed asset it "
        f"declares")
    # `gates/serve_demo.py` starts the endpoint as a subprocess and confirms
    # the listening process is the one it started, which needs lsof. A probe
    # that fetched a URL until it answered would pass against a gateway left
    # running from an earlier session.
    no_lsof = None if shutil.which("lsof") else (
        "needs lsof to confirm which process is listening on the port")
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
        # The write surface. Implementation-defined, off by default, and the
        # last gate is the one that matters: with it off, nothing changed.
        {"name": "golden_publish", "what": "a published record is indistinguishable from a replayed one",
         "cmd": ["gates/golden_publish.py"],
         "skip": no_fountain or no_web or no_js or no_input},
        {"name": "publish_reject", "what": "every refusal is a status and a sentence, and stores nothing",
         "cmd": ["gates/publish_reject.py"],
         "skip": no_fountain or no_web or no_input},
        {"name": "journal_replay", "what": "restart is the same state, and a backup is a recording",
         "cmd": ["gates/journal_replay.py"],
         "skip": no_fountain or no_web or no_input},
        {"name": "publish_live", "what": "a publish reaches a subscriber already on the socket",
         "cmd": ["gates/publish_live.py"],
         "skip": no_fountain or no_web or no_input},
        {"name": "publish_offswitch", "what": "with publishing off, reads are byte-identical and writes are 405",
         "cmd": ["gates/publish_offswitch.py"],
         "skip": no_fountain or no_web or no_input},
        {"name": "publish_duck", "what": "a published record followed from PUT to fetched bytes",
         "cmd": ["gates/publish_duck.py"],
         "skip": no_fountain or no_web or no_input or no_duck},
        {"name": "publish_anchor", "what": "a published anchor is an anchor, and the content it names resolves",
         "cmd": ["gates/publish_anchor.py"],
         "skip": no_fountain or no_web or no_duck or no_js},
        # The client package in lib/spatialdds, which the gateway never
        # imports. Its first section is the one that matters here: the
        # library's own lift and the gateway's `webjson.Lifter` are two
        # implementations of the same three rules, and they are required to
        # agree on every message in both recordings.
        {"name": "library", "what": "the spatialdds client package reads, publishes and refuses as documented",
         "cmd": ["gates/library.py"],
         "skip": no_fountain or no_web or no_duck or no_input or no_requests},
        # 0.2.0: the gateway moved into the distribution, a registry can start
        # with no recording at all, and `pip install` is the whole setup.
        # These three hold that shape.
        {"name": "import_graph", "what": "one gateway, two halves, and no import between them",
         "cmd": ["gates/import_graph.py"], "skip": no_web},
        {"name": "fresh_start", "what": "a registry that starts empty: types from the pin, state through a restart",
         "cmd": ["gates/fresh_start.py"],
         "skip": no_fountain or no_web or no_js or no_input or no_duck},
        {"name": "serve_demo", "what": "the packaged demo serves, and the examples do not know the difference",
         "cmd": ["gates/serve_demo.py"],
         "skip": no_fountain or no_web or no_lsof},
        {"name": "builders", "what": "the record builders produce what the hand-authored records say",
         "cmd": ["gates/builders.py"],
         "skip": no_web or no_js or no_input or no_duck or no_requests},
        # The deployment, replicated rather than built: the image's own
        # instructions, carried out in an empty tree. 0.2.0 rewrote the image
        # around `pip install`, which is the change a hand-check cannot
        # survive.
        {"name": "deploy_image", "what": "the image's own instructions serve the endpoint from the new layout",
         "cmd": ["gates/deploy_image.py"],
         "skip": no_web or no_lsof or no_fountain},
        # N.6, and the clause the conformance suite has never taken: its
        # command check POSTs to the first advertised topic by position, so a
        # working command lane sitting third in a manifest leaves the check on
        # its 405 branch. This gate asserts what the suite structurally
        # cannot -- that the advertisement and the behaviour agree.
        {"name": "commands_n6", "what": "the N.6 command surface accepts, declines with a reason, and advertises only what it serves",
         "cmd": ["gates/commands_n6.py"],
         "skip": no_web or no_fountain},
        # The live-backend contract, promoted from the probe that found only
        # half of it was public. The negative is the point: apply_sample
        # alone must reach a new subscriber and never an open socket, or
        # `notify` is not the second half of anything.
        {"name": "live_backend", "what": "apply_sample and notify are two halves, and the write surface is a separate switch",
         "cmd": ["gates/live_backend.py"],
         "skip": no_web or no_fountain},
        # The browser's half of the HTTP contract. Found from outside, by the
        # demo's browser lane: an `Authorization` header forces a preflight
        # this gateway did not route, so N.6 was unreachable from a browser
        # for every access class, and an authorized operator-scoped response
        # named no origin so its own client could not read it.
        {"name": "cors_preflight", "what": "a credentialed cross-origin request is preflighted and its answer names the asking origin",
         "cmd": ["gates/cors_preflight.py"],
         "skip": no_web},
        # Multi-tenancy: tokens scoped to scenes. The lanes gate is the
        # deployed sandbox's shape in process; the client gate is findings 45
        # and 46, the credential and the scene a client must never guess.
        {"name": "openapi", "what": "the OpenAPI document is generated, current, valid, complete, and served",
         "cmd": ["gates/openapi.py"],
         "skip": no_web or no_openapi},
        {"name": "limits", "what": "every limit refuses one past its boundary with a problem document naming it",
         "cmd": ["gates/limits.py"],
         "skip": no_fountain or no_web},
        {"name": "fuzz", "what": "every hostile request is a problem document, never a 500, never a hang",
         "cmd": ["gates/fuzz.py"],
         "skip": no_fountain or no_web or no_js},
        {"name": "security", "what": "names cannot escape, strings cannot inject, refusals are documents, token checks take constant work",
         "cmd": ["gates/security.py"],
         "skip": no_fountain or no_web},
        {"name": "tenancy", "what": "writes are per lane, refused across lanes, and each lane persists and resets alone",
         "cmd": ["gates/tenancy.py"],
         "skip": no_fountain or no_web or no_js},
        {"name": "client_scope", "what": "the client's token goes only to its endpoint, and a publish never guesses a scene",
         "cmd": ["gates/client_scope.py"],
         "skip": no_fountain or no_web or no_requests},
        {"name": "short_topics", "what": "a four-segment topic is served at its well-known path, exactly as a scene-scoped one",
         "cmd": ["gates/short_topics.py"],
         "skip": no_web},
    ]


def main() -> int:
    argparse.ArgumentParser().parse_args()
    env = dict(os.environ,
               PYTHONPATH=os.pathsep.join([str(ROOT), LIB]))
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
