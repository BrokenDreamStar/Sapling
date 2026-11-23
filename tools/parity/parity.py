#!/usr/bin/env python3
"""parity.py - compare Carpet reference server vs Sapling server over RCON.

The script connects to both servers with a minimal pure-stdlib RCON client,
runs the same command sequence on each, and diffs the outputs after
normalising volatile data (UUIDs, colour codes, decimal coordinates).

Typical use while porting Carpet's fake-player (bot) commands to Sapling:

    # 1. start both servers (each in its own terminal / background)
    tools/parity/run-carpet.sh --wait-ready
    tools/parity/run-sapling.sh --wait-ready

    # 2. compare a built-in bot case
    python3 tools/parity/parity.py --case bot_basic

    # 3. or drive your own command list (one command per line)
    python3 tools/parity/parity.py --commands my-case.txt --ticks 2

Exit codes: 0 = no differences, 1 = differences, 2 = setup/connection error.

Notes on determinism:
  * Built-in cases use vanilla /tick freeze + /tick step so that bot movement
    is measured in exact game ticks instead of wall-clock time.
  * Cases assume the flat world preset from server.properties
    (bedrock -64, dirt -63..-62, grass -61, surface at y=-60).
  * Only commands whose output is compared; /player output is expected to
    differ while Sapling's bot implementation is still being ported.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import select
import socket
import struct
import sys
import time

TICKS_PER_SECOND = 20.0

DEFAULT_REF_HOST = "127.0.0.1"
DEFAULT_REF_PORT = 25575
DEFAULT_SAPLING_HOST = "127.0.0.1"
DEFAULT_SAPLING_PORT = 25576
DEFAULT_PASSWORD = "parity"


# ---------------------------------------------------------------------------
# RCON client (Minecraft RCON protocol)
# ---------------------------------------------------------------------------
class RconError(RuntimeError):
    """Raised for every RCON transport/auth/protocol problem."""


class Rcon:
    """Minimal Minecraft RCON client using only the standard library."""

    LOGIN = 3
    COMMAND = 2
    RESPONSE = 0

    def __init__(self, host, port, password, timeout=10.0, label=None):
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = float(timeout)
        self.label = label or (str(host) + ":" + str(port))
        self.sock = None
        self.next_id = 1

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise RconError("cannot connect to " + self.label + ": " + str(exc)) from exc
        try:
            self.sock.settimeout(self.timeout)
            self._login()
        except Exception:
            self.close()
            raise
        return self

    def close(self):
        sock = self.sock
        self.sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    # -- packet plumbing ---------------------------------------------------
    def _take_id(self):
        request_id = self.next_id
        self.next_id += 1
        if self.next_id >= 2000000000:
            self.next_id = 1
        return request_id

    def _recv_exact(self, count):
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RconError("RCON connection closed by " + self.label)
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _send_packet(self, request_id, packet_type, body):
        payload = struct.pack("<ii", request_id, packet_type) + body + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)

    def _read_packet(self):
        raw_length = self._recv_exact(4)
        (length,) = struct.unpack("<i", raw_length)
        if length < 10 or length > 65536:
            raise RconError("invalid RCON packet length " + str(length) + " from " + self.label)
        payload = self._recv_exact(length)
        request_id, packet_type = struct.unpack("<ii", payload[:8])
        body = payload[8:-2]
        return request_id, packet_type, body

    def _login(self):
        request_id = self._take_id()
        self._send_packet(request_id, self.LOGIN, self.password.encode("utf-8"))
        response_id, packet_type, body = self._read_packet()
        if response_id != request_id:
            raise RconError(
                "RCON login failed for " + self.label + " (check rcon.password / --password)"
            )
        del packet_type, body

    # -- command -----------------------------------------------------------
    def command(self, command, idle=0.15):
        """Run one command and return the full (possibly multi-packet) response."""
        if self.sock is None:
            raise RconError("RCON connection to " + self.label + " is not open")
        request_id = self._take_id()
        self._send_packet(request_id, self.COMMAND, command.encode("utf-8"))
        chunks = []
        deadline = time.monotonic() + self.timeout
        while True:
            if chunks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                wait = min(remaining, idle)
            else:
                wait = self.timeout
            try:
                ready, _, _ = select.select([self.sock], [], [], wait)
            except OSError as exc:
                raise RconError("RCON select failed for " + self.label + ": " + str(exc)) from exc
            if not ready:
                if chunks:
                    break
                raise RconError(
                    "timed out waiting for an RCON response to /" + command + " from " + self.label
                )
            response_id, packet_type, body = self._read_packet()
            del response_id, packet_type
            chunks.append(body)
        text = b"".join(chunks).decode("utf-8", "replace")
        return text.replace("\x00", "").strip()

    def stop_server(self):
        """Best-effort /stop; the server usually closes the socket without replying."""
        try:
            self.command("stop", idle=0.2)
        except RconError:
            pass


# ---------------------------------------------------------------------------
# Output normalisation
# ---------------------------------------------------------------------------
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
HEX32_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")
DECIMAL_RE = re.compile(r"-?\d+\.\d+")
COLOR_RE = re.compile("\u00a7.")
TIMESTAMP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s*", re.MULTILINE)


def format_decimal(token, decimals):
    """Round a decimal token and strip trailing zeros (0.50 -> 0.5)."""
    try:
        value = round(float(token), decimals)
    except (TypeError, ValueError):
        return token
    if value == 0:
        value = 0.0
    text = format(value, "." + str(decimals) + "f")
    text = text.rstrip("0").rstrip(".")
    if text in ("", "-"):
        return "0"
    return text


HERE_RE = re.compile(r"<--\[HERE\]")


def _strip_error_context(text, command):
    """Drop the Brigadier context snippet from syntax-error output.

    Vanilla (Fabric/Carpet) appends the context to the message without a
    separator, Paper emits it as a separate line; the snippet is always a
    substring of the executed command, so strip suffixes that occur in it.
    """
    if not command or "<--[HERE]" not in text:
        return text
    needle = command[1:] if command.startswith("/") else command
    kept = []
    for line in text.split("\n"):
        if "<--[HERE]" not in line:
            kept.append(line)
            continue
        before = line.split("<--[HERE]")[0]
        stripped = before
        for size in range(len(before), 2, -1):
            candidate = before[-size:]
            if candidate in needle:
                stripped = before[:-size]
                break
        if stripped.endswith("..."):
            stripped = stripped[:-3]
        stripped = stripped.rstrip()
        if stripped:
            kept.append(stripped)
    return "\n".join(kept)


COMPOUND_RE = re.compile(r"\{([^{}]*)\}")


def _canonical_compound(match):
    """Order-insensitive rendering of a flat NBT compound.

    Paper and vanilla print compound tags in different key order; the data is
    identical, so sort the fields before diffing.
    """
    inner = match.group(1)
    parts = [part.strip() for part in inner.split(",")]
    if not parts or any(":" not in part for part in parts):
        return match.group(0)
    return "{" + ", ".join(sorted(parts)) + "}"


def normalize(text, decimals=3, mask_patterns=None, command=None):
    """Normalise command output so that only behavioural differences remain."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = COLOR_RE.sub("", text)
    text = TIMESTAMP_RE.sub("", text)
    text = UUID_RE.sub("<uuid>", text)
    text = HEX32_RE.sub("<hex32>", text)
    if mask_patterns:
        for pattern in mask_patterns:
            text = pattern.sub("<mask>", text)
    text = DECIMAL_RE.sub(lambda match: format_decimal(match.group(0), decimals), text)
    # Paper prints the Brigadier context snippet on a separate line while vanilla
    # appends it to the message; strip it so only the actual error is compared.
    text = _strip_error_context(text, command)
    text = COMPOUND_RE.sub(_canonical_compound, text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command steps and built-in cases
# ---------------------------------------------------------------------------
class Step:
    __slots__ = ("command", "wait_ticks", "note", "sync_expect", "sync_timeout", "game_ticks", "tolerance")

    def __init__(self, command, wait_ticks=0.0, note=None, sync_expect=None, sync_timeout=15.0, game_ticks=0.0, tolerance=None):
        self.command = command
        self.wait_ticks = float(wait_ticks)
        self.note = note
        self.sync_expect = sync_expect
        self.sync_timeout = float(sync_timeout)
        self.game_ticks = float(game_ticks)
        self.tolerance = tolerance

    def __repr__(self):
        if self.command is None:
            return "Step(wait " + str(self.wait_ticks) + " ticks)"
        return "Step(" + self.command + ")"


def cmd(command, wait_ticks=0.0, game_ticks=0.0, tolerance=None):
    return Step(command, wait_ticks, game_ticks=game_ticks, tolerance=tolerance)


def wait_game_ticks(ticks):
    """Sleep exactly *ticks* server game ticks (server must be running normally)."""
    return Step(None, game_ticks=ticks)


def wait_for(ticks):
    return Step(None, ticks)


def sync_until(command, expect, timeout=15.0):
    """Poll a probe command on each server until its output contains *expect*.

    Used as a synchronisation barrier (e.g. "wait until the fake player exists")
    so that spawn timing differences never show up as false parity diffs.
    """
    return Step(command, sync_expect=expect, sync_timeout=timeout)


def tick_step(ticks):
    """Advance the frozen tick loop by N ticks (with wall-clock slack so it settles)."""
    return Step("/tick step " + str(ticks), wait_ticks=float(ticks) + 12.0)


def case_smoke():
    """Vanilla sanity checks: chunk loading, blocks, entities, /list.

    MC 26.1 does not keep spawn chunks loaded for a player-less server, so the
    case force-loads chunk [0, 0] first. The flat world preset puts the surface
    at y=-60 (bedrock -64, dirt -63..-62, grass -61). Gamerule names are the
    26.1 snake_case names (advance_time, advance_weather, ...).
    """
    return [
        cmd("/forceload remove 0 0"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule advance_weather false"),
        cmd("/time set 1000"),
        cmd("/weather clear"),
        cmd("/kill @e[type=minecraft:marker,tag=parity_marker]"),
        cmd("/summon minecraft:marker 0.5 100 0.5 {Tags:[\"parity_marker\"]}"),
        cmd(
            "/execute in minecraft:overworld run data get entity "
            "@e[type=minecraft:marker,tag=parity_marker,limit=1] Pos"
        ),
        cmd("/execute if block 0 -64 0 minecraft:bedrock"),
        cmd("/execute if block 0 -61 0 minecraft:grass_block"),
        cmd("/execute if block 0 -60 0 minecraft:air"),
        cmd("/setblock 3 100 4 minecraft:stone"),
        cmd("/execute if block 3 100 4 minecraft:stone"),
        cmd("/seed"),
        cmd("/list"),
        cmd("/kill @e[type=minecraft:marker,tag=parity_marker]"),
        cmd("/setblock 3 100 4 minecraft:air"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_basic():
    """Spawn a fake player, look/move/stop, read its position and rotation."""
    return [
        cmd("/tick unfreeze"),
        cmd("/tick freeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/time set 1000"),
        cmd("/gamerule advance_time false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        tick_step(10),  # both servers reach full attack strength regardless of async spawn tick
        tick_step(2),
        cmd("/data get entity Steve Pos"),
        cmd("/data get entity Steve Rotation"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        tick_step(1),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve look north", wait_ticks=1),
        tick_step(1),
        cmd("/data get entity Steve Rotation"),
        cmd("/player Steve move forward"),
        tick_step(5),
        cmd("/player Steve stop", wait_ticks=1),
        tick_step(1),
        cmd("/data get entity Steve Pos"),
        cmd("/list"),
        cmd("/player Steve look east", wait_ticks=1),
        tick_step(1),
        cmd("/data get entity Steve Rotation"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        wait_for(2),
        cmd("/list"),
        cmd("/forceload remove 0 0"),
        cmd("/tick unfreeze"),
    ]


def case_bot_attack():
    """Spawn a fake player, make it attack a static NoAI zombie, compare damage.

    Note: on the Carpet 26.1 reference server fake players spawn in creative
    mode (playerGameType 1). A single /player attack still deals a small,
    deterministic amount of damage, which is exactly what the parity check
    records. A larger difference on the Sapling side is a real finding.
    """
    return [
        cmd("/tick unfreeze"),
        cmd("/tick freeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/time set 18000"),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/kill @e[type=minecraft:zombie,tag=parity_target]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        tick_step(2),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        tick_step(1),
        cmd(
            "/summon minecraft:zombie 1.5 -60 0.5 "
            "{NoAI:1b,PersistenceRequired:1b,Tags:[\"parity_target\"]}"
        ),
        tick_step(1),
        cmd("/player Steve look at 1.5 -60 0.5", wait_ticks=1),
        tick_step(1),
        # Attack strength depends on how many ticks the fake player has been alive.
        # Frozen /tick step semantics differ between Fabric (Carpet) and Paper, so
        # run the attack phase in real time: both servers then attack at full strength.
        cmd("/tick unfreeze"),
        wait_for(60),
        cmd(
            "/execute in minecraft:overworld run data get entity "
            "@e[type=minecraft:zombie,tag=parity_target,limit=1] Health"
        ),
        cmd("/player Steve attack"),
        wait_for(20),
        cmd(
            "/execute in minecraft:overworld run data get entity "
            "@e[type=minecraft:zombie,tag=parity_target,limit=1] Health"
        ),
        cmd("/player Steve attack continuous"),
        wait_for(40),
        cmd("/player Steve stop", wait_ticks=1),
        wait_for(5),
        cmd(
            "/execute in minecraft:overworld run data get entity "
            "@e[type=minecraft:zombie,tag=parity_target,limit=1] Health"
        ),
        cmd("/data get entity Steve Health"),
        cmd("/list"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        tick_step(2),
        cmd("/kill @e[type=minecraft:zombie,tag=parity_target]"),
        cmd("/forceload remove 0 0"),
        cmd("/tick unfreeze"),
    ]



def _bot_setup_steps():
    """Common unfrozen setup: bot spawned, empty inventory, standing at 0.5,-60,0.5."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(20),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
    ]


def _bot_teardown_steps():
    return [
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/kill @e[type=minecraft:snowball]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_inventory():
    """hotbar / swapHands / drop / dropStack inventory semantics."""
    return _bot_setup_steps() + [
        cmd("/data get entity Steve playerGameType"),
        cmd("/item replace entity Steve hotbar.0 with minecraft:stone 1"),
        cmd("/item replace entity Steve hotbar.1 with minecraft:dirt 64"),
        cmd("/item replace entity Steve hotbar.2 with minecraft:gold_ingot 3"),
        cmd("/item replace entity Steve weapon.offhand with minecraft:diamond 2"),
        wait_for(2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/data get entity Steve SelectedItemSlot"),
        cmd("/player Steve hotbar 2", wait_ticks=2),
        cmd("/data get entity Steve SelectedItemSlot"),
        cmd("/player Steve swapHands", wait_ticks=2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/player Steve drop", wait_ticks=2),
        cmd("/tp Steve 8 -60 8"),
        wait_for(4),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve hotbar 1", wait_ticks=2),
        cmd("/player Steve dropStack", wait_ticks=2),
        cmd("/tp Steve 8 -60 8"),
        wait_for(4),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve drop all", wait_ticks=2),
        cmd("/tp Steve 8 -60 8"),
        wait_for(4),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/kill @e[type=minecraft:item]"),
    ] + _bot_teardown_steps()


def case_bot_use():
    """use on block (lever toggle) and use item (snowball throw)."""
    return _bot_setup_steps() + [
        cmd("/setblock 2 -61 0 minecraft:stone"),
        cmd("/setblock 2 -60 0 minecraft:lever[face=floor]"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        cmd("/player Steve look at 2.5 -59.5 0.5", wait_ticks=2),
        cmd("/execute if block 2 -60 0 minecraft:lever[powered=true]"),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/execute if block 2 -60 0 minecraft:lever[powered=true]"),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/execute if block 2 -60 0 minecraft:lever[powered=true]"),
        cmd("/item replace entity Steve hotbar.0 with minecraft:snowball 3"),
        cmd("/player Steve hotbar 1", wait_ticks=2),
        cmd("/player Steve look up", wait_ticks=2),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/data get entity Steve Inventory[2]"),
        cmd("/data get entity Steve Inventory[3]"),
        cmd("/data get entity Steve Inventory[4]"),
        cmd("/execute if entity @e[type=minecraft:snowball]"),
        cmd("/setblock 2 -60 0 minecraft:air"),
        cmd("/setblock 2 -61 0 minecraft:air"),
    ] + _bot_teardown_steps()


def case_bot_mine():
    """block breaking: creative instant break and survival hand mining."""
    return _bot_setup_steps() + [
        cmd("/data get entity Steve playerGameType"),
        cmd("/setblock 1 -60 0 minecraft:stone"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        cmd("/player Steve look at 1.5 -59.5 0.5", wait_ticks=2),
        cmd("/player Steve attack", wait_ticks=2),
        cmd("/execute if block 1 -60 0 minecraft:air"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(20),
        cmd("/data get entity Steve playerGameType"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        cmd("/setblock 1 -60 0 minecraft:stone"),
        cmd("/player Steve look at 1.5 -59.5 0.5", wait_ticks=2),
        cmd("/player Steve attack continuous"),
        wait_for(220),
        cmd("/player Steve stop", wait_ticks=1),
        cmd("/execute if block 1 -60 0 minecraft:air"),
        cmd("/setblock 1 -60 0 minecraft:air"),
    ] + [
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_mount():
    """mount / dismount a boat and compare the resulting player position."""
    return _bot_setup_steps() + [
        cmd("/summon minecraft:oak_boat 2.5 -60 0.5"),
        cmd("/tp Steve 1.5 -60 0.5 0 0"),
        cmd("/player Steve look at 2.5 -60 0.5", wait_ticks=2),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve mount", wait_ticks=2),
        cmd("/data get entity Steve Pos"),
        cmd("/execute if entity @e[type=minecraft:oak_boat]"),
        cmd("/player Steve dismount", wait_ticks=2),
        cmd("/data get entity Steve Pos"),
        cmd("/kill @e[type=minecraft:oak_boat]"),
    ] + _bot_teardown_steps()


def case_bot_cmd_errors():
    """command tree / argument parsing and error-message parity."""
    return _bot_setup_steps() + [
        cmd("/player"),
        cmd("/player Steve"),
        cmd("/player Steve spawn in"),
        cmd("/player Steve spawn in creative extra"),
        cmd("/player Steve look"),
        cmd("/player Steve look at"),
        cmd("/player Steve turn sideways"),
        cmd("/player Steve move sideways"),
        cmd("/player Steve hotbar 0"),
        cmd("/player Steve hotbar 10"),
        cmd("/player Steve attack interval 0"),
        cmd("/player Steve attack sometimes"),
        cmd("/player Steve drop 41"),
        cmd("/player Steve mount maybe"),
        cmd("/player nobody kill"),
        cmd("/player Steve spawn"),
        cmd("/player Steve spawn"),
    ] + _bot_teardown_steps()



def case_bot_spawn():
    """spawn argument matrix: position/facing/dimension/gamemode variants."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/data get entity Steve playerGameType"),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/data get entity Steve playerGameType"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/player Steve spawn at 10 -60 10 facing 90 0 in minecraft:overworld in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(5),
        cmd("/data get entity Steve Pos"),
        cmd("/data get entity Steve Rotation"),
        cmd("/data get entity Steve playerGameType"),
        cmd("/player Steve spawn", wait_ticks=1),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        # Note: spawning outside the world (e.g. "at 0 -1000 0") is deliberately
        # not compared: the fake falls into the void and dies, and the exact
        # damage tick differs between the Paper and Fabric platforms.
        cmd("/player averyveryverylongname spawn", wait_ticks=1),
        cmd("/player Karl spawn in spectator", wait_ticks=2),
        sync_until("/execute if entity Karl", "Test passed"),
        wait_for(20),
        cmd("/data get entity Karl playerGameType"),
        cmd("/data get entity Karl Pos"),
        cmd("/list"),
        cmd("/player Karl kill", wait_ticks=1),
        sync_until("/execute unless entity Karl", "Test passed"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_fall():
    """fall damage parity for a survival fake player."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/data get entity Steve Health"),
        cmd("/tp Steve 0.5 -50 0.5"),
        wait_for(400),
        cmd("/data get entity Steve Health"),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_death():
    """death handling: list removal, item drops and no XP orbs."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(60),
        cmd("/item replace entity Steve hotbar.0 with minecraft:stone 5"),
        cmd("/item replace entity Steve hotbar.1 with minecraft:dirt 3"),
        wait_for(2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/damage Steve 100"),
        cmd("/damage Steve 100 minecraft:generic"),
        wait_for(40),
        cmd("/list"),
        cmd("/execute if entity Steve"),
        cmd("/data get entity Steve Health"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/execute if entity @e[type=minecraft:experience_orb]"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_playerdata():
    """player data is saved on disconnect and reloaded on the next spawn."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/clear Steve"),
        cmd("/item replace entity Steve hotbar.0 with minecraft:apple 7"),
        cmd("/item replace entity Steve hotbar.1 with minecraft:stick 4"),
        wait_for(2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        wait_for(10),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/data get entity Steve Inventory[1]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_chunkload():
    """a fake player keeps its surrounding chunks loaded (block access far away)."""
    return [
        cmd("/tick unfreeze"),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn at 1000 -60 1000", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(40),
        cmd("/execute if block 1000 -61 1000 minecraft:grass_block"),
        cmd("/execute if block 1003 -61 1000 minecraft:grass_block"),
        cmd("/execute if block 1000 -60 1000 minecraft:air"),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        # Paper keeps chunks loaded for delayChunkUnloadsBy (default 10 s) after a
        # player leaves; vanilla/Carpet unloads immediately, so wait past the delay.
        wait_for(260),
        cmd("/execute if block 1003 -61 1000 minecraft:grass_block"),
    ]


def case_bot_hurt():
    """spawn invulnerability and damageability of a fake player.

    Vanilla 26.1 gives players 60 ticks of spawn invulnerability; Carpet's fake
    players inherit it, so Sapling's must too.
    """
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(10),
        cmd("/data get entity Steve Health"),
        cmd("/damage Steve 1 minecraft:magic"),
        wait_for(5),
        cmd("/data get entity Steve Health"),
        wait_for(80),
        cmd("/damage Steve 1 minecraft:magic"),
        wait_for(5),
        cmd("/data get entity Steve Health"),
        cmd("/damage Steve 100 minecraft:generic"),
        wait_for(40),
        cmd("/list"),
        cmd("/execute if entity Steve"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_physics():
    """game-tick synchronised movement: walk/sneak/sprint distance and jump arc."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve move forward", game_ticks=40),
        cmd("/player Steve stop", wait_ticks=1),
        cmd("/data get entity Steve Pos"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/player Steve sneak", game_ticks=2),
        cmd("/player Steve move forward", game_ticks=40),
        cmd("/player Steve stop", wait_ticks=1),
        cmd("/player Steve unsneak"),
        cmd("/data get entity Steve Pos"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/player Steve sprint", game_ticks=2),
        cmd("/player Steve move forward", game_ticks=40),
        cmd("/player Steve stop", wait_ticks=1),
        cmd("/player Steve unsprint"),
        cmd("/data get entity Steve Pos"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(40),
        cmd("/player Steve jump", game_ticks=2),
        cmd("/data get entity Steve Pos"),
        wait_game_ticks(3),
        cmd("/data get entity Steve Pos"),
        wait_for(40),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]



def case_bot_use_entity():
    """use action on entities: dye a sheep and place an item in an item frame."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/kill @e[type=minecraft:sheep]"),
        cmd("/kill @e[type=minecraft:item_frame]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(60),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/summon minecraft:sheep 1.5 -60 0.5 {NoAI:1b,Color:0b,Silent:1b}"),
        cmd("/player Steve look at 1.5 -59.5 0.5", wait_ticks=2),
        cmd("/item replace entity Steve hotbar.0 with minecraft:red_dye 2"),
        cmd("/player Steve hotbar 1", wait_ticks=2),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/data get entity @e[type=minecraft:sheep,limit=1] Color"),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/summon minecraft:item_frame 1.5 -60.5 0.5 {Facing:3b,TileX:1,TileY:-61,TileZ:0}"),
        cmd("/player Steve look at 1.5 -60.5 0.5", wait_ticks=2),
        cmd("/item replace entity Steve hotbar.0 with minecraft:diamond 1"),
        cmd("/player Steve use", wait_ticks=2),
        cmd("/data get entity @e[type=minecraft:item_frame,limit=1] Item"),
        cmd("/tp Steve 8 -60 8"),
        wait_for(2),
        cmd("/kill @e[type=minecraft:sheep]"),
        cmd("/kill @e[type=minecraft:item_frame]"),
        wait_for(2),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_knockback():
    """attack knockback: normal and sprinting attacks against a NoAI zombie."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/time set midnight"),
        cmd("/kill @e[type=minecraft:zombie,tag=parity_target]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/summon minecraft:zombie 1.5 -60 0.5 {NoAI:1b,PersistenceRequired:1b,Tags:[\"parity_target\"]}"),
        cmd("/player Steve look at 1.5 -59.5 0.5", wait_ticks=2),
        cmd("/player Steve attack", game_ticks=2),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Motion"),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Pos"),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Health"),
        cmd("/kill @e[type=minecraft:zombie,tag=parity_target]"),
        wait_for(10),
        cmd("/summon minecraft:zombie 1.5 -60 0.5 {NoAI:1b,PersistenceRequired:1b,Tags:[\"parity_target\"]}"),
        cmd("/player Steve look at 1.5 -59.5 0.5", wait_ticks=2),
        cmd("/player Steve sprint", game_ticks=2),
        cmd("/player Steve attack", game_ticks=2),
        cmd("/player Steve unsprint"),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Motion"),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Pos"),
        cmd("/data get entity @e[type=minecraft:zombie,tag=parity_target,limit=1] Health"),
        cmd("/kill @e[type=minecraft:zombie,tag=parity_target]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_dimensions():
    """spawning fake players in the nether and the end."""
    return [
        cmd("/tick unfreeze"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn at 8 64 8 facing 0 0 in minecraft:the_nether", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(40),
        cmd("/data get entity Steve Dimension"),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/player Steve spawn at 0 64 0 facing 0 0 in minecraft:the_end", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(40),
        cmd("/data get entity Steve Dimension"),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/player Steve spawn at 0.5 -60 0.5", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(20),
        cmd("/data get entity Steve Dimension"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
    ]



def case_bot_mob_ai():
    """mob AI: a zombie targets, pushes and attacks a fake player (tick-synced).

    Attack cadence is identical, but the sampled health depends on the exact
    tick the query lands on, so per-step tolerances are used.
    """
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/gamerule natural_regeneration false"),
        cmd("/time set midnight"),
        cmd("/kill @e[type=minecraft:zombie]"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/data get entity Steve Health"),
        cmd("/data get entity Steve Pos"),
        cmd("/summon minecraft:zombie 3.5 -60 0.5"),
        wait_game_ticks(20),
        cmd("/data get entity Steve Health", tolerance=4.0),
        cmd("/data get entity Steve Pos", tolerance=2.0),
        cmd("/data get entity @e[type=minecraft:zombie,limit=1] Pos", tolerance=2.0),
        cmd("/kill @e[type=minecraft:zombie]"),
        wait_game_ticks(2),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]

def case_bot_ender_pearl():
    """ender pearl teleport works for fake players (FakeClientConnection isOpen).

    The pearl is thrown into a close wall so the landing point (and therefore
    the teleport destination) is stable across tick phases.
    """
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/setblock 2 -60 0 minecraft:stone"),
        cmd("/setblock 2 -59 0 minecraft:stone"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/item replace entity Steve hotbar.0 with minecraft:ender_pearl 1"),
        cmd("/player Steve hotbar 1", wait_ticks=2),
        cmd("/player Steve look at 2.5 -59.5 0.5", wait_ticks=2),
        cmd("/data get entity Steve Pos"),
        cmd("/player Steve use", game_ticks=8),
        cmd("/data get entity Steve Pos", tolerance=0.6),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/execute if entity @e[type=minecraft:ender_pearl]"),
        cmd("/setblock 2 -60 0 minecraft:air"),
        cmd("/setblock 2 -59 0 minecraft:air"),
        cmd("/kill @e[type=minecraft:ender_pearl]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]

def case_bot_use_continuous():
    """continuous use action throws every snowball in the stack."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/tp Steve 0.5 -60 0.5 0 0"),
        wait_for(2),
        cmd("/item replace entity Steve hotbar.0 with minecraft:snowball 3"),
        cmd("/player Steve hotbar 1", wait_ticks=2),
        cmd("/player Steve look up", wait_ticks=2),
        cmd("/player Steve use continuous"),
        wait_game_ticks(10),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/execute if entity @e[type=minecraft:snowball]"),
        cmd("/player Steve stop", wait_ticks=2),
        cmd("/data get entity Steve Inventory[0]"),
        cmd("/execute if entity @e[type=minecraft:snowball]"),
        cmd("/kill @e[type=minecraft:snowball]"),
        cmd("/kill @e[type=minecraft:item]"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


def case_bot_portal():
    """fake players travel through a nether portal."""
    return [
        cmd("/tick unfreeze"),
        cmd("/forceload add 0 0", wait_ticks=1),
        cmd("/gamerule advance_time false"),
        cmd("/gamerule spawn_mobs false"),
        cmd("/player Steve kill", wait_ticks=1),
        cmd("/player Steve spawn in survival", wait_ticks=2),
        sync_until("/execute if entity Steve", "Test passed"),
        wait_for(80),
        cmd("/clear Steve"),
        cmd("/tp Steve 3.5 -60 0.5 0 0", wait_ticks=2),
        cmd("/fill 3 -59 0 4 -57 0 minecraft:nether_portal[axis=x]"),
        cmd("/data get entity Steve Dimension"),
        wait_game_ticks(120),
        cmd("/data get entity Steve Dimension"),
        cmd("/data get entity Steve Pos", tolerance=0.5),
        wait_game_ticks(40),
        cmd("/data get entity Steve Dimension"),
        cmd("/data get entity Steve Pos", tolerance=0.5),
        cmd("/fill 3 -59 0 4 -57 0 minecraft:air"),
        cmd("/player Steve kill", wait_ticks=1),
        sync_until("/execute unless entity Steve", "Test passed"),
        cmd("/forceload remove 0 0"),
    ]


CASES = {
    "smoke": ("vanilla sanity checks (world/blocks/entities/list)", case_smoke),
    "bot_basic": ("spawn bot, look/move/stop, read position/rotation", case_bot_basic),
    "bot_attack": ("spawn bot, attack a static zombie, compare damage", case_bot_attack),
    "bot_inventory": ("hotbar/swapHands/drop/dropStack inventory semantics", case_bot_inventory),
    "bot_use": ("use on lever block and use snowball item", case_bot_use),
    "bot_mine": ("creative instant break and survival hand mining", case_bot_mine),
    "bot_mount": ("mount/dismount a boat", case_bot_mount),
    "bot_cmd_errors": ("command tree and argument error parity", case_bot_cmd_errors),
    "bot_spawn": ("spawn argument matrix: pos/facing/dimension/gamemode", case_bot_spawn),
    "bot_fall": ("fall damage for a survival fake player", case_bot_fall),
    "bot_death": ("death handling: list removal and item drops", case_bot_death),
    "bot_playerdata": ("player data saved on disconnect and reloaded on spawn", case_bot_playerdata),
    "bot_chunkload": ("fake player keeps surrounding chunks loaded", case_bot_chunkload),
    "bot_hurt": ("immediate damageability of a freshly spawned fake player", case_bot_hurt),
    "bot_physics": ("tick-synchronised walk/sneak/sprint/jump physics", case_bot_physics),
    "bot_use_entity": ("use action on sheep (dye) and item frame", case_bot_use_entity),
    "bot_knockback": ("attack knockback, normal and sprinting", case_bot_knockback),
    "bot_dimensions": ("spawn fake players in nether and end", case_bot_dimensions),
    "bot_mob_ai": ("zombie AI targets and attacks a fake player", case_bot_mob_ai),
    "bot_ender_pearl": ("ender pearl teleport for fake players", case_bot_ender_pearl),
    "bot_use_continuous": ("continuous use throws the whole snowball stack", case_bot_use_continuous),
    "bot_portal": ("nether portal travel for fake players", case_bot_portal),
}


# ---------------------------------------------------------------------------
# Commands file loading
# ---------------------------------------------------------------------------
WAIT_LINE_RE = re.compile(r"^(wait|sleep)\b[\s:]+([0-9]+(?:\.[0-9]+)?)$", re.IGNORECASE)


def load_commands_file(path, default_ticks):
    steps = []
    try:
        handle = open(path, "r", encoding="utf-8")
    except OSError as exc:
        raise ValueError("cannot read commands file " + path + ": " + str(exc)) from exc
    with handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            wait_match = WAIT_LINE_RE.match(line)
            if wait_match:
                steps.append(Step(None, float(wait_match.group(2)), note="line " + str(lineno)))
                continue
            if line.startswith("/"):
                line = line[1:]
            steps.append(Step(line, default_ticks, note="line " + str(lineno)))
    if not steps:
        raise ValueError("commands file is empty: " + path)
    return steps


def build_steps(args):
    if args.commands:
        default_ticks = 2.0 if args.ticks is None else float(args.ticks)
        return load_commands_file(args.commands, default_ticks)
    case_name = args.case_name or "smoke"
    if case_name not in CASES:
        raise ValueError(
            "unknown case '" + case_name + "' (available: " + ", ".join(sorted(CASES)) + ")"
        )
    steps = CASES[case_name][1]()
    if args.ticks is not None:
        ticks = float(args.ticks)
        for step in steps:
            if step.command is not None:
                step.wait_ticks = ticks
    return steps


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.lines = []
        self.records = []

    def emit(self, line=""):
        print(line)
        self.lines.append(line)


GAMETIME_RE = re.compile(r"(\d+)")
NUMBER_SPLIT_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def _gametime(rcon):
    output = rcon.command("/time query gametime")
    match = GAMETIME_RE.search(output)
    if not match:
        raise RconError("cannot parse /time query gametime output: " + output)
    return int(match.group(1))


def wait_for_game_ticks(rcon, ticks):
    """Block until the server's game clock advanced by *ticks* (running normally)."""
    start = _gametime(rcon)
    target = start + int(round(ticks))
    deadline = time.monotonic() + max(15.0, ticks / 20.0 * 2.0 + 15.0)
    while True:
        if _gametime(rcon) >= target:
            return
        if time.monotonic() > deadline:
            raise RconError(
                "timed out waiting for " + str(ticks) + " game ticks on " + rcon.label
            )
        time.sleep(0.005)


def outputs_within_tolerance(ref, sapling, tolerance):
    """Compare two outputs allowing absolute numeric differences (tick jitter)."""
    left = NUMBER_SPLIT_RE.split(ref)
    right = NUMBER_SPLIT_RE.split(sapling)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a == b:
            continue
        try:
            if abs(float(a) - float(b)) <= tolerance:
                continue
        except ValueError:
            return False
        return False
    return True


def sleep_ticks(ticks):
    if ticks and ticks > 0:
        time.sleep(ticks / TICKS_PER_SECOND)


def emit_output(report, name, text):
    lines = text.splitlines()
    if not lines:
        lines = [""]
    report.emit("      %-7s: %s" % (name, lines[0]))
    for line in lines[1:]:
        report.emit("      %-7s  %s" % ("", line))


def save_report(report, save_dir, meta):
    os.makedirs(save_dir, exist_ok=True)
    transcript_path = os.path.join(save_dir, "transcript.txt")
    with open(transcript_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(report.lines) + "\n")
    data = dict(meta)
    data["steps"] = report.records
    report_path = os.path.join(save_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return transcript_path, report_path


def stop_targets(report, target_specs):
    for name, rcon in target_specs:
        try:
            rcon.connect()
        except RconError as exc:
            report.emit("[" + name + "] not reachable: " + str(exc))
            rcon.close()
            continue
        report.emit("[" + name + "] connected to " + rcon.label + ", sending /stop")
        rcon.stop_server()
        rcon.close()
        report.emit("[" + name + "] stop sent")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="parity.py",
        description="Compare Carpet reference server vs Sapling server behaviour over RCON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Built-in cases:\n"
            "  smoke       vanilla sanity checks (world/blocks/entities/list)\n"
            "  bot_basic   spawn bot, look/move/stop, read position/rotation\n"
            "  bot_attack  spawn bot, attack a static zombie, compare damage\n"
            "\n"
            "Commands file format (one command per line, leading '/' optional):\n"
            "  # comment\n"
            "  /gamerule doDaylightCycle false\n"
            "  wait 20                 # wait 20 game ticks (1 s)\n"
            "  /player Steve spawn\n"
            "\n"
            "Examples:\n"
            "  parity.py --case smoke\n"
            "  parity.py --case bot_basic --ticks 2\n"
            "  parity.py --commands tools/parity/cases/my-case.txt\n"
            "  parity.py --only ref --case smoke     # single-server smoke test\n"
            "  parity.py --stop                      # stop both servers via RCON\n"
        ),
    )
    parser.add_argument("--case", dest="case_name", default=None, help="built-in case name (default: smoke)")
    parser.add_argument("--commands", default=None, help="commands file to run on both servers")
    parser.add_argument("--ticks", type=float, default=None,
                        help="default wait after each command, in game ticks (commands file default: 2)")
    parser.add_argument("--only", choices=["both", "ref", "sapling"], default="both",
                        help="which server(s) to run against (default: both)")
    parser.add_argument("--ref-host", default=DEFAULT_REF_HOST)
    parser.add_argument("--ref-port", type=int, default=DEFAULT_REF_PORT)
    parser.add_argument("--sapling-host", default=DEFAULT_SAPLING_HOST)
    parser.add_argument("--sapling-port", type=int, default=DEFAULT_SAPLING_PORT)
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="RCON password (default: parity)")
    parser.add_argument("--rcon-timeout", type=float, default=10.0)
    parser.add_argument("--decimals", type=int, default=3,
                        help="decimal places kept when normalising coordinates (default: 3)")
    parser.add_argument("--mask", action="append", default=[], metavar="REGEX",
                        help="extra regex masked to <mask> before comparison (repeatable)")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="absolute numeric tolerance for comparison (default 0 = exact)")
    parser.add_argument("--raw", action="store_true", help="compare raw RCON output without normalisation")
    parser.add_argument("--save-dir", default=None,
                        help="write transcript.txt and report.json into this directory")
    parser.add_argument("--stop", action="store_true", help="send /stop to the selected server(s) and exit")
    parser.add_argument("--list-cases", action="store_true", help="list built-in cases and exit")
    parser.add_argument("--verbose", action="store_true", help="print wait steps as well")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)
    report = Report()

    if args.list_cases:
        for name in sorted(CASES):
            report.emit("%-12s %s" % (name, CASES[name][0]))
        return 0

    try:
        mask_patterns = [re.compile(pattern) for pattern in args.mask]
    except re.error as exc:
        print("parity.py: invalid --mask regex: " + str(exc), file=sys.stderr)
        return 2

    target_specs = []
    if args.only in ("both", "ref"):
        target_specs.append(
            ("ref", Rcon(args.ref_host, args.ref_port, args.password, args.rcon_timeout))
        )
    if args.only in ("both", "sapling"):
        target_specs.append(
            ("sapling", Rcon(args.sapling_host, args.sapling_port, args.password, args.rcon_timeout))
        )

    if args.stop:
        return stop_targets(report, target_specs)

    try:
        steps = build_steps(args)
    except ValueError as exc:
        print("parity.py: " + str(exc), file=sys.stderr)
        return 2

    source = "commands file " + str(args.commands) if args.commands else "case '" + (args.case_name or "smoke") + "'"
    report.emit("parity.py - reference vs Sapling behaviour comparison")
    report.emit("  source      : " + source)
    report.emit("  targets     : " + ", ".join(name + "=" + rcon.label for name, rcon in target_specs))
    report.emit(
        "  normalise   : "
        + ("raw" if args.raw else "decimals=" + str(args.decimals) + ", uuid/colour masked")
    )
    report.emit("")

    connected = []
    compared = 0
    matched = 0
    differed = 0
    commands_run = 0
    try:
        for name, rcon in target_specs:
            rcon.connect()
            connected.append((name, rcon))
            report.emit("[" + name + "] connected to " + rcon.label)
        report.emit("")
        targets = dict(connected)
        names = [name for name, _ in connected]

        for index, step in enumerate(steps, 1):
            if step.command is None:
                if args.verbose:
                    report.emit(
                        "[%3d] wait %g ticks" % (index, step.game_ticks or step.wait_ticks)
                    )
                if step.game_ticks:
                    for name in names:
                        wait_for_game_ticks(targets[name], step.game_ticks)
                else:
                    sleep_ticks(step.wait_ticks)
                continue

            if step.sync_expect is not None:
                for name in names:
                    deadline = time.monotonic() + step.sync_timeout
                    while True:
                        output = targets[name].command(step.command)
                        if step.sync_expect in output:
                            break
                        if time.monotonic() >= deadline:
                            raise RconError(
                                "sync step timed out on " + name + ": " + step.command
                                + " (last output: " + output + ")"
                            )
                        time.sleep(0.05)
                if args.verbose:
                    report.emit("[%3d] sync %s" % (index, step.command))
                continue

            commands_run += 1
            outputs = {}
            for name in names:
                outputs[name] = targets[name].command(step.command)

            report.emit("[%3d] %s" % (index, step.command))
            for name in names:
                emit_output(report, name, outputs[name])

            normalized = {}
            match = None
            if len(names) == 2:
                compared += 1
                if args.raw:
                    normalized["ref"] = outputs["ref"]
                    normalized["sapling"] = outputs["sapling"]
                else:
                    for name in names:
                        normalized[name] = normalize(outputs[name], args.decimals, mask_patterns, step.command)
                if normalized["ref"] == normalized["sapling"]:
                    matched += 1
                    match = True
                    report.emit("      => MATCH")
                else:
                    tolerance = step.tolerance if step.tolerance is not None else args.tolerance
                    if tolerance > 0 and outputs_within_tolerance(
                        normalized["ref"], normalized["sapling"], tolerance
                    ):
                        matched += 1
                        match = True
                        report.emit("      => MATCH (within tolerance %g)" % tolerance)
                    else:
                        differed += 1
                    match = False
                    report.emit("      => DIFF")
                    diff = difflib.unified_diff(
                        normalized["ref"].splitlines(),
                        normalized["sapling"].splitlines(),
                        fromfile="ref",
                        tofile="sapling",
                        lineterm="",
                    )
                    for line in diff:
                        report.emit("      " + line)

            if step.game_ticks:
                for name in names:
                    wait_for_game_ticks(targets[name], step.game_ticks)
            report.records.append(
                {
                    "index": index,
                    "command": step.command,
                    "wait_ticks": step.wait_ticks,
                    "outputs": outputs,
                    "normalized": normalized,
                    "match": match,
                }
            )
            sleep_ticks(step.wait_ticks)

        report.emit("")
        if compared:
            report.emit(
                "Summary: %d compared, %d matched, %d differed"
                % (compared, matched, differed)
            )
        else:
            report.emit(
                "Summary: %d command(s) executed on %s" % (commands_run, ", ".join(names))
            )
        exit_code = 0 if differed == 0 else 1
    except RconError as exc:
        report.emit("")
        report.emit("ERROR: " + str(exc))
        report.emit(
            "hint: start the servers with tools/parity/run-carpet.sh --wait-ready "
            "and tools/parity/run-sapling.sh --wait-ready"
        )
        exit_code = 2
    finally:
        for _, rcon in connected:
            rcon.close()

    if args.save_dir:
        meta = {
            "source": source,
            "only": args.only,
            "ref": args.ref_host + ":" + str(args.ref_port),
            "sapling": args.sapling_host + ":" + str(args.sapling_port),
            "returncode": exit_code,
        }
        transcript_path, report_path = save_report(report, args.save_dir, meta)
        print("wrote " + transcript_path)
        print("wrote " + report_path)

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
