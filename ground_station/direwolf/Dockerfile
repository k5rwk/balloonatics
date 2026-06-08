# -------------------
# The build container (mirrors ka9q-radio/Dockerfile to get tune + pcmrecord)
# -------------------
FROM debian:trixie-slim AS build

# ka9q-radio git ref to use (keep in sync with ka9q-radio/Dockerfile)
ARG REF=44660845c60f440196d589a7d1ea92c71504682f

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget

RUN wget -O /etc/apt/sources.list.d/ka9q-sources.list http://www.ka9q.net/debian/ka9q-sources.list && \
    wget -O /usr/share/keyrings/ka9q-radio.asc http://www.ka9q.net/debian/ka9q-radio.asc

RUN apt-get update

RUN apt-get install -y --no-install-recommends \
    avahi-utils \
    build-essential \
    make \
    cmake \
    git \
    libairspy-dev \
    libairspyhf-dev \
    libbladerf-dev \
    libfobos-dev \
    libhydrasdr-dev \
    libasound2-dev \
    libavahi-client-dev \
    libbsd-dev \
    libfftw3-dev \
    libhackrf-dev \
    libogg-dev \
    libopus-dev \
    libsamplerate0-dev \
    libusb-1.0-0-dev \
    libusb-dev \
    libiniparser-dev \
    libncurses5-dev \
    portaudio19-dev \
    rsync \
    time \
    unzip \
    uuid-dev \
    && rm -rf /var/lib/apt/lists/*

ADD https://github.com/steve-m/librtlsdr/archive/refs/heads/master.zip /tmp/librtlsdr.zip
RUN unzip /tmp/librtlsdr.zip -d /tmp && \
  mkdir -p /tmp/librtlsdr-master/build && \
  cd /tmp/librtlsdr-master/build && \
  cmake -Wno-dev ../ && \
  make && \
  make install && \
  rm -rf /tmp/librtlsdr-master

ADD https://github.com/ka9q/ka9q-radio/archive/$REF.zip /tmp/ka9q-radio.zip
RUN unzip /tmp/ka9q-radio.zip -d /tmp && \
  mv /tmp/ka9q-radio-* /src && \
  rm /tmp/ka9q-radio.zip

WORKDIR /src
RUN make

# -------------------------
# Direwolf TNC + ka9q-radio audio plumbing
# -------------------------
FROM debian:trixie-slim

# Direwolf, avahi for resolving the *-pcm.local PCM multicast streams, plus the
# ka9q-radio runtime libs that tune/pcmrecord link against.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    direwolf \
    avahi-utils \
    libnss-mdns \
    libairspy0 \
    libairspyhf1 \
    libasound2 \
    libbsd0 \
    libfftw3-bin \
    libfftw3-single3 \
    libhackrf0 \
    libogg0 \
    libopus0 \
    libportaudio2 \
    libsamplerate0 \
    libiniparser4 \
    bc \
    procps \
    tini && \
    rm -rf /var/lib/apt/lists/*

# Copy just the ka9q-radio tools we need for audio routing.
COPY --from=build \
    /src/src/tune \
    /src/src/pcmrecord \
    /usr/local/bin/

# librtlsdr / any locally-built libs the tools depend on.
COPY --from=build /usr/local/lib /usr/local/lib
RUN ldconfig

# Audio routing helper (tune + pcmrecord -> direwolf) and config. Both are
# bind-mounted over at runtime via docker-compose so they can be edited
# without rebuilding.
COPY docker_ka9q_direwolf.sh /usr/bin/docker_ka9q_direwolf.sh
COPY direwolf.conf /direwolf.conf
RUN chmod +x /usr/bin/docker_ka9q_direwolf.sh

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "-c", "/usr/bin/docker_ka9q_direwolf.sh"]
