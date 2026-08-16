#!/usr/bin/env python3
"""Measure the powers -> server frame stream, to localise stalls.

Runs the *exact* powers command server.py would run (same Config, same env), but
instead of serving frames it timestamps each one and reports the gap distribution.
This isolates radiod/powers from the HTTP/SSE/browser side: if the stalls show up
here, they are upstream of server.py.

  docker compose exec spectrum python3 /app/diag.py

Env overrides work the same as for server.py, so A/B tests are one-liners:

  # does it stop stalling once a response fits without IP fragmentation?
  docker compose exec -e BINS=300 spectrum python3 /app/diag.py

  # narrowband instead of wideband
  docker compose exec -e CROSSOVER=2400000 spectrum python3 /app/diag.py

  # no radiod-side averaging
  docker compose exec -e AVERAGE=1 spectrum python3 /app/diag.py

Options:
  --seconds N   how long to measure (default 60)
  --stall S     gap (s) at or above which a frame is reported individually
                (default 0.5)
  -v            pass -v to powers, so its stderr chatter is interleaved
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from server import Config, parse_line   # noqa: E402


def arg(name, default, cast=float):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = min(len(sorted_vals) - 1, int(round(p / 100.0 * (len(sorted_vals) - 1))))
    return sorted_vals[k]


def main():
    duration = arg("--seconds", 60.0)
    stall = arg("--stall", 0.5)

    cfg = Config()
    argv = cfg.powers_argv()
    if "-v" in sys.argv:
        argv.insert(1, "-v")

    # Payload arithmetic: BIN_DATA is one float32 per bin inside a single UDP
    # datagram. Anything over the path MTU has to be IP-fragmented, and a single
    # lost fragment discards the whole frame -- which costs powers a full -T
    # timeout (1 s by default), not just one frame.
    bin_bytes = int(cfg.bins) * 4
    frags = max(1, (bin_bytes + 200 + 1479) // 1480)
    print("diag: %s" % " ".join(argv))
    print("diag: %s path, %s bins -> ~%d B of BIN_DATA per datagram (~%d IP "
          "fragments at 1500 MTU)" % (cfg.spectrum_path(), cfg.bins, bin_bytes, frags))
    print("diag: expecting a frame every %.0f ms; measuring for %.0fs\n"
          % (cfg.interval * 1000, duration))

    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=None,
                                bufsize=1, text=True)
    except FileNotFoundError:
        sys.exit("diag: powers binary not found: %s" % cfg.powers_bin)
    gaps = []
    bad = 0
    stalls = []
    t0 = time.monotonic()
    last = None
    try:
        for line in proc.stdout:
            now = time.monotonic()
            if now - t0 > duration:
                break
            frame = parse_line(line)
            if not frame:
                bad += 1
                continue
            if last is not None:
                g = now - last
                gaps.append(g)
                if g >= stall:
                    stalls.append((now - t0, g, frame["n"]))
                    print("  STALL at t=%6.2fs: %.3fs gap (%d bins)"
                          % (now - t0, g, frame["n"]))
            last = now
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    elapsed = max(1e-6, time.monotonic() - t0)
    frames = len(gaps) + 1 if gaps or last is not None else 0
    print("\ndiag: %d frames in %.1fs = %.1f Hz (expected %.1f Hz)"
          % (frames, elapsed, frames / elapsed, 1 / cfg.interval))
    if frames == 0:
        print("diag: powers produced no parseable frames at all -- check its "
              "stderr (rerun with -v); this is a radiod/multicast problem, not "
              "a display one.")
    if bad:
        print("diag: %d unparseable lines" % bad)
    if gaps:
        s = sorted(gaps)
        print("diag: gap ms  min %.0f  p50 %.0f  p90 %.0f  p99 %.0f  max %.0f"
              % (s[0] * 1000, pct(s, 50) * 1000, pct(s, 90) * 1000,
                 pct(s, 99) * 1000, s[-1] * 1000))
        lost = sum(g for g in gaps if g >= stall)
        print("diag: %d stalls >= %.1fs, %.1fs lost to them (%.0f%% of the run)"
              % (len(stalls), stall, lost, 100 * lost / elapsed))
        # powers' retransmission timeout is 1 s, so stalls landing near a whole
        # number of seconds mean the response datagram never arrived.
        whole = [g for g in gaps if g >= 0.9 and abs(g - round(g)) < 0.15]
        if whole:
            print("diag: %d gaps sit within 0.15s of a whole second -- that is "
                  "powers timing out waiting for a response that never arrived, "
                  "i.e. the status datagram was dropped, not merely late."
                  % len(whole))
    print("\ndiag: check the host's reassembly counters against this run:")
    print("      nstat -az | grep -iE 'reasm|frag'   # ReasmFails / ReasmTimeout")
    print("      netstat -su | grep -iE 'reassembl|receive errors'")


if __name__ == "__main__":
    main()
