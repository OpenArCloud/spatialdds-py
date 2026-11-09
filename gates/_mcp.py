"""Shared plumbing for the MCP gates: a venue to read, and a client to read it.

**The venue.** `samples/mcp/venue.json` is SpatialDDS-demo's Littlefield
venue as its OWM binding served it to a reader without a token
(`tools/mcp_venue.py`). `Venue` serves that copy the way the demo's bridge
serves the live one (`bridges/web_bridge/binding.py`): the package's gateway,
embedded, with the world lanes classified public by the embedder, the
command lane operator-scoped, live subscriptions held open, and the two
halves of a live backend, `publish.apply_sample` and `gw_http.notify`, to
change it. Those are the calls the bridge makes when its bus moves, so the
events a watcher hears here are the ones it hears there, a writer's lost
liveliness included. The relationship type is the sandbox's own extension
module (deploy/extensions/sandbox_owm02.py), which needs cyclonedds.

Every request the venue answers is logged with whether it carried a
credential, so a gate can hold the MCP server to reading as a stranger.

**The client.** The official SDK's `mcp.Client`, over stdio to
`python -m spatialdds.mcp_server` from this checkout (or over streamable
HTTP), with every JSON-RPC message logged when a transcript is wanted.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib" / "spatialdds"
EXT = ROOT / "deploy" / "extensions"
VENUE = ROOT / "samples" / "mcp" / "venue.json"
FIXTURES = ROOT / "samples" / "mcp"
for p in (str(LIB), str(EXT)):
    if p not in sys.path:
        sys.path.insert(0, p)

#: The venue's lanes the capture holds no records for, by type segment.
STREAM_FQNS = {"model_pose": "spatial.owm.ModelPose",
               "model_command": "spatial.owm.ModelCommand"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Venue:
    """The captured venue, served live on a loopback port, in this process."""

    def __init__(self, fixture: Path = VENUE) -> None:
        from spatialdds.server import commands, gateway, gw_http, publish
        self.gateway, self.gw_http, self.publish = gateway, gw_http, publish
        self.doc = json.loads(fixture.read_text())
        st = gateway.build_state(None, None)
        importlib.import_module("sandbox_owm02")
        from spatialdds.dds import codec
        for ext in codec.extensions().values():
            gateway.add_extension(st, ext.type_name, ext.fqn, ext.record)
        self.res: Dict[str, Any] = {}
        for lane in self.doc["lanes"]:
            t = lane["topic"]
            _scene, stream, type_seg, version = t["name"].split("/")[1:]
            fqn = lane.get("fqn") or STREAM_FQNS[type_seg]
            agg = st.model.get(fqn)
            key = next(m.name for m in agg.members if m.is_key) \
                if agg else ""
            res = gateway.Resource(_scene, stream, type_seg, version,
                                   t["name"], fqn, key, t["access"],
                                   t["durability"] == "TRANSIENT_LOCAL")
            publish.register_lane(st, res)
            for rec in lane.get("records", []):
                publish.apply_sample(st, res.topic, res, str(rec[key]), rec)
            self.res[type_seg] = res
        st.scenes = sorted({r.scene for r in st.resources.values()})
        self.st = st
        reg = commands.Registry()
        reg.register("spatial.owm.ModelCommand",
                     lambda _r: commands.Outcome.decline(
                         "this is a copy of the venue, and moves nothing"))
        #: (method, path, carried a credential), for every request answered.
        self.requests: List[tuple] = []
        self._reg = reg
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self._ready.wait(30) or self._error:
            raise RuntimeError(f"the venue did not start: {self._error}")

    def _run(self) -> None:
        from aiohttp import web
        from spatialdds.server.limits import HANDLER_ARGS
        asyncio.set_event_loop(self.loop)

        @web.middleware
        async def log(request, handler):
            self.requests.append((request.method, request.path,
                                  "Authorization" in request.headers))
            return await handler(request)

        async def start():
            app = self.gw_http.make_app(
                self.st, "http://127.0.0.1", "venue-operator-not-given-out",
                publish_enabled=False, live_subscriptions=True,
                command_registry=self._reg)
            app.middlewares.insert(0, log)
            self.app = app
            self.runner = web.AppRunner(app, **HANDLER_ARGS)
            await self.runner.setup()
            site = web.TCPSite(self.runner, "127.0.0.1", 0)
            await site.start()
            self.port = site._server.sockets[0].getsockname()[1]
            self.base = f"http://127.0.0.1:{self.port}"
            app["base"] = self.base
        try:
            self.loop.run_until_complete(start())
        except BaseException as e:                          # noqa: BLE001
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self.loop.run_forever()

    def _in_loop(self, fn: Callable[[], Any]) -> Any:
        async def go():
            return fn()
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(10)

    # -- what the venue's bus would do ------------------------------------
    def records(self, type_seg: str) -> Dict[str, dict]:
        res = self.res[type_seg]
        return self._in_loop(lambda: {k: i.data for k, i in
                                      self.st.latched[res.topic].items()})

    def write(self, type_seg: str, data: dict) -> None:
        """A sample from the writer: what the bridge does on a bus sample."""
        res = self.res[type_seg]
        key = str(data[res.key])

        def go():
            self.publish.apply_sample(self.st, res.topic, res, key, data)
            self.gw_http.notify(self.app, res.topic,
                                {"event": "sample", "topic": res.topic,
                                 "key": key, "data": data})
        self._in_loop(go)

    def liveliness(self, type_seg: str, key: str, alive: bool) -> None:
        """A writer's liveliness changed: passed on, the record untouched."""
        res = self.res[type_seg]
        self._in_loop(lambda: self.gw_http.notify(
            self.app, res.topic,
            self.gateway.liveliness(res.topic, key, alive)))

    def withdraw(self, type_seg: str, key: str, reason: str) -> None:
        """A clean withdrawal: a RETIRED terminal sample, then the dispose."""
        res = self.res[type_seg]
        last = dict(self.records(type_seg)[key])
        last.update(state="RETIRED", state_reason=reason)

        def go():
            self.gw_http.notify(self.app, res.topic,
                                {"event": "sample", "topic": res.topic,
                                 "key": key, "data": last})
            self.publish.apply_dispose(self.st, res.topic, key)
            self.gw_http.notify(self.app, res.topic,
                                {"event": "dispose", "topic": res.topic,
                                 "key": key, "reason": reason})
        self._in_loop(go)

    def subscribers(self, type_seg: str) -> int:
        res = self.res[type_seg]
        return self._in_loop(lambda: len(self.app["subs"].get(res.topic,
                                                              ())))

    def stop(self) -> None:
        async def down():
            await self.runner.cleanup()
        try:
            asyncio.run_coroutine_threadsafe(down(), self.loop).result(10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(10)


# ---- the client ------------------------------------------------------------

def server_env() -> Dict[str, str]:
    """The checkout's package, as `pip install` would give it."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(LIB)
    env["PYTHONWARNINGS"] = "ignore"
    return env


def stdio(args: List[str], log: Optional[List[dict]] = None) -> Any:
    """`spatialdds-mcp ARGS` as an MCP client launches it: over stdio.

    `spatialdds-mcp` is `python -m spatialdds.mcp_server` from this checkout,
    the one substitution, so the gate tests the code beside it rather than
    an install.
    """
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "spatialdds.mcp_server",
                                         *args],
                                   env=server_env())
    if log is None:
        return Client(params)
    return Client(_Recorded(params, log))


class _RecRead:
    def __init__(self, inner, log: List[dict]) -> None:
        self.inner, self.log = inner, log

    async def receive(self):
        m = await self.inner.receive()
        _note(self.log, "server", m)
        return m

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


class _RecWrite:
    def __init__(self, inner, log: List[dict]) -> None:
        self.inner, self.log = inner, log

    async def send(self, item) -> None:
        _note(self.log, "client", item)
        await self.inner.send(item)

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


def _note(log: List[dict], who: str, m: Any) -> None:
    msg = getattr(m, "message", None)
    if msg is None:
        log.append({"from": who, "t": round(time.time(), 3),
                    "error": repr(m)})
        return
    log.append({"from": who, "t": round(time.time(), 3),
                "message": msg.model_dump(mode="json", by_alias=True,
                                          exclude_none=True)})


class _Recorded:
    """A transport whose every message, both ways, is logged: stdio when
    given server parameters, streamable HTTP when given a URL."""

    def __init__(self, target, log: List[dict]) -> None:
        self.target, self.log = target, log

    @asynccontextmanager
    async def _open(self):
        if isinstance(self.target, str):
            from mcp.client.streamable_http import streamable_http_client
            opened = streamable_http_client(self.target)
        else:
            from mcp.client.stdio import stdio_client
            opened = stdio_client(self.target)
        async with opened as (read, write):
            yield _RecRead(read, self.log), _RecWrite(write, self.log)

    async def __aenter__(self):
        self._cm = self._open()
        return await self._cm.__aenter__()

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


def http(url: str, log: Optional[List[dict]] = None) -> Any:
    """An MCP client over streamable HTTP, recorded when `log` is given."""
    from mcp import Client
    return Client(url if log is None else _Recorded(url, log))


def text_of(result: Any) -> str:
    """The first text block: the renderings, one line per record."""
    return result.content[0].text if result.content else ""


def doc_of(result: Any) -> dict:
    return result.structured_content or json.loads(result.content[1].text)


class HttpServer:
    """`spatialdds-mcp --http` from this checkout, started and owned here."""

    def __init__(self, args: List[str]) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.log = open(os.devnull, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "spatialdds.mcp_server", "--http",
             f"127.0.0.1:{self.port}", *args],
            env=server_env(), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True)
        self.banner = ""
        deadline = time.time() + 60
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            self.banner += line
            if "streamable HTTP on" in line or self.proc.poll() is not None:
                break
        # Owned by its pid (finding 77): the started process must be the
        # one answering.
        from harness import own_server
        ours, who = own_server(self.proc, self.port, timeout=30)
        if not ours:
            self.stop()
            raise RuntimeError(f"spatialdds-mcp --http is not the one "
                               f"answering ({who}): {self.banner}")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.log.close()


def wait_for(pred: Callable[[], bool], timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.1)
    return pred()


def run(fn: Callable[..., Any], *args: Any) -> Any:
    """Run an async function to completion, as the SDK's own examples do."""
    return asyncio.run(fn(*args))
