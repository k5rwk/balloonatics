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
#     * STARTUP_JITTER (default 15s): random sleep before tuning, so a bulk
#       `docker compose up` doesn't hit radiod with N simultaneous tune calls.
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

SSRC=$(($RXFREQ / 1000))01
RADIO=$(echo $SDR_DEVICE | sed 's/-pcm//g')

exit_rx() {
    echo "Exiting..."
    echo "Closing channel $SSRC at frequency $RXFREQ"
    timeout 2 tune --samprate 48000 --mode iq --low $FSK_LOWER --high $FSK_UPPER --frequency 0 --ssrc $SSRC --radio $RADIO
    pkill bash
}

# Spread out startup so a bulk `up` doesn't tune all channels at the same instant.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s before tuning"
    sleep "$DELAY"
fi

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using SSRC: $SSRC"
echo "Using PCM stream: $SDR_DEVICE"
echo "Using FSK estimation range: $FSK_LOWER - $FSK_UPPER Hz"

# Start the receive chain.
# Note that we now pass in the SDR centre frequency ($RXFREQ) and 'target' signal frequency ($RXFREQ)
# to enable providing additional metadata to Habitat / Sondehub.
echo "Configuring receiver on ka9q-radio"
tune --samprate 48000 --mode iq --low $FSK_LOWER --high $FSK_UPPER --frequency $RXFREQ --ssrc $SSRC --radio $RADIO

echo "Starting receiver chain"
pcmrecord --ssrc $SSRC --catmode --raw $SDR_DEVICE --timeout $PCM_TIMEOUT | \
  $DECODER -q --stats=5 -g -m binary --fsk_lower=$FSK_LOWER --fsk_upper=$FSK_UPPER - - | \
  python3 -m horusdemodlib.uploader --freq_hz $RXFREQ --freq_target_hz $RXFREQ $@ &

echo "Started everything, waiting for any failed processes"

wait -n
exit_rx
