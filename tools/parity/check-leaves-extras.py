#!/usr/bin/env python3
"""Verify the restored Leaves fake-player extras (Sapling-only features).

Three independent extensions, all default-off so Carpet parity is unchanged:
  * cache-skin        - Mojang skin textures are fetched/cached and attached to
                        the fake player's GameProfile (visible in PlayerInfoUpdate)
  * skip-sleep-check  - fake players count as sleeping without a bed (fauxSleeping),
                        so they can complete the players_sleeping_percentage
  * spawn-phantom     - fake players are included in phantom spawning using their
                        own insomnia counter (reset after sleeping through a night)
"""

import os
import re
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
import parity  # noqa: E402
import mc_client  # noqa: E402

SAPLING_RUN = "run-sapling.sh"
RCON_PORT = 25576
GAME_PORT = 25566
LEAVES_YML = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "leaves.yml")
PAPER_WORLD = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "config", "paper-world-defaults.yml")
PROXY_JVM = (
    "-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=7897 "
    "-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=7897"
)


def run(script, *args, env_extra=None):
    env = os.environ.copy()
    env.update(env_extra or {})
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def set_yml(key, value):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    pattern = r"(\n    " + re.escape(key) + r": )[^\n]+"
    if re.search(pattern, text) is None:
        raise SystemExit("could not find " + key + " in leaves.yml")
    updated = re.sub(pattern, r"\g<1>" + value, text, count=1)
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(updated)


def set_yml_list(key, values):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    pattern = r"(\n    " + re.escape(key) + r":)(?:[^\n]*)(?:\n    - [^\n]*)*"
    if re.search(pattern, text) is None:
        raise SystemExit("could not find " + key + " in leaves.yml")
    replacement = "\n    " + key + ":"
    if values:
        replacement += "".join("\n    - " + value for value in values)
    else:
        replacement += " []"
    updated = re.sub(pattern, replacement, text, count=1)
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(updated)


def set_paper_values():
    with open(PAPER_WORLD, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r"player-insomnia-start-ticks: \d+", "player-insomnia-start-ticks: 100", text)
    text = re.sub(r"phantoms-spawn-attempt-min-seconds: \d+", "phantoms-spawn-attempt-min-seconds: 1", text)
    text = re.sub(r"phantoms-spawn-attempt-max-seconds: \d+", "phantoms-spawn-attempt-max-seconds: 1", text)
    text = re.sub(r"phantoms-only-attack-insomniacs: \w+", "phantoms-only-attack-insomniacs: false", text)
    with open(PAPER_WORLD, "w", encoding="utf-8") as handle:
        handle.write(text)


def restore_paper_values(backup):
    with open(PAPER_WORLD, "w", encoding="utf-8") as handle:
        handle.write(backup)


def read_varint(data, offset):
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7


def extract_texture(packets):
    import base64
    import json

    needle = b"\x08textures"
    for packet_id, data in packets:
        idx = data.find(needle)
        if idx < 0:
            continue
        value_length, offset = read_varint(data, idx + len(needle))
        raw = data[offset:offset + value_length]
        try:
            payload = json.loads(base64.b64decode(raw + b"=" * (-len(raw) % 4)))
        except Exception:
            continue
        url = payload.get("textures", {}).get("SKIN", {}).get("url")
        return packet_id, url
    return None, None


def capture_client(name="ExtrasWatch"):
    captured = []
    original = mc_client.MinecraftClient._recv_packet

    def logged(self):
        packet_id, data = original(self)
        if self.state == "play":
            captured.append((packet_id, data))
        return packet_id, data

    mc_client.MinecraftClient._recv_packet = logged
    client = mc_client.connect("127.0.0.1", GAME_PORT, name)
    return client, captured, original


def phase_limits():
    print("## bot limits (global + per-player fallback)")
    results = {}

    set_yml("limit", "1")
    set_yml("player-limit", "-1")
    run(SAPLING_RUN, "--stop")
    run(SAPLING_RUN, "--reset-world", "--wait-ready")
    with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
        rcon.command("/gamerule spawn_mobs false")
        first_reply = rcon.command("/player BotOne spawn")
        deadline = time.monotonic() + 15
        first_entity = ""
        while time.monotonic() < deadline:
            first_entity = rcon.command("/execute if entity BotOne")
            if "Test passed" in first_entity:
                break
            time.sleep(0.2)
        second_reply = rcon.command("/player BotTwo spawn")
        time.sleep(1.5)
        second_entity = rcon.command("/execute if entity BotTwo")
        listed = rcon.command("/list")
        rcon.command("/player BotOne kill")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "Test failed" in rcon.command("/execute unless entity BotOne"):
                break
            time.sleep(0.2)
        rcon.command("/player BotOne kill")
        time.sleep(0.5)
        retry_reply = rcon.command("/player BotTwo spawn")
        deadline = time.monotonic() + 15
        retry_entity = ""
        while time.monotonic() < deadline:
            retry_entity = rcon.command("/execute if entity BotTwo")
            if "Test passed" in retry_entity:
                break
            time.sleep(0.2)
        rcon.command("/player BotTwo kill")
        results["global"] = {
            "first": first_entity, "second_reply": second_reply, "second": second_entity,
            "list": listed, "retry_reply": retry_reply, "retry": retry_entity,
        }
    run(SAPLING_RUN, "--stop")
    print("  global limit:", results["global"])

    set_yml("limit", "10")
    set_yml("player-limit", "1")
    run(SAPLING_RUN, "--stop")
    run(SAPLING_RUN, "--reset-world", "--wait-ready")
    client, captured, original = capture_client("LimitOwner")
    try:
        client.wait_play(25)
        with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
            rcon.command("/gamerule spawn_mobs false")
            rcon.command("/op LimitOwner")
            time.sleep(1.0)
            client.send_chat_command("player LimBotA spawn")
            deadline = time.monotonic() + 15
            allowed = ""
            while time.monotonic() < deadline:
                allowed = rcon.command("/execute if entity LimBotA")
                if "Test passed" in allowed:
                    break
                time.sleep(0.2)
            client.send_chat_command("player LimBotB spawn")
            time.sleep(2.5)
            denied = rcon.command("/execute if entity LimBotB")
            console_reply = rcon.command("/player ConsoleBot spawn")
            deadline = time.monotonic() + 15
            console_entity = ""
            while time.monotonic() < deadline:
                console_entity = rcon.command("/execute if entity ConsoleBot")
                if "Test passed" in console_entity:
                    break
                time.sleep(0.2)
            for name in ("LimBotA", "LimBotB", "ConsoleBot"):
                rcon.command("/player " + name + " kill")
            rcon.command("/deop LimitOwner")
            results["player"] = {
                "allowed": allowed, "denied": denied,
                "console_reply": console_reply, "console_entity": console_entity,
            }
        run(SAPLING_RUN, "--stop")
    finally:
        mc_client.MinecraftClient._recv_packet = original
        client.close()
        run(SAPLING_RUN, "--stop")
    print("  player limit:", results["player"])

    set_yml("limit", "10")
    set_yml("player-limit", "-1")
    ok = (
        "Test passed" in results["global"]["first"]
        and "Bot number limit exceeded" in results["global"]["second_reply"]
        and "Test failed" in results["global"]["second"]
        and "Test passed" in results["global"]["retry"]
        and "Test passed" in results["player"]["allowed"]
        and "Test failed" in results["player"]["denied"]
        and "Test passed" in results["player"]["console_entity"]
    )
    return ok


def phase_skin():
    print("## cache-skin")
    results = {}
    for enabled in (True, False):
        set_yml("cache-skin", "true" if enabled else "false")
        run(SAPLING_RUN, "--stop")
        run(SAPLING_RUN, "--reset-world", "--wait-ready",
            env_extra={"PARITY_JAVA_OPTS": PROXY_JVM})
        client, captured, original = capture_client()
        try:
            client.wait_play(25)
            with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
                rcon.command("/gamerule spawn_mobs false")
                before = len(captured)
                rcon.command("/player Notch spawn")
                deadline = time.monotonic() + 25
                entity = ""
                while time.monotonic() < deadline:
                    entity = rcon.command("/execute if entity Notch")
                    if "Test passed" in entity:
                        break
                    time.sleep(0.2)
                time.sleep(2.0)
                packet_id, url = extract_texture(captured[before:])
                results[enabled] = {
                    "entity": entity,
                    "texture_packet": packet_id,
                    "skin_url": url,
                }
                rcon.command("/player Notch kill")
        finally:
            mc_client.MinecraftClient._recv_packet = original
            client.close()
            run(SAPLING_RUN, "--stop")
        print("  cache-skin=" + str(enabled) + ":", results[enabled])
    ok = (
        "Test passed" in results[True]["entity"]
        and results[True]["skin_url"]
        and "textures.minecraft.net" in results[True]["skin_url"]
        and results[False]["skin_url"] is None
    )
    return ok


def phase_sleep():
    print("## skip-sleep-check")
    results = {}
    for enabled in (True, False):
        set_yml("skip-sleep-check", "true" if enabled else "false")
        run(SAPLING_RUN, "--stop")
        run(SAPLING_RUN, "--reset-world", "--wait-ready")
        with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
            rcon.command("/gamerule players_sleeping_percentage 100")
            rcon.command("/gamerule spawn_mobs false")
            rcon.command("/gamerule natural_regeneration false")
            rcon.command("/gamerule advance_time true")
            rcon.command("/forceload add 0 0")
            rcon.command("/time set 18000")
            rcon.command("/player SleepA kill")
            rcon.command("/player SleepB kill")
            rcon.command("/player SleepA spawn in survival")
            rcon.command("/player SleepB spawn in survival")
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                a = rcon.command("/execute if entity SleepA")
                b = rcon.command("/execute if entity SleepB")
                if "Test passed" in a and "Test passed" in b:
                    break
                time.sleep(0.2)
            time.sleep(1.0)
            rcon.command("/tp SleepA 0.5 -60 0.5 0 0")
            rcon.command("/tp SleepB 3.5 -60 3.5 0 0")
            rcon.command("/setblock 1 -60 0 minecraft:red_bed[part=foot,facing=south]")
            rcon.command("/setblock 1 -60 1 minecraft:red_bed[part=head,facing=south]")
            rcon.command("/player SleepA look at 1.5 -59.5 0.5")
            time.sleep(0.3)
            rcon.command("/player SleepA use")
            occupied = rcon.command("/execute if block 1 -60 0 minecraft:red_bed[part=foot,facing=south,occupied=true]")
            start = time.monotonic()
            clock = 18000
            while time.monotonic() - start < 25:
                match = re.search(r"at (\d+) tick", rcon.command("/time query time"))
                clock = int(match.group(1)) if match else clock
                if clock > 23000:
                    break
                time.sleep(0.25)
            results[enabled] = {"occupied": occupied, "clock": clock, "elapsed": round(time.monotonic() - start, 1)}
            rcon.command("/player SleepA kill")
            rcon.command("/player SleepB kill")
            run(SAPLING_RUN, "--stop")
        print("  skip-sleep-check=" + str(enabled) + ":", results[enabled])
    ok = "Test passed" in results[True]["occupied"] and results[True]["clock"] > 23000 and results[False]["clock"] < 22000
    return ok


def phase_phantom():
    print("## spawn-phantom")
    with open(PAPER_WORLD, "r", encoding="utf-8") as handle:
        paper_backup = handle.read()
    set_paper_values()
    results = {}
    try:
        for enabled in (False, True):
            set_yml("spawn-phantom", "true" if enabled else "false")
            run(SAPLING_RUN, "--stop")
            run(SAPLING_RUN, "--reset-world", "--wait-ready")
            with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
                rcon.command("/gamerule spawn_mobs true")
                rcon.command("/gamerule advance_time false")
                rcon.command("/forceload add 0 0")
                rcon.command("/setblock 0 69 0 minecraft:stone")
                rcon.command("/time set 18000")
                rcon.command("/player PhantomBot kill")
                rcon.command("/player PhantomBot spawn in survival")
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if "Test passed" in rcon.command("/execute if entity PhantomBot"):
                        break
                    time.sleep(0.2)
                rcon.command("/tp PhantomBot 0.5 70 0.5 0 0")
                rcon.command("/time set 18000")
                start = time.monotonic()
                phantom = None
                for _ in range(40):
                    reply = rcon.command("/execute if entity @e[type=minecraft:phantom]")
                    if "Test passed" in reply:
                        phantom = (round(time.monotonic() - start, 1), reply)
                        break
                    time.sleep(0.5)
                results[enabled] = phantom
                rcon.command("/kill @e[type=minecraft:phantom]")
                rcon.command("/player PhantomBot kill")
                run(SAPLING_RUN, "--stop")
            print("  spawn-phantom=" + str(enabled) + ":", results[enabled])
    finally:
        restore_paper_values(paper_backup)
        run(SAPLING_RUN, "--stop")
    ok = results[False] is None and results[True] is not None
    return ok


def phase_names():
    print("## prefix/suffix + unable-fakeplayer-names")
    results = {}

    set_yml("prefix", '"Pre"')
    set_yml("suffix", '"Suf"')
    run(SAPLING_RUN, "--stop")
    run(SAPLING_RUN, "--reset-world", "--wait-ready")
    with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
        rcon.command("/gamerule spawn_mobs false")
        reply = rcon.command("/player NameTest spawn")
        deadline = time.monotonic() + 15
        full_entity = ""
        while time.monotonic() < deadline:
            full_entity = rcon.command("/execute if entity PreNameTestSuf")
            if "Test passed" in full_entity:
                break
            time.sleep(0.2)
        raw_entity = rcon.command("/execute if entity NameTest")
        listed = rcon.command("/list")
        # subsequent commands must use the final name
        move_reply = rcon.command("/player PreNameTestSuf move forward")
        rcon.command("/player PreNameTestSuf stop")
        rcon.command("/player PreNameTestSuf kill")
        results["prefix_suffix"] = {
            "spawn_reply": reply,
            "full_entity": full_entity,
            "raw_entity": raw_entity,
            "list_has_full": "PreNameTestSuf" in listed,
            "move_reply": move_reply,
        }
    run(SAPLING_RUN, "--stop")
    print("  prefix/suffix:", results["prefix_suffix"])

    set_yml("prefix", '""')
    set_yml("suffix", '""')
    set_yml_list("unable-fakeplayer-names", ["Blocked"])
    run(SAPLING_RUN, "--stop")
    run(SAPLING_RUN, "--reset-world", "--wait-ready")
    with parity.Rcon("127.0.0.1", RCON_PORT, "parity") as rcon:
        rcon.command("/gamerule spawn_mobs false")
        blocked_reply = rcon.command("/player Blocked spawn")
        blocked_entity = rcon.command("/execute if entity Blocked")
        allowed_reply = rcon.command("/player AllowedName spawn")
        deadline = time.monotonic() + 15
        allowed_entity = ""
        while time.monotonic() < deadline:
            allowed_entity = rcon.command("/execute if entity AllowedName")
            if "Test passed" in allowed_entity:
                break
            time.sleep(0.2)
        rcon.command("/player AllowedName kill")
        results["blacklist"] = {
            "blocked_reply": blocked_reply,
            "blocked_entity": blocked_entity,
            "allowed_reply": allowed_reply,
            "allowed_entity": allowed_entity,
        }
    run(SAPLING_RUN, "--stop")
    print("  blacklist:", results["blacklist"])

    set_yml_list("unable-fakeplayer-names", [])
    ok = (
        "Test passed" in results["prefix_suffix"]["full_entity"]
        and "Test failed" in results["prefix_suffix"]["raw_entity"]
        and results["prefix_suffix"]["list_has_full"]
        and "not allowed" in results["blacklist"]["blocked_reply"]
        and "Test failed" in results["blacklist"]["blocked_entity"]
        and "Test passed" in results["blacklist"]["allowed_entity"]
    )
    return ok


def main():
    if not os.path.exists(LEAVES_YML):
        raise SystemExit("sapling leaves.yml not found; start the parity sapling server once")
    run("run-carpet.sh", "--stop")
    # make sure the new config keys exist in leaves.yml (first start writes defaults)
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        config_text = handle.read()
    if "luckperms-meta-key:" not in config_text:
        run(SAPLING_RUN, "--reset-world", "--wait-ready")
        run(SAPLING_RUN, "--stop")
    ok = True
    for name, phase in (("names", phase_names), ("limits", phase_limits), ("skin", phase_skin),
                        ("sleep", phase_sleep), ("phantom", phase_phantom)):
        passed = phase()
        print(name + ": " + ("MATCH" if passed else "FAILED"))
        ok = ok and passed
    # restore defaults (all features off, Carpet parity)
    for key, value in (("cache-skin", "false"), ("skip-sleep-check", "false"), ("spawn-phantom", "false"),
                       ("prefix", '""'), ("suffix", '""'), ("limit", "10"), ("player-limit", "-1")):
        set_yml(key, value)
    set_yml_list("unable-fakeplayer-names", [])
    run(SAPLING_RUN, "--stop")
    print("leaves extras: " + ("MATCH" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
