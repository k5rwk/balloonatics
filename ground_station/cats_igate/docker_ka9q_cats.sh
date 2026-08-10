#!/usr/bin/env bash
#
#	CATS KA9Q-Radio Helper Script
#
#	Pulls a (statically defined) FM-demodulated audio channel from ka9q-radio
#	via pcmrecord and pipes it into CATS decoding.
#
#	The channel is defined statically in ka9q-radio/radiod.conf (section [aprs])
#	rather than tuned dynamically, so:
#	  * No `tune` call is needed here - radiod owns the channel for its lifetime.
#	  * SSRC is auto-assigned by radiod as the frequency in kHz (RXFREQ / 1000),
#	    see radio.c. So 433.108 MHz -> SSRC 433108.
#	  * The [cats] preset forces squelch fully open, so audio streams
#	    continuously -> pcmrecord never hits its idle timeout -> no restart loop.
#
#	Modelled on docker_ka9q_single.sh from the horusdemodlib service.
#

set -e
set -u
set -o pipefail
set -x

# Tunables (overridable via environment / compose)
PCM_TIMEOUT="${PCM_TIMEOUT:-30}"
STARTUP_JITTER="${STARTUP_JITTER:-15}"
SAMPRATE="${SAMPRATE:-48000}"
BAUD_RATE="${BAUD_RATE:-9600}"
CATS_CONF="${CATS_CONF:-/config.toml}"

# Static-channel SSRC convention used by radiod: frequency in kHz.
SSRC=$(($RXFREQ / 1000))

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using PCM stream: $SDR_DEVICE"
echo "CATS: $SAMPRATE Hz, baud: $BAUD_RATE"

# Spread out startup so a bulk `up` doesn't slam pcmrecord during the CPU spike.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s"
    sleep "$DELAY"
fi

echo "Starting receiver chain"

# pcmrecord streams raw 16-bit signed PCM on stdout; CATS reads it from
# stdin (DEFAULT) at the matching sample rate.
# ls -l /usr/bin

pcmrecord --catmode --raw $SDR_DEVICE --timeout $PCM_TIMEOUT | cats-sdr-igate

echo "Started everything, waiting for any failed processes"

# These can be used for troubleshooting
# which cats-sdr-igate
# which cats-sdr-igate && \
# ls -l $(which cats-sdr-igate)

wait -n
echo "A process in the receive chain exited; shutting down so the container can restart."
pkill bash
