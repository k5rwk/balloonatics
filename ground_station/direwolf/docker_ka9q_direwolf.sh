#!/usr/bin/env bash
#
#	Direwolf KA9Q-Radio Helper Script
#
#	Pulls a (statically defined) FM-demodulated audio channel from ka9q-radio
#	via pcmrecord and pipes it into Direwolf's stdin for AX.25/APRS decoding.
#
#	The channel is defined statically in ka9q-radio/radiod.conf (section [aprs])
#	rather than tuned dynamically, so:
#	  * No `tune` call is needed here - radiod owns the channel for its lifetime.
#	  * SSRC is auto-assigned by radiod as the frequency in kHz (RXFREQ / 1000),
#	    see radio.c. So 433.000 MHz -> SSRC 433000.
#	  * The [aprs] preset forces squelch fully open, so audio streams
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
BAUD_RATE="${BAUD_RATE:-1200}"
DIREWOLF_CONF="${DIREWOLF_CONF:-/direwolf.conf}"

# Static-channel SSRC convention used by radiod: frequency in kHz.
SSRC=$(($RXFREQ / 1000))

echo "Using SDR Centre Frequency: $RXFREQ Hz"
echo "Using PCM stream: $SDR_DEVICE"
echo "Direwolf: $SAMPRATE Hz, baud: $BAUD_RATE"

# Spread out startup so a bulk `up` doesn't slam pcmrecord during the CPU spike.
if [ "$STARTUP_JITTER" -gt 0 ]; then
    DELAY=$((RANDOM % STARTUP_JITTER))
    echo "Staggering startup: sleeping ${DELAY}s"
    sleep "$DELAY"
fi

echo "Starting receiver chain"
# pcmrecord streams raw 16-bit signed PCM on stdout; Direwolf reads it from
# stdin ( '-' ) at the matching sample rate. ADEVICE in direwolf.conf must be
# set to 'stdin null'.
pcmrecord --catmode --raw $SDR_DEVICE --timeout $PCM_TIMEOUT | \
  direwolf -c $DIREWOLF_CONF -r $SAMPRATE -B $BAUD_RATE -t 0 - &

echo "Started everything, waiting for any failed processes"

wait -n
echo "A process in the receive chain exited; shutting down so the container can restart."
pkill bash
