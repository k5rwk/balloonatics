# LAN-isolated Avahi/mDNS registry for the ka9q-radio stack.
#
# This container is the *only* place the ka9q stream names (rtl-sdr.local,
# aprs-*, wenet-*, horus-*) are registered. It runs in its own network
# namespace (the compose service has NO host networking), so it never announces
# anything on the LAN. radiod publishes here over D-Bus; each consumer's
# libnss-mdns resolves here over the unix socket -- both via shared *volumes*,
# which are network-namespace-agnostic. The host's own avahi-daemon is left
# untouched, so <host>.local keeps working on the LAN.
FROM debian:trixie-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      avahi-daemon \
      dbus \
    && rm -rf /var/lib/apt/lists/*

COPY avahi-daemon.conf /etc/avahi/avahi-daemon.conf
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
