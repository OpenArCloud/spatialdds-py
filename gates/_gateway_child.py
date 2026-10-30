"""A gateway on a bus, for gates/entity_publishing.py to kill and stop.

    python3 gates/_gateway_child.py MODE -- SERVE-ARGS...

The real command line (`python -m spatialdds.serve`), in a process of its
own so it can be SIGKILLed. Modes: `serve`, and `autodispose`, the control
arm, which restores the DDS default (autodispose_unregistered_instances
TRUE) on every writer before the gateway starts.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib" / "spatialdds"))


def main() -> int:
    mode, rest = sys.argv[1], sys.argv[sys.argv.index("--") + 1:]
    if mode == "autodispose":
        from cyclonedds import qos as _q
        from spatialdds.dds import _participant, qos
        _participant.writer_qos = lambda p, lifespan_sec=None: _q.Qos(
            *qos._common(p, lifespan_sec))
        print("PLANT autodispose left at the DDS default", flush=True)
    from spatialdds import serve
    return serve.main(rest)


if __name__ == "__main__":
    sys.exit(main())
