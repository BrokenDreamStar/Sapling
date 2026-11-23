#!/usr/bin/env python3
"""Restart-persistence check: Carpet fake players are not saved across restarts."""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools", "parity"))
from parity import Rcon  # noqa: E402


def run(script, *args):
    subprocess.run(
        [os.path.join(ROOT, "tools", "parity", script), *args],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def refresh(script):
    run(script, "--stop")
    run(script, "--wait-ready")


def probe(script, rcon_port, label):
    refresh(script)
    with Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        rcon.command("/player PersistBot kill")
        rcon.command("/player PersistBot spawn")
        time.sleep(3)
        before = rcon.command("/list")
        rcon.command("/save-all")
        time.sleep(1)
    run(script, "--stop")
    run(script, "--wait-ready")
    with Rcon("127.0.0.1", rcon_port, "parity") as rcon:
        after = rcon.command("/list")
        entity = rcon.command("/execute if entity PersistBot")
    print(label + " online before restart: " + before)
    print(label + " online after restart : " + after)
    print(label + " entity after restart : " + entity)
    return after, entity


def main():
    ref = probe("run-carpet.sh", 25575, "ref     ")
    sap = probe("run-sapling.sh", 25576, "sapling ")
    ok = ref == sap
    print("restart persistence parity: " + ("MATCH" if ok else "DIFFERED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
