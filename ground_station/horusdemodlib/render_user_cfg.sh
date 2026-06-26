#!/usr/bin/env bash
#
#	user.cfg renderer for horus-server
#
#	Fills the read-only user.cfg template (callsign / radio_comment /
#	antenna_comment) from the compose MYCALL / RADIO_COMMENT / ANTENNA_COMMENT
#	variables, then exec's the real server command passed as arguments.
#
#	Only the uploader (this horus-server process, via uploader_server.py or
#	horusdemodlib.server) reads these fields -- the 26 feeders mount the same
#	user.cfg but never read it, so they don't need rendering.
#
#	The template is bind-mounted read-only at /user.cfg, so the rendered copy is
#	written to /tmp (tmpfs, the service's working dir); point the server's -c at it.
#
set -euo pipefail

SRC="${USER_CFG_TEMPLATE:-/user.cfg}"
DST="${USER_CFG:-/tmp/user.cfg}"
MYCALL="${MYCALL:-CHANGEME}"
RADIO_COMMENT="${RADIO_COMMENT:-HorusDemodLib + ka9q-radio}"
ANTENNA_COMMENT="${ANTENNA_COMMENT:-1/4 wave magmount}"

# '|' delimiter: the comment values can contain '/' (e.g. "1/4 wave magmount").
sed -e "s|__MYCALL__|${MYCALL}|g" \
    -e "s|__RADIO_COMMENT__|${RADIO_COMMENT}|g" \
    -e "s|__ANTENNA_COMMENT__|${ANTENNA_COMMENT}|g" \
    "$SRC" > "$DST"

echo "Rendered $DST for callsign ${MYCALL} (radio: ${RADIO_COMMENT}, antenna: ${ANTENNA_COMMENT})"

exec "$@"