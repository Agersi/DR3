"""UDP telemetry listener for DiRT Rally 2.0.

Binds the UDP telemetry port configured for DR3 (``telemetry_port`` in
config.json, default 20999) and records per-stage run trajectories. The game's
``hardware_settings_config.xml`` ``<udp>`` element is normalized so the game
sends to a port DR3 receives: a missing/disabled ``<udp>`` is enabled (at the
DR3 port), an enabled one on 20777 (the SimHub default) is left alone, and any
other enabled port is rewritten to the DR3 port. See read_game_udp() /
ensure_game_udp_for_dr3().
The game emits one 264-byte datagram (~50 Hz) while a stage is live; field [1]
is the stage clock (s) and field [2] is distance-along-stage (m), which is all
that is needed to reproduce the per-segment split times that
``RaceNetChallenges.GetStageSplits`` serves upstream (evenly spaced in distance,
each segment = whatever the car actually took to cover it).

The listener is source-agnostic: it captures every completed start->finish run
the game emits, regardless of context (club / time trial / official). The
dispatcher correlates a just-driven run with a club ``StageComplete`` or a
``TimeTrial.PostTime`` and extracts the splits from it. Raw per-sample fields
are retained on each run so the capture can later grow (throttle/brake/steering,
website telemetry comparison) without reworking the listener.

Field map (264-byte packet = 66 little-endian floats):
    [1] stage clock (s)   [2] distance along stage (m)
    [4][5][6] position xyz (m)   [7] speed (m/s)   [61] track length (m)
"""
from __future__ import annotations

import os
import re
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# UDP field map
# ---------------------------------------------------------------------------

F_STAGE_TIME = 1   # stage clock (s), real-time from pre-stage start
F_DIST = 2         # distance along stage (m)
F_POS_X = 4        # position x (m)
F_POS_Y = 5        # position y (m)
F_POS_Z = 6        # position z (m)
F_SPEED = 7        # speed (m/s)
F_TRACK_LEN = 61   # track length (m)

# Fields retained per sample. Extend here to capture more telemetry later
# (throttle/brake/steering, etc.) — the listener and StageRun are agnostic to
# which fields are stored, only segment_times() reads clock + distance.
TELEMETRY_FIELDS = (F_STAGE_TIME, F_DIST, F_POS_X, F_POS_Y, F_POS_Z, F_SPEED)

_PACKET_SIZE = 264          # 66 * 4 bytes
_NUM_FLOATS = _PACKET_SIZE // 4
_STRUCT_FMT = "<%df" % _NUM_FLOATS

# Run-segmentation thresholds (metres) — match tools/udp_stage_splits.py, which
# was calibrated against real upstream GetStageSplits responses.
START_THRESH = 10.0         # "at the start line" when dist < this
FINISH_THRESH = 10.0        # "finished" when dist > track_len - this

# Keep only the most recent completed runs. A run is claimed by take_*() right
# after its stage closes, so a small ring is enough; it bounds memory if a run
# goes unclaimed (e.g. a DNF that never produces a clean StageComplete).
_MAX_COMPLETED = 6


def _xml_port(text: str, tag: str, require_enabled: bool) -> Optional[int]:
    """Port from the ``<tag ... port="NNNN" />`` element of the game's
    hardware_settings_config.xml; None when absent or unparseable."""
    m = re.search(r"<%s\b[^>]*/?>" % tag, text)
    if not m:
        return None
    el = m.group(0)
    if require_enabled and 'enabled="true"' not in el:
        return None
    p = re.search(r'port="(\d+)"', el)
    if p:
        port = int(p.group(1))
        if 0 < port < 65536:
            return port
    return None


def detect_udp_port(default: int = 20777) -> int:
    """Return the UDP port DR3 should listen on for the game's telemetry.

    Reads the game's hardware_settings_config.xml (best effort). An enabled
    ``<custom_udp>`` stream wins — SimHub keeps the standard ``<udp>`` port
    (20777) and DR3 listens on the custom one; otherwise the standard
    ``<udp>`` port is used. Falls back to ``default`` when the file is missing
    or unparseable.
    """
    try:
        home = os.path.expanduser("~")
        path = os.path.join(
            home, "Documents", "My Games", "DiRT Rally 2.0",
            "hardwaresettings", "hardware_settings_config.xml")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            port = (_xml_port(text, "custom_udp", require_enabled=True)
                    or _xml_port(text, "udp", require_enabled=False))
            if port:
                return port
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# Game hardware config — telemetry UDP enable/normalize for DR3
# ---------------------------------------------------------------------------

# The SimHub default the game ships on. We never rewrite an enabled <udp>
# element that is already on this port (SimHub receives there and forwards to
# DR3's own port).
SIMHUB_UDP_PORT = 20777


def game_config_path() -> str:
    """Absolute path of the game's hardware_settings_config.xml."""
    home = os.path.expanduser("~")
    return os.path.join(
        home, "Documents", "My Games", "DiRT Rally 2.0",
        "hardwaresettings", "hardware_settings_config.xml")


def _xml_elem_attrs(tag: str, text: str) -> Optional[dict]:
    """Attribute dict of the first ``<tag ...>`` element in ``text``; None when
    absent. The leading ``<`` in the match keeps this from ever hitting a
    differently-named element that merely ends in the tag name (e.g.
    ``custom_udp`` vs ``udp``)."""
    m = re.search(r"<%s\b([^>]*?)/?>" % tag, text)
    if not m:
        return None
    attrs = {}
    for name, value in re.findall(r'(\w+)="([^"]*)"', m.group(1)):
        attrs[name] = value
    return attrs


def read_game_udp(path: Optional[str] = None) -> dict:
    """Best-effort description of the game's ``<udp>`` element for the status
    indicator. Returns ``{"present": bool, "enabled": bool, "port": int|None}``;
    present=False / enabled=False / port=None when the file is missing or has no
    ``<udp>`` line."""
    out = {"present": False, "enabled": False, "port": None}
    try:
        p = path or game_config_path()
        if not os.path.isfile(p):
            return out
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return out
    attrs = _xml_elem_attrs("udp", text)
    if attrs is None:
        return out
    out["present"] = True
    out["enabled"] = attrs.get("enabled") == "true"
    port = attrs.get("port", "")
    if port.isdigit():
        n = int(port)
        if 0 < n < 65536:
            out["port"] = n
    return out


def _set_attr_in_elem(text: str, tag: str, attr: str, value: str) -> str:
    """Rewrite ``attr`` to ``value`` on the first ``<tag ...>`` element, or add
    it if absent. Only that attribute changes — the rest of the element (and its
    exact self-closing style) is preserved."""
    m = re.search(r"<%s\b[^>]*>" % tag, text)
    if not m:
        return text
    el = m.group(0)
    am = re.search(r'\b%s="[^"]*"' % attr, el)
    if am:
        new_el = el[:am.start()] + f'{attr}="{value}"' + el[am.end():]
    else:
        close = el.rfind(">")
        slash = el.rfind("/")
        if slash != -1 and slash == close - 1:
            new_el = el[:slash] + f' {attr}="{value}"' + el[slash:]
        else:
            new_el = el[:close] + f' {attr}="{value}"' + el[close:]
    return text[:m.start()] + new_el + text[m.end():]


def ensure_game_udp_for_dr3(target_port: int, path: Optional[str] = None,
                            log=None) -> Optional[str]:
    """Normalize the game's ``<udp>`` element so DR3 (listening on
    ``target_port``) receives telemetry. Silent edit + ``.bak`` backup; writes
    only when a change is needed. Returns a short action description for the log,
    or None when nothing changed / no file / no safe edit.

    Rules:
      * no ``<udp>`` element          -> insert one, enabled, at target_port
      * disabled                      -> set enabled="true" (port kept)
      * enabled and port == 20777     -> leave untouched (SimHub default)
      * enabled and other port        -> rewrite port to target_port
    """
    p = path or game_config_path()
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception:
        return None

    attrs = _xml_elem_attrs("udp", text)
    new_text = None
    action = None

    if attrs is None:
        el = ('<udp enabled="true" extradata="3" ip="127.0.0.1" '
              f'port="{target_port}" delay="1" />')
        m = re.search(r"<motion_platform\b[^>]*>", text)
        if not m:
            return None
        new_text = text[:m.end()] + "\n        " + el + text[m.end():]
        action = f"game config had no <udp> line — added one on :{target_port}"
    else:
        enabled = attrs.get("enabled") == "true"
        port = int(attrs["port"]) if attrs.get("port", "").isdigit() else None
        if not enabled:
            new_text = _set_attr_in_elem(text, "udp", "enabled", "true")
            action = f"game UDP was disabled — enabled it on :{port or '?'}"
        elif port is not None and port != SIMHUB_UDP_PORT and port != target_port:
            new_text = _set_attr_in_elem(text, "udp", "port", str(target_port))
            action = (f"game UDP was on :{port} — set it to DR3's port "
                      f":{target_port}")

    if new_text is None or new_text == text:
        return None
    try:
        with open(p + ".bak", "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    except Exception as exc:
        if log:
            log(f"[TEL] could not update game UDP config: {exc}")
        return None
    if log:
        log(f"[TEL] {action} (backup written to ...{os.path.basename(p)}.bak)")
    return action


@dataclass
class StageRun:
    """One completed start->finish drive of a single stage."""

    start_wall: float            # time.time() when the run began (car at start)
    finish_wall: float           # time.time() when the car crossed the line
    total_time_s: float          # stage clock at the finish crossing
    track_length_m: float        # track length used for the split points
    samples: List[Tuple[float, ...]] = field(default_factory=list)

    def segment_times(self, n: int) -> Optional[List[float]]:
        """Per-segment times (seconds) for ``n`` evenly-spaced (by distance)
        split points. Segments sum to the run's stage-clock span; the first
        boundary is anchored at clock=0 so the opening segment absorbs any
        pre-stage delay (matching the upstream wire invariant that the splits
        sum to TotalTime). Returns None when there are not enough samples to
        resolve every split point.
        """
        if n <= 0 or len(self.samples) < 2:
            return None
        length = self.track_length_m
        if length <= 0:
            return None
        try:
            ic = TELEMETRY_FIELDS.index(F_STAGE_TIME)
            idist = TELEMETRY_FIELDS.index(F_DIST)
        except ValueError:
            return None

        # (distance, stage_clock) pairs in sample order.
        run = [(s[idist], s[ic]) for s in self.samples]

        def crossing(target_dist: float) -> float:
            """Stage clock when the car first reaches target_dist (linear
            interpolation between the two samples straddling it)."""
            for i in range(1, len(run)):
                d0, c0 = run[i - 1]
                d1, c1 = run[i]
                if d1 >= target_dist >= d0 or (i == 1 and d0 >= target_dist):
                    if d1 == d0:
                        return c1
                    f = max(0.0, min(1.0, (target_dist - d0) / (d1 - d0)))
                    return c0 + (c1 - c0) * f
            return run[-1][1]

        bounds = [length * i / n for i in range(n + 1)]
        cross = [crossing(b) for b in bounds]
        cross[0] = 0.0  # official stage-clock start (includes pre-stage in seg 1)
        segs = [cross[i + 1] - cross[i] for i in range(n)]
        if any(t < 0 for t in segs):
            return None
        return segs

    def has_wrap(self, threshold: float = 25.0) -> bool:
        """True when distance jumps backwards by more than ``threshold`` metres
        between consecutive samples — the signature of a multi-lap circuit whose
        distance counter resets at the line (a rally stage drives one monotonic
        pass and never does this). Wrapped runs can't be resolved into per-lap
        split points, so they're rejected for split capture."""
        try:
            idist = TELEMETRY_FIELDS.index(F_DIST)
        except ValueError:
            return False
        prev: Optional[float] = None
        for s in self.samples:
            d = s[idist]
            if prev is not None and (prev - d) > threshold:
                return True
            prev = d
        return False

    def max_distance(self) -> float:
        """The greatest distance-along-stage value recorded in the run (0.0 when
        empty). A single monotonic pass tops out near track_length_m; a multi-lap
        circuit driven with a cumulative counter goes far beyond it."""
        try:
            idist = TELEMETRY_FIELDS.index(F_DIST)
        except ValueError:
            return 0.0
        m = 0.0
        for s in self.samples:
            if s[idist] > m:
                m = s[idist]
        return m


def scale_segments_to_total(segs: List[float], total_s: float) -> Optional[List[float]]:
    """Scale measured per-segment times (seconds) so they sum exactly to
    ``total_s`` — the upstream wire invariant that Splits sum ~= TotalTime. The
    run's own stage clock can differ slightly from the submitted StageTime, so
    proportional scaling keeps the segments consistent with the official total.
    Returns None on bad input (empty, non-positive total, or a negative result).
    """
    if not segs or total_s <= 0:
        return None
    raw = sum(segs)
    if raw <= 0:
        return None
    scale = total_s / raw
    out = [s * scale for s in segs]
    rounded = [round(t, 3) for t in out]
    # Absorb both float drift and per-segment rounding into the final segment so
    # the stored list sums exactly to total_s (the wire invariant).
    rounded[-1] = round(total_s - sum(rounded[:-1]), 3)
    if any(t < 0 for t in rounded):
        return None
    return rounded


class TelemetryListener:
    """Receives the game's UDP telemetry and segments it into completed runs.

    Start it once the server is up (it binds its own port on a daemon thread)
    and stop it on shutdown. ``take_run_since`` / ``take_latest_completed``
    claim individual runs for the dispatcher to turn into splits.
    """

    def __init__(self, port: int = 20777, logger: Optional[Callable[[str], None]] = None):
        self.port = int(port)
        self._log = logger or (lambda m: None)
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._completed: List[StageRun] = []
        # Per-run accumulation state (touched only from the receive thread).
        self._buffer: Optional[List[Tuple[float, ...]]] = None
        self._run_start_wall = 0.0
        self._prev_at_start = False
        self._prev_at_finish = False
        self._track_len = 0.0
        self.packets_seen = 0
        # time.time() of the last valid packet (0.0 until the first arrives) —
        # lets the GUI tell "connected" (fresh data) from "listening, silent".
        self.last_packet_wall = 0.0
        # Distinct non-standard packet sizes seen (logged once each) — surfaces
        # a game stream on this port that doesn't match the expected layout.
        self._odd_sizes: dict = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Bind the port and start the receive thread. Returns False (and logs)
        if the port cannot be bound — telemetry is then simply unavailable."""
        if self.running:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.port))
            sock.settimeout(0.5)
        except OSError as exc:
            self._log(
                f"[TELEMETRY] cannot bind UDP :{self.port} ({exc}); telemetry "
                f"disabled — splits will use the even profile. If another tool "
                f"(e.g. SimHub) owns this port, forward its UDP output to a free "
                f'port and set "telemetry_port" in config.json to that port.')
            return False
        self._sock = sock
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="dr3-telemetry", daemon=True)
        self._thread.start()
        self._log(f"[TELEMETRY] listening on UDP :{self.port}")
        return True

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        s = self._sock
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        self._thread = None
        self._sock = None

    # ------------------------------------------------------------------
    # Receive loop + run segmentation
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            try:
                data, _addr = sock.recvfrom(_PACKET_SIZE + 256)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) != _PACKET_SIZE:
                count = self._odd_sizes.get(len(data), 0) + 1
                self._odd_sizes[len(data)] = count
                if count == 1:
                    self._log(
                        f"[TELEMETRY] packet of unexpected size {len(data)} bytes "
                        f"from {_addr[0]}:{_addr[1]} (expected {_PACKET_SIZE})")
            if len(data) < _PACKET_SIZE:
                continue
            try:
                vals = struct.unpack(_STRUCT_FMT, data[:_PACKET_SIZE])
            except struct.error:
                continue
            self.packets_seen += 1
            self.last_packet_wall = time.time()
            if self.packets_seen == 1:
                self._log(f"[TELEMETRY] first packet received from {_addr[0]}:{_addr[1]}")
            self._on_sample(time.time(), vals)

    def _on_sample(self, wall: float, vals: Tuple[float, ...]) -> None:
        # Track length is constant per stage; the latest valid value wins.
        tl = vals[F_TRACK_LEN]
        if 100.0 < tl < 200000.0:
            self._track_len = tl

        dist = vals[F_DIST]
        clock = vals[F_STAGE_TIME]
        sample = tuple(vals[i] for i in TELEMETRY_FIELDS)
        length = self._track_len

        at_start = dist < START_THRESH
        at_finish = (length > 0.0) and (dist > length - FINISH_THRESH)

        # Edge detection (mirrors tools/udp_stage_splits.find_runs): a run
        # begins on the transition INTO the start zone and ends on the
        # subsequent transition INTO the finish zone, so an idle-at-start or
        # post-finish dwell never spawns phantom runs.
        if at_start and not self._prev_at_start:
            self._buffer = [sample]
            self._run_start_wall = wall
        elif self._buffer is not None:
            self._buffer.append(sample)

        if (at_finish and not self._prev_at_finish and self._buffer is not None):
            run = StageRun(
                start_wall=self._run_start_wall,
                finish_wall=wall,
                total_time_s=clock,
                track_length_m=length,
                samples=list(self._buffer),
            )
            self._push_completed(run)
            self._buffer = None

        self._prev_at_start = at_start
        self._prev_at_finish = at_finish

    def _push_completed(self, run: StageRun) -> None:
        with self._lock:
            self._completed.append(run)
            if len(self._completed) > _MAX_COMPLETED:
                self._completed = self._completed[-_MAX_COMPLETED:]

    # ------------------------------------------------------------------
    # Run claiming (called from the dispatcher thread)
    # ------------------------------------------------------------------

    def take_run_since(self, since_wall: float, margin: float = 1.0) -> Optional[StageRun]:
        """Claim and return the most recent completed run that FINISHED at or
        after ``since_wall`` (a small margin absorbs clock skew). None when no
        such run exists. The run is removed so it cannot be claimed twice.

        Matching on the finish — not the start — is what makes club rally
        stages work: a car idles in the start zone (dist < START_THRESH) while
        parked in the service area, which opens its telemetry buffer well before
        StageBegin arms ``since_wall``. So the current stage's run *starts*
        before the wall but *finishes* after it. A stale run from an earlier
        stage always finishes before the wall (stages are sequential), so
        finish-based matching still excludes it."""
        cutoff = since_wall - margin
        with self._lock:
            for i in range(len(self._completed) - 1, -1, -1):
                if self._completed[i].finish_wall >= cutoff:
                    return self._completed.pop(i)
        return None

    def take_latest_completed(self) -> Optional[StageRun]:
        """Claim and return the most recent completed run (any start time)."""
        with self._lock:
            if not self._completed:
                return None
            return self._completed.pop()
