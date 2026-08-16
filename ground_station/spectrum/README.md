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
| `INTERVAL`    | `0.1`              | seconds between FFT polls = display update rate — also sets radiod's channel lifetime, [see below](#interval-sets-radiods-channel-lifetime) |
| `AVERAGE`     | `8`                | FFTs radiod averages into each frame (`powers -a`)     |
| `TIMEOUT`     | `0.25`             | seconds `powers` waits for a response (`powers -T`) — [see below](#stalls-frame-size-and-ip-fragmentation) |
| `SMOOTHING`   | `0.2`              | **default** IIR τ (s) for the browser (adjust live)    |
| `LOG_DIR`     | `/data`            | directory for spectrum CSV logs (mount a volume)       |
| `LOG_INTERVAL`| `0`                | if >0, auto-start logging at this period (s)           |
| `RADIOD_CONF` | `/etc/radio/radiod.conf` | radiod.conf to derive tuning from                |
| `FREQUENCY`   | _radiod.conf_      | center frequency, Hz — override                        |
| `BINS`        | _samprate/BIN_WIDTH_ | number of FFT bins — override                        |
| `MCAST`       | _radiod.conf_      | radiod status multicast group — override               |
| `SSRC`        | `7438`             | SSRC for the transient spectrum channel                |
| `CROSSOVER`   | _unset_ (→ `powers`' 200 Hz) | rbw threshold for the narrowband path ([see below](#the-wideband-vs-narrowband-path)) |
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

## INTERVAL sets radiod's channel lifetime

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

## Stalls, frame size and IP fragmentation

Symptom: the frame counter freezes for whole seconds at a time (`/status` shows
`last_frame_age` climbing to several seconds), then a burst of frames arrives.

Each response carries `BINS` float32 values as `BIN_DATA` in a **single UDP
datagram**. At the default 2400 bins that is 9600 bytes, which the IP layer has to
fragment into ~7 packets on a 1500-MTU interface. **Losing any one fragment
discards the entire frame**, and `powers` then waits out its `-T` timeout before
re-polling. Its own default is 1 second, so one lost fragment = one second of
frozen display — which reads as a hard stall and a burst, not as a dropped frame.

`TIMEOUT` caps the cost of a loss. **It does not stop the loss.** To find out
whether fragmentation is really what is happening, use [`diag.py`](diag.py), which
runs the exact `powers` command the server would and reports the gap distribution:

```sh
docker compose exec spectrum python3 /app/diag.py                  # as configured
docker compose exec -e BINS=300 spectrum python3 /app/diag.py      # fits one datagram
```

If the stalls vanish at `BINS=300` (1200 bytes, no fragmentation) the diagnosis is
confirmed. `diag.py` also flags gaps that land within 0.15 s of a whole second,
which means the response never arrived rather than merely arriving late. Cross-check
against the host's counters over the same window:

```sh
nstat -az | grep -iE 'reasm|frag'          # ReasmFails / ReasmTimeout climbing
netstat -su | grep -iE 'reassembl|receive errors'
```

Real fixes, if confirmed, are to keep the payload under the MTU (fewer `BINS`, i.e.
coarser resolution or a narrower span) or to put radiod's multicast on a large-MTU
interface — [`radiod.conf`](../ka9q-radio/radiod.conf) has a commented-out
`iface = lo`, and loopback's MTU is 65536. That only works because every consumer in
this stack is host-networked on the same box, and it moves **every** radiod stream,
not just this one, so make that change deliberately rather than as a spectrum tweak.

## The wideband vs narrowband path

radiod's spectrum pseudo-demod has two implementations and chooses between them by
comparing the resolution bandwidth to a *crossover* rbw:

| | selected when | how it works | cost |
|---|---|---|---|
| **wideband** | `rbw > crossover` | windowed FFT of size `samprate/rbw` straight off the raw A/D input buffer | one FFT per averaged frame |
| **narrowband** | `rbw <= crossover` | a full complex downconverter at `bin_count*rbw` feeding a ring buffer, then the FFT | downconverter + filter + ring fill, **per poll** |

At our settings the narrowband path was running a 2.4 MHz complex downconverter
every poll — the dominant cost in the whole analyzer, and its
`create_filter_output` is also what made the channel-lifetime churn described above
so expensive.

We used to force it anyway, by setting `CROSSOVER` to the full span, because on a
complex (I/Q) front end like the RTL-SDR the wideband path filled only the
*positive*-frequency half of the span — the lower half came back as zeros, a flat
line below center. **That bin-mapping bug is fixed** in the ka9q-radio ref this
image now pins; the fix is in `wideband_poll()` in `spectrum.c`, annotated
`(fixed by KE5GDB)` — it's the patch from this project, upstreamed.

So `CROSSOVER` is now left unset and `powers`' own default of 200 Hz applies:

- at the default `BIN_WIDTH` of 1000 Hz → **wideband** (cheap, full two-sided span)
- ask for fine resolution (`BIN_WIDTH` ≤ 200) → **narrowband**, which is the right
  path for a narrow span

Set `CROSSOVER` explicitly to override; `CROSSOVER = BIN_WIDTH * BINS` restores the
old always-narrowband behaviour. The active path is printed at startup and shown in
the UI's header line, so you can confirm which one you're on.

Two cosmetic differences to expect on the wideband path: the narrowband
downconverter trimmed a 400 Hz margin for its filter skirts, so you now see the
front end's own roll-off at the span edges, and the RTL-SDR's DC spike sits at
center rather than being filtered out.

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
