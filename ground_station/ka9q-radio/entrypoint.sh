#!/bin/sh
set -e

# When SDRPLAY=1, start the SDRplay API service daemon in the background before
# radiod opens the device. The library talks to this daemon over a Unix socket.
if [ "${SDRPLAY:-0}" = "1" ]; then
    echo "SDRPLAY=1: starting sdrplay_apiService..."
    /opt/sdrplay_api/sdrplay_apiService &
    sleep 1
fi

exec /usr/bin/tini -- "$@"
