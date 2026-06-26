# Web spectrum analyzer

A self-contained live FFT + waterfall display for ka9q-radio, served over HTTP.

It drives ka9q-radio's [`powers`](../ka9q-radio-git/src/powers.c) utility, which
asks a `radiod` instance to spin up a transient spectrum (`SPECT_DEMOD`) channel
on its status multicast group and prints rtl_power-style FFT bin energies. The
Python server ([`server.py`](server.py)) parses each frame and fans it out to
browsers over Server-Sent Events; [`index.html`](index.html) renders the trace
and a scrolling waterfall on a `<canvas>` with no external JS.

```
radiod  ──status multicast──>  powers  ──stdout──>  server.py  ──SSE──>  browser
        (rtl-sdr.local)        (FFT bins, dB)       (parse+fan-out)      (canvas)
```

## Running

It is wired into the stack's [`docker-compose.yml`](../docker-compose.yml) as the
`spectrum` service. Bring it up with the rest of the ground station:

```sh
docker compose up -d --build spectrum
```

Then open <http://localhost:8000/> (or `http://<host>:8000/`).

Like every other ka9q consumer it uses **host networking** (needed for the
multicast), so it binds `PORT` directly on the host and resolves radiod's
`*.local` status group through the shared, containerised avahi.

## Configuration

All via environment variables (set in `docker-compose.yml`):

| Var          | Default          | Meaning                                                |
|--------------|------------------|--------------------------------------------------------|
| `PORT`       | `8000`           | HTTP port (bound directly on the host)                 |
| `MCAST`      | `rtl-sdr.local`  | radiod status multicast group                          |
| `SSRC`       | `7438`           | SSRC for the transient spectrum channel                |
| `FREQUENCY`  | `432000000`      | center frequency, Hz (default = the RTL SDR center)    |
| `BIN_WIDTH`  | `1000`           | resolution bandwidth per bin, Hz                       |
| `BINS`       | `2400`           | number of FFT bins (`BINS * BIN_WIDTH` = total span)   |
| `INTERVAL`   | `0.025`          | seconds between FFT polls = display update rate        |
| `SMOOTHING`  | `0.2`            | IIR integration time constant τ, seconds (see below)   |
| `CROSSOVER`  | _span_           | rbw threshold for narrowband mode (see note below)     |
| `SOURCE`     | _unset_          | optional `powers -o` source name/address               |
| `POWERS_BIN` | `powers`         | path to the powers binary                              |
| `POWERS_ARGS`| _unset_          | full override of the powers args (after the binary)    |
| `REPLAY_FILE`| _unset_          | replay a saved powers log instead of running `powers`  |

The default span (`2400 × 1000 Hz` = 2.4 MHz centered on 432 MHz) matches the
RTL-SDR passband in [`radiod.conf`](../ka9q-radio/radiod.conf) (`samprate =
2400000`), so it shows the whole receiver bandwidth, including the horus channels
(432.5–433.0) and the APRS channel (433.0).

To change the port, edit `PORT` under the `spectrum` service in
`docker-compose.yml`.

## UI

- **auto dB** — track the noise floor / peak automatically (uncheck to set
  `min`/`max` by hand). **contrast** squeezes the color window for punch.
- **max hold** — overlay the per-bin peak hold on the trace.
- **pause** — freeze the display (the feed keeps running).
- Hover anywhere to read the frequency and power under the cursor.

## Demo without an SDR

Point `REPLAY_FILE` at a captured `powers` log to loop it (no radiod needed):

```sh
docker run --rm -p 8000:8000 \
  -e REPLAY_FILE=/sample.log -v /path/to/log_powers_ka9q-radio:/sample.log:ro \
  balloonatics/spectrum:local
```

## Integration period (SMOOTHING)

Each `powers` frame is a *single* FFT block (~1 ms of samples for the default
config) — `powers` doesn't expose radiod's `SPECTRUM_AVG`, so it never integrates
server-side, and a raw trace is noisy. We integrate **client-side** with an IIR
(exponential) filter, per bin, in **linear power** (unbiased; smoothing dB
directly would skew toward the nulls):

```
ema += alpha * (x - ema),   alpha = 1 - e^(-dt/tau)
```

`tau` is `SMOOTHING` (seconds) and `dt` is the *measured* interval between frames,
so the amount of integration is independent of the frame rate / jitter. Unlike
boxcar averaging, this **decouples integration from update rate**: the display
refreshes every `INTERVAL` (40 Hz by default) but each frame carries ~`tau` of
integration, cutting the noise like averaging ~`tau/INTERVAL` independent FFTs
(≈ 8 at the defaults). Raise `SMOOTHING` for a smoother, slower-reacting trace;
set it to `0` to show the raw per-frame FFT.

Because the same smoothed stream drives the waterfall, a brief signal leaves a
short (~`tau`) vertical tail — usually helpful for catching weak carriers, and
shortened by lowering `tau`.

## Why CROSSOVER defaults to the full span

radiod's spectrum pseudo-demod has two implementations and chooses between them by
comparing the resolution bandwidth to a *crossover* rbw: `rbw > crossover` uses the
**wideband** path (it reads the front-end FFT directly); `rbw <= crossover` uses the
**narrowband** path (a complex downconverter). On a complex (I/Q) front end like the
RTL-SDR the wideband path only fills the *positive*-frequency half of the requested
span — the lower half comes back as zeros, i.e. a flat line below the center
frequency. We therefore default `CROSSOVER` to the full span (`BIN_WIDTH * BINS`) so
`rbw <= crossover` always holds and `powers` stays on the narrowband path, producing
a correct two-sided spectrum. Override it only if you specifically want the wideband
path.

## Notes

- The Docker image compiles **only** `powers` from the pinned ka9q-radio source,
  so it is tiny: `powers` links just `libbsd`/`librt`/`libm`/`libpthread`, and the
  runtime adds python3 + `libnss-mdns`. Keep the build `ARG REF` in the
  [`Dockerfile`](Dockerfile) in step with the SDR image's ref so the status/TLV
  protocol matches what radiod emits.
- The server is stdlib-only (no pip dependencies); the HTTP server, SSE transport
  and parser are all in `server.py`.
