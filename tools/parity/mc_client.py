#!/usr/bin/env python3
"""Minimal Minecraft 26.1 (protocol 775) client used by the shadow parity check.

It only implements what is needed to become a *real* connected player:
handshake -> login -> configuration -> play, keeping the connection alive and
answering pings/keep-alives. Every play packet other than keep-alive/ping/
disconnect is ignored. This lets the parity harness test Carpet's /player shadow
behaviour (which replaces a real player's connection with a fake one).
"""

import hashlib
import socket
import struct
import threading
import time
import uuid
import zlib

PROTOCOL_VERSION = 775

# state -> (clientbound ids, serverbound ids)
LOGIN_CB = {"disconnect": 0, "finished": 2, "compression": 3, "hello": 1}
LOGIN_SB = {"hello": 0, "acknowledged": 3}
CONFIG_CB = {
    "custom_payload": 1,
    "disconnect": 2,
    "finish": 3,
    "keep_alive": 4,
    "ping": 5,
    "registry_data": 7,
    "select_known_packs": 14,
    "code_of_conduct": 19,
}
CONFIG_SB = {
    "client_information": 0,
    "finish": 3,
    "keep_alive": 4,
    "pong": 5,
    "select_known_packs": 7,
    "accept_code_of_conduct": 9,
}
PLAY_CB = {"disconnect": 32, "login": 49, "keep_alive": 44, "ping": 61, "start_configuration": 118}
PLAY_SB = {"keep_alive": 28, "client_information": 14, "pong": 45}


def offline_uuid(name):
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))


def write_varint(value):
    value &= 0xFFFFFFFF
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes([byte | 0x80])
        else:
            out += bytes([byte])
            return out


def read_varint_from(buffer, offset=0):
    value = 0
    shift = 0
    while True:
        byte = buffer[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def write_string(text):
    data = text.encode("utf-8")
    return write_varint(len(data)) + data


def read_string(data, offset):
    length, offset = read_varint_from(data, offset)
    return data[offset:offset + length].decode("utf-8"), offset + length


class MinecraftClient:
    def __init__(self, host, port, name, timeout=30.0):
        self.host = host
        self.port = port
        self.name = name
        self.timeout = timeout
        self.sock = None
        self.compression = -1
        self.state = "offline"
        self.play_event = threading.Event()
        self.closed_event = threading.Event()
        self.disconnect_info = None
        self.error = None
        self.last_play_packet = None
        self.play_packets = 0
        self.packet_log = []
        self._thread = None
        self._lock = threading.Lock()

    # -- transport ---------------------------------------------------------
    def _recv_exact(self, count):
        chunks = []
        remaining = count
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ConnectionError("connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_varint(self):
        value = 0
        shift = 0
        while True:
            byte = self._recv_exact(1)[0]
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise ConnectionError("varint too long")

    def _send_packet(self, packet_id, body=b""):
        payload = write_varint(packet_id) + body
        if self.compression >= 0:
            if len(payload) >= self.compression:
                payload = write_varint(len(payload)) + zlib.compress(payload)
            else:
                payload = write_varint(0) + payload
        with self._lock:
            self.sock.sendall(write_varint(len(payload)) + payload)

    def _recv_packet(self):
        length = self._recv_varint()
        data = self._recv_exact(length)
        if self.compression >= 0:
            data_length, offset = read_varint_from(data)
            if data_length:
                data = zlib.decompress(data[offset:])
            else:
                data = data[offset:]
        packet_id, offset = read_varint_from(data)
        return packet_id, data[offset:]

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._thread = threading.Thread(target=self._run, name="mc-client-" + self.name, daemon=True)
        self._thread.start()
        return self

    def wait_play(self, timeout=None):
        if not self.play_event.wait(self.timeout if timeout is None else timeout):
            raise TimeoutError("client did not reach play state (state=%s error=%s)" % (self.state, self.error))

    def wait_closed(self, timeout=10.0):
        return self.closed_event.wait(timeout)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.closed_event.set()

    def _run(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            self._login_handshake()
            self._login_loop()
            self._configuration_loop()
            self.state = "play"
            self.play_event.set()
            self._play_loop()
        except Exception as exc:  # noqa: BLE001 - report any protocol problem
            self.error = repr(exc)
            self.disconnect_info = self.disconnect_info or self.error
        finally:
            self.closed_event.set()
            try:
                if self.sock is not None:
                    self.sock.close()
            except OSError:
                pass

    def _login_handshake(self):
        host = self.host.encode("utf-8")
        body = (
            write_varint(PROTOCOL_VERSION)
            + write_varint(len(host)) + host
            + struct.pack(">H", self.port)
            + write_varint(2)  # next state: login
        )
        self._send_packet(0x00, body)
        self._send_packet(
            LOGIN_SB["hello"], write_string(self.name) + offline_uuid(self.name).bytes
        )

    def _login_loop(self):
        while True:
            packet_id, data = self._recv_packet()
            if packet_id == LOGIN_CB["compression"]:
                threshold, _ = read_varint_from(data)
                self.compression = threshold
            elif packet_id == LOGIN_CB["finished"]:
                self.state = "configuration"
                self._send_packet(LOGIN_SB["acknowledged"])
                return
            elif packet_id == LOGIN_CB["disconnect"]:
                reason, _ = read_string(data, 0)
                self.disconnect_info = "login disconnect: " + reason
                raise ConnectionError(self.disconnect_info)
            elif packet_id == LOGIN_CB["hello"]:
                raise ConnectionError("server requested encryption (online-mode server?)")
            # ignore custom query / cookie requests
            else:
                continue

    def _send_client_information(self, packet_id):
        body = (
            write_string("en_us")
            + struct.pack(">b", 8)          # view distance
            + write_varint(0)               # chat visibility FULL
            + b"\x01"                       # chat colors
            + struct.pack(">B", 0x7F)       # model customisation (all layers)
            + write_varint(1)               # main hand RIGHT
            + b"\x00"                       # text filtering
            + b"\x01"                       # allows listing
            + write_varint(0)               # particle status ALL
        )
        self._send_packet(packet_id, body)

    def _configuration_loop(self):
        while True:
            packet_id, data = self._recv_packet()
            if packet_id == CONFIG_CB["select_known_packs"]:
                self._send_packet(CONFIG_SB["select_known_packs"], write_varint(0))
            elif packet_id == CONFIG_CB["finish"]:
                self._send_packet(CONFIG_SB["finish"])
                return
            elif packet_id == CONFIG_CB["keep_alive"]:
                if len(data) >= 8:
                    self._send_packet(CONFIG_SB["keep_alive"], data[:8])
            elif packet_id == CONFIG_CB["ping"]:
                if len(data) >= 4:
                    self._send_packet(CONFIG_SB["pong"], data[:4])
            elif packet_id == CONFIG_CB["code_of_conduct"]:
                self._send_packet(CONFIG_SB["accept_code_of_conduct"])
            elif packet_id == CONFIG_CB["disconnect"]:
                self.disconnect_info = "configuration disconnect: " + data.hex()
                raise ConnectionError(self.disconnect_info)
            # custom payload / registry data / tags etc: ignore

    def _play_loop(self):
        while True:
            try:
                packet_id, data = self._recv_packet()
            except (ConnectionError, OSError, struct.error, EOFError):
                return
            self.play_packets += 1
            self.last_play_packet = packet_id
            if len(self.packet_log) < 80:
                self.packet_log.append((packet_id, len(data), data[:24].hex()))
            if packet_id == PLAY_CB["keep_alive"]:
                self._send_packet(PLAY_SB["keep_alive"], data[:8])
            elif packet_id == PLAY_CB["ping"]:
                self._send_packet(PLAY_SB["pong"], data[:4])
            elif packet_id == PLAY_CB["login"]:
                self._send_client_information(PLAY_SB["client_information"])
            elif packet_id == PLAY_CB["disconnect"]:
                self.disconnect_info = "play disconnect: " + data.hex()
                return
            elif packet_id == PLAY_CB["start_configuration"]:
                self.disconnect_info = "server requested reconfiguration"
                return
            # ignore everything else


def connect(host, port, name, timeout=30.0):
    client = MinecraftClient(host, port, name, timeout).start()
    return client
