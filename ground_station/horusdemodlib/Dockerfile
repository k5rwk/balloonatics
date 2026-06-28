# Overlay image for the Horus audio-server fleet: upstream horusdemodlib with
# the tcp-demod branch's server.py and demod.py baked in.
#
# Each feeder (pcmrecord) ships raw IQ audio over TCP to a single shared
# horusdemodlib.server instance, which demodulates in-process (multiprocessing
# pool) and uploads to Sondehub. Everything horusdemodlib needs (decoder,
# demodstats, sondehubamateur, uploader, payloads, horusudp) and pcmrecord
# already live in the upstream base image.
#
# server.py and demod.py are overlaid from SERVER_REF (the tcp-demod branch)
# because that branch carries the non-blocking-accept fix and related changes
# that are not yet in the upstream release that :latest tracks.

ARG SERVER_REF=636c2dffc0e6a5760d882a4e917492a4c26de66d
ARG HORUS_IMAGE=ghcr.io/projecthorus/horusdemodlib:latest

FROM debian:bookworm-slim AS server-src
ARG SERVER_REF
RUN apt-get -y update && apt-get -y install --no-install-recommends \
        ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*
ADD https://github.com/projecthorus/horusdemodlib/archive/${SERVER_REF}.zip /tmp/server.zip
RUN unzip -q /tmp/server.zip -d /tmp && mv /tmp/horusdemodlib-${SERVER_REF} /src-server

FROM ${HORUS_IMAGE}
# Overlay server.py and demod.py from the tcp-demod branch.
COPY --from=server-src /src-server/horusdemodlib/demod.py /src-server/horusdemodlib/server.py \
     /usr/local/lib/python3.11/dist-packages/horusdemodlib/
