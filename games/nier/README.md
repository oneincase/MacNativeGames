# NieR:Automata The End of YoRHa Edition (experimental)

Run the Nintendo Switch build of *NieR:Automata* natively on Apple Silicon, the way `games/totk` runs Tears of the
Kingdom. The port started from the Bayonetta 3 layer and keeps its own copies of the rewriter, loader, Horizon host
and NVN-on-Metal replay in this folder (`runtime/`, `third_party/d2r`, `third_party/d2r_gfx`, `prebuilt/`,
`scripts/`), so changes here never affect another game and nothing here reads `games/totk` or `games/by3`.

**State**: the app boots the game to the start menu and plays the opening (title menus, difficulty select, prologue
narration and the prologue cutscene) with picture, keyboard input, audio sessions and save storage at 30 fps.
Geometry-shader draws are skipped and one driver-constant word reads 0, so the picture is not yet the console's.
Details and open gaps: [docs/status-1.0.3.md](docs/status-1.0.3.md). How it was made: [CHANGELOG.md](CHANGELOG.md).

You must own the game: use your own NSP/NSZ and the `prod.keys` dumped from your own Switch. Nothing from the game is
in this repository; `NIER_build/` (the work folder) is ignored by git.

## Inputs

- Base `0100B8E016F76000` (v0) and update 1.0.3 `0100B8E016F76800` (v131072, delivered as NSZ; decompress with
  `nsz -D --keys prod.keys`).
- The code always comes from the update (`main` + `main.npdm` change; `rtld`, `sdk`, `subsdk0` are identical to base).
  Base RomFS is a plain filesystem; the update's is a sparse BKTR patch. Both are needed for the merged RomFS.

## Build and play

From `games/nier`, with the TotK build's Python environment (`PY`, it has `cryptography`, `lz4`, `zstandard`):

```sh
WORK=/path/to/NIER_build
PY=/Volumes/bit2t/switch/totk/TOTK_build/venv/bin/python
BASE="<base>.nsp" UPDATE="<update>.nsp" KEYS=prod.keys
make code     WORK=$WORK PY=$PY BASE="$BASE" UPDATE="$UPDATE" KEYS=$KEYS   # once per game version
make prepare  WORK=$WORK PY=$PY                                          # once per code change
make app      WORK=$WORK PY=$PY GAME="$BASE" KEYS=$KEYS                  # after any runtime change
open "NieR Automata.app"
```

What the steps do (each is idempotent and re-verifies its inputs against `assets/expected-v1.0.3.json`):

| Step | Input | Result |
| --- | --- | --- |
| `make code`: containers + ExeFS | base NSP, update NSP | `<work>/extracted/{base,update}/exefs`, verified FS header/hash table/blocks |
| merge RomFS | both NSPs | `<work>/extracted/v103/romfs` (660 files, 11.64 GB), all six IVFT levels verified |
| rewrite | update `main`, `sdk`, `subsdk0` | `<work>/rewritten/v103/*.patched.bin` + `.gates.tsv`, hashes pinned in `assets/expected-v1.0.3.json` |
| runtime tables | rewritten modules + RomFS | `<work>/build/runtime_tables.h`, `romfs_config.h`, `runtime-index.bin` |
| shader tools | `prebuilt/macos-arm64/totk_xlate`, pinned SPIRV-Cross | `<work>/build/shader-service/`, `<work>/build/spirv-cross/` |
| app | runtime + launcher + icon | `NieR Automata.app` (ad-hoc signed, icon from the game's control data) |

The app runs the game from the work folder named in its `Info.plist` (`NIERWorkFolder`); do not move or delete it.
Runtime output goes to `<work>/logs/nier.log` (previous run kept as `nier.previous.log`).

## Running without the bundle

```sh
make runtime WORK=$WORK
cd $WORK && TOTK_GPU_ALLOW_SUBMIT=1 ./build/nier-native rewritten/v103 boot
```

`TOTK_GPU_MIDCLEAR=1` is defaulted for this game inside the runtime; `TOTK_GPU_ALLOW_SUBMIT=1` (and usually
`TOTK_GPU_WIDEN=1 TOTK_GPU_BLOCKRT=1`) is what the launcher sets, because the shared replay layer keeps GPU submission
off for a game by default.

## Diagnostics

`TOTK_INPUT_FILE` takes a command file (the launcher creates `<work>/logs/input.txt`); append lines to it:

```
key 38 120                 # press Mac key code 38 (J = A) for 120 ms
shot <path>.png            # the next presented frame
wshot <path>.png           # the window as shown, HUD included
flag drvfill 0             # switch a graphics behaviour while the game runs
threads                    # every guest thread's wait state
close                      # quit
```

`TOTK_INPUT_SCRIPT="3:38:120,6:36:120"` presses keys at given seconds after start without a file.

## What the game uses

- NVN (SDK 12.3.9), Platinum's engine, Wwise-style audio through `audren:u`, movies as VP9 WebM, data in the merged
  RomFS, saves through the host save storage.
- Unimplemented Horizon services abort with their name when a guest asks for them (`sm: service "…" not implemented`);
  the SDK profanity filter (`nn::ngc`) is stubbed, and it is the only `fsp-srv` user seen so far.
