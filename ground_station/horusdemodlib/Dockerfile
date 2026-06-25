# Overlay image for the Horus edge-DSP fleet: upstream horusdemodlib + the native
# C horus_demod + the shared uploader server.
#
# In the edge-DSP model each feeder demods natively in C (horus_demod, ~3 MB /
# ~1% CPU per stream) and ships only decoded text to one shared uploader_server.py.
# Nothing here runs the Python demod or a central Python audio server, so the image
# only needs to add two things on top of the upstream base:
#   * the native C horus_demod binary  (the feeders' DSP), and
#   * uploader_server.py               (the central decode + Sondehub process).
# Everything the uploader imports (decoder, demodstats, sondehubamateur, uploader,
# payloads, horusudp) and pcmrecord already live in the upstream base image.
#
# horus_demod is compiled FROM source in a stage matching the runtime base
# (debian:bookworm-slim) so `docker compose build` yields a binary for whatever
# architecture builds it - no committed, arch-locked artifact. The source is a
# pinned tarball (same pattern upstream uses for ka9q-radio), pinned to the SAME
# v0.6.1 release the base image is built from, so the C demod matches the lib.

ARG HORUS_REF=cd3bd498e764841fbf392e3d5c56f496ee542eb1   # v0.6.1
ARG HORUS_IMAGE=ghcr.io/projecthorus/horusdemodlib:latest

FROM debian:bookworm-slim AS demodbuild
ARG HORUS_REF
RUN apt-get -y update && apt-get -y install --no-install-recommends \
        cmake build-essential ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*
ADD https://github.com/projecthorus/horusdemodlib/archive/${HORUS_REF}.zip /tmp/src.zip
RUN unzip -q /tmp/src.zip -d /tmp && mv /tmp/horusdemodlib-${HORUS_REF} /src && \
    cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build --target horus_demod -j"$(nproc)"

FROM ${HORUS_IMAGE}
# Native C horus_demod - the feeders' DSP (replaces upstream's ~50 MB Python wrapper).
COPY --from=demodbuild /build/src/horus_demod /usr/local/bin/horus_demod
# Shared edge-DSP uploader: consumes native horus_demod text output (hex + stats)
# from many feeders over TCP and uploads to Sondehub. Locally maintained (a
# multi-connection refactor of horusdemodlib.uploader).
COPY uploader_server.py /usr/local/bin/uploader_server.py
