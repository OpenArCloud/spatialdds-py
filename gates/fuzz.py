#!/usr/bin/env python3
"""Schema-seeded fuzzing of the write surface and the resolver.

Valid records are generated from the *published* JSON Schemas (never from this
repository's own builders), then mutated: required members removed, members
retyped, `has_*` guards flipped and their members dropped, JSON truncated,
nesting made absurd, strings made huge, bytes made invalid UTF-8, bodies sent
empty or under the wrong content type, and `spatialdds://` URIs made hostile
at the resolver.

The assertion is the same for every case:

* a body the published schema rejects is answered **4xx with an RFC 7807
  problem document** -- never a 2xx, never a 500;
* a body the schema accepts may be accepted (a mutation can leave a record
  valid), and is otherwise still a problem document;
* every answer arrives within `DEADLINE` seconds;
* the gateway logs **no traceback** for any of it.

0.8.0's world lanes are fuzzed on the same terms: the entity lane and a
deployment's keyed claim type (`gates/_claim_type.py`, schema as the endpoint
publishes it under N.2), with their own cases on top of the mutations:
`?refresh=` made hostile on OBSERVED and DERIVED bodies and offered to
declarations and records, the asserter set to someone else, the body sent into
a scene the token does not grant, and `DELETE ?reason=` made hostile. A
`?refresh=` is accepted only when it is a finite number of seconds in range,
an asserter only when it is empty or the principal's own. The gateway is on a
bus of its own, so what the world lanes accept, and what they withdraw, also
goes through the DDS writer; a bus write that fails fails the gate.

Two passes. The fixed corpus (`gates/fuzz_corpus.json` plus the deterministic
seed) is the gate. The randomized pass draws a fresh seed, reports how many
cases it ran, and on a failure prints the minimized case so it can be added to
the corpus -- `--add` does that.

    gates/fuzz.py [--random N] [--add]
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import random
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gates"))

import aiohttp                                                 # noqa: E402
import jsonschema                                              # noqa: E402

from harness import FOUNTAIN, MANIFESTS, Gateway, report, state  # noqa: E402

import spatialdds as sd                                        # noqa: E402
from spatialdds.server import bus as bus_, gateway, publish, tenancy, webjson  # noqa: E402

from _claim_type import CLAIM_IDL, Claim                       # noqa: E402

AUTH = "demo.spatialdds.org"
SCENE = "ut-austin-south-mall"
CORPUS = ROOT / "gates" / "fuzz_corpus.json"
SEED = 1729
DEADLINE = 5.0
SCHEMAS = webjson.schemas()
PRINCIPAL = "fuzz"
#: A scene the fuzz token is not granted: a write there is another lane's.
OTHER_SCENE = "fuzz-not-my-lane"
EXT = sd.dds.extend("gate.claim", Claim, CLAIM_IDL)
STREAM = {"announce": "discovery", "content_announce": "content",
          "geo_anchor": "frames", "entity": "world", EXT.type_name: "world"}
KEY = {"announce": "service_id", "content_announce": "content_id",
       "geo_anchor": "anchor_id", "entity": "entity_id", EXT.type_name: "id"}
HOSTILE_REFRESH = [
    "", "0", "-1", "0.5", "0.999", "1", "3600", "3600.0001", "3601", "1e3",
    "1e400", "-1e400", "nan", "NaN", "inf", "-inf", "Infinity", "abc", "30s",
    " 30", "0x10", "1_0", "%00", "\u0030", "9" * 5000, "30&refresh=31",
]
HOSTILE_ASSERTER = ["someone-else", "FUZZ", "fuzz ", " fuzz", "fuzz\u0000",
                    "fuzz\n", "A" * 200_000, "operator", "spatialdds-gateway"]
HOSTILE_REASON = ["", "a" * 4000, "\r\nX-Injected: 1", "%00", "%ff%fe",
                  "<script>x</script>", "refresh lapsed", "%E2%80%AE", "?&=#"]


def schema_for(st: gateway.State, fqn: str) -> dict:
    """The schema the endpoint publishes: vendored, or an extension's own."""
    return SCHEMAS.get(fqn) or webjson.schema_from_model(st.model, fqn)


def refresh_ok(v: str) -> bool:
    """What the basis rule must accept: finite seconds in range, and no more."""
    try:
        f = float(v.split("&", 1)[0])
    except ValueError:
        return False
    return publish.REFRESH_MIN_S <= f <= publish.REFRESH_MAX_S


# ---- valid records from the published schemas -------------------------------

def resolve_ref(root: dict, node: dict) -> dict:
    while "$ref" in node:
        node = root["$defs"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def generate(root: dict, node: dict, rng: random.Random, depth: int = 0):
    node = resolve_ref(root, node)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return rng.choice(node["enum"])
    if "oneOf" in node:
        return generate(root, rng.choice(node["oneOf"]), rng, depth + 1)
    t = node.get("type")
    if t == "object":
        return {k: generate(root, v, rng, depth + 1)
                for k, v in node.get("properties", {}).items()}
    if t == "array":
        lo = node.get("minItems", 0)
        hi = node.get("maxItems", max(lo, 2 if depth < 4 else 0))
        n = rng.randint(lo, min(hi, lo + 2))
        return [generate(root, node.get("items", {}), rng, depth + 1)
                for _ in range(n)]
    if t == "string":
        if node.get("pattern") == "^-?\\d+$":
            return str(rng.randint(0, 2**40))
        return rng.choice(["", "x", "fountain", "ü-ñ"])
    if t == "integer":
        return rng.randint(node.get("minimum", 0),
                           min(node.get("maximum", 1000), 1000))
    if t == "number":
        return round(rng.uniform(-90, 90), 6)
    if t == "boolean":
        return rng.choice([True, False])
    return None


def valid_record(seg: str, root: dict, key_name: str,
                 rng: random.Random) -> tuple[dict, str]:
    fqn = root.get("title", seg)
    for _ in range(50):
        rec = generate(root, root, rng)
        key_field = KEY[seg]
        rec[key_field] = key_name
        # A valid entity is a declaration: an observed one with no
        # ?refresh= is correctly refused (0.8.0's basis rule), and the
        # baseline case has to be one the endpoint accepts.
        if "basis" in rec:
            rec["basis"] = "DECLARED"
        # A claim's asserter is stamped from the principal when empty.
        if publish.ASSERTER in rec:
            rec[publish.ASSERTER] = ""
        for k in ("manifest_uri",):
            if k in rec:
                rec[k] = ""
        try:
            jsonschema.validate(rec, root)
            return rec, key_field
        except jsonschema.ValidationError:
            continue
    raise SystemExit(f"could not generate a valid {fqn} from its published "
                     f"schema in 50 tries; that is a finding about the schema")


# ---- mutations ----------------------------------------------------------------

def mutations(rec: dict, root: dict, rng: random.Random, n: int) -> list:
    """(label, body bytes, content-type) cases derived from one valid record."""
    out = []
    req = list(resolve_ref(root, root).get("required", []))
    keys = list(rec)
    for _ in range(n):
        kind = rng.randrange(14)
        r = copy.deepcopy(rec)
        ctype = "application/json"
        if kind == 0 and req:
            k = rng.choice(req); r.pop(k, None); label = f"drop required {k}"
        elif kind == 1:
            k = rng.choice(keys)
            r[k] = rng.choice([None, 7, "s", [], {}, True, 1e308])
            label = f"retype {k}"
        elif kind == 2:
            guards = [k for k in keys if k.startswith("has_")]
            if not guards:
                continue
            g = rng.choice(guards)
            r[g] = rng.choice(["true", 1, None])
            label = f"guard {g} not a boolean"
        elif kind == 3:
            guards = [k for k in keys if k.startswith("has_")
                      and k[4:] in r]
            if not guards:
                continue
            g = rng.choice(guards)
            r[g] = True
            r.pop(g[4:], None)
            label = f"{g} true with its member gone"
        elif kind == 4:
            raw = json.dumps(r).encode()
            out.append((f"truncated at {len(raw) // 2}", raw[:len(raw) // 2],
                        ctype)); continue
        elif kind == 5:
            out.append(("nesting depth 50000",
                        b"[" * 50000 + b"]" * 50000, ctype)); continue
        elif kind == 6:
            k = rng.choice([k for k in keys if isinstance(rec[k], str)] or keys)
            r[k] = "A" * 200_000
            label = f"200 kB string in {k}"
        elif kind == 7:
            raw = json.dumps(r).encode().replace(b'"', b'"\xff\xfe', 1)
            out.append(("invalid UTF-8", raw, ctype)); continue
        elif kind == 8:
            out.append(("empty body", b"", ctype)); continue
        elif kind == 9:
            out.append(("text/plain", json.dumps(r).encode(), "text/plain"))
            continue
        elif kind == 10:
            out.append(("not an object", rng.choice(
                [b"[]", b"null", b"42", b'"x"', b"NaN", b"Infinity"]), ctype))
            continue
        elif kind == 11:
            out.append(("duplicate keys", b'{"a":1,"a":2}', ctype)); continue
        elif kind == 12:
            k = rng.choice(keys)
            r[k] = 10**400
            label = f"huge integer in {k}"
        else:
            r["extra_" + str(rng.randrange(100))] = {"deep": [1, {"x": None}]}
            label = "unknown member"
        out.append((label, json.dumps(r).encode(), ctype))
    return out


HOSTILE_URIS = [
    "", "spatialdds://", "spatialdds:///", "spatialdds://demo.spatialdds.org",
    f"spatialdds://{AUTH}/../../etc/passwd",
    f"spatialdds://{AUTH}/{SCENE}/content/%2e%2e%2f%2e%2e%2fetc",
    f"spatialdds://{AUTH}/{SCENE}/content/x\x00y",
    f"spatialdds://{AUTH}/{SCENE}/content/" + "a" * 20000,
    f"spatialdds://{AUTH}@evil.example/{SCENE}/content/x",
    "spatialdds://evil.example/x/content/y", "http://example.com/",
    "javascript:alert(1)", "spatialdds://ДЕМО.рф/x/y/z",
    f"spatialdds://{AUTH}/{SCENE}/content/\r\nX-Injected: 1",
]


# ---- the run ------------------------------------------------------------------

class Tracebacks(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.seen: list[str] = []

    def emit(self, record):
        if record.exc_info or "Traceback" in record.getMessage():
            self.seen.append(f"{record.name}: {record.getMessage()[:160]}")


def is_problem(h, body: bytes) -> bool:
    if not h.get("Content-Type", "").startswith("application/problem+json"):
        return False
    try:
        d = json.loads(body)
    except ValueError:
        return False
    return isinstance(d, dict) and {"type", "title", "status"} <= set(d)


async def run_cases(s, base: str, tok: str, rng: random.Random,
                    per_type: int, st: gateway.State
                    ) -> tuple[int, list[dict]]:
    failures, n = [], 0
    auth = {"Authorization": f"Bearer {tok}"}
    for seg, fqn in sorted({**publish.PUBLISHABLE, **st.publishable}.items()):
        stream = STREAM[seg]
        key = f"fuzz-{seg}"
        root = schema_for(st, fqn)
        rec, _kf = valid_record(seg, root, key, rng)
        path = f"/{SCENE}/{stream}/{seg}/v1/{quote(key, safe='')}"
        cases = [("valid record", json.dumps(rec).encode(),
                  "application/json")] + mutations(rec, root, rng, per_type)
        for label, body, ctype in cases:
            n += 1
            try:
                doc = json.loads(body)
                schema_ok = jsonschema.Draft202012Validator(root).is_valid(doc)
            except (ValueError, RecursionError, UnicodeDecodeError):
                schema_ok = False
            t0 = time.monotonic()
            try:
                async with s.put(base + path, data=body,
                                 headers={**auth, "Content-Type": ctype},
                                 timeout=aiohttp.ClientTimeout(
                                     total=DEADLINE)) as r:
                    code, h, rb = r.status, r.headers, await r.read()
            except asyncio.TimeoutError:
                failures.append({"target": path, "case": label, "why": "hang"})
                continue
            dt = time.monotonic() - t0
            why = None
            if code >= 500:
                why = f"{code}"
            elif code >= 400 and not is_problem(h, rb):
                why = f"{code} not a problem document"
            elif code < 300 and not schema_ok and ctype == "application/json":
                why = f"{code}: accepted a body the schema rejects"
            elif ctype != "application/json" and code != 415:
                why = f"{code}: a body sent as {ctype} must be a 415"
            elif label == "valid record" and code not in (200, 201):
                why = f"valid record refused {code}: {rb[:160]!r}"
            if why:
                failures.append({"target": path, "case": label, "why": why,
                                 "seconds": round(dt, 3)})
        wn, wf = await world_cases(s, base, auth, rng, st, seg, fqn, rec, path)
        n += wn
        failures += wf
    # The resolver, read and withdraw, with hostile URIs.
    for uri in HOSTILE_URIS + [f"spatialdds://{AUTH}/{SCENE}/content/"
                               + "".join(rng.choice("/%.\\:@?#") for _ in
                                         range(12))]:
        for verb in ("get", "delete"):
            n += 1
            q = "/spatialdds/resolve?uri=" + quote(uri, safe="")
            try:
                async with s.request(verb.upper(), base + q,
                                     headers=auth if verb == "delete" else {},
                                     timeout=aiohttp.ClientTimeout(
                                         total=DEADLINE)) as r:
                    code, h, rb = r.status, r.headers, await r.read()
            except asyncio.TimeoutError:
                failures.append({"target": q[:80], "case": verb, "why": "hang"})
                continue
            if code >= 500 or code < 400 or not is_problem(h, rb):
                failures.append({"target": q[:80], "case": verb,
                                 "why": f"{code} {rb[:100]!r}"})
    return n, failures


async def answer(s, verb: str, url: str, auth: dict, body: bytes | None = None
                 ) -> tuple[int | None, dict, bytes]:
    try:
        async with s.request(verb, url, data=body,
                             headers={**auth, "Content-Type": "application/json"},
                             timeout=aiohttp.ClientTimeout(total=DEADLINE)) as r:
            return r.status, dict(r.headers), await r.read()
    except asyncio.TimeoutError:
        return None, {}, b""


def judge(code, h, rb, may_accept: bool, must_be: tuple = ()) -> str | None:
    """None if the answer is one the write surface may give, else why not."""
    if code is None:
        return "hang"
    if code >= 500:
        return f"{code}: {rb[:120]!r}"
    if code >= 400 and not is_problem(h, rb):
        return f"{code} not a problem document"
    if code < 300 and not may_accept:
        return f"{code}: accepted what the rule refuses"
    if must_be and code not in must_be:
        return f"{code}, expected one of {must_be}: {rb[:120]!r}"
    return None


async def world_cases(s, base: str, auth: dict, rng: random.Random,
                      st: gateway.State, seg: str, fqn: str, rec: dict,
                      path: str) -> tuple[int, list[dict]]:
    """0.8.0's own paths on one lane: refresh, basis, asserter, scene, reason."""
    n, out = 0, []
    world = publish.is_world(st, fqn)

    def fail(case, why):
        out.append({"target": path, "case": case, "why": why})

    body = json.dumps(rec).encode()
    for v in rng.sample(HOSTILE_REFRESH, 8) + ["30"]:
        n += 1
        q = "?refresh=" + (v if "&" in v or "%" in v else quote(v, safe=""))
        if world:
            live = {**rec, "basis": rng.choice(sorted(publish.LIVE_BASES))}
            code, h, rb = await answer(s, "PUT", base + path + q, auth,
                                       json.dumps(live).encode())
            why = judge(code, h, rb, refresh_ok(v),
                        (200, 201) if refresh_ok(v) else ())
            if why:
                fail(f"{live['basis']} ?refresh={v[:40]!r}", why)
        n += 1
        # A declaration, or a record lane, takes no interval at all.
        code, h, rb = await answer(s, "PUT", base + path + q, auth, body)
        why = judge(code, h, rb, False)
        if why:
            fail(f"declaration ?refresh={v[:40]!r}", why)
    if world:
        n += 1
        live = {**rec, "basis": rng.choice(sorted(publish.LIVE_BASES))}
        code, h, rb = await answer(s, "PUT", base + path, auth,
                                   json.dumps(live).encode())
        why = judge(code, h, rb, False, (400,))
        if why:
            fail(f"{live['basis']} with no ?refresh=", why)
    if publish.ASSERTER in rec:
        # Absent is not empty: the published schema requires the member, so
        # the body as sent is refused, never stamped (found by this gate).
        n += 1
        code, h, rb = await answer(
            s, "PUT", base + path, auth, json.dumps(
                {k: v for k, v in rec.items() if k != publish.ASSERTER}).encode())
        why = judge(code, h, rb, False, (400,))
        if why:
            fail("asserter absent", why)
        for a in rng.sample(HOSTILE_ASSERTER, 5) + [PRINCIPAL]:
            n += 1
            code, h, rb = await answer(
                s, "PUT", base + path, auth,
                json.dumps({**rec, publish.ASSERTER: a}).encode())
            why = judge(code, h, rb, a == PRINCIPAL,
                        (200, 201) if a == PRINCIPAL else ())
            if why:
                fail(f"asserter {a[:40]!r}", why)
    # Another lane: the same body into a scene the token does not grant.
    n += 1
    other = path.replace(f"/{SCENE}/", f"/{OTHER_SCENE}/", 1)
    code, h, rb = await answer(s, "PUT", base + other, auth, body)
    why = judge(code, h, rb, False, (403,))
    if why:
        out.append({"target": other, "case": "a scene not granted", "why": why})
    # Withdrawals with hostile reasons, each of a freshly put instance.
    for r in rng.sample(HOSTILE_REASON, 4):
        n += 2
        code, h, rb = await answer(s, "PUT", base + path, auth, body)
        why = judge(code, h, rb, True, (200, 201))
        if why:
            fail("put before a withdrawal", why)
        q = "?reason=" + (r if r.startswith("%") or r == "?&=#"
                          else quote(r, safe=""))
        code, h, rb = await answer(s, "DELETE", base + path + q, auth)
        why = judge(code, h, rb, True, (200, 204))
        if why:
            fail(f"DELETE ?reason={r[:40]!r}", why)
    n += 1
    code, h, rb = await answer(s, "DELETE", base + path + "?reason=gone", auth)
    why = judge(code, h, rb, False, (404,))
    if why:
        fail("DELETE of a withdrawn instance", why)
    return n, out


class BusErrors:
    """The gateway's stderr, kept: `_to_bus` says a failed bus write there."""

    def __init__(self, under):
        self.under, self.seen = under, []

    def write(self, text):
        if "bus write failed" in text:
            self.seen.append(text.strip()[:200])
        return self.under.write(text)

    def flush(self):
        self.under.flush()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=150,
                    help="mutations per type in the randomized pass")
    ap.add_argument("--add", action="store_true",
                    help="add the randomized pass's failing seeds to the corpus")
    a = ap.parse_args()
    corpus = json.loads(CORPUS.read_text()) if CORPUS.is_file() else {
        "seeds": []}
    tb = Tracebacks()
    for name in ("aiohttp.server", "aiohttp.web", "aiohttp.access", ""):
        logging.getLogger(name).addHandler(tb)
    tok = tenancy.issue()
    table = tenancy.TokenTable([(tenancy.digest(tok), tenancy.Principal(
        PRINCIPAL, "openarcloud.org", frozenset({SCENE})))])
    checks: list[tuple[str, bool, str]] = []
    st = state(FOUNTAIN, MANIFESTS)
    gateway.add_extension(st, EXT.type_name, EXT.fqn, EXT.record)
    bus = bus_.Bus(bus_.free_domain())
    errs = BusErrors(sys.stderr)
    sys.stderr = errs
    gw = await Gateway(st, advertise=f"https://{AUTH}", publish=True,
                       tokens=table, bus=bus).start()
    try:
        async with aiohttp.ClientSession() as s:
            total, fails = 0, []
            for seed in [SEED, *corpus["seeds"]]:
                n, f = await run_cases(s, gw.base, tok, random.Random(seed), 60,
                                       st)
                total += n
                fails += [{**x, "seed": seed} for x in f]
            checks.append((f"fixed corpus: {total} cases over seeds "
                           f"{[SEED, *corpus['seeds']]}, every answer a problem "
                           f"document or a valid accept, none slow",
                           not fails, json.dumps(fails[:5])))
            rseed = random.SystemRandom().randrange(2**31)
            n, f = await run_cases(s, gw.base, tok, random.Random(rseed),
                                   a.random, st)
            print(f"  randomized pass: seed {rseed}, {n} cases, "
                  f"{len(f)} failure(s)")
            if f:
                print("  minimized:", json.dumps(f[:3]))
                if a.add:
                    corpus["seeds"].append(rseed)
                    CORPUS.write_text(json.dumps(corpus, indent=2) + "\n")
                    print(f"  seed {rseed} added to {CORPUS.name}")
            checks.append((f"randomized pass: {n} cases (seed {rseed})",
                           not f, json.dumps(f[:3])))
            lanes = sorted({**publish.PUBLISHABLE, **st.publishable})
            checks.append((f"the lanes fuzzed are every publishable one, the "
                           f"world lanes and the extension included: {lanes}",
                           {"entity", EXT.type_name} <= set(lanes)
                           and set(publish.PUBLISHABLE) <= set(lanes),
                           f"{lanes}"))
    finally:
        await gw.stop()
        sys.stderr = errs.under
    checks.append(("no bus write failed for anything the world lanes accepted "
                   "or withdrew", not errs.seen, json.dumps(errs.seen[:3])))
    checks.append(("no traceback in the gateway's log for any case",
                   not tb.seen, json.dumps(tb.seen[:3])))
    return report("fuzz", checks,
                  "every hostile request is a problem document, never a 500, "
                  "never a hang")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
