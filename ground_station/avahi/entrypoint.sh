#!/bin/sh
#
# Start a private system D-Bus (used by radiod's avahi-publish to register
# records) and then avahi-daemon in the foreground. Both sockets land in shared
# volumes -- /run/dbus and /run/avahi-daemon -- so the radiod and consumer
# containers can reach them.
set -e

# D-Bus needs a machine-id and a clean socket dir.
dbus-uuidgen --ensure=/etc/machine-id
mkdir -p /run/dbus
rm -f /run/dbus/pid /run/dbus/system_bus_socket
dbus-daemon --system --fork

# avahi drops to the 'avahi' user and writes its control socket here; make sure
# the shared volume is writable by it and free of a stale socket from last run.
mkdir -p /run/avahi-daemon
rm -f /run/avahi-daemon/socket
chown avahi:avahi /run/avahi-daemon

exec avahi-daemon --no-chroot -f /etc/avahi/avahi-daemon.conf
