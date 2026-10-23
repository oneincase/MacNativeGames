# smo-recomp audit: what the other Odyssey port does that we don't

Lead-researcher audit, 22 October 2026 (session 72839a). Two agents read the whole of https://github.com/Coz-0/smo-recomp: the renderer (`runtime/gfx`, docs, tests, tools) and everything else (OS/kernel, services, input, audio, movies, game hooks, tests, tooling). They checked about 100 items against our tree as *have*, *partial* or *missing*. The top items below were then confirmed against our own code and data. The earlier GitHub survey took only three ideas from this project because it was filtered to three measured bottlenecks. This page is the full pass.

**Caveats.**
- smo-recomp targets Odyssey **1.4.1** (we have 1.3.0). Its addresses and struct offsets are version-specific. Some addresses in its `display.c` comments come from Smash Bros. (ssbu-recomp), not Odyssey.
- smo-recomp is **GPL-3.0-or-later**. Reimplement from the documented behaviour; don't copy code.

## Tier A: crashes and stability (do first)

| # | Item | Evidence in our tree | Effort |
| --- | --- | --- | --- |
| A1 | **Compute thread groups over 1,024.** Metal's maximum is 1,024 threads per group; smo-recomp splits wider groups into k groups along x when the shader uses no shared memory (`mx_translate.h` ~L96). | **Confirmed:** 12 of Odyssey's compute shaders use **1536×1×1**, and **all 12 are in our runtime cache** (bound during play). `nvn_mtl.m:10840`/`10842` passes the size straight to Metal. Invalid dispatch: wrong results or GPU faults; a plausible contributor to the old GPU panics (unverified). | M |
| A2 | **Geometry stages.** The Ruined Kingdom lightning uses three. smo-recomp emulates them; we stop the game. | **Confirmed:** `totk_shader_rt.m:308` calls `die()` for tessellation/geometry stages. Stopgap: skip the draw and log it once. Real support: compute emulation. | S (stopgap), L (real) |
| A3 | **Unhandled online/OS imports reaching an unregistered service.** Our IPC registers only `audout:u`, `audren:u`, `bsd:u` and `set`; anything else ends in `runtime_fail` (`runtime/ipc.c:255`). | 1.3.0 imports `nifm::HandleNetworkRequestResult` (smo-recomp answers 0x17c6e so the game stops trying the internet), `SubmitNetworkRequest`, `IsNetworkRequestOnHold`, `GetCurrentPrimaryIpAddress`, bcat file/directory/progress calls, `friends` AsyncContext, `nsd::ResolveEx`, `IsNetworkServiceAccountAvailable`, plus **Labo VR** `pl::Begin/EndVrMode` and HOME-button blocking. None are overridden. The title menu has a **"Playing in VR"** entry, so picking it may crash us (unverified). Add a static audit: imports without an override whose SDK path needs an unregistered service. | S–M |
| A4 | **agl texture reclaim.** agl refcounts pool slots and abandons textures; smo-recomp frees an image after 8 unused frames. They saw memory reach 12.9 GB in a boss rematch stage. | `hle_nvn.c:401` releases only on re-init over a live object. Likely unbounded growth in long sessions; this ties into open task 3 (long runs). Verify the agl offsets on 1.3.0. | M |
| A5 | **Thread preemption gap.** Priorities 17 (model draw-buffer update) and 18 (DecompThread) share core 2. With them unseparated, smo-recomp saw "pure virtual called" crashes when entering stages from the title menu (2 in 91 runs; 0 in 276 once separated). | Our `threads.c` enforces strict priority order per virtual core, but only at SVC entry. A DecompThread that makes no SVCs can hold the core. Reproduce by entering stages from the title repeatedly. | M |
| A6 | **Journaled save commit.** smo-recomp uses a copy-on-write work folder, an atomic commit marker, roll-forward at the next mount, and refuses Commit while a file is open for writing; six self-tests. | `fs_host.c` writes immediately, so a crash or kill mid-save can tear a slot. | M |

**A1 detail, corrected after the chief engineer's challenge (22 October).**
- **Never run by the game.** The 12 [1536,1,1] variants are **seed-only**: the game never translated them. Their pipelines exist because the warm-up builds every seed compute shader (99 `C` rows in `pso_manifest.tsv`).
- **A guard already exists.** The runtime reads the group size from `control+0x748` and `MDIE`s above 1,024 (`rt_local`); logs only ever show 32×1×1. So no over-limit dispatch has happened, and if one is ever bound it is a hard stop.
- **Original code confirms it.** The Maxwell bytes, extracted from `ShaderData/alComputeShader.szs`, read only `SR_LANEID` and `SR_CTAID.X/Y`, never `SR_TID`. The translator lost nothing.
- **Status: latent.** Optional fix: clamp to 32×1×1 when the MSL has no thread-in-group index, threadgroup memory or barrier.

**A3 detail (redone against both override tables, `system_services.c` and `services.c`).**
- **Already covered:** the VR and HOME-blocking imports are now overridden (`services.c` 227–324).
- **No crash today.** The non-blocking `nifm` trio has 9 call sites in `main` (0x6c9530–0x6cc984). In the SDK all three go through `nifm::detail::GetNetworkConnectionPointer`, which returns 0xca6e without IPC because our `Initialize` override skips the SDK's own init.
- **But the game reads that as an error.** smo-recomp's source (`smo_nn.c:327–332`) says the game treats anything other than 0 or 0x17c6e as an error, so `HandleNetworkRequestResult` should answer 0x17c6e ("not connected, stop").
- **`account::IsNetworkServiceAccountAvailable`** (1 call site) should be overridden to write false and return success.

## Tier B: correctness

| # | Item | Evidence | Effort |
| --- | --- | --- | --- |
| B1 | **Vsync event rate.** smo-recomp drives the game's vsync event from one fixed 60 Hz clock. | Ours signals every registered vsync event from a `CVDisplayLink` at the **panel's** rate (`frontend.m` `vsync_callback`), so 120/s on this Mac. Nobody has reported double speed, but whether SMO counts these events is unverified. **Measure** game time against wall time on 120 Hz (a cutscene or a timed challenge), then decide. | S |
| B2 | **Depth target smaller than the colour targets.** Odyssey binds a 2×2 dummy depth to full-screen passes; smo-recomp runs those passes without depth (it fixed title-screen Mario and speckled shadows). | Unverified in ours. | S |
| B3 | **Indexed QUADS** (Sand Kingdom room geometry). | `nvn_mtl.m:10636` skips indexed quads; quad arrays are handled. | S |
| B4 | **ORDER_FRAGMENTS barrier**: split the pass when it samples its own target. | `nvn_mtl.m:8009` ignores `OP_BARRIER`. | S |
| B5 | **`nvnTextureInvalidateTexels`** re-swizzles GPU texels into pool memory; agl's SMAA lookup textures use it. | `hle_nvn.c:512` is a no-op. | S–M |
| B6 | **Unimplemented NVN calls**: count and skip instead of stopping the game. | `hle_nvn.c:71` calls `die`. Consider a skip mode for play builds. | S |
| B7 | **Audio through the SDK's own DSP path** (`CreateAudioRendererManagerByDfc` + `CommandListProcessor`, 5 ms clock). | Ours reimplements the renderer from Ryujinx algorithms. 1.3.0's `sdk.elf` exports the DFC path, so the console's own mixer could run natively: an exactness option if audio bugs appear (missing Cappy voice lines were reported in emulators). | M–L |

## Tier C: performance

- **Look-ahead over each submitted batch** queues new pipeline combinations (pair, formats, topology) to the front of the worker queue (`vk_pipelines` L1490). We have prediction rescans but no format/topology look-ahead. M.
- **Fragment-only programs and shared-vertex cross pairs built ahead.** FRAGONLY took their first-run draw builds from 24 to 0. Add **per-stage variant lists** replayed at program handover. M.
- **Background pre-upload of compressed textures**, two frames after creation, with a 4 ms/1 ms budget near scenes. Fewer first-entry stalls. M.
- **Anisotropic filtering override setting.** S.
- **agl sampler registration sort.** smo-recomp measured 1–10 ms per frame, but that was mostly indirect-call overhead in recompiled code. Measure natively before doing anything.

## Tier D: testing and automation (biggest multiplier for every other task)

- **Nerve tracker.** Hook `al::NerveKeeper::update` to name every actor's current state and log nerve changes; read Mario's and the camera's positions. Offsets are 1.4.1, so find the 1.3.0 ones with their symbol-matching method: `tools/lifter/gen_tables.py` matches masked instruction patterns and callee sets against the symbolized 1.0.0 `main`.
- **Poll-counted input sequences** (`@nerve`, `@until-nerve`, `@goto` by camera, `@near`, `@movie`) and a **Cap Kingdom checkpoint test** (14 nerve checkpoints plus zero-exception, zero-drop and zero-underrun gates). Our probes press keys on timers.
- **`START_STAGE` / `STAGE_LIST`** (hook `StageScene::init`, or call the game's own `ChangeStageInfo` and `tryChangeNextStage`), **`save_warp.py`** (FileN.bin = 16-byte header with CRC-32 + BYML v3; sets stage, world, checkpoint and scenario), and a **stress test of every stage and scenario at 4× speed**.
- **Fixed random seed** (`GenerateRandomBytes`; ours uses `arc4random`, `services.c:345`) and **input record/replay** anchored to screen changes, for reproducible runs.
- **Flicker detector and draw-diff tools.** We have pass dumps but no diff tool.
- **Known game behaviour.** Starting a painting-only boss stage directly overflows the 512 KB scene heap; enter through the painting room.

## Tier E: features

Amiibo (nfp state machine plus a menu built from the game's layouts); in-window Help/manual (WKWebView with a `window.nx` shim; ours reports the page closed at once); capture button to album; pause when the window loses focus (ours keeps running, `frontend.m` `runtime_front_focus` returns 1).

## Already equivalent or better here

Async translation and prefetch at `nvnProgramSetShaders`; loading barrier (`mtl_warm_wait`, no progress bar); virtual pools (`totk_vpool.c`); dirty tracking; occlusion queries without blocking; 3D copies keeping the z range; `NEAR_IS_MINUS_W`; invariant positions (`DM_PRESERVE_INVARIANCE=1` set in `system_services.c`); freeze watchdog and timeline margin (built from the earlier survey); priority-ordered virtual cores (stricter than theirs); shared-event rebinding; controller mapping and Mahony motion fusion; H.264 movies through VideoToolbox; crash chains and memory watch; game-slot limits.

## Recommended order

1. **A1** (compute group split) and **A2 stopgap** (skip geometry draws): small, confirmed, crash or GPU-fault class.
2. **A3** (import coverage plus static audit; check the VR menu entry) and **B1** (measure game speed on 120 Hz).
3. **A4** (texture reclaim), together with the long-run stability task.
4. **Tier D nerve tracker + sequences**, since they make every later fix testable.
5. **A6, A5, B2–B5**, then Tier C.
