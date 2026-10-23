#!/usr/bin/env python3
"""Bounded local Odyssey skip-to-gameplay probe with isolated saves and captures."""
from pathlib import Path
import os
import subprocess
import sys
import time

GAME = Path(__file__).resolve().parents[1]
BUILD = GAME / "Odyssey_build"
TEST = BUILD / "capture" / time.strftime("skip-%Y%m%d-%H%M%S")   # one folder per run: captures are evidence


def main():
    TEST.mkdir(parents=True, exist_ok=True)
    commands = TEST / "commands.txt"
    commands.write_text("")
    env = os.environ.copy()
    env.update(TOTK_GPU_ALLOW_SUBMIT="1", TOTK_GPU_WIDEN="1", TOTK_GPU_BLOCKRT="1",
               TOTK_INPUT_FILE=str(commands), TOTK_PROFILE_DIR=str(TEST / "profile"),   # empty profile: new game
               TOTK_SLOT_OWNER="probe", TOTK_SLOT_PURPOSE="bounded skip to gameplay")
    log = BUILD / (sys.argv[1] if len(sys.argv) > 1 else "boot-skip-to-gameplay.log")
    # A fresh profile shows Start / Start in Assist Mode after the title screen; the
    # 06:10 run pressed Plus there, then started the story with A at 57 s (story text
    # at 65 s, Bowser by 75 s). Confirm Start first and press Plus once Bowser is up.
    schedule = [(25, "key 38 100"), (33, "shot " + str(TEST / "mode-select.png")),
                (35, "key 38 100"), (52, "shot " + str(TEST / "cutscene.png")),
                (56, "key 36 100"), (58, "key 36 100"),
                (60, "shot " + str(TEST / "dialog.png")),
                (61, "key 38 100"), (70, "shot " + str(TEST / "after-skip-70.png")),
                (80, "shot " + str(TEST / "after-skip-80.png")),
                (84, "key 13 300"), (87, "shot " + str(TEST / "after-move.png")),
                (95, "shot " + str(TEST / "movie-95.png"))]
    with log.open("w") as output:
        process = subprocess.Popen([str(BUILD / "build/odyssey-native"), "rewritten/local", "boot"],
                                   cwd=BUILD, env=env, stdout=output, stderr=subprocess.STDOUT)
        start = time.monotonic()
        try:
            for when, command in schedule:
                delay = start + when - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if process.poll() is not None:
                    break
                with commands.open("a") as stream:
                    stream.write(command + "\n")
            if process.poll() is None:
                delay = start + 100 - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        finally:
            if process.poll() is None:
                with commands.open("a") as stream:
                    stream.write("close\n")
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
    print(f"Probe exit {process.returncode}; local log: {log}")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
