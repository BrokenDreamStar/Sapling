#!/usr/bin/env python3
"""Compare the commandPlayer permission rule for the /player command.

Only a source with permission level 4 (the RCON console) is available, so the
interesting case is commandPlayer=false -> the whole /player command tree is
hidden/denied identically on both servers; commandPlayer=4 -> console may use it.
"""

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
import parity  # noqa: E402

CARPET_RCON = 25575
SAPLING_RCON = 25576
LEAVES_YML = os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "leaves.yml")


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def set_sapling_command_player(value):
    with open(LEAVES_YML, "r", encoding="utf-8") as handle:
        text = handle.read()
    if not re.search(r"command-player: [^\n]+", text):
        raise SystemExit("command-player not found in leaves.yml")
    text = re.sub(r"(command-player: )[^\n]+", lambda match: match.group(1) + value, text, count=1)
    with open(LEAVES_YML, "w", encoding="utf-8") as handle:
        handle.write(text)


def probe_commands(rcon_port, expect_spawn):
    results = []
    with parity.Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/player Steve kill")
        time.sleep(0.3)
        for command in ("/player", "/player Steve spawn", "/player Steve spawn in survival"):
            reply = rcon.command(command)
            results.append((command, parity.normalize(reply, command=command)))
            time.sleep(0.2)
        if expect_spawn:
            # fake-player spawning is asynchronous (profile resolution)
            deadline = time.monotonic() + 10.0
            seen = "Test failed"
            while time.monotonic() < deadline:
                seen = rcon.command("/execute if entity Steve")
                if "Test passed" in seen:
                    break
                time.sleep(0.1)
        else:
            time.sleep(1.5)
            seen = rcon.command("/execute if entity Steve")
        results.append(("/execute if entity Steve", seen))
        results.append(("/list", rcon.command("/list")))
        rcon.command("/player Steve kill")
    return results


def main():
    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")

    # phase 1: commandPlayer = false (command tree should be denied)
    set_sapling_command_player("'false'")
    run("run-carpet.sh", "--reset-world", "--wait-ready")
    run("run-sapling.sh", "--reset-world", "--wait-ready")
    with parity.Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet commandPlayer false:", repr(rcon.command("/carpet commandPlayer false")))
    ref_false = probe_commands(CARPET_RCON, expect_spawn=False)
    sap_false = probe_commands(SAPLING_RCON, expect_spawn=False)

    # phase 2: commandPlayer = 4 (console allowed)
    with parity.Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet commandPlayer 4:", repr(rcon.command("/carpet commandPlayer 4")))
    set_sapling_command_player("4")
    run("run-sapling.sh", "--stop")
    run("run-sapling.sh", "--wait-ready")
    ref_four = probe_commands(CARPET_RCON, expect_spawn=True)
    sap_four = probe_commands(SAPLING_RCON, expect_spawn=True)
    print("ref     commandPlayer=false:", ref_false)
    print("sapling commandPlayer=false:", sap_false)
    print("ref     commandPlayer=4    :", ref_four)
    print("sapling commandPlayer=4    :", sap_four)

    # restore
    with parity.Rcon("127.0.0.1", CARPET_RCON, "parity") as rcon:
        print("carpet commandPlayer ops:", repr(rcon.command("/carpet commandPlayer ops")))
    run("run-carpet.sh", "--stop")
    run("run-sapling.sh", "--stop")
    set_sapling_command_player("ops")
    run("run-sapling.sh", "--reset-world", "--wait-ready")
    run("run-sapling.sh", "--stop")

    ok = ref_false == sap_false and ref_four == sap_four
    if not ok:
        print("DIFF false:", [item for item in zip(ref_false, sap_false) if item[0] != item[1]])
        print("DIFF four :", [item for item in zip(ref_four, sap_four) if item[0] != item[1]])
    print("command permission parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
