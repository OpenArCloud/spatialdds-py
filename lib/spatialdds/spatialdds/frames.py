"""Frames, and the one piece of arithmetic that fails silently.

    import spatialdds as sd
    t, q = sd.frames.enu_to_ecef_pose(30.2849, -97.7341, 150.0)
    p_earth = sd.frames.to_earth((t, q), (11.7, -14.3, -1.4))
    lat, lon, h = sd.frames.geodetic_of(p_earth)

Pure Python and the standard library: no extra is needed, so a web client and a
native participant compose frames with the same code. Poses are accepted as
`(t, q)` pairs, as `{"t": …, "q": …}` documents (the N.1 form of `PoseSE3`), or
as anything with `.t` and `.q` (a generated `PoseSE3`). Quaternions are
`(x, y, z, w)`.

**One copy.** Four codebases each carried their own WGS84 projection, with three
different ECEF→geodetic inversions between them (docs/NATIVE-SDK-CENSUS.md,
subject 7). This is spatialdds_ros's `frames.py`, the most complete of the
four, made the only one. A gate holds it numerically equal to the demo's
`plaza.enu_to_ecef_pose` at the venue anchor while both exist.

**The direction, stated once.** A georeference `T_parent_child` is the child
frame's pose *in* its parent: `parent_ref` is earth-fixed, `child_ref` is the
local frame, and

    p_earth = t + R(q) · p_local          and back:  p_local = Rᵀ(q) · (p_earth - t)

so it maps points **from the child frame into the parent**. (spatialdds_ros's
docstring said the reverse while its code did this; filed there.) An inverted
transform still produces a well-formed pose with plausible numbers and puts
everything near the centre of the earth, which is why `compose_point` is the
only composition this module offers and it never guesses.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Mapping, Optional, Sequence, Tuple

__all__ = [
    "WGS84_A", "WGS84_F", "EARTH_FIXED_FQN",
    "frame_uuid", "enu_to_ecef_pose", "geodetic_of",
    "rotate", "to_earth", "from_earth", "frame_problem", "compose_point",
]

#: WGS84, the one ellipsoid every SpatialDDS geodetic quantity is on (§2).
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
#: The name of the earth-fixed frame, as `spatialdds.earth_fixed()` builds it.
EARTH_FIXED_FQN = "earth-fixed"

_E2 = WGS84_F * (2.0 - WGS84_F)


def frame_uuid(fqn: str) -> str:
    """A frame's uuid, derived from its name: UUIDv5 of the fqn.

    Load-bearing rather than tidy: a scene, a catalogue and an announce naming
    the same frame must produce the same uuid, or they describe three frames
    that happen to share a name.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, fqn))


def enu_to_ecef_pose(lat_deg: float, lon_deg: float,
                     height_m: float) -> Tuple[list, list]:
    """A local ENU frame's origin and basis, in earth-fixed metres.

    Returns `(t, q)`: the translation is the origin's ECEF position and the
    quaternion is the ENU basis at that origin, so the pose maps **from** the
    local frame **into** earth-fixed.
    """
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = WGS84_A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
    t = [(n + height_m) * math.cos(lat) * math.cos(lon),
         (n + height_m) * math.cos(lat) * math.sin(lon),
         (n * (1.0 - _E2) + height_m) * math.sin(lat)]

    # The ENU basis at (lat, lon), as columns: east, north, up.
    sl, cl = math.sin(lon), math.cos(lon)
    sp, cp = math.sin(lat), math.cos(lat)
    east = (-sl, cl, 0.0)
    north = (-sp * cl, -sp * sl, cp)
    up = (cp * cl, cp * sl, sp)
    m = ((east[0], north[0], up[0]),
         (east[1], north[1], up[1]),
         (east[2], north[2], up[2]))

    # Matrix to quaternion, branching on the trace for numerical conditioning.
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2][1] - m[1][2]) / s
        y = (m[0][2] - m[2][0]) / s
        z = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w = (m[2][1] - m[1][2]) / s
        x = 0.25 * s
        y = (m[0][1] + m[1][0]) / s
        z = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w = (m[0][2] - m[2][0]) / s
        x = (m[0][1] + m[1][0]) / s
        y = 0.25 * s
        z = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w = (m[1][0] - m[0][1]) / s
        x = (m[0][2] + m[2][0]) / s
        y = (m[1][2] + m[2][1]) / s
        z = 0.25 * s
    return t, [x, y, z, w]


def geodetic_of(t: Sequence[float]) -> Tuple[float, float, float]:
    """ECEF metres back to `(lat, lon, height)`: the inverse of the translation
    above, iterated to convergence well past millimetres (8 rounds).

    Provided so an inverted or mis-scaled transform can be caught by composing
    it with `enu_to_ecef_pose`, not by reading it.
    """
    x, y, z = t[0], t[1], t[2]
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p)
    for _ in range(8):
        n = WGS84_A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1.0 - _E2 * n / (n + h)))
    n = WGS84_A / math.sqrt(1.0 - _E2 * math.sin(lat) ** 2)
    return math.degrees(lat), math.degrees(lon), p / math.cos(lat) - n


def _tq(pose: Any) -> Tuple[Sequence[float], Sequence[float]]:
    if isinstance(pose, Mapping):
        return pose["t"], pose["q"]
    if isinstance(pose, (tuple, list)) and len(pose) == 2:
        return pose[0], pose[1]
    return pose.t, pose.q


def rotate(q: Sequence[float], v: Sequence[float]) -> list:
    """`v` rotated by the unit quaternion `q` = (x, y, z, w)."""
    x, y, z, w = q
    # v' = v + 2w(u×v) + 2u×(u×v), with u = (x, y, z)
    cx = y * v[2] - z * v[1]
    cy = z * v[0] - x * v[2]
    cz = x * v[1] - y * v[0]
    return [v[0] + 2.0 * (w * cx + y * cz - z * cy),
            v[1] + 2.0 * (w * cy + z * cx - x * cz),
            v[2] + 2.0 * (w * cz + x * cy - y * cx)]


def to_earth(pose: Any, point: Sequence[float]) -> list:
    """A point in a georeferenced frame, in earth-fixed metres."""
    t, q = _tq(pose)
    r = rotate(q, point)
    return [t[i] + r[i] for i in range(3)]


def from_earth(pose: Any, point: Sequence[float]) -> list:
    """An earth-fixed point, in the frame `pose` georeferences."""
    t, q = _tq(pose)
    x, y, z, w = q
    d = [point[i] - t[i] for i in range(3)]
    return rotate((-x, -y, -z, w), d)


def _get(ref: Any, name: str, default: Any = None) -> Any:
    return ref.get(name, default) if isinstance(ref, Mapping) \
        else getattr(ref, name, default)


def frame_problem(ref: Any) -> Optional[str]:
    """Why a frame cannot be composed as given, or `None`.

    Two checks, both of which would otherwise place something wrongly with no
    error anywhere: a uuid that is not the one its name derives to means two
    frames share a name; and a frame that is not metric would need the §2.13
    conversion, which this module does not attempt. A declared scale other than
    one metre per unit, and a `SCALE_UNKNOWN` frame, are both refused: §2.13
    forbids treating the second as metric at all.
    """
    fqn, uid = _get(ref, "fqn", ""), _get(ref, "uuid", "")
    if uid and uid != frame_uuid(fqn):
        return (f"frame {fqn!r} carries uuid {uid}, not the "
                f"{frame_uuid(fqn)} its name derives to: two frames sharing "
                f"a name")
    if _get(ref, "has_scale", False):
        status = str(_get(ref, "scale_status", ""))
        if status.endswith("SCALE_UNKNOWN"):
            return (f"frame {fqn!r} declares SCALE_UNKNOWN; §2.13 forbids "
                    f"treating it as metric")
        mpu = float(_get(ref, "meters_per_unit", 1.0))
        if abs(mpu - 1.0) > 1e-9:
            return (f"frame {fqn!r} declares {mpu} m per unit; only metric "
                    f"frames are composed")
    return None


def compose_point(point: Sequence[float], from_fqn: str, to_fqn: str,
                  georeferences: Mapping[str, Any]
                  ) -> Tuple[Optional[list], Optional[str]]:
    """`point`, given in `from_fqn`, expressed in `to_fqn`.

    Returns `(point, None)` or `(None, reason)`, **never a defaulted
    position.** The same frame needs nothing. Two different frames need both
    georeferences (`georeferences` maps a frame fqn to its `T_parent_child`),
    and if either is missing the honest answer is that the point cannot be
    placed.
    """
    if from_fqn == to_fqn:
        return list(point), None
    theirs = georeferences.get(from_fqn)
    mine = georeferences.get(to_fqn)
    if mine is None:
        return None, (f"no georeference for {to_fqn!r}, so nothing in another "
                      f"frame can be composed into it")
    if theirs is None:
        return None, f"no georeference for {from_fqn!r}"
    return from_earth(mine, to_earth(theirs, point)), None
