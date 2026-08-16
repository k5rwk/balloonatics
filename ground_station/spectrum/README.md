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
        (rtl-sdr.local)        (FFT bins, dB)    (parse + fan-out raw)   (IIR smooth,
                                                  + optional CSV log)     trace, waterfall,
                                                                          markers)
```

The frames go to the browser, which does the IIR smoothing, rendering, markers and
readouts. The server can also log the frames to CSV for replay.

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

## Tuning is derived from radiod.conf

Center frequency, span and the status group are **read from
[`radiod.conf`](../ka9q-radio/radiod.conf)** (mounted into the container), so the
SDR tuning lives in exactly one place and never has to be copied into compose.
Following ka9q semantics: `[global] status` is the multicast group, `[global]
hardware` names the device section, and that section's `frequency` / `samprate`
give the center and span. The span becomes `samprate`, so `BINS = samprate /
BIN_WIDTH` covers the whole receiver passband. Switch the SDR (e.g. rtlsdr →
sdrplay with a 4 MHz samprate) and the analyzer follows automatically. Any of
`FREQUENCY` / `BINS` / `MCAST` still overrides if set explicitly.

## Configuration

All via environment variables (set in `docker-compose.yml`):

| Var           | Default            | Meaning                                                |
|---------------|--------------------|--------------------------------------------------------|
| `PORT`        | `8000`             | HTTP port (bound directly on the host)                 |
| `BIN_WIDTH`   | `1000`             | resolution bandwidth per bin, Hz (span = samprate/this)|
| `INTERVAL`    | `0.025`            | seconds between FFT polls = display update rate        |
| `AVERAGE`     | `8`                | FFTs radiod averages into each frame (`powers -a`)     |
| `TIMEOUT`     | `0.25`             | seconds `powers` waits for a response (`powers -T`)    |
| `SMOOTHING`   | `0.2`              | **default** IIR τ (s) for the browser (adjust live)    |
| `LOG_DIR`     | `/data`            | directory for spectrum CSV logs (mount a volume)       |
| `LOG_INTERVAL`| `0`                | if >0, auto-start logging at this period (s)           |
| `RADIOD_CONF` | `/etc/radio/radiod.conf` | radiod.conf to derive tuning from                |
| `FREQUENCY`   | _radiod.conf_      | center frequency, Hz — override                        |
| `BINS`        | _samprate/BIN_WIDTH_ | number of FFT bins — override                        |
| `MCAST`       | _radiod.conf_      | radiod status multicast group — override               |
| `SSRC`        | `7438`             | SSRC for the transient spectrum channel                |
| `CROSSOVER`   | _unset_            | rbw threshold for narrowband mode (see note below)     |
| `SOURCE`      | _unset_            | optional `powers -o` source name/address               |
| `POWERS_BIN`  | `powers`           | path to the powers binary                              |
| `POWERS_ARGS` | _unset_            | full override of the powers args (after the binary)    |
| `REPLAY_FILE` | _unset_            | replay a saved powers/log CSV instead of running powers|

To change the port, edit `PORT` under the `spectrum` service in
`docker-compose.yml`.

## UI

- **auto dB** — track the noise floor / peak automatically (uncheck to set
  `min`/`max` by hand). **contrast** squeezes the color window for punch.
- **τ (s)** — IIR smoothing time constant, live (0 = raw); see below.
- **max hold** — overlay the per-bin peak hold on the trace.
- **pause** — freeze the display (the feed keeps running).
- Hover anywhere to read the frequency and power under the cursor.

### Markers

- **Click** the plot to drop a marker; **drag** it to move; **✕** (in the panel)
  or **clear ✕** removes them.
- A point marker reads out the power at its frequency. Give a marker a
  **bandwidth** (the `kHz` box in its panel row, or tick **band** before dropping
  so new markers start with the default bandwidth) to turn it into a **PSD /
  channel-power marker**: it then reports the integrated power across the band
  (`dB`), the power spectral density (`dB/Hz`) and the in-band peak.
- Powers from `powers` are uncalibrated, so marker readouts are **relative** dB.

### Logging a flight

- Set the **log** interval (s) and hit **start log** to record; **logs ▾** lists
  saved files with download links. Or auto-start at boot with `LOG_INTERVAL`.
- Logs are written as the **same CSV format `powers` emits**, so to review a
  flight afterwards just point `REPLAY_FILE` at the file and the whole UI
  (waterfall, markers, PSD) replays it — see below.

## Replay a logged flight (or demo without an SDR)

Point `REPLAY_FILE` at a captured log (or any `powers` CSV) to loop it through the
full UI — no radiod or SDR needed:

```sh
docker run --rm -p 8000:8000 \
  -e REPLAY_FILE=/flight.csv -v /path/to/spectrum-20260626T....csv:/flight.csv:ro \
  balloonatics/spectrum:local
```

Because logs and `powers` share one CSV format, anything the analyzer records is
directly replayable here.

## Integration period (SMOOTHING / τ)

radiod integrates `AVERAGE` FFTs into each frame (`powers -a`, i.e. `SPECTRUM_AVG`),
which is cheaper than polling that many times as fast. The **browser** then adds an
IIR (exponential) filter, per bin, in **linear power** (unbiased; smoothing dB
directly would skew toward the nulls):

```
ema += alpha * (x - ema),   alpha = 1 - e^(-dt/tau)
```

`tau` is the **τ (s)** control (initialised from `SMOOTHING`) and `dt` is the
*measured* interval between frames, so the amount of integration is independent of
the frame rate / jitter. Unlike boxcar averaging, this **decouples integration from
update rate**: the display refreshes every `INTERVAL` (40 Hz by default) but each
frame carries ~`tau` of integration, cutting the noise like averaging ~`tau/INTERVAL`
independent FFTs (≈ 8 at the defaults, measured ~2.8× / √8). Raise τ for a smoother,
slower-reacting trace; set it to `0` to show what radiod sent verbatim. Doing the
smoothing client-side means it is adjustable live and the **logs capture the
unsmoothed frames**.

Because the same smoothed stream drives the waterfall, a brief signal leaves a
short (~`tau`) vertical tail — usually helpful for catching weak carriers, and
shortened by lowering `tau`.

## Why CROSSOVER is left unset

radiod's spectrum pseudo-demod has two implementations and chooses between them by
comparing the resolution bandwidth to a *crossover* rbw: `rbw > crossover` uses the
**wideband** path (an FFT off the raw A/D input); `rbw <= crossover` uses the
**narrowband** path (a full complex downconverter at `bin_count * rbw`, run per
poll). Wideband is much the cheaper of the two.

We used to force `CROSSOVER` to the full span to stay on the narrowband path,
because on a complex (I/Q) front end like the RTL-SDR the wideband path only filled
the *positive*-frequency half of the requested span — the lower half came back as
zeros, i.e. a flat line below the center frequency. That bin-mapping bug is fixed
in the ka9q-radio ref this image pins, so `CROSSOVER` is now left unset and
`powers`' own default (200 Hz) applies: at the default `BIN_WIDTH` of 1000 Hz that
selects wideband, while a fine `BIN_WIDTH` (≤ 200) still drops to narrowband, which
is the right path for a narrow span. Set `CROSSOVER = BIN_WIDTH * BINS` to force the
old always-narrowband behaviour.

## Notes

- The Docker image compiles **only** `powers` from the pinned ka9q-radio source,
  so it is tiny: `powers` links just `libbsd`/`librt`/`libm`/`libpthread`, and the
  runtime adds python3 + `libnss-mdns`. Keep the build `ARG REF` in the
  [`Dockerfile`](Dockerfile) in step with the SDR image's ref so the status/TLV
  protocol matches what radiod emits.
- The server is stdlib-only (no pip dependencies); the HTTP server, SSE transport
  and parser are all in `server.py`.
