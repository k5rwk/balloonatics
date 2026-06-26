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
MYCALL="${MYCALL:-CHANGEME}"

# Static-channel SSRC convention used by radiod: frequency in kHz.
SSRC=$(($RXFREQ / 1000))

# Compute the APRS-IS passcode for a callsign (Steve Dimse's well-known hash:
# seed 0x73e2, XOR each character pair as high/low byte, mask to 15 bits). The
# SSID (anything after '-') is stripped and the callsign upper-cased first.
aprs_passcode() {
    local call="${1%%-*}"
    call="${call^^}"
    local hash=$((16#73e2)) i ch
    for (( i = 0; i < ${#call}; i++ )); do
        printf -v ch '%d' "'${call:i:1}"
        if (( i % 2 == 0 )); then
            hash=$(( hash ^ (ch << 8) ))
        else
            hash=$(( hash ^ ch ))
        fi
    done
    echo $(( hash & 0x7fff ))
}

# Render the read-only template into a writable config with the callsign and
# passcode filled in. Direwolf has no CLI flags for MYCALL/IGLOGIN, so this has
# to go through the config file.
RENDERED_CONF=/tmp/direwolf.conf
BASECALL="${MYCALL%%-*}"
if [ -z "$MYCALL" ] || [ "$BASECALL" = "CHANGEME" ]; then
    # No real callsign: fill MYCALL but disable the APRS-IS login so direwolf
    # doesn't try (and fail) to authenticate with a bogus passcode.
    echo "MYCALL is unset/CHANGEME -- APRS-IS uplink (IGLOGIN) disabled."
    sed -e "s/__MYCALL__/${MYCALL:-N0CALL}/g" \
        -e "s/^IGLOGIN .*/#&  # disabled: set MYCALL in docker-compose.yml/" \
        "$DIREWOLF_CONF" > "$RENDERED_CONF"
else
    PASSCODE=$(aprs_passcode "$MYCALL")
    echo "Rendering direwolf.conf for $MYCALL (APRS-IS passcode $PASSCODE)"
    sed -e "s/__MYCALL__/${MYCALL}/g" \
        -e "s/__PASSCODE__/${PASSCODE}/g" \
        "$DIREWOLF_CONF" > "$RENDERED_CONF"
fi
DIREWOLF_CONF="$RENDERED_CONF"

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
