#!/usr/bin/env bash
#
#	Horus Binary KA9Q-Radio -> central audio server (A/B TEST METHOD)
#
#   This is the *audio-server* feeder, kept for benchmarking against the edge-DSP
#   method (docker_ka9q_server.sh). Here the feeder does NO DSP: it ships raw
#   48 kHz IQ audio straight to a central horusdemodlib server (server.py) that
#   demodulates in-process (multiprocessing pool) and uploads. Costs far more CPU
#   centrally than edge-DSP (the Python/cffi demod is ~8x the native C cost), but
#   is the simplest topology. Point the horus services at this script + run
#   `python3 -m horusdemodlib.server` on horus-server to use it.
#
#   Protocol (see horusdemodlib/server.py): open a TCP connection, send one JSON
#   config line, then stream raw PCM. server.py builds one HorusLib decoder per
#   connection from that config. Uses bash /dev/tcp (no netcat needed).
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

# Modem parameters for the JSON config. Defaults match the Horus Binary v1/v2
# setup and the IQ PCM streams in radiod.conf ([global] mode = iq -> stereo I/Q,
# so IQ=true).
MODE="${MODE:-binary}"
MODEMRATE="${MODEMRATE:-100}"
SAMPLERATE="${SAMPLERATE:-48000}"
IQ="${IQ:-true}"
TONESPACING="${TONESPACING:--1}"

# Frequency estimator limits, derived from RXBANDWIDTH.
FSK_LOWER=$(echo "$RXBANDWIDTH / -2" | bc)
FSK_UPPER=$(echo "$RXBANDWIDTH / 2" | bc)

# Spread out startup so a bulk `up` doesn't slam pcmrecord all at once.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s"
    sleep "$DELAY"
fi

# Per-connection config line consumed by server.py's configure().
CONFIG=$(printf '{"mode":"%s","samplerate":%s,"modemrate":%s,"freq_hz":%s,"freq_target_hz":%s,"tonespacing":%s,"iq":%s,"fsk_lower":%s,"fsk_upper":%s}' \
    "$MODE" "$SAMPLERATE" "$MODEMRATE" "$RXFREQ" "$RXFREQ" "$TONESPACING" "$IQ" "$FSK_LOWER" "$FSK_UPPER")

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using PCM stream: $SDR_DEVICE (dedicated static channel in radiod.conf)"
echo "Using FSK estimation range: $FSK_LOWER - $FSK_UPPER Hz"
echo "Feeding central audio server at ${SERVER_HOST}:${SERVER_PORT}"
echo "Config line: $CONFIG"

# Open the TCP connection to the central server. If it isn't up yet this fails
# under `set -e`, the script exits, and (with restart:always) we retry.
exec 3<>"/dev/tcp/${SERVER_HOST}/${SERVER_PORT}"

# Send the config line first (its own write, so it lands as the server's first
# recv before any audio), then stream raw PCM. pcmrecord starts slower than this
# small write, so ordering holds in practice. When pcmrecord exits (idle
# --timeout) or the server drops the socket, the write fails and we exit to restart.
echo "Starting receiver chain"
printf '%s\n' "$CONFIG" >&3
pcmrecord --catmode --raw "$SDR_DEVICE" --timeout "$PCM_TIMEOUT" >&3

echo "Receive chain exited; shutting down so the container can restart."