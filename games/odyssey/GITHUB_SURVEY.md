# GitHub survey: techniques that fit the Odyssey port

Lead-researcher survey, 22 October 2026 (session 72839a). Three parallel agents read source code on GitHub for:

- **CPU cost per draw:** DXVK `02e2028c3f`, citron (a yuzu fork) `f942c35679`, MoltenVK `5189f8de86`, Hydra `c0ab8599cb`, and the Metal backend in a Ryujinx fork.
- **GPU readback, dirty tracking and uploads:** citron, a Ryujinx fork, Dolphin, DuckStation, PCSX2 and Hydra.
- **Native ports, latency and pacing, Apple silicon, profiling:** smo-recomp, UnleashedRecomp, RT64/plume, citron, Hydra, Tracy, and upstream MacNativeGames.

The central claims were spot-checked against our code. Upstream yuzu and Ryujinx are blocked on GitHub (DMCA), so their logic was read through citron and through a Ryujinx fork whose match with upstream is unverified.

Each item names the bottleneck it targets: **H1** = 73–87 ms readback drains, **H2** = about 63 ms latency, **H3** = render-submit CPU of about 9 µs per draw; see [`HITCHES_AND_LATENCY.md`](HITCHES_AND_LATENCY.md).

> **Measured since this survey (22 Oct evening).** H3 is not the Bonneton bottleneck: submit is 2–3.6 ms and the eight caches were timing-neutral. So items 6–12 (per-draw CPU) are lower priority than ranked here.
>
> H1 is confirmed. The waits split three ways: fence (about 35 %), global upload poll (36 %) and CPU copy (29 %); the replay drain is about 0. Items 4–5 stay top, with the spec sent to the chief engineer: per-rendering fence, freshness check before waiting, and no global upload poll.

## Tier 1: small, proven, do first

1. **Latency: throttle the game on the window-texture sync, as smo-recomp does for this game (H2).**
   - **smo-recomp** ([`runtime/gfx/display.c`](https://github.com/Coz-0/smo-recomp/blob/main/runtime/gfx/display.c), [`nvn_gl.c`](https://github.com/Coz-0/smo-recomp/blob/main/runtime/gfx/nvn_gl.c) `sync_wait`):
     - One 60 Hz refresh clock drives everything.
     - `frame_queued()` latches the refresh on which the queued frame will appear.
     - When the game waits on the sync returned by `nvnWindowAcquireTexture`, `display_wait_refresh()` blocks until that refresh.
     - Their notes say Odyssey's main thread reads the VSync count and then waits on that sync, so throttling there happens after input and timing are sampled.
   - **Ours** (`hle_nvn.c:2465` `nvnWindowAcquireTexture`): the acquire waits only for the previous present of the *same swapchain slot* (`o->avail[*idx]`, up to 4 slots) and returns its sync already signalled. That allows 3–4 frames in flight, which matches the measured 63 ms.
   - **Fix:** have the acquire, or its sync, wait for the present ticket of the newest frame minus (N−1). Put it behind an environment variable and A/B test N = 1 against N = 2.
   - **Same pattern elsewhere:** UnleashedRecomp calls `Video::WaitOnSwapChain()` at the top of the game update. plume's Metal `MetalSwapChain::wait()` uses `lastPresentedId >= currentPresentId - (maxFrameLatency - 1)`, bumped in the present buffer's completed handler.
2. **Port three small upstream MacNativeGames fixes our copy lacks (H3, hitches).** All three were confirmed missing in our code.
   - **`5f5de5e`:** the texture registry lock becomes `os_unfair_lock`, so its owner gets priority donation and the submit thread stops waiting on it. Ours is still `pthread_mutex_t g_rmu` (`nvn_mtl.m:4739`).
   - **`c5d8671`:** the present thread keeps its declared real-time computation when the frame period changes. Ours resets it in `set_time_constraint()` (`hle_nvn.c`), and nxnative saw that push the thread onto efficiency cores.
   - **Rest of `9517487`:** `getenv("TOTK_CHAIN_LOG")` still runs on every `EndRecording` and `Submit` (`hle_nvn.c:1102`, `2295`); upstream measured per-call getenv at 26 ms of CPU per second. Read it once instead.
3. ~~Turn on the eight replay caches.~~ Correctness gates passed, but they were timing-neutral in Bonneton, so they stay off (chief engineer, 22 October).

## Tier 2: the big structural wins

4. **Wait only for the write being read (H1).** The Ryujinx model:
   - `BufferModifiedRangeList.WaitForAndFlushRanges` stores, for each modified range, the sync number current when the GPU wrote it. On a CPU flush it waits only on the newest such sync among the overlapping ranges.
   - `SyncManager.Wait` forces a submit of the not-yet-submitted command buffer that holds that sync, rather than draining everything.
   - Applied here: stamp each mirrored render with its replay serial and its command buffer's `MTLSharedEvent` value. On invalidate, replay only up to that serial (committing early if needed), then wait for that value.
   - Because the game runs about 50 ms ahead, the read target is usually several frames old and already done, so the wait is often zero.
   - Safety rule, agreed with the chief engineer: drain at least to the game's last *waited* sync, because writeback entries are created by the replay thread.
5. **Copy hot readback targets early, on the GPU (H1).**
   - **Ryujinx "flush balance"** (`TextureGroupHandle.cs`: threshold 7, maximum 60): a texture that is often read back gets copied into a persistent buffer before its sync fence.
   - **yuzu/citron "preemptive download"** (`texture_cache.h`): after the first CPU read, each later bind of that target queues an asynchronous download that the fence thread writes back after the fence.
   - **Applied here:** count readbacks per mirrored texture. For hot ones (the 256×128 and 128×128 targets), append a block-linear swizzle compute kernel to the writing command buffer that writes straight into the guest pool's no-copy `MTLBuffer` (unified memory). The invalidate then only checks an event, and the CPU `wb_level_back` work disappears.
   - **Pitfall:** citron's synchronous fallback still idles the GPU (`runtime.Finish()`). Their speed comes from the preemptive path.
6. **Resolve bindings only when their source changed (H3).**
   - **yuzu textures:** `DescriptorTable::Read` compares the 32-byte texture descriptor (TIC) against a cached copy per index. `VisitImageView` looks the view up only when the descriptor is new.
   - **yuzu buffers:** `BufferCache::FindBuffer` maps a GPU address to a buffer through a flat page table (no hashing). `UpdateUniformBuffers` skips slots whose buffer ID is still resolved.
   - **Our side:** `tex_slots`, `pool_query_cache` and `arg_bind_cache`, among the eight caches, are partial versions of this. If the profile still shows per-draw resolution after they are on, the yuzu structure is the target design.
7. **Dirty masks ANDed with what the shader uses (H3).**
   - DXVK `ApplyDirtyGraphicsBindings` computes `shadersDirty & shadersUsed`, then per stage only `bindingsUsed & bindingsDirty`. `commitGraphicsState` is a chain of `m_flags.test(GpDirtyX)` checks.
   - The Ryujinx Metal backend rewrites a set's argument buffer only when that set's dirty flag is set (`EncoderStateManager.RenderResourcesPrepass`).
   - We already have dirty flags for pipeline and depth-stencil state and per-argument dirtiness. **But 23 call sites run `args_dirty_all()`**, including the pipeline-prediction scan `pso_predict_rest`, which re-dirties every binding after each scan.
   - With rescans (`b5cbbca`, up to 32 per submission), exploration scenes with many new pipelines may pay a full re-resolve many times per frame. **Test first:** run `TOTK_PSO_RESCANS=0` and then `TOTK_PSO_PREDICT=0` against the default on the Bonneton route, watching submit ms and instructions per draw.
   - **Fix if confirmed:** save and restore the per-argument dirty state around the scan instead of marking everything dirty.

## Tier 3: after profiling confirms the hot spots

8. **Merge consecutive draws with no state change** (DXVK `BatchDraw`/`BatchDrawIndexed`; `DxvkContext::draw(count, draws)` commits state once for N draws). Metal still needs one call per draw, so this removes state cost, not call cost.
9. **Two-stage replay** (DXVK `DxvkCsChunk` plus `DxvkCsThread`; yuzu `Scheduler::Record` with a worker). Decode and resolve on one thread, Metal encode on another. This only helps once items 6–7 have shrunk the work.
10. **Shadowed encoder state and offset-only rebinds.** MoltenVK `bindBuffer` calls `setVertexBufferOffset` when only the offset changed; adapt the idea to Metal 4 argument tables with `setAddress`. Add each resource to the residency set once, never per draw.
11. **Skip hazard tracking when nothing has a pending write** (DXVK runs `checkGraphicsHazards` only when needed).
12. **Pipeline transition cache.** yuzu `current_pipeline->Next(key)` compares with the current key, scans a short transition list, and hashes only on a miss.

## Uploads and memory tracking

13. **Upload paths.**
    - Already native: BC/ASTC formats, GPU deswizzle (`tex/nvn_deswizzle_gpu.m`).
    - Next: read block-linear data straight from the pool's no-copy buffer in the compute kernel, on a **second command queue** synchronised by an `MTLSharedEvent`, so the submit thread only encodes (yuzu `StagingBufferPool` ring for staging).
    - Hydra (the closest Metal reference) still deswizzles on the CPU and has no GPU-to-guest texture readback, so it is not a model here.
14. **Dirty tracking with 16 KB host pages and 4 KB guest pages.**
    - yuzu `WordManager` keeps bitmaps per 4 KB page, using host protection only to arm them, and batches CPU-write invalidation at draw time (`CachedCPU`).
    - Ryujinx `RegionHandle` and DuckStation switch constantly written pages to "volatile" / manual check, which stops fault storms.
    - Applied here: a 16 KB fault should dirty only its four 4 KB subpages, never whole resources. Check ours against this.

## Profiling without Instruments (Developer Mode is off)

- **smo-recomp** has an `SMO_FRAMETL` timeline plus `tools/dev/frametl.py`, with per-frame gap, replay, GPU wait, present and **margin before the refresh**. Our `totk_timeline.c` is close; adding `margin` measures frames ahead directly, for item 1.
- **smo-recomp's freeze watchdog** (`nvn_vk_watchdog.c`) dumps lock owners and all thread stacks when no frame arrives for 5 s. It is useful for long-run hang and panic hunting (open task 3).
- **`sample <pid> 10`** works without Developer Mode, which is how H3's hot spots will be found. **Metal HUD** (`MTL_HUD_ENABLED=1`) gives frame and GPU-time graphs on screen (unverified on this Mac).
- **Tracy** has a Metal GPU-zone header (`TracyMetal.hmm`); whether it works with Metal 4 command buffers is unverified.

## Not useful here

- Superpages and `os_workgroup` for render threads: no project uses them. `os_workgroup` is only for audio.
- Hydra's presentation (no pacing).
- Interpolation (RT64, Shipwright) needs game-tagged matrices, and we already have MetalFX frame interpolation.
- Game Mode keys are already in `package_native_app.py`.

## Sources

The three agents' downloads, with file and line references, are in the session scratchpad (`gh-survey-a/`, `-b/`, `-c/`). The links above point to the files read. Upstream commits are in `git -C MacNativeGames log 43f53b4..origin/main`.
