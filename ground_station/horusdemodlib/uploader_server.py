#!/usr/bin/env python3
"""
Shared multi-connection uploader for horus_demod text output (edge-DSP model).

Each feeder runs the DSP natively at the edge:

    pcmrecord | horus_demod -q --stats=5 -g -m binary ... | (TCP to this server)

so the heavy FFT demod (~1% CPU/stream in C) is spread across cores by the OS,
and this single process only parses the resulting telemetry and uploads to
Sondehub. That makes it cheap enough to serve the whole fleet from one instance
(no per-stream Python interpreter, no central Python DSP).

Protocol, per connection:
  * First line: a JSON config object, e.g.
      {"freq_hz": 432700000, "freq_target_hz": 432700000, "baud_rate": 100}
  * Subsequent lines: raw horus_demod stdout - hex binary packets, "$$" UKHAS
    strings, or "{...}" modem-statistics JSON.

This mirrors horusdemodlib.uploader's line handling, but keeps one FSKDemodStats
tracker and frequency metadata per connection while sharing a single Sondehub
uploader thread and payload-ID list across all of them.
"""

import argparse
import codecs
import json
import logging
import select
import socket
import time
import traceback

import horusdemodlib
import horusdemodlib.payloads
from horusdemodlib.decoder import decode_packet, parse_ukhas_string
from horusdemodlib.payloads import init_custom_field_list, init_payload_id_list
from horusdemodlib.demodstats import FSKDemodStats
from horusdemodlib.sondehubamateur import SondehubAmateurUploader
from horusdemodlib.horusudp import send_payload_summary
from horusdemodlib.uploader import read_config

READ_ONLY = select.POLLIN | select.POLLPRI | select.POLLHUP | select.POLLERR
MIN_DOWNLOAD_INTERVAL = 30 * 60  # seconds between payload-list re-downloads


class Connection:
    """Per-feeder decode state: its own stats tracker + frequency metadata."""

    def __init__(self, sock, uploader, summary_port, logfile):
        self.sock = sock
        self.uploader = uploader
        self.summary_port = summary_port
        self.logfile = logfile
        self.buffer = b""
        self.configured = False
        self.freq_hz = None
        self.freq_target_hz = None
        self.baud_rate = None
        self.demod_stats = FSKDemodStats(peak_hold=True)
        self.next_download_time = time.time()

    def feed(self, data):
        """Buffer incoming bytes and dispatch each complete newline-terminated line."""
        self.buffer += data
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            text = line.decode(errors="replace").rstrip()
            if text:
                self._handle_line(text)

    def _handle_line(self, line):
        if not self.configured:
            self._configure(line)
            return
        if line.startswith("$$"):
            self._handle_ukhas(line)
        elif line.startswith("{"):
            # Modem statistics line - update SNR / frequency estimates.
            self.demod_stats.update(line)
        else:
            self._handle_binary(line)

    def _configure(self, line):
        cfg = json.loads(line)
        self.freq_hz = cfg.get("freq_hz")
        self.freq_target_hz = cfg.get("freq_target_hz")
        self.baud_rate = cfg.get("baud_rate")
        self.configured = True
        logging.info(
            "New decoder configured: freq_hz=%s baud=%s" % (self.freq_hz, self.baud_rate)
        )

    def _annotate(self, decoded):
        """Add SNR / centre-frequency / tone-spacing / baud metadata to a decode."""
        decoded["snr"] = self.demod_stats.snr
        if self.freq_hz:
            decoded["f_centre"] = int(self.demod_stats.fest_mean) + int(self.freq_hz)
        if self.demod_stats.fest_spacing > 0.0:
            decoded["tone_spacing"] = int(self.demod_stats.fest_spacing)
        if self.baud_rate:
            decoded["baud_rate"] = int(self.baud_rate)
        return decoded

    def _publish(self, decoded, logline):
        send_payload_summary(decoded, port=self.summary_port)
        self.uploader.add(decoded)
        if self.logfile:
            self.logfile.write(logline + "\n")
            self.logfile.flush()

    def _handle_ukhas(self, line):
        logging.info("Received raw RTTY packet: %s" % line)
        try:
            decoded = self._annotate(parse_ukhas_string(line))
            logline = "$$" + line.split("$")[-1]
            self._publish(decoded, logline)
            logging.info(
                "Decoded String (SNR %.1f dB): %s" % (self.demod_stats.snr, logline)
            )
        except Exception:
            logging.error("Decode Failed: %s" % traceback.format_exc())

    def _handle_binary(self, line):
        logging.info("Received raw binary packet: %s" % line)
        try:
            raw = codecs.decode(line, "hex")
        except Exception as e:
            logging.error("Error parsing line as hexadecimal (%s): %s" % (e, line))
            return
        try:
            decoded = decode_packet(raw)
            if decoded["callsign"] == "UNKNOWN_PAYLOAD_ID" and time.time() > self.next_download_time:
                logging.info("Observed unknown Payload ID, attempting to re-download lists.")
                horusdemodlib.payloads.HORUS_PAYLOAD_LIST = init_payload_id_list(filename="payload_id_list.txt")
                horusdemodlib.payloads.HORUS_CUSTOM_FIELDS = init_custom_field_list(filename="custom_field_list.json")
                self.next_download_time = time.time() + MIN_DOWNLOAD_INTERVAL
                decoded = decode_packet(raw)
            decoded = self._annotate(decoded)
            self._publish(decoded, decoded["ukhas_str"])
            logging.info(
                "Decoded Binary Packet (SNR %.1f dB): %s"
                % (self.demod_stats.snr, decoded["ukhas_str"])
            )
        except Exception as e:
            logging.error("Decode Failed: %s" % e)
            logging.debug("Traceback: %s" % traceback.format_exc())

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Shared Horus telemetry uploader - consumes horus_demod output over TCP."
    )
    parser.add_argument("-c", "--config", type=str, default="user.cfg", help="Configuration file to use.")
    parser.add_argument("--tcp-port", type=int, default=4921, help="TCP port to listen on.")
    parser.add_argument("--log", type=str, default="telemetry.log", help="Decoded telemetry log file ('none' to disable).")
    parser.add_argument("--noupload", action="store_true", default=False, help="Disable SondeHub upload.")
    parser.add_argument("-v", action="store_true", default=False, help="Verbose debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s",
        level=logging.DEBUG if args.v else logging.INFO,
    )
    logging.info("horusdemodlib v%s - shared uploader server" % horusdemodlib.__version__)

    user_config = read_config(args.config)
    if user_config is None:
        logging.critical("Could not load %s, exiting." % args.config)
        raise SystemExit(1)

    logfile = open(args.log, "a") if args.log != "none" else None

    horusdemodlib.payloads.HORUS_PAYLOAD_LIST = init_payload_id_list(filename="payload_id_list.txt")
    horusdemodlib.payloads.HORUS_CUSTOM_FIELDS = init_custom_field_list(filename="custom_field_list.json")
    logging.info("Payload list contains %d entries." % len(horusdemodlib.payloads.HORUS_PAYLOAD_LIST))

    if user_config["station_lat"] == 0.0 and user_config["station_lon"] == 0.0:
        user_pos = None
    else:
        user_pos = [user_config["station_lat"], user_config["station_lon"], 0.0]

    uploader = SondehubAmateurUploader(
        upload_rate=2,
        user_callsign=user_config["user_call"],
        user_position=user_pos,
        user_radio=user_config["radio_comment"],
        user_antenna=user_config["antenna_comment"],
        software_name="horusdemodlib",
        software_version=horusdemodlib.__version__,
        inhibit=args.noupload,
    )
    logging.info("Using User Callsign: %s" % user_config["user_call"])

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", args.tcp_port))
    server.listen(1000)
    logging.info("Listening for horus_demod feeds on TCP %d" % args.tcp_port)

    poller = select.poll()
    poller.register(server, READ_ONLY)
    fd_to_conn = {server.fileno(): server}

    try:
        while True:
            for fd, flag in poller.poll():
                p = fd_to_conn[fd]
                if p is server:
                    sock, addr = server.accept()
                    sock.setblocking(0)
                    fd_to_conn[sock.fileno()] = Connection(
                        sock, uploader, user_config["summary_port"], logfile
                    )
                    poller.register(sock, READ_ONLY)
                    logging.info("New feeder connected: %s" % (addr,))
                    continue

                data = b""
                try:
                    data = p.sock.recv(4096)
                except Exception:
                    pass

                if data:
                    try:
                        p.feed(data)
                    except Exception:
                        logging.error("Error handling feed: %s" % traceback.format_exc())
                else:
                    poller.unregister(p.sock)
                    p.close()
                    del fd_to_conn[fd]
                    logging.info("Feeder disconnected.")
    except KeyboardInterrupt:
        logging.info("Caught CTRL-C, exiting.")
    finally:
        uploader.close()


if __name__ == "__main__":
    main()
