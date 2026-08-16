# -------------------
# The build container
# -------------------
FROM debian:trixie-slim AS build

# ka9q-radio git ref to use
#ARG REF=refs/heads/main
ARG REF=69931b5fb4ab1476ede957951f56dbcce396303d
# ARG REF=2d0959a4d9ad1ffc5b2ac5924264a16f1d73f6f9 # KE5GDB SDRplay AGC

# Add KA9Q repo
ADD --chmod=666 http://www.ka9q.net/debian/ka9q-sources.list /etc/apt/sources.list.d/ka9q-sources.list
ADD --chmod=666 http://www.ka9q.net/debian/ka9q-radio.asc /usr/share/keyrings/ka9q-radio.asc

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
    libbladerf-dev \
    libfobos-dev \
    libhydrasdr-dev \
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

# Install SDRplay API: headers for build-time plugin compilation; library and
# service daemon are copied to the runtime image via /usr/local/lib and /opt/sdrplay_api.
COPY SDRplay_RSP_API-Linux-3.15.2.run /tmp/
RUN ARCH=$(dpkg --print-architecture) && \
    sh /tmp/SDRplay_RSP_API-Linux-3.15.2.run --noexec --target /tmp/sdrplay_pkg && \
    cp /tmp/sdrplay_pkg/${ARCH}/libsdrplay_api.so.3.15 /usr/local/lib/ && \
    ln -sf libsdrplay_api.so.3.15 /usr/local/lib/libsdrplay_api.so.3 && \
    ln -sf libsdrplay_api.so.3 /usr/local/lib/libsdrplay_api.so && \
    cp /tmp/sdrplay_pkg/inc/sdrplay_api*.h /usr/local/include/ && \
    mkdir -p /opt/sdrplay_api && \
    cp /tmp/sdrplay_pkg/${ARCH}/sdrplay_apiService /opt/sdrplay_api/ && \
    chmod 755 /opt/sdrplay_api/sdrplay_apiService && \
    ldconfig && \
    rm -rf /tmp/sdrplay_pkg /tmp/SDRplay_RSP_API-Linux-3.15.2.run

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
# ADD https://github.com/ke5gdb/ka9q-radio/archive/$REF.zip /tmp/ka9q-radio.zip # KE5GDB SDRplay AGC
RUN unzip /tmp/ka9q-radio.zip -d /tmp && \
  mv /tmp/ka9q-radio-* /src && \
  rm /tmp/ka9q-radio.zip

# Set the working directory
WORKDIR /src

# Build the application using the provided Makefile. ENABLE_SDRPLAY defaults to
# 0 in the upstream Makefile ("proprietary API"); we always build it since the
# API files are present and the plugin is only activated at runtime via SDRPLAY=1.
RUN make -j$(nproc) ENABLE_SDRPLAY=1

# -------------------------
# The application container
# -------------------------
FROM debian:trixie-slim

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
    libiniparser4 \
    libncurses6 \
    libncursesw6 \
    libogg0 \
    libopus0 \
    libportaudio2 \
    libsamplerate0 \
    libudev1 \
    tini && \
    rm -rf /var/lib/apt/lists/*

# Copy the built binaries from the build stage
COPY --from=build \
    /src/src/aprs \
    /src/src/aprsfeed \
    /src/src/cwd \
    /src/src/packetd \
    /src/src/radiod \
    /usr/local/sbin/

# Copy radio drivers
COPY --from=build \
    /src/src/*.so \
    /usr/local/lib/ka9q-radio/

# Copy additional binaries from the build stage
COPY --from=build \
    /src/src/control \
    /src/src/jt-decoded \
    /src/src/metadump \
    /src/src/monitor \
    /src/src/pcmrecord \
    /src/src/ctcss \
    /src/src/powers \
    /src/src/tune \
    /src/src/wd-record \
    /usr/local/bin/

# Copy shared resources from the build stage
COPY --from=build /src/share /usr/local/share/ka9q-radio

# Copy rtl-sdr and SDRplay libraries from the build container.
COPY --from=build /usr/local/lib /usr/local/lib
# SDRplay API service daemon (library was carried above via /usr/local/lib)
COPY --from=build /opt/sdrplay_api/sdrplay_apiService /opt/sdrplay_api/sdrplay_apiService
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
RUN mkdir -p /etc/radio /etc/fftw /var/lib/ka9q-radio /opt/sdrplay_api

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# entrypoint.sh optionally starts sdrplay_apiService (when SDRPLAY=1) then execs tini
ENTRYPOINT ["/entrypoint.sh"]

# Define the default command to run the application
CMD ["/usr/local/sbin/radiod", "/etc/radio/radiod.conf"]
