#!/usr/bin/env python3
"""
Web spectrum analyzer for ka9q-radio.

Drives the ka9q-radio `powers` utility (which sets up a transient SPECT_DEMOD
channel on a radiod instance and prints rtl_power-style FFT bin energies), parses
each frame, and fans the *raw* frames out to browsers over Server-Sent Events. The
browser (index.html) does the IIR smoothing, renders a live FFT trace + scrolling
waterfall, supports drop-on markers (point + PSD/bandwidth), and can drive the
optional spectrum logger here.

No third-party Python packages: the HTTP server, SSE transport and powers parser
are all stdlib, so the runtime image only needs python3 + the powers binary.

Configuration is entirely via environment variables (see docker-compose.yml):

  PORT         TCP port to listen on                         (default 8000)
  BIND         address to bind                               (default 0.0.0.0)
  RADIOD_CONF  radiod.conf to derive tuning from             (default
                                                              /etc/radio/radiod.conf)
  MCAST        radiod status multicast group                 (default: [global]
                                                              status, else rtl-sdr.local)
  SSRC         SSRC for the transient spectrum channel       (default 7438)
  FREQUENCY    center frequency, Hz                          (default: [sdr]
                                                              frequency, else 432000000)
  BIN_WIDTH    resolution bandwidth per bin, Hz              (default 1000)
  BINS         number of FFT bins                            (default: [sdr]
                                                              samprate/BIN_WIDTH, else 2400)
  INTERVAL     seconds between powers polls = display rate   (default 0.1)
               NOTE: also sets radiod's channel lifetime --
               see the note below before lowering it.
  AVERAGE      FFTs radiod averages into each frame (-a)     (default 8)

Tuning (center frequency, span, status group) is read from radiod.conf so it is
not duplicated in compose; any of MCAST/FREQUENCY/BINS still overrides if set.
  SMOOTHING    DEFAULT IIR time constant tau (s) for the      (default 0.2)
               browser's client-side smoothing (0 = off).
               Just a starting value for the UI control now;
               the smoothing itself runs in the browser.
  CROSSOVER    rbw threshold (Hz) at/below which powers uses  (default unset ->
               the narrowband path instead of the cheaper      powers' own 200 Hz)
               wideband one; unset defers to powers
               (see note below)
  LOG_DIR      directory for spectrum CSV logs               (default /data)
  LOG_INTERVAL if >0, auto-start logging at this period (s)  (default 0)
  SOURCE       optional `powers -o` source name/address      (default unset)
  POWERS_BIN   path to the powers binary                     (default powers)
  POWERS_ARGS  full override of powers args (after the bin)  (default unset)
  REPLAY_FILE  replay a saved powers log instead of running  (default unset)
               powers -- also how you review a logged flight

Logs are written in the exact rtl_power/powers CSV format, so a log can be played
back through the same UI with REPLAY_FILE=<that file>.

Note on INTERVAL: powers derives radiod's channel self-destruct timer from it,
sending LIFETIME = INTERVAL * 2 * 50 *frames* of 20 ms each -- i.e. a lifetime of
just 2*INTERVAL seconds, truncated to whole frames. If a poll ever slips past
that, radiod tears the transient spectrum channel down (filter output, FFTW plan,
ring buffer, window) and the next poll rebuilds all of it; the rebuilt channel
also answers the first poll with no BIN_DATA, so powers spins re-sending setup
commands until it settles. At the old INTERVAL of 0.025 that budget was
0.025*2*50 = 2.5 -> 2 frames = 40 ms against a ~25 ms poll gap, which made the
display glitch and burned CPU. 0.1 gives 10 frames = 200 ms, ~2x the poll gap.
Don't drop INTERVAL below ~0.05 without re-checking this. Integration is better
bought with AVERAGE (below) than with a faster poll.

Note on AVERAGE: newer powers exposes radiod's SPECTRUM_AVG as -a, so radiod
averages N FFTs from its ring buffer into each response. This is strictly cheaper
than polling N times as fast, and it is why INTERVAL can be 4x slower than it was
without losing noise performance. The browser's IIR (SMOOTHING) still runs on top
and paces off the *measured* frame interval, so it adapts to the slower rate on
its own. Keep AVERAGE modest: powers splits the request into `pieces` when
average/rbw exceeds 80 ms and its normalization of that split is wrong (it scales
by 1/pieces using an integer divide while still asking radiod for the full
average), so multi-piece results come back several dB high.

Note on CROSSOVER: radiod's spectrum pseudo-demod has two implementations and
picks between them by comparing the resolution bandwidth to a "crossover" rbw:
rbw > crossover uses the wideband path (an FFT straight off the raw A/D input
buffer), rbw <= crossover uses the narrowband path (a full complex downconverter
at bin_count*rbw, run per poll). Wideband is much the cheaper of the two.

We used to force CROSSOVER to the full span to stay on the narrowband path,
because on a complex (I/Q) front end like the RTL-SDR the wideband path filled
only the positive-frequency half of the span -- the lower half came back as zeros,
a flat line below center. That bin-mapping bug is fixed as of the ka9q-radio ref
this image pins, so CROSSOVER is now left unset and powers' own default (200 Hz)
applies: at our BIN_WIDTH of 1000 that selects wideband, while asking for fine
resolution (BIN_WIDTH <= 200) still drops to narrowband, which is the right path
for a narrow span. Set CROSSOVER explicitly to override -- CROSSOVER =
BIN_WIDTH*BINS restores the old always-narrowband behaviour.
"""

import errno
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")

stop_event = threading.Event()

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def safe_log_name(name):
    """Reject path traversal / odd characters in a log filename."""
    name = os.path.basename(name or "")
    return name if name and _NAME_RE.match(name) else None


def _parse_hz(s):
    """Parse a ka9q frequency value: plain Hz, or with a k/m/g suffix."""
    s = str(s).strip().lower()
    mult = 1.0
    if s and s[-1] in "kmg":
        mult = {"k": 1e3, "m": 1e6, "g": 1e9}[s[-1]]
        s = s[:-1]
    return float(s) * mult


def read_radiod_conf(path):
    """Derive tuning from radiod.conf so it isn't duplicated in compose.

    Returns {status, frequency, samprate} (any may be missing) or {} on failure.
    Follows ka9q semantics: [global] hardware names the device section that holds
    `frequency` and `samprate`; [global] status is the metadata multicast group.
    """
    import configparser
    cp = configparser.ConfigParser(
        strict=False, interpolation=None,
        inline_comment_prefixes=("#", ";"))
    try:
        with open(path) as fh:
            cp.read_file(fh)
    except (OSError, configparser.Error):
        return {}
    out = {}
    try:
        status = cp.get("global", "status", fallback=None)
        if status:
            out["status"] = status.strip()
        hw = cp.get("global", "hardware", fallback="sdr").strip()
        if cp.has_section(hw):
            sec = cp[hw]
            if sec.get("frequency"):
                out["frequency"] = _parse_hz(sec.get("frequency"))
            if sec.get("samprate"):
                out["samprate"] = _parse_hz(sec.get("samprate"))
    except (configparser.Error, ValueError):
        pass
    return out


class Config:
    def __init__(self):
        self.port = int(env("PORT", "8000"))
        self.bind = env("BIND", "0.0.0.0")
        self.ssrc = env("SSRC", "7438")
        self.bin_width = env("BIN_WIDTH", "1000")
        self.interval = float(env("INTERVAL", "0.1"))
        # radiod-side integration (powers -a). See the AVERAGE note in the module
        # docstring for why this is preferred over a shorter INTERVAL.
        self.average = max(1, int(float(env("AVERAGE", "8"))))

        # Tuning is derived from radiod.conf (single source of truth) unless an
        # env var overrides it -- so the SDR center/width never has to be copied
        # into compose. Precedence: env > radiod.conf > built-in default.
        self.radiod_conf = env("RADIOD_CONF", "/etc/radio/radiod.conf")
        conf = read_radiod_conf(self.radiod_conf)
        self.tuned_from = "radiod.conf" if conf else "defaults"

        self.mcast = env("MCAST") or conf.get("status") or "rtl-sdr.local"
        if env("FREQUENCY"):
            self.frequency = env("FREQUENCY")
        elif conf.get("frequency"):
            self.frequency = str(int(conf["frequency"]))
        else:
            self.frequency = "432000000"
        if env("BINS"):
            self.bins = env("BINS")
        elif conf.get("samprate"):
            self.bins = str(int(round(conf["samprate"] / float(self.bin_width))))
        else:
            self.bins = "2400"
        self.smoothing = max(0.0, float(env("SMOOTHING", "0.2")))   # UI default tau
        # Unset by default: defer to powers' own crossover (200 Hz), which picks
        # the cheap wideband path at our BIN_WIDTH and the narrowband path if you
        # ask for fine resolution. See the module docstring.
        self.crossover = env("CROSSOVER")
        self.log_dir = env("LOG_DIR", "/data")
        self.log_interval = float(env("LOG_INTERVAL", "0"))
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
            "-a", str(self.average),     # radiod-side averaging (SPECTRUM_AVG)
            "-c", "-1",            # run forever
        ]
        if self.crossover:
            argv += ["-C", str(self.crossover)]
        if self.source:
            argv += ["-o", self.source]
        argv.append(self.mcast)
        return argv

    POWERS_DEFAULT_CROSSOVER = 200.0   # powers' own default when -C is omitted

    def spectrum_path(self):
        """Which radiod implementation this config selects: rbw > crossover picks
        the wideband path (an FFT straight off the raw A/D), rbw <= crossover the
        narrowband one (a full complex downconverter per poll)."""
        xover = float(self.crossover) if self.crossover else self.POWERS_DEFAULT_CROSSOVER
        return "wideband" if float(self.bin_width) > xover else "narrowband"

    def public(self):
        """Config fields the browser may want for labelling/init."""
        return {
            "mcast": self.mcast,
            "ssrc": self.ssrc,
            "frequency": float(self.frequency),
            "bin_width": float(self.bin_width),
            "bins": int(self.bins),
            "interval": self.interval,
            "average": self.average,
            "path": self.spectrum_path(),
            "smoothing": self.smoothing,
            "replay": bool(self.replay_file),
            "logging": True,
            "tuned_from": self.tuned_from,
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


def frame_to_csv(frame):
    """Render a frame back to the rtl_power/powers CSV line format (replayable)."""
    f0, bw, n, p = frame["f0"], frame["bw"], frame["n"], frame["p"]
    stop = f0 + bw * (n - 1)
    return "%s, %.0f, %.0f, %.0f, %d, %s" % (
        frame["t"], f0, stop, bw, n, ", ".join("%.2f" % v for v in p))


class Hub:
    """Fans the latest frame out to all connected SSE subscribers."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs = set()
        self.latest = None          # last published frame (JSON string)
        self.latest_frame = None    # last published frame (dict, for the logger)
        self.frames = 0
        self.last_frame_ts = 0.0

    def publish(self, frame):
        data = json.dumps(frame, separators=(",", ":"))
        with self._lock:
            self.latest = data
            self.latest_frame = frame
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


class Logger:
    """Append the latest raw spectrum frame to a CSV file every `interval`
    seconds, so a flight can be replayed/plotted afterwards. The CSV is the same
    format powers emits, so REPLAY_FILE can play it straight back.
    """

    def __init__(self, hub, log_dir):
        self.hub = hub
        self.dir = log_dir
        self._lock = threading.Lock()
        self._stop = None
        self._thread = None
        self.active = False
        self.interval = 0.0
        self.name = None
        self.path = None
        self.lines = 0
        self.error = None

    def start(self, interval, name=None):
        self.stop()
        try:
            interval = max(0.05, float(interval))
        except (TypeError, ValueError):
            interval = 1.0
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as e:
            with self._lock:
                self.error = str(e)
            return self.status()
        name = safe_log_name(name) if name else None
        if not name:
            name = "spectrum-%s.csv" % datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if not name.endswith(".csv"):
            name += ".csv"
        ev = threading.Event()
        with self._lock:
            self.name = name
            self.path = os.path.join(self.dir, name)
            self.interval = interval
            self.lines = 0
            self.error = None
            self.active = True
            self._stop = ev
            self._thread = threading.Thread(
                target=self._run, args=(self.path, interval, ev), daemon=True)
            self._thread.start()
        sys.stderr.write("spectrum: logging every %.2fs -> %s\n" % (interval, self.path))
        sys.stderr.flush()
        return self.status()

    def _run(self, path, interval, ev):
        last_t = None
        try:
            with open(path, "a", buffering=1) as fh:
                nxt = time.monotonic()
                while not ev.is_set() and not stop_event.is_set():
                    frame = self.hub.latest_frame
                    if frame is not None and frame.get("t") != last_t:
                        fh.write(frame_to_csv(frame) + "\n")
                        last_t = frame.get("t")
                        with self._lock:
                            self.lines += 1
                    nxt += interval
                    delay = nxt - time.monotonic()
                    if delay < 0:
                        nxt = time.monotonic()
                        delay = interval
                    ev.wait(delay)
        except OSError as e:
            with self._lock:
                self.error = str(e)
            sys.stderr.write("spectrum: log write error: %s\n" % e)
            sys.stderr.flush()
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self.active = False

    def stop(self):
        with self._lock:
            ev = self._stop
            self._stop = None
            self.active = False
        if ev:
            ev.set()
        return self.status()

    def status(self):
        with self._lock:
            return {
                "active": self.active,
                "interval": self.interval,
                "file": self.name,
                "lines": self.lines,
                "dir": self.dir,
                "error": self.error,
            }

    def list_logs(self):
        out = []
        try:
            for n in sorted(os.listdir(self.dir), reverse=True):
                if not n.endswith(".csv"):
                    continue
                try:
                    st = os.stat(os.path.join(self.dir, n))
                except OSError:
                    continue
                out.append({"name": n, "size": st.st_size, "mtime": int(st.st_mtime)})
        except OSError:
            pass
        return out


def powers_reader(cfg, hub):
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
                    hub.publish(frame)
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


def replay_reader(cfg, hub):
    """Replay a saved powers log on a loop, pacing by INTERVAL. For review/demos."""
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
                    hub.publish(frame)
                    stop_event.wait(cfg.interval)
        except OSError as e:
            sys.stderr.write("spectrum: replay error: %s\n" % e)
            sys.stderr.flush()
            stop_event.wait(2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    cfg = None     # set on the class before serving
    hub = None
    logger = None

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

    def _json(self, obj, code=200):
        self._send(code, "application/json", json.dumps(obj))

    def _params(self):
        """Merge query-string and (urlencoded or JSON) body params."""
        params = {}
        for k, v in parse_qs(urlparse(self.path).query).items():
            params[k] = v[0]
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length).decode("utf-8", "replace")
            ctype = self.headers.get("Content-Type", "")
            if "application/json" in ctype:
                try:
                    d = json.loads(body)
                    if isinstance(d, dict):
                        params.update({k: str(v) for k, v in d.items()})
                except ValueError:
                    pass
            else:
                for k, v in parse_qs(body).items():
                    params[k] = v[0]
        return params

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._serve_index()
        elif path == "/stream":
            self._serve_stream()
        elif path == "/config":
            self._json(self.cfg.public())
        elif path == "/status":
            self._json({**self.hub.status(), "log": self.logger.status()})
        elif path == "/log/status":
            self._json(self.logger.status())
        elif path == "/logs":
            self._json(self.logger.list_logs())
        elif path.startswith("/logs/"):
            self._serve_log_file(path[len("/logs/"):])
        elif path in ("/healthz", "/health"):
            self._send(200, "text/plain", "ok\n")
        else:
            self._send(404, "text/plain", "not found\n")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/log/start":
            p = self._params()
            interval = p.get("interval", self.cfg.log_interval or 1)
            self._json(self.logger.start(interval, p.get("name")))
        elif path == "/log/stop":
            self._json(self.logger.stop())
        else:
            self._send(404, "text/plain", "not found\n")

    def _serve_index(self):
        try:
            with open(INDEX_HTML, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, "text/plain", "index.html missing\n")
            return
        self._send(200, "text/html; charset=utf-8", body, {"Cache-Control": "no-cache"})

    def _serve_log_file(self, name):
        safe = safe_log_name(name)
        if not safe:
            self._send(400, "text/plain", "bad name\n")
            return
        base = os.path.realpath(self.logger.dir)
        rp = os.path.realpath(os.path.join(base, safe))
        if os.path.dirname(rp) != base:
            self._send(403, "text/plain", "forbidden\n")
            return
        try:
            with open(rp, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(404, "text/plain", "not found\n")
            return
        self._send(200, "text/csv", body,
                   {"Content-Disposition": 'attachment; filename="%s"' % safe})

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
    logger = Logger(hub, cfg.log_dir)

    Handler.cfg = cfg
    Handler.hub = hub
    Handler.logger = logger
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

    reader = replay_reader if cfg.replay_file else powers_reader
    t = threading.Thread(target=reader, args=(cfg, hub), daemon=True)
    t.start()

    if cfg.log_interval > 0:
        logger.start(cfg.log_interval)

    def shutdown(*_):
        stop_event.set()
        logger.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    span_mhz = float(cfg.bin_width) * int(cfg.bins) / 1e6
    sys.stderr.write(
        "spectrum: tuning from %s: center %.3f MHz, %.2f MHz span, %s bins @ %s Hz, status=%s\n"
        % (cfg.tuned_from, float(cfg.frequency) / 1e6, span_mhz, cfg.bins,
           cfg.bin_width, cfg.mcast))
    # powers sends radiod LIFETIME = interval*2*50 frames of 20 ms (see docstring).
    lifetime_frames = int(cfg.interval * 2 * 50)
    sys.stderr.write(
        "spectrum: polling @ %.0f ms, radiod averaging %d FFTs/frame on the %s "
        "path, channel lifetime %d frames (%.0f ms); client smoothing default "
        "tau=%.3fs\n"
        % (cfg.interval * 1000, cfg.average, cfg.spectrum_path(), lifetime_frames,
           lifetime_frames * 20, cfg.smoothing))
    if lifetime_frames < 4:
        sys.stderr.write(
            "spectrum: WARNING: INTERVAL=%.3f leaves radiod only %d frame(s) of "
            "channel lifetime; the spectrum channel will be torn down and rebuilt "
            "whenever a poll slips, causing glitchy output and high CPU. Raise "
            "INTERVAL to >= 0.05 (0.1 recommended) and buy integration with "
            "AVERAGE instead.\n" % (cfg.interval, lifetime_frames))
    sys.stderr.write("spectrum: serving on http://%s:%d/\n" % (cfg.bind, cfg.port))
    sys.stderr.flush()
    try:
        httpd.serve_forever()
    finally:
        stop_event.set()
        logger.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
