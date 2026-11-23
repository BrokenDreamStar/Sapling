#!/usr/bin/env python3
"""Compare the console log lines around fake-player join/leave/death events.

Both platforms use the same vanilla messages; Paper prefixes the login position
with the dimension id and orders the "joined the game"/"logged in" lines
differently, so the comparison is order-insensitive and normalises those bits.
"""

import collections
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
from parity import Rcon  # noqa: E402

LOG_PATHS = {
    25575: os.path.join(ROOT, ".carpet-ref", "parity", "carpet-server", "logs", "latest.log"),
    25576: os.path.join(ROOT, ".carpet-ref", "parity", "sapling-server", "logs", "latest.log"),
}
PREFIX_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] \[[^]]*\]: ")
KEEP_WORDS = ("LogBot", "joined the game", "left the game", "logged in with entity id")


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def normalise(lines):
    out = []
    for line in lines:
        line = PREFIX_RE.sub("", line.rstrip())
        if not any(word in line for word in KEEP_WORDS):
            continue
        line = line.replace("([minecraft:overworld]", "(")
        # entity ids depend on each server's internal counter; not meaningful here
        line = re.sub(r"with entity id \d+", "with entity id <id>", line)
        out.append(line)
    return collections.Counter(out)


def scenario(script, rcon_port, label):
    run(script, "--stop")
    run(script, "--reset-world", "--wait-ready")
    offset = os.path.getsize(LOG_PATHS[rcon_port])
    with Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/player LogBot kill")
        rcon.command("/player LogBot spawn in survival")
        time.sleep(3)
        rcon.command("/player LogBot kill")
        time.sleep(1)
        rcon.command("/player LogBot spawn in survival")
        time.sleep(3)
        rcon.command("/damage LogBot 100 minecraft:generic")
        time.sleep(1)
    with open(LOG_PATHS[rcon_port], "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        lines = handle.read().splitlines()
    counter = normalise(lines)
    print(label + " log messages:")
    for line, count in sorted(counter.items()):
        print("  x%d %s" % (count, line))
    return counter


def main():
    ref = scenario("run-carpet.sh", 25575, "ref     ")
    sap = scenario("run-sapling.sh", 25576, "sapling ")
    ok = ref == sap
    if not ok:
        print("DIFF ref-only  :", sorted((ref - sap).items()))
        print("DIFF sap-only  :", sorted((sap - ref).items()))
    print("log message parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
