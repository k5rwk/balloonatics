# -------------------
# The build container
# -------------------
FROM debian:bookworm-slim AS build

# ka9q-radio git ref to use
#ARG REF=refs/heads/main
ARG REF=a81d2ee057080f544ccb7d3176eb4944f111f283

# Install necessary packages for building the application
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    avahi-utils \
    build-essential \
    make \
    cmake \
    git \
    libairspy-dev \
    libairspyhf-dev \
    libasound2-dev \
    libavahi-client-dev \
    libbsd-dev \
    libfftw3-dev \
    libhackrf-dev \
    libiniparser-dev \
    libncurses5-dev \
    libncursesw5-dev \
    libogg-dev \
    libopus-dev \
    libsamplerate0-dev \
    libusb-1.0-0-dev \
    libusb-dev \
    portaudio19-dev \
    rsync \
    time \
    unzip \
    uuid-dev \
    && rm -rf /var/lib/apt/lists/*

#RUN git clone https://github.com/steve-m/librtlsdr.git /tmp/librtlsdr && \
ADD https://github.com/steve-m/librtlsdr/archive/refs/heads/master.zip /tmp/librtlsdr.zip
RUN unzip /tmp/librtlsdr.zip -d /tmp && \
  mkdir -p /tmp/librtlsdr-master/build && \
  cd /tmp/librtlsdr-master/build && \
  cmake -Wno-dev ../ && \
  make && \
  make install && \
  ls -alR /usr/local/lib/* && \
  rm -rf /tmp/librtlsdr-master


# Download the source code into the build container
ADD https://github.com/ka9q/ka9q-radio/archive/$REF.zip /tmp/ka9q-radio.zip
RUN unzip /tmp/ka9q-radio.zip -d /tmp && \
  mv /tmp/ka9q-radio-* /src && \
  rm /tmp/ka9q-radio.zip

# Set the working directory
WORKDIR /src

# Build the application using the provided Makefile
RUN make -f Makefile.linux

# -------------------------
# The application container
# -------------------------
FROM debian:bookworm-slim

# Install necessary packages for running the application
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    avahi-utils \
    libairspy0 \
    libairspyhf1 \
    libasound2 \
    libbsd0 \
    libfftw3-bin \
    libfftw3-single3 \
    libhackrf0 \
    libiniparser1 \
    libncurses5 \
    libncursesw5 \
    libogg0 \
    libopus0 \
    libportaudio2 \
    libsamplerate0 \
    tini && \
    rm -rf /var/lib/apt/lists/*

# Copy the built binaries from the build stage
COPY --from=build \
    /src/aprs \
    /src/aprsfeed \
    /src/cwd \
    /src/packetd \
    /src/radiod \
    /usr/local/sbin/

# Copy radio drivers
COPY --from=build \
    /src/*.so \
    /usr/local/lib/ka9q-radio/

# Copy additional binaries from the build stage
COPY --from=build \
    /src/control \
    /src/jt-decoded \
    /src/metadump \
    /src/monitor \
    /src/pcmrecord \
    /src/pl \
    /src/powers \
    /src/tune \
    /src/wd-record \
    /usr/local/bin/

# Copy shared resources from the build stage
COPY --from=build /src/share /usr/local/share/ka9q-radio

# Copy rtl-sdr from the build container.
COPY --from=build /usr/local/lib /usr/local/lib
RUN ldconfig

RUN echo "[wenet] \n\
        # Raw quadrature (I/Q) output, usually for further demodulation \n\
        demod = linear \n\
        samprate = 921600 \n\
        low =  -460800 \n\
        high = +460800 \n\
        stereo = yes \n\
        pll = no \n\
        shift = 0 \n\
        envelope = no \n\
        conj = no \n\
        hang-time = 1.1 \n\
        recovery-rate = 20" >> /usr/local/share/ka9q-radio/presets.conf

# Create necessary directories
RUN mkdir -p /etc/radio /etc/fftw /var/lib/ka9q-radio

# Use tini as the init system to handle reaping zombie processes and signal forwarding
ENTRYPOINT ["/usr/bin/tini", "--"]

# Define the default command to run the application
CMD ["/usr/local/sbin/radiod", "/etc/radio/radiod.conf"]
