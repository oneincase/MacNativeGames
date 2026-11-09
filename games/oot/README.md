# The Legend of Zelda: Ocarina of Time (Switch 2)

Experimental, on the Tears of the Kingdom runtime (`games/totk`, `GAME_OOT` + `GAME_NS2`). Title `0400486024DEC000`, SDK 22.2.8, NVN2 231.40. No game content is included. `build_app.py` makes a Mac app from your own dump. See `docs/ns2-port.md`.

Works on a 16 GB Apple silicon Mac: title and menu text, a new game, the opening movie with subtitles, the first in-engine scene, writing a save, and loading a save into Kokiri Forest (Link's house and the forest view).

Known problems:

- Memory: **no leak in Kokiri Forest; the height of the sawtooth is the item, and the port's own copies were the part
  that could move.** Two 20 minute sessions (performance profile, autoload, idle): resident memory rose with the world
  then sawtoothed between about 9.4 and 12.1 GB (12,061 MB peak) while the wired memory the guard watches peaked at
  10,265 MB against the 12,288 MB limit (`wired_guard_check`, exit 87), and pools stayed at 2,874-3,031 of 16,384. 1,014
  MB of it were asset texture copies, all of them used within 90 submissions, so an eviction sweep found nothing idle to
  drop - until a budget was set: `TOTK_TEX_EVICT_MB` at 640 MB evicted 626 copies (442 MB) in one sweep, no copy was
  asked for again afterwards, and the wired peak fell to 9,656 MB. The budget is now the default for this game
  (`TOTK_TEX_EVICT=0` turns it off, `TOTK_TEX_EVICT_MB` changes it). Not measured: another area, a longer session than 20
  minutes, and a session that walks far enough to ask for an evicted copy again (the re-upload path exists and is used on
  the iPhone). The raw pool table also overflows by a few entries (a red line in the log; addresses in those pools are not
  reached).
- Frame rate: the performance profile is the default now, and held 57-60 fps through that 5 minute Kokiri Forest session
  (game time 1.000). Docked, on one 16 GB Mac, single runs: 60 fps held at the spawn point in Link's house and in the
  forest view after leaving it, about 53 fps facing the house's door, about 55 fps on the deck outside (frame p50 17.4 to
  17.8 ms, p95 about 21.7 ms). The rest of the world has not been measured in either profile. GPU 15 to 18 ms in the
  heavier docked views, about 16 ms in the performance profile. Spikes of 40 to 50 ms with low GPU time remain in some
  views (CPU side: the virtual cores' waits, measured 2026-11-09; the frame stays GPU bound otherwise). Pool tables are
  walked whole 3 times a frame (2 to 2.5 ms).
- The picture's pale haze: **fixed 2026-11-08; it was the upscaler bypass.** The bypass skipped agl's eight enhancement
  layers and also replaced the last kernel (`agl_ndsr_vr_light`) with a plain copy, which dropped that kernel's light and
  tone step: in Kokiri Forest a frame measured mean luminance 0.641, saturation 0.133 and contrast 0.120, against 0.421 /
  0.255 / 0.159 with the whole network. The last kernel's MSL reads one texture and writes one image (the pair the copy
  used), so it does not consume the layers' output: the default now skips the layers and runs that kernel as translated
  (0.423 / 0.256 / 0.164, within 1 % of the network), for 52 to 58 fps at the title against the network's 17.5 and no cost
  over the old copy in the forest (27 to 35 fps, GPU 29 to 31 ms). `TOTK_NDSR_BYPASS=0` runs the whole network, `=1` the
  old copy. The picture is softer than the console's only in what the eight layers added.
- Draws of shaders that read an image or buffer texture handle from memory are skipped. A texture copy that scales
  (2048x1152 to 1280x720 docked, 1920x1080 to 1280x720 in the performance profile below) is not implemented and is
  dropped, one red line; nothing was seen to be missing from the picture.

## Performance profile (2026-11-09)

The settings panel's display page has a row, "performance profile (from the next launch)": performance reports the Switch
handheld mode (`nn::oe` OperationMode 0, PerformanceMode Normal) so the game uses the configuration Nintendo made for the
handheld's lower clock, and quality is the game's own docked configuration. The game then renders a 1440x810 scene into a
1920x1080 output with 1000x1000 shadows, instead of 1536x864 into 2048x1152 with 1600x1600 shadows. **Performance is this
game's default** (user, 2026-11-09): it held 60 fps through a 5 minute session where docked gave 39-47.

At the Kokiri Forest spawn (same save, autoload, warm cache, one 25 s window each, 20 s of play before it): frame p50
25.3 ms docked against 17.3 (TOTK_OPERATION_MODE) and 18.3 (saved choice) ms, 39.1 against 54.6 and 52.5 fps, 1 % low 30.1
against 38.9 and 27.4, p99 31.8 against 24.3 and 29.6. The game time stayed with real time (1.000) and the session logged
57-60 fps. The picture and the HUD were checked in a screenshot (the panel shows the row, the HUD's output size reads
1920x1080), and walking with the keyboard still moves Link. The choice is saved in `graphics-profile.txt` next to
`controls.txt` and read at start, so it is the next launch's; `TOTK_OPERATION_MODE` decides first, and
`graphics-profile.txt` holding "docked" gives the quality profile. The panel's resolution row says "the game's own
(2048x1152)" instead of "1080p", which was wrong for this game.

The game has these two profiles and no other: a window of another size does not change its render sizes (a 1280x720 window
still rendered 1536x864 and 2048x1152, measured the same day), so a window size is not a performance switch; it only makes
the final present cheaper. Not yet checked: the gamepad in the handheld profile, whether the game records the mode in its
save, and a long session's heat.

For a Metal trace that names the frame's passes, `DM_NVN_ENC_LABEL=1` labels every render pass (`p07 1536x864 <shader key>
d29`) and, since 2026-11-09, the dispatch, copy and upload encoders (`d20`, `b18`, `u00`); off by default, diagnostic only.

Code-level budget (2026-11-09): `DM_SUBMIT_SPLIT=1` times the replay loop by kind of operation. Kokiri Forest, the
performance profile: **0.5 ms state / 5.5 ms draw / 0.07 copy / 0.18 other a frame**, with the pool table's 1.7 ms inside
the draw part (3 rebuilds a frame, because the game re-registers about 96 textures a frame - a cross-frame cache would not
hit). The frame itself is GPU bound (about 16 ms), so this is headroom, not frame rate. This game builds with `-O3 -flto`
(`NATIVE_OPT_OOT`, Makefile): two alternating pairs measured its draw encoding at 5.50 ms against `-O2`'s 5.90. The frame
is GPU bound, so that is CPU headroom, not frame rate; `NATIVE_OPT_OOT=-O2` restores the old build, and the other games
keep `-O2`. `TOTK_REUP_LOG=1` shows where the two texture
re-uploads a frame come from: one 1920x1080 RGBA8 texture of 8.8 MB and two 256x32 ones of 1 MB each, about 10.8 MB a
frame (650 MB/s at 60 fps); they are uploaded because the game changes them every frame, and sharing them instead needs a
zero-copy texture alias (the port's "shared storage" only shares one texture between guest serials). The present pass
tone-maps the picture at the drawable's size (3024x1700 for a 1512x850-point window), so presenting the 1920x1080 picture
1:1 would save pixels but soften the tone map - an option for the maintainer, not a default.

Output size (open): the game picks 2048x1152 itself (scene 1536x864, NDSR to 2048x1152), and the port has no switch for it -
`runtime_output_modes()` returns 0 for this game, unlike Bananza and Pokopia. What a patch would have to change, measured
with `TOTK_SIZE_PROBE`, `TOTK_SIZE_FORCE` and `TOTK_SIZE_TARGETS` (runtime/game_patches_ns2.c):
- the size the window crop reads is the heap object's +0xb4 (width) and +0xb8 (height), read at main+0x11e7b64 and handed to
  nvnWindowSetCrop at main+0x11e7b70 through the chain main+0x1203200 <- main+0xf28d04 <- main+0x9467d4 <- main+0x346c75c;
- forcing 1920x1080 there (TOTK_SIZE_FORCE) moves the crop and the output/upscale targets, but not the scene's render
  targets, which are created earlier: the passes stay 1536x864, so no GPU time is saved;
- three code paths that write the fields - the object copies at main+0x1fca4c4 and main+0x179e514 and the "take the larger"
  resize at main+0x488f64 - are not what builds the display object: overriding them (TOTK_SIZE_TARGETS) changes other
  objects (1920x1080 appears in the frame) but the crop stays 2048x1152;
- the module holds no copy of 2048x1152 (no 32/16-bit pair, no float pair, no movz #0x800 / #0x480 pair within 24
  instructions) and the preset list 1280x720 .. 3840x2160 at main+0x3f59550 has no adrp reference, so the value is computed;
  2048x1152 is 1920x1080 x 16/15, which is where to look next.

Memory and frame rate, what changed (`nvn_mtl.m`):

- Only pools the driver gave an address in go into the raw pool table (`mtl_pool_addressed`, after seedds/nxnative, MIT). A texture upload from any other pool borrows a temporary zero-copy buffer (`up_temp_src`); a pool under 64 MB borrowed more than 8 times stays resident instead. `TOTK_POOL_ADDR_ONLY=0` restores the old behaviour.
- The texture table walk asked `mtl_addr` whether a texture was in a pool, which also wired the pool: 3.1 GB, the 2,432 MB graphics pool among it, for a check. `pool_holds` answers without wiring. Resident pools: 5.1 GB to 2.0 GB.
- `DispatchComputeIndirect` no longer stops the CPU to read its counts: Metal reads them when the dispatch runs, and a GPU copy puts them in the driver segment for the shader. The wait was 74% of the replay thread's samples. Forest view: 148 ms to 40-46 ms a frame. `TOTK_DISPATCH_READBACK=1` restores it.
- The check that a texture's pool object still exists read its magic through the kernel, per ID and per table walk: 27% of the replay thread. `pool_obj_live` remembers the answer per pool until the frame changes or a pool starts or ends (`TOTK_POOL_LIVE_CACHE=0` asks every time). Forest view: 43.5 to 34.8 ms a frame, one run each.
- Draw passes are ordered by the game's own barriers (`barrier_game`, after seedds/nxnative, MIT), on for this game only: a pass's vertex work may overlap the previous pass's fragments. `TOTK_BARRIER_GAME=0` restores a full barrier for every pass. Forest view: 34.8 to about 30.8 ms (two runs). The picture was spot-checked only.
- Compute dispatches with no game barrier between them run side by side (after cheemu21/kid-with-sword-remake, MIT; `TOTK_DISPATCH_OVERLAP=0` orders them). About 6.5 to 3.9 ms at the start of a frame.
- Task and mesh draws stay in the open pass; each used to end it before and after itself. Passes a frame 147 to 98, and the replay thread encodes the passes (`TOTK_MESH_INPASS=0` restores it).
- A raw memory access remembers the pool table entry it chose last in its invocation (`patches/0011`, after seedds/nxnative 0f510fd5, MIT) instead of searching the table for each word. Mesh draws were about two thirds of the GPU time facing the door: 27.7 to 18.1 ms, with the changes above. A new shader cache name: the first run retranslates, for minutes.
- The words of one 64- or 128-bit raw access are read by one vector load (`patches/0012`, after seedds/nxnative 04419bc2, MIT): the pool table is searched once for the access, the load brings word 0 back and leaves words 1-3 in a private `gmem_vec` array the following accesses read, where each word did its own table check, delta and load. Same save at the autoload spawn (2048x1152 output from a 1536x864 scene), warm cache, one host binary and the same cache for both arms except the 51 variants that take the path (`scripts/ab_translator.py`, 3 repetitions, the arm order flipped each repetition); the pairs are the measurement, not the absolute numbers: GPU p50 31.5 to 25.0 ms, frame p50 31.4 to 25.1, p95 33.9 to 27.5, p99 37.0 to 29.9, 39.7 against 31.7 fps, and 43.2 against 37.6 fps over the first 25 s after the world starts. 51 of that cache's 998 variants take the path; the other 947 came out byte for byte the same (`scripts/diag/cache_compare.py`). Same present count, both arms, window shots (3024x1700): luminance 0.03 %, contrast 0.13 %, saturation 0.26 % apart - what still differs is the game's animation, the two runs being at different dialogue steps. The translation changed, so the cache name changed with it (chain v9 to v10) and again with the table below (v11): the first run after an update retranslates.
- The cached views of a texture as another type: swept in slices rather than whole every 64 submissions (frame p95 54-62 to about 21 ms at the spawn point), and dropped with their texture ID's view (they leaked 88 a frame). `TOTK_TYPE_VIEW_SWEEP=all` and `TOTK_TYPE_VIEW_DROP=0` restore the old behaviour.
- Shaders report which textures they really read (`dm_pool_mark`, a `seen` bitmap per table, in `Pools.cs` and `pool_seen` in `nvn_mtl.m`, after seedds/nxnative, MIT), so the table walk makes views only for those; the budget is now 32 MB a frame and 128 MB for what was read, up to a twentieth of the working set in all (`TOTK_POOL_CAP_MB`). Fragment shaders that do not discard or write depth run `[[early_fragment_tests]]`. Same 170 s forest run: Metal peak 12.5 to 10.7 GB, wired memory no longer over the limit, deck 19.6 to 18.4 ms.
- A direct-mapped cache in front of the map of texture type views (`g_tv_fast`, cleared at every removal; `TOTK_TYPE_VIEW_CACHE=0` turns it off). Deck, single runs: frame p50 18.5 to 17.4-17.8 ms.
- Tried after the vector load and not kept: the pool table's hint word (tried as a patch and withdrawn: word 6 of an entry names the entry of one 2 MiB chunk, as seedds/nxnative 04419bc2's host table has it) - with the vector load, one invocation's accesses already hit the last-entry word, so the one search per invocation it removes is inside the noise (80 variants reverted to their scalar helper, one binary, 3 repetitions: paired +0.05 fps / -0.04 ms, one sign each). Also withdrawn: a table range merge (a pool that starts where the previous range ends and has the same delta extends that entry) - it never triggers (0 of 3,537 ranges in a forest run), every pool having its own delta.
- The pool table holds 1 << 13 entries (`patches/0013`, the size that commit's host has) instead of 1 << 12: the forest reaches about 3,600 ranges in two minutes and went past 4,095 in the longer sessions, where the pools that no longer fit were dropped and their addresses read 0 (the red line is gone now). One extra step for a search that runs once per shader invocation: against the 4,096-entry table, one binary and one cache but for the 73 raw-path variants, paired -0.60 fps / -0.13 ms over 4 repetitions, i.e. in the noise; the picture at the same present counts (these shots 20 presents apart differ by 0.0215, the two tables by 0.0255) is within 1 % (luminance 0.32 %, contrast 0.71 %, saturation 0.24 %).
- Per-pass GPU time cannot be read on this OS: `DM_NVN_PASSGPU`'s stage-boundary timestamps come back quantized - a settled frame's passes add up to about 0.3 ms against a 25 ms command buffer span, with `DM_NVN_PASSGPU_SERIAL=1` as without it - so the table ranks what a pass draws, not what it costs. Which pass earns the 25 ms needs a GPU counter or Xcode's Metal System Trace.
- A shader cache rebuilt on this Mac costs 14 long frames of 15.1 s in all (the longest 4.2 s) over the first 90 s of play - 828 variants, 37.3 fps over that window against about 40 warm - when the compiled shader libraries are already in `build/shader-libs`; a chain the machine never ran also compiles those once (815 library loads took 34.4 s of the first 90 s in the cold run before this, 21.6 fps over that window).
- The application pool is 9 GB, as nxnative's profile, instead of the 10 GB other Switch 2 ports here take.

Game speed:

- The game stepped 1/60 s a frame whatever the frame took: slow below 60 fps, fast above it (125 fps at the title). Its step is worked out in one place, `main+0x6e6cc4`: 60 / the frame-rate controller's fps, added to a running count and handed to the game's time object (`main+0x3b4e300`), which derives seconds and its other values from it. That step is now the measured frame time, averaged over 5 frames as on Tears of the Kingdom, so game time adds up to real time at any frame rate, in even steps (`TOTK_REALTIME=0` restores it). The controller stays as shipped. The host holds 60 fps by its own clock (`TOTK_UNCAPPED_HZ`), ignoring the game's present interval.
- A `game speed` line every 30 s gives the ratio to real time: 0.97 to 1.00 in a 3 minute session at 45 to 60 fps. That is the host's count, not a comparison with the console: Link's speed has not been checked against one. It felt right to the player. `TOTK_GAME_SPEED` (0.25 to 2, default 1) scales game time without touching the frame cap.
- The same 3 s walk ends in about the same place at 51 fps and at a 30 fps cap (compared by eye from screenshots). Fractional steps have only been played for those minutes around the start of the game: cutscenes and combat are not checked.

Build the app, from the repository root, with your dump as a folder holding `base/exefs` and `base/romfs` (no update, no keys):

```sh
python3 build_app.py <dump folder> --work <work folder> -o "Ocarina of Time.app"
```

Use a work folder on an APFS disk with about 60 GB free. The first build takes about an hour on a 16 GB Mac, as it prepares the game's 42,302 shaders; the app is 29 GB. The first start compiles what Metal did not keep, about 30 s at the warm-up screen. System fonts are optional (`--shared-fonts`); no run of this game has asked for them. It is Mac only. The app uses this game's own shader translator (`games/oot/prebuilt/macos-arm64`, made with `tools/totk_xlate/publish_native.sh`): `games/totk/prebuilt` predates the patches this game needs, and changing it would change every other game.

For development, from `games/totk`, with the game imported as `extracted/oot`:

```sh
make -j6 binary-oot
TOTK_GPU_ALLOW_SUBMIT=1 TOTK_GPU_WIDEN=1 TOTK_GPU_BLOCKRT=1 TOTK_GPU_MIDCLEAR=1 build/totk-native-oot rewritten/oot boot
```

Shaders translate in the background by default for this game (`TOTK_ASYNC_SHADERS=0` waits for each one, and the first scene stays black for as long as they take). A development run needs a shader service built from the current patches: `tools/totk_xlate/publish_native.sh <dotnet> <absolute path>/games/totk/build/shader-service`.
