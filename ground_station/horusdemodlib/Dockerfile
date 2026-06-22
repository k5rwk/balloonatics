# Overlay image: upstream horusdemodlib + the native C horus_demod.
#
# Upstream ships horus_demod as a Python console-script wrapping the C DSP via
# cffi, which costs a full ~50MB interpreter per decoder. We compile the
# equivalent native C binary (horusdemodlib src/horus_demod.c, ~3MB RSS) and
# overlay it at /usr/local/bin/horus_demod, shadowing the Python entry point.
# The receive pipeline calls `horus_demod` by name, so this is a drop-in.
#
# The binary is built FROM source in a stage that matches the runtime base
# (debian:bookworm-slim), so `docker compose build` produces a correct binary
# for whatever architecture is doing the build - no committed, arch-locked
# artifact. The source is fetched as a pinned tarball (same pattern the upstream
# Dockerfile uses for ka9q-radio), so this does not depend on any local clone.

# horusdemodlib source ref to compile the C demod from. Keep in sync with the
# version baked into HORUS_IMAGE below. Currently v0.6.1-1 (843ef98).
ARG HORUS_REF=843ef98f574ebffe4e62aed58345ff4eacb39a81
ARG HORUS_IMAGE=ghcr.io/projecthorus/horusdemodlib:latest

FROM debian:bookworm-slim AS demodbuild
ARG HORUS_REF
RUN apt-get -y update && apt-get -y install --no-install-recommends \
        cmake build-essential ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*
ADD https://github.com/projecthorus/horusdemodlib/archive/${HORUS_REF}.zip /tmp/src.zip
RUN unzip -q /tmp/src.zip -d /tmp && \
    cmake -S /tmp/horusdemodlib-${HORUS_REF} -B /build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build /build --target horus_demod -j"$(nproc)"

FROM ${HORUS_IMAGE}
COPY --from=demodbuild /build/src/horus_demod /usr/local/bin/horus_demod
