# Odyssey port playbook: outside techniques for later problems

Research collected on 22 October 2026 from emulator projects, recompilation ports, Super Mario Odyssey modding tools and Apple's Metal documentation. It is a reference for problems this port is likely to hit; status lives in [`README.md`](README.md), and work already done is in [`TECHNIQUES.md`](TECHNIQUES.md).

Each item gives what it is, why it matters here, and whether this tree already has it. Offsets come from community patch files and have not been checked against the local dump. AGENTS.md rule 5 applies before any game-specific use: verify the module, address and data layout locally first.

## 0. Already covered by this tree

These were flagged as top risks, but the code already handles them. Don't spend time on them.

- **Guest `svc`, `mrs cntpct_el0` and `mrs cntvct_el0`:** the rewriter routes them to host gates (`by3/third_party/d2r/patch_nso.py`, `runtime/gate.S`). This covers the 19.2 MHz vs 24 MHz timer difference.
- **Darwin's x18 platform register:** the guest's x18 is moved to a thread slot (`runtime/gate.S`).
- **Horizon TLS (`tpidrro_el0`):** host-managed in `runtime/loader.c`.
- **Renderer features already present:**
  - `MTLResidencySet`, `CAMetalDisplayLink` pacing and Metal 4 queues;
  - MetalFX spatial upscaling (`totk_mfx_up.m`) and MetalFX frame interpolation (`totk_mfx.m`);
  - D24S8 mapped to `Depth32Float_Stencil8`;
  - GPU frame capture (`gpucapture <path>` in the input file);
  - pipeline archive, shader prefetch and async translation (see TECHNIQUES.md).
- **Version identity:** the community's 1.3.0 main build ID (Calypso `client/config/VersionList.sym`) is `b424be150a8e7d78701cbe7a439d9ebf`, which matches our extracted update main NSO. 1.3.0 has a single main build, so 1.3.0 pchtxt files (`@nsobid-B424BE15…`) target our exact binary.

**GitHub survey (22 Oct):** [`GITHUB_SURVEY.md`](GITHUB_SURVEY.md) ranks code-cited techniques from smo-recomp, yuzu/citron, Ryujinx, DXVK, MoltenVK, UnleashedRecomp and plume against our three measured bottlenecks.

**In-game hitches and latency:** see [`HITCHES_AND_LATENCY.md`](HITCHES_AND_LATENCY.md). Most 73–87 ms hitches in play look like full-pipeline drains on memory-pool invalidate (writeback), and frames reach the screen about 63 ms after submission.

**Shader stutter:** see [`SHADER_STUTTER.md`](SHADER_STUTTER.md). In short: the static inventory had used BotW's control rewrite; in `'totk'` mode it matches 828 of 844 runtime shaders, so the whole game's shaders can be translated and compiled offline.

## 1. Highest value next

1. **Done (22 October): Metal Toolchain installed and shader libraries precompiled.**
   - The toolchain is installed (build 27A266a, `xcodebuild -showComponent MetalToolchain` reports *installed*). It had been blocked by Low Data Mode and a wedged `mobileassetd`.
   - `build/shader-libs/` held 30,090 `.metallib` files at 10:01 after the whole-game shader seed (SHADER_STUTTER.md).
   - Still needed: app packaging copies `build/shader-libs/` beside the app's game binary. That's the coordinator's job; confirm on the board.
2. **Done (verified 09:14): GPU fault reporting.**
   - Set `MTLCommandBufferDescriptor.errorOptions = MTLCommandBufferErrorOptionEncoderExecutionStatus` and log `MTLCommandBufferEncoderInfoErrorKey` from the error's `userInfo`. This names the encoder that faulted, which is the best lead on the earlier IOGPUFamily panics.
   - Debug runs:
     - `MTL_DEBUG_LAYER=1`;
     - `MTL_SHADER_VALIDATION=1` with `MTL_SHADER_VALIDATION_GLOBAL_MEMORY=1` and `MTL_SHADER_VALIDATION_REPORT_TO_STDERR=1`;
     - scope these to suspect pipelines with `MTL_SHADER_VALIDATION_ENABLE_PIPELINES` (all verified in `man MetalValidation`).
   - Clamp guest-derived indices and strides in translated shaders. Ryujinx fixed SMO's "waterfall triangle" by enabling robust buffer access, so out-of-bounds vertex reads happen in this game.
3. **Name every stall.**
   - Put `os_signpost` intervals around guest frames, NVN submits, shader and pipeline compiles, texture uploads and RomFS/Yaz0 reads. They then appear in Instruments' Game Performance and Metal System Trace.
   - Command line: `/Applications/Xcode.app/Contents/Developer/usr/bin/xctrace record --template "Metal System Trace" --attach <pid> --time-limit 20s --output t.trace`. Plain `xcrun xctrace` fails because `xcode-select` points at the Command Line Tools.
   - For one stall: `sample <pid> 5 -file out.txt`.
   - In-game HUD: `MTL_HUD_ENABLED=1` and `MTL_HUD_LOGGING_ENABLED=1`. A good frame-interval histogram has at most two regular buckets.
4. **Deterministic input replay for regression tests.**
   - Calypso (https://github.com/tetraxile/Calypso) is the model: input injected at `al::NpadController::calc`, a stage and scenario start point, and frame stepping.
   - Inputs use the nx-TAS text format, one line per frame with frame number, buttons and both sticks (https://github.com/hamhub7/nx-TAS). Old scripts to adapt are at https://github.com/MonsterDruide1/smo-tas-archive.
   - Our `TOTK_INPUT_FILE` timed commands are a first step. Moving from seconds to frame numbers would make runs repeatable.
5. **Golden-image checks.**
   - Capture fixed frames from a replay and compare them with NVIDIA FLIP (`pip install flip-evaluator`, https://github.com/NVlabs/flip) against a threshold. Dolphin's FifoCI does the same for every revision.
   - Lock the internal resolution while testing (see 3.2 below).

## 2. Known SMO trouble spots from emulators (test these in our build)

- **Particles freezing in Bowser's Kingdom and the painting kingdoms:** seen in yuzu, Eden and Ryubing (https://github.com/eden-emulator/Issue-Reports/issues/388, https://github.com/Ryubing/Issues/issues/203). The cause was small CPU writes to pages the GPU uses (yuzu PR #10942). Use it as a check for how CPU-written NVN buffers reach our no-copy `MTLBuffer`s and dirty-page tracking.
- **Missing Cappy voice and moon sounds in the 3-moon sequence:** same reports. Suggests a missing voice or effect path in audio HLE.
- **Seven Cap Kingdom fragment shaders failing to translate** (ScarletNX PR #244, https://github.com/scarletnx/ScarletNX/pull/244). Causes:
  - TMML.B not decoded;
  - multi-register TMML writes not tracked;
  - IPA of `a[0x2e0]`/`a[0x2e4]` not mapped to point-sprite coordinates.

  Symptoms were missing speech-bubble markers and floating particles. Check our translator for the same cases.
- **Ryujinx #2518:** occlusion query results had to be 32-bit, and buffer-upload tracking cut Metro Kingdom UI frame time from about 160 ms to 40 ms.
- **yuzu fixes:**
  - mustache colour from `gl_FrontFacing` typed as uint (PR #6928);
  - corruption from buffer–image granularity (PR #6834);
  - Bowser's Kingdom folding screens.

  Source: https://yuzu-mirror.github.io/game/super-mario-odyssey/
- **Rainbow noise on Sand Kingdom rock:** suspected NaN or uninitialised values (https://github.com/NextendoNetwork/Ryujinx-Nextendo/issues/63). Ryubing reports a crash in the Ruined Kingdom on macOS (https://github.com/Ryubing/Compatibility/issues/1).

## 3. Performance and visual options from 1.3.0 mods

Source: https://github.com/citron-neo/Citron-Mods, folder `0100000000010000/1.3.0 by Fl4sh_`. Offsets are relative to main `.text` (`offset_shift 0x100`) and are unverified locally.

1. **SMO already runs at 60 fps.** No gameplay delta-time mod exists or is needed. Keep the game's 60 Hz logic. Any higher-fps option would have to be renderer-side interpolation (the Zelda64Recomp/RT64 approach), which comes later.
2. **Disable dynamic resolution:** a NOP at `0x5EE438` (theboy181). Use it for deterministic captures. The yuzu wiki notes it breaks undocked mode.
3. **Fixed 1080p docked:**
   - patches the 1600×900 immediates (`0x4e27bc`, `0x4bf2a4`) and edits `romfs/SystemData/ProjectGraphicsQuality.szs`;
   - the mod says anything above 1080p crashes, so higher resolutions need our Metal render targets decoupled from the guest's buffer sizes;
   - MetalFX spatial upscaling of the final image is the low-risk alternative, since the tree already has `totk_mfx_up.m`.
4. **Ultrawide and any aspect ratio:**
   - an aspect float at `0x96816c` plus a NOP at `0x73de88`; 4:3/any aspect at `0x74D2EC`;
   - the HUD isn't adjusted, though upstream TotK has a UI-squeeze approach (`totk_ui_squeeze.h`).
5. **Other toggles:** LOD bias floats at `0x8fe52c` and `0x98b648`. Disable FXAA with a branch at `0x9BD3A8` (from the Retroid repository; not device-validated).
6. **Upstream mechanism:** MacNativeGames already has `TOTK_PCHTXT` and `--mod` for pchtxt patches (upstream commits `e55ad1c`, `ddbf796`). Port that rather than hand-patching.

## 4. Tools for testing and saves

- **smo-practice** (https://github.com/MonsterDruide1/smo-practice) supports 1.3.0: stage warp, teleport, moon refresh and an autosave toggle. Its `syms130.ld` is a copy of the 1.0.0 symbols. Real 1.3.0 offsets are in `patches/130.slpatch`, including a `001E7770: ret` crash fix worth looking at.
- **Hooking references:**
  - LibHakkun (multi-version symbols): https://codeberg.org/fruityloops1/LibHakkun
  - exlaunch: https://github.com/shadowninja108/exlaunch
  - OdysseyHeaders: https://github.com/MonsterDruide1/OdysseyHeaders
- **Save format:** `File*.bin` is a 0x10-byte header followed by a little-endian BYML v3 document with keys `WorldId`, `NextWorldId`, `GameProgressData.UnlockWorldNum` and `WorldInfo[].PayShineNum/ScenarioNo/MainShineNo` (https://github.com/anorum/mario_odyssey_save_editor). It apparently has no checksum (unverified). That would let us build test saves at specific kingdoms locally, alongside the real saves `profiles/cap-kingdom-start` and `profiles/bonneton-cappy`. Other format tools:
  - byml-v2: https://github.com/zeldamods/byml-v2
  - MoonFlow (text and event flows): https://github.com/Amethyst-szs/MoonFlow
- **OdysseyDecomp** is 1.0.0. Its TODO says 1.3.0 was built with different optimisation, so names map across but addresses and inlining don't.

## 5. Frame pacing, display and upscaling

- **Pacing:** keep one model. The tree uses `CAMetalDisplayLink`, so don't mix in `presentAfterMinimumDuration:`, which asserts on display-link drawables.
  - `preferredFrameLatency` trades latency for smoothness.
  - `maximumDrawableCount` 2 gives lower latency; 3 is smoother.
- **Game Mode:**
  - add `LSApplicationCategoryType` set to a games category, plus `LSSupportsGameMode` (macOS 26) or `GCSupportsGameMode`, to the app's Info.plist;
  - it only works in full screen and doubles the Bluetooth controller polling rate;
  - this is a packaging change, owned by the other session.
- **MetalFX frame interpolation** (`MTLFXFrameInterpolator`, macOS 26) needs depth and motion vectors. The existing `totk_mfx.m` path derives motion vectors itself from depth plus the camera matrices, so the real work is finding SMO's depth target, camera uniform block and UI shaders (about 1–2 weeks of probing). It adds at least one game frame of latency; see [`HIGH_FPS.md`](HIGH_FPS.md) (22 October correction). Temporal upscaling has the same requirement. Spatial upscaling, or an MSL port of FSR1/CAS (MIT, about 200 lines), needs only colour.

## 6. Accuracy features the NVN layer may need later

From the Ryujinx Metal backend (now unmaintained) and KosmicKrisp (Vulkan 1.4 on Metal, a good reference for workarounds).

- **Triangle fans and quads:** convert with index patterns. Indexed or indirect cases need a compute re-index pass.
- **Primitive restart:** always on for strips in Metal.
- **Provoking vertex:** handle in the shader.
- **Transform feedback and geometry shaders:** emulate with compute.
- **Conditional rendering:** emulate via indirect draws.
- **ASTC HDR:** native support isn't guaranteed. Keep a decode fallback; BCn and ASTC LDR are native on M3.
- **Write tracking:** host pages are 16 KB, so `mprotect` tracking is coarse. Store instrumentation in the rewriter, or hashing, is more precise.

## 7. Engine and system engineering

- **Threads:** keep game, render and audio threads at `QOS_CLASS_USER_INTERACTIVE` and loaders at `USER_INITIATED`. Never put work the game waits on at background QoS, which runs on efficiency cores. Use host sync primitives with priority inheritance rather than spin-waits across priorities.
- **Audio:**
  - Join the device's `os_workgroup` from threads that feed CoreAudio. This needs a time-constraint policy first, or the join returns EINVAL.
  - Keep the render callback lock-free.
  - Correct clock drift by resampling the 48 kHz stream with a slowly adjusted ratio (about ±0.5 %) that holds the ring buffer near a target fill. Dolphin uses time-stretching for the same problem.
  - Read `kAudioDevicePropertyLatency` and the safety offset for Bluetooth.
- **Timers:** `mach_wait_until`, with short spin-finishes for presents (Apple TN2169).
- **Input:**
  - send `GCMotion.rotationRate` into the six-axis service (motion cap throw);
  - optional gyro camera, using player-space gyro, auto-calibration and a tightening threshold (https://gamedeveloper.com/design/the-absolute-basics-of-good-gyro-controls);
  - layered remapping (physical, then virtual Npad, then guest) saved to a config file;
  - mouse delta mapped to the camera stick with a sensitivity setting.
- **Saves:** write to a temporary file, call `fcntl(F_FULLFSYNC)` (plain `fsync` is weaker on macOS), `rename()`, and keep a few rotating backups.
- **Crashes:**
  - handler on an alternate stack (`sigaltstack` + `SA_ONSTACK`) doing only async-signal-safe work;
  - report the guest backtrace as module + offset into the NSO, so addresses match the dump;
  - PLCrashReporter is an option.
- **Distribution model:** ship no game data. Validate the user's dump against known hashes and extract on first run, as Ship of Harkinian (`supportedHashes.json`), Unleashed Recompiled (install wizard) and OpenGOAL do.
- **Packaging:**
  - store data in `~/Library/Application Support/<bundle id>` and pick the dump with `NSOpenPanel`, which avoids the Documents-folder privacy prompt the current app triggers;
  - notarisation needs the hardened runtime, with only the needed entitlements (`com.apple.security.cs.allow-jit` and/or `allow-unsigned-executable-memory` for mapped guest code);
  - never ship `get-task-allow`.
