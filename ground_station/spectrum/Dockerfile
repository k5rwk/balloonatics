# Web spectrum analyzer for ka9q-radio.
#
# Two stages:
#   1. build  -- compile ONLY the `powers` utility from the ka9q-radio source.
#                powers links just -lbsd -lrt -lm -lpthread, so the build needs
#                very little and the runtime needs only libbsd0. (We still need
#                the iniparser/opus headers to *compile* the libradio objects,
#                even though powers doesn't link them.)
#   2. runtime -- debian-slim + python3 (stdlib only) + the powers binary, plus
#                libnss-mdns so the container can resolve radiod's `*.local`
#                status group through the shared, containerised avahi (same
#                mechanism the horus feeders use).

# -------------------
# The build container
# -------------------
FROM debian:trixie-slim AS build

# Pinned to the same ka9q-radio ref the SDR image builds from, so powers speaks
# the exact status/TLV protocol radiod is emitting.
ARG REF=69931b5fb4ab1476ede957951f56dbcce396303d

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    libbsd-dev \
    libfftw3-dev \
    libiniparser-dev \
    libopus-dev \
    unzip \
    && rm -rf /var/lib/apt/lists/*

ADD https://github.com/ka9q/ka9q-radio/archive/$REF.zip /tmp/ka9q-radio.zip
RUN unzip -q /tmp/ka9q-radio.zip -d /tmp && \
    mv /tmp/ka9q-radio-* /src && \
    rm /tmp/ka9q-radio.zip

WORKDIR /src/src
RUN make -j"$(nproc)" powers && \
    install -Dm755 powers /out/powers

# -------------------------
# The application container
# -------------------------
FROM debian:trixie-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 \
    libbsd0 \
    libnss-mdns \
    avahi-utils \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Resolve radiod's *.local status group via the shared avahi (libnss-mdns talks
# to /run/avahi-daemon/socket, bind-mounted from the avahi container).
RUN sed -i 's/^hosts:.*/hosts: files mdns4_minimal [NOTFOUND=return] dns mdns/' \
    /etc/nsswitch.conf

COPY --from=build /out/powers /usr/local/bin/powers
COPY server.py index.html /app/

WORKDIR /app
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/server.py"]
