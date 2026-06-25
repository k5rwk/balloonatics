#!/usr/bin/env bash
#
#	Horus Binary KA9Q-Radio -> native demod -> shared uploader (edge-DSP)
#
#   Pulls one channel's PCM stream with ka9q-radio (pcmrecord), demodulates it
#   *here* with the native C horus_demod (~1% CPU/stream), and streams only the
#   resulting telemetry text (hex packets + stats JSON) to a single shared
#   uploader server over TCP. Doing the DSP at the edge lets the OS spread 26
#   demods across all cores, and keeps the central process cheap (it just decodes
#   text and uploads). Contrast with the previous model, which shipped raw 48 kHz
#   IQ audio to a central Python demod - ~8x more CPU for the same work.
#
#   Protocol (see uploader_server.py): open a TCP connection, send one JSON config
#   line ({freq_hz, freq_target_hz, baud_rate}), then stream horus_demod's stdout.
#   Everything is newline-delimited text, so the handshake is robust to TCP
#   coalescing. We use bash's /dev/tcp so no netcat is needed in the image.
#

set -e
set -u
set -o pipefail
set -x

# Tunables (overridable via environment / compose)
PCM_TIMEOUT="${PCM_TIMEOUT:-30}"
STARTUP_JITTER="${STARTUP_JITTER:-3}"
SERVER_HOST="${SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${SERVER_PORT:-4921}"

# Modem parameters. Defaults match the Horus Binary v1/v2 setup and the IQ PCM
# streams in radiod.conf ([global] mode = iq -> stereo I/Q, so IQ=true -> -q).
DECODER="${DECODER:-horus_demod}"
MODE="${MODE:-binary}"
MODEMRATE="${MODEMRATE:-100}"
IQ="${IQ:-true}"
STATS_INTERVAL="${STATS_INTERVAL:-5}"

# IQ input -> horus_demod's -q (stereo) flag.
IQFLAG=""
[ "$IQ" = "true" ] && IQFLAG="-q"

# Frequency estimator limits, derived from RXBANDWIDTH.
FSK_LOWER=$(echo "$RXBANDWIDTH / -2" | bc)
FSK_UPPER=$(echo "$RXBANDWIDTH / 2" | bc)

# Spread out startup so a bulk `up` doesn't slam pcmrecord all at once.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s"
    sleep "$DELAY"
fi

# Config line consumed by uploader_server.py: just the metadata it needs to
# annotate decodes (the demod parameters are passed to horus_demod directly).
CONFIG=$(printf '{"freq_hz":%s,"freq_target_hz":%s,"baud_rate":%s}' \
    "$RXFREQ" "$RXFREQ" "$MODEMRATE")

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using PCM stream: $SDR_DEVICE (dedicated static channel in radiod.conf)"
echo "Using FSK estimation range: $FSK_LOWER - $FSK_UPPER Hz"
echo "Feeding shared uploader at ${SERVER_HOST}:${SERVER_PORT}"
echo "Config line: $CONFIG"

# Open the TCP connection to the shared uploader. If it isn't up yet this fails
# under `set -e`, the script exits, and (with restart:always) we retry.
exec 3<>"/dev/tcp/${SERVER_HOST}/${SERVER_PORT}"

# Send the config line, then run the receive chain: pcmrecord -> native demod ->
# socket. horus_demod -g puts modem stats on stdout alongside decoded packets, so
# the uploader gets both telemetry and SNR. When pcmrecord exits (idle --timeout)
# or the uploader drops the socket, the pipeline ends and we exit to restart.
echo "Starting receiver chain"
printf '%s\n' "$CONFIG" >&3
pcmrecord --catmode --raw "$SDR_DEVICE" --timeout "$PCM_TIMEOUT" | \
    "$DECODER" $IQFLAG --stats="$STATS_INTERVAL" -g -m "$MODE" \
        --fsk_lower="$FSK_LOWER" --fsk_upper="$FSK_UPPER" - - >&3

echo "Receive chain exited; shutting down so the container can restart."
