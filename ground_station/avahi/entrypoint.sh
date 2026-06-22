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
# the shared volume is writable by it. NOTE: this is a *persistent* named volume,
# so a stale pid file from the previous run survives a restart -- and since avahi
# runs as PID 1, its "is the old PID still alive?" check always says yes and it
# refuses to start ("Daemon already running on PID 1"). Clear the pid (and an
# orphaned socket) before launching.
mkdir -p /run/avahi-daemon
rm -f /run/avahi-daemon/pid /run/avahi-daemon/socket
chown avahi:avahi /run/avahi-daemon

exec avahi-daemon --no-chroot -f /etc/avahi/avahi-daemon.conf
