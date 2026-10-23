# Above 60 fps: why the port runs at 60 and what the options are

Lead-researcher assessment, 22 October 2026 (session 72839a). Read-only: OdysseyDecomp (1.0.0) and `open-ead/sead`, the upstream TotK and BotW ports, and our renderer. Nothing was built or run.

## Why 60

`runtime/system_services.c` sets `DM_PRESENT_HZ=60`, deliberately. Odyssey's game logic advances one fixed step per frame, so pacing it on a 120 Hz base would run the whole game at double speed. The display link (`CAMetalDisplayLink`) inherits that 60 Hz rate even though this Mac's panel is 120 Hz. Those are two separate settings: the link can run at 120 Hz while the game stays at 60 fps (`DM_PRESENT_LINK=2`).

## How SMO advances time: fixed steps, no delta time

These conclusions are inferred from the 1.0.0 decomp; they are unverified against 1.3.0.

- **Main loop.** `al::GameFrameworkNx` / `sead::GameFrameworkNx` is paced only by vblank (`mVBlankWaitInterval`). `calcFps()` measures and never drives a step. There is no delta-time path and no variable-frame-rate object like BotW/TotK's `ksys::VFR`.
- **State machines.** `al::NerveKeeper::update()` increments `mStep` once per frame. Timers are integer step tests (`isStep`, `isGreaterEqualStep`, `isIntervalStep`, `calcNerveRate`): at least ~949 call sites in 197 files.
- **Movement.** Velocities are per frame and gravity is added per frame, with ~460 velocity set/add/scale sites; damping per frame does not scale linearly with step size. Animation frame rates are frames per game frame. About 108 hand-written frame counters exist besides these.

**Upstream's TotK/BotW unlock does not transfer.** Those games ship a frame-ratio system that upstream fed the measured frame time (`games/totk/runtime/game_patches.c`, `games/botw/runtime/game_patches_botw.c`). Even then they needed a series of follow-up fixes:

- UI and fades ran 4× fast (`0d4a81c`);
- ragdoll substeps exploded (`0d4a81c`);
- hair and cloth ran fast (`fd4ddaa`, `290ec9f`);
- trampolines stopped bouncing above 100 fps (`27c3b43`);
- menu input repeat ran fast (`866beb9`);
- movies needed holding at 30 fps (`52d8d35`);
- a 6 % speed bias came from averaging (`d4ae4f9`).

SMO has no such ratio to hook.

## Options, ranked

| Option | Effort | Risk | Benefit |
| --- | --- | --- | --- |
| **(a) 120 Hz display link, game stays 60** (`DM_PRESENT_LINK=2`) | hours (env-var A/B; assigned to the Applier as task 0c) | low; watch present-cadence p95 for judder | cuts display latency (the link's ~3-period lead may drop from 3 × 16.7 toward 3 × 8.3 ms, unverified); no smoothness gain |
| **(b) MetalFX frame interpolation to 120** (`totk_mfx.m`, `MFX_HALF` = 60 game + 60 interpolated) | about 1–2 weeks of probe and dump work | medium: ghosting on Mario and moving objects, because motion vectors are derived from depth plus the camera, so object motion is not tracked; no gameplay risk | looks like 120; adds at least one game frame of latency (+16.7 ms) plus about 4 ms GPU per interpolated frame |
| **(c) Real 120 fps** (make the engine time-based) | months, realistically a decomp-based recompile | high: jump arcs and physics drift, long bug tail | true 120 Hz feel |

**Recommendation: do (a) now** (with the full-screen A/B). **Consider (b) later as an opt-in setting**, once SMO's own inputs are found. Today's Odyssey build uses TotK's hooks, which are wrong for SMO:

- the scene-colour copy point and canvas format;
- the depth target;
- the camera uniform block (slot and size) and its view-projection, near/far and FOV offsets;
- the UI shader hashes and the scene-end draw.

**Don't attempt (c).**

## Sources

- https://github.com/MonsterDruide1/OdysseyDecomp: `lib/al/Library/Nerve/NerveKeeper.cpp`, `NerveUtil.h`, `LiveActor.cpp`, `ActorAnimFunction.h`, `Framework/GameFrameworkNx.h`.
- https://github.com/open-ead/sead: `include/framework/nx/seadGameFrameworkNx.h`.
- Upstream MacNativeGames commits listed above; renderer `totk_mfx.m`, `totk_mfx_mode.h`, `nvn_obj.h` (camera block), `nvn_mtl.m` (`canvas_learn`, `mfx_take_camera`).
