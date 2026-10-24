#!/usr/bin/env python3
"""scenecap — synchronise the capture kernel into every game tree.

    sync.py                     copy the scenecap-owned files into games/*/third_party/d2r_gfx/gfx/nvn/
    sync.py --check             report a game tree whose copy differs from tools/scenecap/ (exit 1)
    sync.py --game nier         only one game (repeatable)
    sync.py --list              print the files and the game trees this tool knows
    sync.py --root DIR          operate on another checkout (tests, staged trees)
    sync.py --quiet             only print drift, refusals and errors
    sync.py --force             overwrite a same-named file that does not look like a scenecap file

tools/scenecap/ is the only source tree.  The game build compiles every *.c under gfx/nvn/, so each game
needs its own copy of the capture kernel (and of the container writer it links against); keeping those
copies byte-identical is what this script is for.  It touches only the six files listed in FILES below and
never another file in the game tree.  A game that has no gfx/nvn/ directory (botw, today) is reported as
skipped rather than failed.

Exit codes: 0 in sync (or a clean --check), 1 drift or an error, 2 usage error.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))

# The capture kernel: the NVN -> Metal glue, the game-independent recorder and the container writer.
# (source subdirectory under tools/scenecap/, file name)
FILES = (
    ("format", "nvs_format.h"),
    ("format", "nvs_format.c"),
    ("capture", "scenecap_capture.h"),
    ("capture", "scenecap_capture.c"),
    ("capture", "scenecap_nvn.h"),
    ("capture", "scenecap_nvn.inc"),
)
DEST_DIR = "third_party/d2r_gfx/gfx/nvn"
# Every file above carries this word in its header; a same-named file without it belongs to the game.
MARKER = b"scenecap"


class SyncError(Exception):
    """A tree cannot be synchronised without clobbering something that is not ours."""


def source_path(root: str, subdir: str, name: str) -> str:
    return os.path.join(root, "tools", "scenecap", subdir, name)


def game_dirs(root: str) -> list[str]:
    """Every games/<name> with a gfx/nvn/ directory, plus the names that had no such directory."""
    games_root = os.path.join(root, "games")
    found: list[str] = []
    if not os.path.isdir(games_root):
        return found
    for name in sorted(os.listdir(games_root)):
        path = os.path.join(games_root, name)
        if not os.path.isdir(path):
            continue
        if os.path.isdir(os.path.join(path, DEST_DIR)):
            found.append(name)
    return found


def games_without_nvn(root: str) -> list[str]:
    games_root = os.path.join(root, "games")
    names: list[str] = []
    if not os.path.isdir(games_root):
        return names
    for name in sorted(os.listdir(games_root)):
        path = os.path.join(games_root, name)
        if os.path.isdir(path) and not os.path.isdir(os.path.join(path, DEST_DIR)):
            names.append(name)
    return names


def read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def looks_like_scenecap(path: str) -> bool:
    data = read_bytes(path)
    if data is None:
        return True  # absent: nothing to protect
    return MARKER in data[:4096].lower()


def check_game(root: str, game: str) -> list[str]:
    """Paths (repo relative) that are missing or differ from the source tree."""
    drift: list[str] = []
    for subdir, name in FILES:
        source = source_path(root, subdir, name)
        destination = os.path.join(root, "games", game, DEST_DIR, name)
        wanted = read_bytes(source)
        if wanted is None:
            raise SyncError(f"source file is missing: {os.path.relpath(source, root)}")
        have = read_bytes(destination)
        if have != wanted:
            drift.append(os.path.relpath(destination, root))
    return drift


def sync_game(root: str, game: str, *, force: bool) -> int:
    """Copy the files into one game; returns the number of files written."""
    destination_dir = os.path.join(root, "games", game, DEST_DIR)
    written = 0
    staged: list[tuple[str, str]] = []
    try:
        for subdir, name in FILES:
            source = source_path(root, subdir, name)
            destination = os.path.join(destination_dir, name)
            wanted = read_bytes(source)
            if wanted is None:
                raise SyncError(f"source file is missing: {os.path.relpath(source, root)}")
            if read_bytes(destination) == wanted:
                continue
            if not looks_like_scenecap(destination) and not force:
                raise SyncError(
                    f"{os.path.relpath(destination, root)} exists and does not look like a scenecap file; "
                    "refusing to overwrite it (use --force if that file really is a stale copy)"
                )
            handle, temporary = tempfile.mkstemp(prefix=".scenecap-sync-", dir=destination_dir)
            try:
                with os.fdopen(handle, "wb") as out:
                    out.write(wanted)
                os.chmod(temporary, 0o644)
            except BaseException:
                os.unlink(temporary)
                raise
            staged.append((temporary, destination))
        for temporary, destination in staged:
            os.replace(temporary, destination)
            written += 1
        staged.clear()
        return written
    finally:
        for temporary, _ in staged:  # only on an error path
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def selected_games(root: str, wanted: list[str] | None) -> list[str]:
    available = game_dirs(root)
    if not wanted:
        return available
    unknown = [name for name in wanted if name not in available]
    if unknown:
        raise SyncError(
            "no such game tree with " + DEST_DIR + ": " + ", ".join(unknown) + " (known: " + ", ".join(available) + ")"
        )
    return [name for name in available if name in wanted]


def command_list(root: str) -> int:
    print(f"source tree   {os.path.join(root, 'tools', 'scenecap')}")
    print(f"destination   games/<game>/{DEST_DIR}")
    print(f"files         {len(FILES)}")
    for subdir, name in FILES:
        print(f"  {subdir}/{name}")
    games = game_dirs(root)
    skipped = games_without_nvn(root)
    print(f"games         {len(games)}: {', '.join(games) if games else '-'}")
    if skipped:
        print(f"skipped       {len(skipped)} without {DEST_DIR}: {', '.join(skipped)}")
    return 0


def run(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"scenecap-sync: {root}: no such directory", file=sys.stderr)
        return 2
    if args.list:
        return command_list(root)

    games = selected_games(root, args.game)
    skipped = [] if args.game else [name for name in games_without_nvn(root)]
    verbose = not args.quiet

    if args.check:
        failures = 0
        for game in games:
            drift = check_game(root, game)
            if drift:
                failures += 1
                print(f"drift   games/{game}: {len(drift)} file(s) differ from tools/scenecap/", file=sys.stderr)
                for path in drift:
                    print(f"          {path}", file=sys.stderr)
            elif verbose:
                print(f"ok      games/{game}")
        for name in skipped:
            if verbose:
                print(f"skipped games/{name} (no {DEST_DIR})")
        if failures:
            print(
                f"scenecap-sync: {failures} game tree(s) out of sync; run tools/scenecap/sync.py to update them",
                file=sys.stderr,
            )
            return 1
        if verbose:
            print(f"all {len(games)} game tree(s) carry the same {len(FILES)} scenecap file(s)")
        return 0

    written_total = 0
    for game in games:
        try:
            written = sync_game(root, game, force=args.force)
        except SyncError as exc:
            print(f"scenecap-sync: games/{game}: {exc}", file=sys.stderr)
            return 1
        written_total += written
        if verbose:
            state = f"wrote {written} file(s)" if written else "already up to date"
            print(f"sync    games/{game}: {state}")
    for name in skipped:
        if verbose:
            print(f"skipped games/{name} (no {DEST_DIR})")
    if verbose:
        print(f"synced {written_total} file(s) across {len(games)} game tree(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync.py",
        description="Keep the scenecap capture kernel in sync with every game tree (tools/scenecap is the source).",
    )
    parser.add_argument("--check", action="store_true", help="only report game trees whose copy differs (exit 1)")
    parser.add_argument("--game", action="append", metavar="NAME", help="limit to one game (repeatable)")
    parser.add_argument("--root", default=_REPO_ROOT, help="checkout to operate on (default: this repository)")
    parser.add_argument("--list", action="store_true", help="print the synced files and game trees, change nothing")
    parser.add_argument("--force", action="store_true", help="overwrite a same-named file that is not marked scenecap")
    parser.add_argument("--quiet", action="store_true", help="print only drift, refusals and errors")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SyncError as exc:
        print(f"scenecap-sync: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
