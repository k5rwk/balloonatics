#!/usr/bin/env python3
"""
Web spectrum analyzer for ka9q-radio.

Drives the ka9q-radio `powers` utility (which sets up a transient SPECT_DEMOD
channel on a radiod instance and prints rtl_power-style FFT bin energies), parses
each frame, and fans the frames out to browsers over Server-Sent Events. The
browser (index.html) renders a live FFT trace + scrolling waterfall on a canvas.

No third-party Python packages: the HTTP server, SSE transport and powers parser
are all stdlib, so the runtime image only needs python3 + the powers binary.

Configuration is entirely via environment variables (see docker-compose.yml):

  PORT         TCP port to listen on                         (default 8000)
  BIND         address to bind                               (default 0.0.0.0)
  MCAST        radiod status multicast group                 (default rtl-sdr.local)
  SSRC         SSRC for the transient spectrum channel       (default 7438)
  FREQUENCY    center frequency, Hz                          (default 432000000)
  BIN_WIDTH    resolution bandwidth per bin, Hz              (default 1000)
  BINS         number of FFT bins                            (default 2400)
  INTERVAL     seconds between powers polls = display rate   (default 0.025)
  SMOOTHING    IIR time constant tau, seconds (0 disables).   (default 0.2)
               Each bin is exponentially smoothed in linear
               power: ema += alpha*(x-ema), alpha = 1-e^(-dt/tau).
               Decouples integration from update rate -- the
               display refreshes every INTERVAL but carries
               ~tau of integration. Cuts noise like averaging
               ~tau/INTERVAL frames.
  CROSSOVER    rbw threshold (Hz) below which powers uses     (default = span:
               narrowband/two-sided mode; default forces it   BIN_WIDTH*BINS)
               so the full spectrum is shown, not just the
               positive half (see note below)
  SOURCE       optional `powers -o` source name/address      (default unset)
  POWERS_BIN   path to the powers binary                     (default powers)
  POWERS_ARGS  full override of powers args (after the bin)  (default unset)
  REPLAY_FILE  replay a saved powers log instead of running  (default unset)
               powers -- handy for demos with no SDR attached

Note on CROSSOVER: radiod's spectrum pseudo-demod has two implementations and
picks between them by comparing the resolution bandwidth to a "crossover" rbw:
rbw > crossover uses the wideband path (reads the front-end FFT), rbw <= crossover
uses the narrowband path (a complex downconverter). On a complex (I/Q) front end
like the RTL-SDR the wideband path only fills the positive-frequency half of the
span -- the lower half comes back as zeros (a flat line). We default CROSSOVER to
the full span so rbw <= crossover always holds and powers stays on the narrowband
path, giving a correct two-sided spectrum.
"""

import errno
import json
import math
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")

stop_event = threading.Event()


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


class Config:
    def __init__(self):
        self.port = int(env("PORT", "8000"))
        self.bind = env("BIND", "0.0.0.0")
        self.mcast = env("MCAST", "rtl-sdr.local")
        self.ssrc = env("SSRC", "7438")
        self.frequency = env("FREQUENCY", "432000000")
        self.bin_width = env("BIN_WIDTH", "1000")
        self.bins = env("BINS", "2400")
        self.interval = float(env("INTERVAL", "0.025"))
        self.smoothing = max(0.0, float(env("SMOOTHING", "0.2")))   # IIR tau, seconds
        # Default crossover to the full span so powers stays on the narrowband
        # (two-sided) path; see the module docstring.
        self.crossover = env("CROSSOVER", str(int(float(self.bin_width) * int(self.bins))))
        self.source = env("SOURCE")
        self.powers_bin = env("POWERS_BIN", "powers")
        self.powers_args = env("POWERS_ARGS")
        self.replay_file = env("REPLAY_FILE")

    def powers_argv(self):
        """Build the argv for the powers subprocess."""
        if self.powers_args:
            return [self.powers_bin] + shlex.split(self.powers_args)
        argv = [
            self.powers_bin,
            "-s", str(self.ssrc),
            "-f", str(self.frequency),
            "-w", str(self.bin_width),
            "-b", str(self.bins),
            "-i", str(self.interval),
            "-C", str(self.crossover),   # force narrowband/two-sided spectrum
            "-c", "-1",            # run forever
        ]
        if self.source:
            argv += ["-o", self.source]
        argv.append(self.mcast)
        return argv

    def public(self):
        """Config fields the browser may want for labelling."""
        return {
            "mcast": self.mcast,
            "ssrc": self.ssrc,
            "frequency": float(self.frequency),
            "bin_width": float(self.bin_width),
            "bins": int(self.bins),
            "interval": self.interval,
            "smoothing": self.smoothing,
            "replay": bool(self.replay_file),
        }


def parse_line(line):
    """Parse one rtl_power-style line emitted by powers.

    Format: time, start_freq, stop_freq, bin_width, nbins, db0, db1, ...
    Bins are in ascending frequency order, so bin i is at start_freq + i*bin_width.
    Returns a compact dict (short keys keep the SSE payload small) or None.
    """
    parts = line.split(",")
    if len(parts) < 6:
        return None
    try:
        t = parts[0].strip()
        f0 = float(parts[1])
        bw = float(parts[3])
        n = int(parts[4])
        powers = [float(x) for x in parts[5:]]
    except ValueError:
        return None
    if not powers:
        return None
    # Tolerate a count/data mismatch rather than dropping the frame.
    if len(powers) != n:
        n = len(powers)
    return {"t": t, "f0": f0, "bw": bw, "n": n, "p": powers}


class Hub:
    """Fans the latest frame out to all connected SSE subscribers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = set()
        self.latest = None          # last published frame (JSON string)
        self.frames = 0
        self.last_frame_ts = 0.0

    def publish(self, frame):
        data = json.dumps(frame, separators=(",", ":"))
        with self._lock:
            self.latest = data
            self.frames += 1
            self.last_frame_ts = time.time()
            subs = list(self._subs)
        for q in subs:
            self._offer(q, data)

    @staticmethod
    def _offer(q, data):
        # Never block the reader on a slow client: drop the oldest queued frame.
        try:
            q.put_nowait(data)
        except Full:
            try:
                q.get_nowait()
            except Empty:
                pass
            try:
                q.put_nowait(data)
            except Full:
                pass

    def subscribe(self):
        q = Queue(maxsize=4)
        with self._lock:
            self._subs.add(q)
            latest = self.latest
        if latest is not None:
            self._offer(q, latest)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def status(self):
        with self._lock:
            return {
                "subscribers": len(self._subs),
                "frames": self.frames,
                "last_frame_age": (time.time() - self.last_frame_ts) if self.last_frame_ts else None,
            }


class Smoother:
    """Exponential (IIR) integration of the spectrum in LINEAR power.

    Each bin is smoothed as ema += alpha*(x - ema), with alpha derived from a
    time constant tau and the *measured* inter-frame interval, so the amount of
    integration is independent of the (possibly jittery) frame rate. Averaging in
    linear power keeps it unbiased -- averaging dB directly would skew toward the
    nulls. Unlike boxcar averaging this emits every frame, so the display updates
    continuously instead of stepping once per integration window.
    """

    def __init__(self, hub, tau):
        self.hub = hub
        self.tau = max(0.0, float(tau))
        self._ema = None        # smoothed linear power per bin
        self._last = None       # monotonic time of previous frame

    def _alpha(self, dt):
        if self.tau <= 0.0 or dt <= 0.0:
            return 1.0
        return min(1.0, 1.0 - math.exp(-dt / self.tau))

    def add(self, frame):
        if self.tau <= 0.0:
            self.hub.publish(frame)        # smoothing disabled -> passthrough
            return
        p = frame["p"]
        lin = [10.0 ** (db * 0.1) for db in p]   # dB -> linear power
        now = time.monotonic()
        if self._ema is None or len(self._ema) != len(lin):
            self._ema = lin                # first frame / geometry change: seed
        else:
            a = self._alpha(now - self._last)
            ema = self._ema
            for i in range(len(lin)):
                ema[i] += a * (lin[i] - ema[i])
        self._last = now
        out = [round(10.0 * math.log10(v), 2) if v > 0.0 else -200.0
               for v in self._ema]
        frame_out = dict(frame)
        frame_out["p"] = out
        frame_out["tau"] = self.tau
        self.hub.publish(frame_out)


def powers_reader(cfg, sink):
    """Run powers (with restart-on-exit) and publish each parsed frame."""
    argv = cfg.powers_argv()
    sys.stderr.write("spectrum: launching: %s\n" % " ".join(shlex.quote(a) for a in argv))
    sys.stderr.flush()
    while not stop_event.is_set():
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=sys.stderr,
                bufsize=1, text=True,
            )
        except FileNotFoundError:
            sys.stderr.write("spectrum: powers binary not found: %s\n" % cfg.powers_bin)
            sys.stderr.flush()
            stop_event.wait(5)
            continue
        try:
            for line in proc.stdout:
                if stop_event.is_set():
                    break
                frame = parse_line(line)
                if frame:
                    sink.add(frame)
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        if not stop_event.is_set():
            sys.stderr.write("spectrum: powers exited, restarting in 2s\n")
            sys.stderr.flush()
            stop_event.wait(2)


def replay_reader(cfg, sink):
    """Replay a saved powers log on a loop, pacing by INTERVAL. For demos."""
    sys.stderr.write("spectrum: replay mode from %s\n" % cfg.replay_file)
    sys.stderr.flush()
    while not stop_event.is_set():
        try:
            with open(cfg.replay_file) as fh:
                for line in fh:
                    if stop_event.is_set():
                        break
                    frame = parse_line(line)
                    if not frame:
                        continue
                    # Restamp so the waterfall advances in real time.
                    frame["t"] = datetime.now(timezone.utc).isoformat()
                    sink.add(frame)
                    stop_event.wait(cfg.interval)
        except OSError as e:
            sys.stderr.write("spectrum: replay error: %s\n" % e)
            sys.stderr.flush()
            stop_event.wait(2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg = None   # set on the class before serving
    hub = None

    def log_message(self, fmt, *args):
        pass  # quiet; powers/errors go to stderr

    def _send(self, code, ctype, body, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._serve_index()
        elif path == "/stream":
            self._serve_stream()
        elif path == "/config":
            self._send(200, "application/json", json.dumps(self.cfg.public()))
        elif path == "/status":
            self._send(200, "application/json", json.dumps(self.hub.status()))
        elif path in ("/healthz", "/health"):
            self._send(200, "text/plain", "ok\n")
        else:
            self._send(404, "text/plain", "not found\n")

    def _serve_index(self):
        try:
            with open(INDEX_HTML, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, "text/plain", "index.html missing\n")
            return
        self._send(200, "text/html; charset=utf-8", body,
                   {"Cache-Control": "no-cache"})

    def _serve_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = self.hub.subscribe()
        try:
            # Tell the client how we're tuned, before any frames.
            self._sse(f"event: config\ndata: {json.dumps(self.cfg.public())}\n\n")
            while not stop_event.is_set():
                try:
                    data = q.get(timeout=15)
                except Empty:
                    self._sse(": ping\n\n")   # heartbeat keeps the socket alive
                    continue
                self._sse(f"data: {data}\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.hub.unsubscribe(q)

    def _sse(self, text):
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()


def main():
    cfg = Config()
    hub = Hub()

    Handler.cfg = cfg
    Handler.hub = hub
    # Bind before launching powers so a port collision fails fast and clean.
    try:
        httpd = ThreadingHTTPServer((cfg.bind, cfg.port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.stderr.write(
                "spectrum: port %d is already in use on this host -- set PORT to a "
                "free port (host networking binds it directly; `ss -ltnp | grep :%d` "
                "shows the owner)\n" % (cfg.port, cfg.port))
            sys.stderr.flush()
            sys.exit(1)
        raise
    httpd.daemon_threads = True

    sink = Smoother(hub, cfg.smoothing)
    reader = replay_reader if cfg.replay_file else powers_reader
    t = threading.Thread(target=reader, args=(cfg, sink), daemon=True)
    t.start()

    def shutdown(*_):
        stop_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    if cfg.smoothing > 0:
        sys.stderr.write(
            "spectrum: IIR smoothing tau=%.3fs, polling/updating @ %.0f ms (~%.0f frames integrated)\n"
            % (cfg.smoothing, cfg.interval * 1000, cfg.smoothing / cfg.interval))
        sys.stderr.flush()
    sys.stderr.write("spectrum: serving on http://%s:%d/\n" % (cfg.bind, cfg.port))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    finally:
        stop_event.set()
        httpd.server_close()


if __name__ == "__main__":
    main()