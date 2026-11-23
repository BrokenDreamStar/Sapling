"""Shared server-list-ping helpers for the parity checks."""

import json
import socket
import struct


def read_varint(sock):
    value = 0
    shift = 0
    while True:
        byte = sock.recv(1)
        if not byte:
            raise RuntimeError("connection closed while reading varint")
        value |= (byte[0] & 0x7F) << shift
        if not byte[0] & 0x80:
            return value
        shift += 7


def write_varint(value):
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes([byte | 0x80])
        else:
            out += bytes([byte])
            return out


def status(host, port, protocol=763):
    sock = socket.create_connection((host, port), timeout=5)
    try:
        host_bytes = host.encode()
        payload = (
            write_varint(0x00)
            + write_varint(protocol)
            + write_varint(len(host_bytes)) + host_bytes
            + struct.pack(">H", port)
            + write_varint(1)
        )
        sock.sendall(write_varint(len(payload)) + payload)
        sock.sendall(write_varint(1) + write_varint(0x00))
        read_varint(sock)
        read_varint(sock)
        length = read_varint(sock)
        data = b""
        while len(data) < length:
            data += sock.recv(length - len(data))
        return json.loads(data.decode("utf-8"))
    finally:
        sock.close()


def players_view(payload):
    players = payload.get("players", {})
    return {
        "online": players.get("online"),
        "max": players.get("max"),
        "sample": sorted(name.get("name", "") for name in players.get("sample", []) or []),
    }
