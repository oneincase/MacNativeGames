# In-game hitches and input latency: findings and techniques

Lead-researcher notes, 22 October 2026 (session 72839a). Offline analysis of `Odyssey_build/boot-resume-cap.log`, a bounded run that resumes into Cap Kingdom and plays, read together with the renderer source. Nothing here has been measured with new instrumentation yet. Each item ends with the measurement that would confirm it.

> **Update, 22 October evening (measured by the Applier and the chief engineer).**
> - **H1 is confirmed.** In 10 s `sample` profiles in Bonneton (`Odyssey_build/capture/h3-ab/sample-on1.txt`, `sample-on3.txt`), the game's main thread is blocked in `mtl_pool_invalidate` → `wb_write_list` for about 7 % of its samples.
>   - Summed over all stacks in `sample-on3`: newest-fence wait (`mtl_finish_gpu`, `nvn_mtl.m:2183`) 935 samples; polling all pending uploads with 1 ms sleeps (line 2184) 967; CPU write-back copy 778; replay drain (`x_drain`) only 4.
>   - So the fix is to wait less (the rendering's own fence, no global upload poll) and to skip waiting when the CPU has already overwritten the target, not to drain less.
>   - Writeback hits only 256×128 R32F targets (382 times, 264 already overwritten by the CPU, yet the full wait is paid before that check) and 128×128 RGBA16F targets (147 times).
> - **H3 does not hold in Bonneton.** Submit is about 2–3.6 ms per frame at 270–520 draws, and the eight replay caches were timing-neutral (they stay off).
>   - The "submit+present" figure used below includes present-side waiting, so it overstated replay CPU cost.
>   - The per-draw instruction counts are real, but they don't make Bonneton submit-bound.

> **0b result, 22 October (implemented by the chief engineer from this note's H1).**
> - **Part A:** check freshness before waiting.
> - **Part B:** wait only on the rendering's own fence, or the previous render's for the double-buffer pattern; no global upload poll unless `TOTK_BARRIER_START=0`.
> - **Game-thread samples blocked in `mtl_pool_invalidate`:** 20.1 % / 36.5 % before (`capture/h3-ab/sample-on1`, `-on3`) → 13.9 % / 15.5 % after (`capture/wb-partb/sample-partb`, `-partb2`). The fence wait and the upload poll are gone.
> - **Remainder:** about 95 % of what is left is the per-level readback blit on the legacy queue (`wb_level_back` → `q_cb` → `waitUntilCompleted`), one GPU round trip per readback.
> - **Next:** copy hot readback targets into a staging buffer inside the rendering's own Metal 4 command buffer, so invalidate only waits a finished fence and copies on the CPU (GITHUB_SURVEY.md item 5).

> **Memory pressure on this Mac, 22 October (0m stall analysis).**
> - **The Mac has 8 GB of RAM.**
> - **The 0m test's whole-process stalls (21.9 s and 5.3 s) were system memory pressure.** During that run the kernel's `memorystatus` killed idle processes about every second, free pages fell to about 68 MB, and about 4 GB sat compressed. The same warm-up workload (51,618 shaders) took 70 s instead of 21 s.
> - **The part B run logged no pressure.**
> - **Cause, corrected by the chief engineer:** the warm-up was already slow from launch, before any leak could build up, and the footprint was only about 0.1 GB higher (3.5 vs 3.4 GB). So the machine most likely had less free memory during that run (other apps and sessions); the staging leak was at most a contributor. An ABBA rerun with memory recorded before each run will separate the two.
> - **Lessons:**
>   - A/B timings on this Mac are only comparable at similar free memory; record `memory_pressure` before each run.
>   - The game's 3.4–3.5 GB footprint plus loading 51,618 shader libraries at start is a memory risk worth measuring and scoping (upstream warms only the current version's shaders).

## What the play log shows

- **Steady state has headroom.** GPU is about 5 ms per frame and the render-submit thread about 2 ms per frame (≈11 M translated instructions per frame, 277 draws), against a 16.7 ms budget. Presentation holds 60 fps with p99 16.67 ms per 300 frames.
- **Most hitches are not graphics.** 38 long frames occur after 60 s. **32 of them** have submit+present < 10 ms, GPU < 10 ms and no shader or pipeline compile, yet last **73–87 ms** (one 119 ms). They come in clusters (61–63 s, 68–69 s, 74 s, 81–82 s, 85–90 s, …).
- **Latency is high.** The display-link line reports submission running *ahead* of its target present by a median of 49.8 ms. A game frame takes a median of **63.5 ms** from enqueue to screen (minimum 50 ms), about 4 frames at 60 Hz. `preferredFrameLatency` is already 1 (`g_present_latency`), so the depth comes from the renderer's present queue (`g_pq`, `PQ` slots) and the asynchronous replay, not from Core Animation.
- **Many GPU-to-CPU writebacks.** Render results copied back to guest memory: 8,000 textures (1,000 MB) by 130 s, about 60 per second, mostly small targets (256×128, 128×128).

## Correction and the biggest bucket: render-submit CPU cost (H3, highest value)

"Most hitches are not graphics" held only for `boot-resume-cap.log` (standing in Cap Kingdom). Long frames after 60 s in the exploration runs, by category:

| Log | Long frames | Shader/pipeline compile | Upload | Non-graphics 73–87 ms (H1) | Submit-heavy, no compile/upload |
| --- | ---: | ---: | ---: | ---: | ---: |
| `boot-resume-cap` | 38 | 0 | 0 | 32 (1.7 s) | 6 (0.2 s) |
| `boot-resume-play` | 290 | 8 (3.5 s) | 4 (0.4 s) | 27 (2.6 s) | **251 (8.5 s)** |
| `boot-resume-stickbits` | 172 | 0 | 1 (0.2 s) | 15 (1.6 s) | **156 (6.0 s)** |
| `boot-resume-trace` | 158 | 21 (10.8 s) | 0 | 10 (1.3 s) | **127 (4.7 s)** |

The submit-heavy frames in `boot-resume-play` are steady heavy frames, not spikes:
- frame median 30.6 ms, so a missed vsync;
- submit+present median 19.6 ms, p90 32.3 ms;
- GPU median 9.1 ms.

The CPU replay that turns NVN commands into Metal is over budget. The periodic `翻译` (translation) lines show why: **22,000–49,000 CPU instructions per draw**, about 9 µs per draw. That is 11.4 ms of submit CPU per frame at 1,215 draws. Upstream TotK replays about 12,000 draws in roughly 6 ms, around 0.5 µs per draw, so Odyssey's copy is roughly 15–20× more expensive per draw.

**Lead 1, zero code change.** Upstream's eight replay caches are **off** in Odyssey's copy (Bayonetta-derived), and upstream turned them **on** by default in `dfa7ec4`. Measured upstream: TotK shrine frame 12.25 → 11.0 ms, submit 7.1 → 6.0 ms; pool query cache 0.55 ms, texture plan 0.3 ms, usnap index 0.2 ms. All were verified with check modes, with no mismatch over hundreds of millions of lookups. In Odyssey they turn on by environment variable alone:

```
TOTK_USNAP_INDEX=1 TOTK_ARG_EPOCH=1 TOTK_TEX_SLOTS=1 TOTK_ARG_BIND_CACHE=1 TOTK_POOL_QUERY_CACHE=1 TOTK_SHADOW_INDEX=1 TOTK_PKG_FINAL_CACHE=1 TOTK_TEXTURE_PLAN=1
```

For a correctness pass, add the check modes once: `TOTK_ARG_EPOCH_CHECK=1 TOTK_TEX_SLOTS_CHECK=1 TOTK_TEXTURE_PLAN_CHECK=1 TOTK_POOL_TLS_CHECK=1 TOTK_TEX_GEN_CHECK=1`. Any mismatch aborts. Then A/B all-on against all-off on the resume route; compare the `翻译` lines (instructions per draw, submit CPU ms) and long-frame counts. If it holds, flip the defaults as upstream did.

**Check modes and failure modes for the eight caches** (answered for the chief engineer on 22 October):
- The first gate passed: all eight on, with `TOTK_ARG_EPOCH_CHECK`, `TOTK_TEX_SLOTS_CHECK` and `TOTK_TEXTURE_PLAN_CHECK` set; 160 s resume run, no mismatch.
- Three more caches have check modes as runtime flags rather than env vars. Send `flag usnap_index_check 1`, `flag pool_query_check 1` and `flag shadow_index_check 1` through `TOTK_INPUT_FILE`.
- Only `arg_bind_cache` and `pkg_final_cache` have no check.

| Cache | If wrong | Where to look |
| --- | --- | --- |
| `usnap_index` | wrong uniform data for a draw | misplaced, flickering or wrongly lit objects |
| `shadow_index` | stale per-draw constants | animation a frame behind, trails |
| `pool_query_cache` | wrong buffer for an address | exploded or missing geometry; **possible out-of-bounds GPU read**, so keep runs bounded |
| `arg_bind_cache` | the previous draw's buffer kept | wrong geometry or constants after a binding change; reset per encoder, so low risk |
| `pkg_final_cache` | a recycled allocation shows another texture's pixels where an upload skipped a mip or layer | wrong textures briefly, distant garbage, wrong cube-map reflections; stress with kingdom transitions, costume shop, photo mode, map |

**Lead 2, profile next.** Even with the caches, 9 µs per draw is far above TotK. Run Time Profiler on the submit thread in Bonneton (`xctrace record --template "Time Profiler" --attach <pid>`) to find the per-draw hot spots. Candidates from upstream's history:
- per-draw texture and descriptor resolution;
- uniform snapshots;
- pool lookups;
- render-pass breaks.

Upstream's `TOTK_TIMELINE` records render passes with their draw counts.

## H1: the 73–87 ms hitches are full-pipeline drains on memory-pool invalidate (strong hypothesis for the standing case)

`mtl_pool_invalidate` → `wb_write_list` in `nvn_mtl.m` runs whenever the game calls `nvnMemoryPoolInvalidateMappedRange` over memory a GPU rendering wrote. It calls:

1. `nvn_x_drain()`, which waits until the asynchronous replay thread has translated **everything** the game has queued;
2. `mtl_finish_gpu()`, which waits on `g_fence`, the **newest** submitted command buffer, plus pending uploads.

The wait therefore covers the whole pipeline, not just the rendering being read. When the game runs about 50 ms ahead of the screen, that wait is roughly the queue depth plus GPU time, which matches the measured 73–87 ms and the absence of graphics work in those frames. It also explains why most writebacks are cheap: when the queue happens to be empty, the drain costs almost nothing.

**Confirm** (one bounded run, implementer):
- time `nvn_x_drain` and `mtl_finish_gpu` inside `wb_write_list`;
- log any wait over 10 ms with the frame number, and the queue depth (`g_pq_in - g_pq_read` and the replay backlog);
- correlate with the `[长帧]` lines.

The new os_signpost markers (`dm_signpost.h`) make this cheap: add an interval named `writeback-wait`.

**Techniques**, best first. This is the standard emulator approach: yuzu's buffer-cache "async downloads" and sync points, Ryujinx's per-resource flush on sync points.

1. **Wait only for the rendering being read.** Record the replay sequence number and the GPU fence value when a mirrored texture is rendered (`wb_ent` already stores a `stamp`). On invalidate, drain the replay thread only up to that sequence number and `mtl_wait` on that fence, not `g_fence`. Work queued after the rendering no longer blocks the CPU read.
2. **Copy back eagerly on the GPU, then just wait.**
   - Right after a render pass that writes a mirrored-pool texture, encode the texture → guest-memory copy in NVN layout on the GPU: a blit into a no-copy `MTLBuffer` over the pool, swizzling in a compute kernel where needed (`tex/nvn_deswizzle_gpu.m` has the inverse path).
   - The invalidate then only waits for that command buffer, and the CPU-side `wb_level_back` work disappears.
   - Do this only for textures the game has actually read back before, by keeping a small history keyed by memory address, so other render targets pay nothing.
3. **Shrink the queue.** Fewer frames in flight shortens any drain and also cuts latency (H2).

> **H2 correction, 22 October (bound measured by the chief engineer).**
> - In Bonneton, `TOTK_ACQ_SYNC=old` (game throttled to the previous frame's GPU completion) changed enqueue-to-screen p50 by only about 0.4 ms (63.5 → 63.1 ms; logs `Odyssey_build/boot-acq-*.log`, analysis `scripts/analyze_latency_ab.py`). **The game is not running ahead.**
> - The ~50 ms is the display link's own lead: `update.targetPresentationTimestamp − now` in the `CAMetalDisplayLink` callback, which is exactly 3 × 16.67 ms. The link runs at **60 Hz on a 120 Hz panel**, and the game is **windowed**, so WindowServer composites every frame. Our queue adds only about 13.6 ms (under one link period).
> - Knobs to A/B, no code needed:
>   - `DM_PRESENT_LINK=2`: 120 Hz link, game presents every other tick. Watch the present-cadence p95 for judder.
>   - Full screen with `BY3_FULLSCREEN=1`: direct-to-display, typically one compositor frame less (unverified).
>   - `DM_PRESENT_LATENCY` is already 1.
> - The frames-in-flight cap below does not address this. Treat the rest of this section as superseded.

## H2: about 4 frames of latency come from queue depth, not the display

The game submits about 50 ms before its frame is shown, so input-to-photon is about 63 ms plus game logic. For a platformer, the target is 1–2 frames.

**Techniques:**

1. **Cap frames in flight.** Block the game in `nvnQueuePresentTexture` or the next window texture acquire while more than N presents are pending (N = 1 or 2). This is the same idea as NVIDIA Reflex, AMD Anti-Lag and the "max pre-rendered frames" setting in DXVK and Ryujinx. Measure enqueue-to-present p50 and frame-time p99 at N = 1, 2 and the current setting.
2. **Start the game frame just in time.** With `CAMetalDisplayLink` already driving presents, release the game thread a fixed time before the next target present. Use the measured submit + GPU time (about 7 ms) plus a margin, not as early as possible. Apple's WWDC21 10147 pacing guidance and `presentDrawable:afterMinimumDuration:` apply the same principle on the present side.
3. **Watch the cost of merging.** `合并丢弃旧帧` (old frames merged and dropped) runs at about 10 % (30 of 300). Dropped game frames are wasted CPU and GPU work, a sign the queue runs too deep.

**Confirm:**
- the queue depth at present time (`g_pq_in - g_pq_read`), logged every 300 frames;
- the replay backlog;
- the game thread's wait time in present or acquire.

A/B test N = 1 and N = 2 on the same resume route, with frame times plus the existing latency line.

## Smaller leads from the same log

- **Writeback volume.** 60 writebacks per second at about 7.7 MB/s. If technique 2 of H1 is adopted, also measure the CPU time `wb_level_back` spends per second.
- **Kernel time on the submit thread.** It is 0.565 ms of 1.99 ms (28 %). Worth a Time Profiler look for lock or futex churn. On macOS 14.4+, `os_sync_wait_on_address` / `os_sync_wake_by_address_any` are futex-style primitives that can replace condition-variable round trips in HLE arbiters (`runtime/threads.c`). Unmeasured; investigate only if profiling shows sync overhead.

## Sources

- **Local.** `Odyssey_build/boot-resume-cap.log`; `nvn_mtl.m` (`mtl_pool_invalidate`, `wb_write_list`, `mtl_finish_gpu`, the `g_pq` present queue, `g_present_latency`); `hle_nvn.c` (`nvn_x_drain`).
- **yuzu buffer cache.** Async downloads and sync points in `src/video_core/buffer_cache/` (yuzu mirrors); Ryujinx `Ryujinx.Graphics.Gpu/Synchronization`.
- **Apple.** WWDC21 10147 "Achieve smooth frame pacing"; `CAMetalDisplayLink` documentation; `os_sync_wait_on_address(3)` (macOS 14.4+).
