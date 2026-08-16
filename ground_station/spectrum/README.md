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
(avg AVERAGE FFTs   (rtl-sdr.local)   (FFT bins, dB)  (parse + fan-out)   (IIR smooth,
 per response)                                     + optional CSV log)     trace, waterfall,
                                                                           markers)
```

radiod integrates `AVERAGE` FFTs into each response; the server passes those frames
through untouched, and the browser adds a live-adjustable IIR stage on top before
rendering the trace, waterfall, markers and readouts. The server logs the frames as
they arrive from `powers` (pre-IIR) for replay.

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
| `INTERVAL`    | `0.1`              | seconds between FFT polls = display update rate — also sets radiod's channel lifetime, [see below](#dont-lower-interval-it-sets-radiods-channel-lifetime) |
| `AVERAGE`     | `8`                | FFTs radiod averages into each frame (`powers -a`)     |
| `SMOOTHING`   | `0.2`              | **default** IIR τ (s) for the browser (adjust live)    |
| `LOG_DIR`     | `/data`            | directory for spectrum CSV logs (mount a volume)       |
| `LOG_INTERVAL`| `0`                | if >0, auto-start logging at this period (s)           |
| `RADIOD_CONF` | `/etc/radio/radiod.conf` | radiod.conf to derive tuning from                |
| `FREQUENCY`   | _radiod.conf_      | center frequency, Hz — override                        |
| `BINS`        | _samprate/BIN_WIDTH_ | number of FFT bins — override                        |
| `MCAST`       | _radiod.conf_      | radiod status multicast group — override               |
| `SSRC`        | `7438`             | SSRC for the transient spectrum channel                |
| `CROSSOVER`   | _span_             | rbw threshold for narrowband mode (see note below)     |
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

## Don't lower `INTERVAL` — it sets radiod's channel lifetime

`powers` derives radiod's channel self-destruct timer from the poll interval,
sending `LIFETIME = INTERVAL * 2 * 50` **frames of 20 ms each** — a lifetime of
`2 * INTERVAL` seconds, truncated to whole frames. If a poll ever slips past that
budget, radiod tears the transient spectrum channel down (filter output, FFTW
plan, ring buffer, window) and the next poll rebuilds all of it; the rebuilt
channel then answers its first poll with no `BIN_DATA`, so `powers` spins
re-sending setup commands at 10 ms intervals until it settles.

At the old `INTERVAL` of `0.025` that budget was `0.025*2*50 = 2.5` → **2 frames
= 40 ms**, against a poll gap of ~25 ms plus round trip — so the channel was
being destroyed and reconstructed constantly, which showed up as a glitchy,
inconsistent display and a large CPU hit. `0.1` gives 10 frames = 200 ms, about
2× the poll gap. **Don't go below ~`0.05`** without re-checking this; the server
logs a warning at startup if the lifetime drops under 4 frames.

This only became a problem in newer ka9q-radio: older `powers` never sent
`LIFETIME` at all, and since `lifestart` is only ever assigned from that command,
the transient channel was effectively immortal.

If you want a smoother trace, raise `AVERAGE` — not the poll rate.

## Integration period (AVERAGE, SMOOTHING / τ)

Integration happens in two places, and the cheap one is server-side.

**radiod (`AVERAGE` → `powers -a` → `SPECTRUM_AVG`)** averages N consecutive FFTs
out of its ring buffer into each response. This costs one poll instead of N, so
it is strictly cheaper than raising the frame rate, and it is why `INTERVAL` can
be 4× slower than it used to be without losing noise performance.

> Keep `AVERAGE` modest. `powers` splits the request into `pieces` when
> `average/rbw` exceeds 80 ms, and its normalization of that split is wrong — it
> scales by `1/pieces` using an integer divide while still asking radiod for the
> full `average` — so multi-piece results come back several dB high. At the
> default `BIN_WIDTH` of 1000 Hz, stay at or below ~16.

**The browser** then integrates what arrives with an IIR (exponential) filter,
per bin, in **linear power** (unbiased; smoothing dB directly would skew toward
the nulls):

```
ema += alpha * (x - ema),   alpha = 1 - e^(-dt/tau)
```

`tau` is the **τ (s)** control (initialised from `SMOOTHING`) and `dt` is the
*measured* interval between frames, so the amount of integration is independent of
the frame rate / jitter — the browser adapted to the slower `INTERVAL` on its own,
with no change needed. Unlike boxcar averaging, this **decouples integration from
update rate**: the display refreshes every `INTERVAL` (10 Hz by default) but each
frame carries ~`tau` of integration on top of radiod's `AVERAGE`. Raise τ for a
smoother, slower-reacting trace; set it to `0` to show what radiod sent verbatim.
Doing this stage client-side means it is adjustable live, and the **logs capture
the unsmoothed frames** (already `AVERAGE`-averaged by radiod).

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
  [`Dockerfile`](Dockerfile) in step with [the SDR image's
  ref](../ka9q-radio/Dockerfile) so the status/TLV protocol matches what radiod
  emits. This is not cosmetic — the two have drifted in ways that break silently
  or loudly: newer radiod asserts `spectrum.fft_avg >= 1` in `narrowband_poll()`,
  and an older `powers` never sends `SPECTRUM_AVG` at all.
- The server is stdlib-only (no pip dependencies); the HTTP server, SSE transport
  and parser are all in `server.py`.
