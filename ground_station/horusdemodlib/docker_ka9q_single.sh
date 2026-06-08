#!/usr/bin/env bash
#
#	Horus Binary KA9Q-Radio Helper Script (patched for bulk startup on Pi4)
#
#   Uses ka9q-radio (pcmrecord) to receive a chunk of spectrum, and passes it into horus_demod.
#
#   Local patches vs. upstream:
#     * PCM_TIMEOUT (default 30s, was 1s): how long pcmrecord tolerates an idle gap
#       before exiting. A 1s value makes the pipeline die during the CPU spike when
#       many decoders start at once, which (with restart:always) becomes a restart
#       death-spiral. 30s rides out the startup contention.
#     * STARTUP_JITTER (default 15s): random sleep before starting, so a bulk
#       `docker compose up` doesn't slam pcmrecord all at once.
#     * STATIC CHANNELS: each frequency is now a dedicated static channel in
#       ka9q-radio/radiod.conf with its own PCM multicast stream (SDR_DEVICE),
#       so we no longer `tune` here. This avoids every pcmrecord sharing one
#       multicast group and receiving all channels' IQ (an O(N^2) kernel fan-out
#       that was burning ~35% sys CPU). radiod auto-assigns the static SSRC as
#       the frequency in kHz, i.e. RXFREQ/1000.
#

set -e
set -u
set -o pipefail
set -x

# Tunables (overridable via environment / compose)
PCM_TIMEOUT="${PCM_TIMEOUT:-30}"
STARTUP_JITTER="${STARTUP_JITTER:-15}"

# Calculate the frequency estimator limits
FSK_LOWER=$(echo "$RXBANDWIDTH / -2" | bc)
FSK_UPPER=$(echo "$RXBANDWIDTH / 2" | bc)

# Static-channel SSRC convention used by radiod: frequency in kHz.
SSRC=$(($RXFREQ / 1000))

# Spread out startup so a bulk `up` doesn't slam pcmrecord all at once.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s"
    sleep "$DELAY"
fi

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using SSRC: $SSRC (static channel in radiod.conf)"
echo "Using PCM stream: $SDR_DEVICE"
echo "Using FSK estimation range: $FSK_LOWER - $FSK_UPPER Hz"

# Start the receive chain. The channel is defined statically in radiod.conf, so
# there is no `tune` step - we just read this channel's dedicated PCM stream.
# We pass in the SDR centre frequency ($RXFREQ) and 'target' signal frequency
# ($RXFREQ) to provide additional metadata to Habitat / Sondehub.
echo "Starting receiver chain"
pcmrecord --ssrc $SSRC --catmode --raw $SDR_DEVICE --timeout $PCM_TIMEOUT | \
  $DECODER -q --stats=5 -g -m binary --fsk_lower=$FSK_LOWER --fsk_upper=$FSK_UPPER - - | \
  python3 -m horusdemodlib.uploader --freq_hz $RXFREQ --freq_target_hz $RXFREQ $@ &

echo "Started everything, waiting for any failed processes"

wait -n
echo "A process in the receive chain exited; shutting down so the container can restart."
pkill bash
