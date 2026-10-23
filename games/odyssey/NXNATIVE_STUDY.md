# LumaDock and nxnative: what we can learn

Lead-researcher study, 23 October 2026 (session 72839a). Everything was read only; no code from either repository was run.

## What they are

**LumaDock** (https://github.com/Xc1Ym/LumaDock, MIT, 13 commits, created 20 October) is a SwiftUI launcher and build GUI. It does not run games itself: it drives a Python worker that extracts an NSP, rewrites the code, converts shaders, builds and installs a Mac host for an engine the user supplies, **nxnative**.

- **Useful ideas:**
  - staged SHA-256 build caching and an atomic install (build to `.partial`, then swap a `current` symlink);
  - a check that the shader chain stamp in reflection files and metallibs matches;
  - `c1_guard` constant-buffer bounds proofs run in a process pool;
  - a JSON input-config schema;
  - crash classification of logs.
- **Don't install its release DMG:** unsigned bundled runtimes from a new single-author repository.

**nxnative** (https://github.com/seedds/nxnative, MIT, 271 commits, 1,288 stars, active) is "Switch games running natively on iPhone, iPad and Mac: NSO rewriter, Horizon HLE host, NVN→Metal".

- **Relationship to us:** it is a **sibling** of our renderer. Both copied the generic parts of D2R Mobile (ours: `third_party/d2r_gfx/README.md`, D2R Mobile commit 9f0c257). Fixes travel both ways; upstream MacNativeGames commits `c5d8671` and `dab728a` cite nxnative.
- **Rewritten history:** nxnative rewrote its main branch, so three SHAs upstream cites moved: 04ea8f8 → 5efe1d8, b301431 → 45b01ee, 7ed061f → 67f1398.
- **It has an Odyssey profile** (`dm/games/0100000000010000/game.tsv`) whose **main, sdk and subsdk0 build IDs match ours byte for byte** (they label it 1.4.1: a later update with the same executables but slightly different data, checked 23 Oct; our build accepts 1.3.0 data only) (main `b424be150a8e7d78701cbe7a439d9ebf`). They label it "1.4.1 / v393216"; our NACP says 1.3.0. It is the same executable, so their `main+0x…` addresses apply to our build. Verify by disassembly before use, per AGENTS.md rule 5.
- **smo-recomp's "1.4.1"** likely names the same final build too: our binary already imports the Labo VR calls that smo-recomp lists as new in "1.4.1". This is unverified; check one of their addresses.
- **Odyssey evidence is qualitative.** nxnative reports "played at 60 fps on an iPhone", a smoke route to frame 20,100 and pixel-identical HUD checks, with no frame-time numbers. LumaDock's own record reports Odyssey at 60 fps (p95 17.06 ms) for about 23 minutes on an M5 Pro, with 27,981 of 27,984 shaders converted. The 3 failures are `alThunderShader` geometry shaders.

## Already equivalent in our tree

Precise vertex positions via `DM_PRESERVE_INVARIANCE`; the point-size constant (`c0+0x610`); texture compare including swizzle; H.264 movies through VideoToolbox; account, bcat, oe, pctl, time and debug-pad services; nifm reported offline; focus mode stored; per-pipeline binary archives; GPU error handling and a watchdog; full screen; `presentAfterMinimumDuration`; per-thread pool cache; precompiled metallibs (built on 22 October).

## Ranked adoption list (checked against our code)

| # | Item | Evidence | Effort |
| --- | --- | --- | --- |
| 1 | **Geometry stages: skip instead of abort.** nxnative binds no library for stage 2 and still draws; Odyssey's 3 thunder geometry shaders are untranslated there too. | Ours `MDIE`s in `prog_get` and `die`s at `totk_shader_rt.m:308`: the first thunder draw ends the run. Same item as SMO_RECOMP_AUDIT A2. | 1–2 h |
| 2 | ~~**Display link: present in order, at most one frame behind, instead of always the newest**~~ **Downgraded on 23 October:** my 10 % figure misread a cumulative counter; the real merge rate is 0.22–0.45 %. Original text: (`6400306`, `DM_PRESENT_FIFO`). On-time presents went from 98.2 % to 99.98 % in their tests; add the hold for present interval ≥ 2 (`fa5d4d8`). | **Confirmed:** our link callback takes the newest ticket and merges older ones. `合并丢弃旧帧` is cumulative: 17–35 merged out of about 7,800 presents across 10 runs, 0–5 per 300 ticks, scattered. Low value. Keep the MetalFX interpolation branch. | ½–1 day |
| 3 | **`nvnQueueFinish` waits for the GPU only, not for queued presents** (`b0a9079`; one game's first load went from 78 s to 6 s). | **Confirmed:** ours is `x_drain(); mtl_finish()` = `mtl_present_flush()` + `mtl_finish_gpu()` (`nvn_mtl.m:2189`). Check that acquire still waits for its own present ticket. | tiny |
| 4 | **Six-axis always present plus a Shake key** (Space on the Mac) for Cappy's motion throws (`89145e8`). The game's analyzer is at `main+0x5d5590`; history reads at `main+0x93fd84`. | Ours: six-axis is absent or at rest without a gyro controller, and there is no shake key. Odyssey takes motion moves only in docked mode. | ~2 h |
| 5 | **Fill all 16 `GetNpadStates` entries** with consecutive sampling numbers. Odyssey reads all 16 and ignores the count (`main+0x93fc20`); in nxnative the sticks were dead in play until this fix. | Ours writes entry 0 and returns 1. It works today, but it's fragile. | 30 min |
| 6 | **Non-zero ZCull storage size** (`7c490ed`): the cause was Unity 2017.4's pool allocator (Cuphead), so it doesn't apply to Odyssey's agl engine. Keep our default. | **Confirmed:** ours returns 0 (`totk_nvn_ext.c:253–254`). | tiny |
| 7 | **Flushed-range tracking and initial-content fill** (`1eaaab8`, `9d941f8`): new textures filled from CPU-flushed storage and from CPU_NO_ACCESS pool contents. | Ours: `FlushMappedRange` is a no-op. A correctness gap; no visible symptom known yet. | low |
| 8 | **Yield back-off by default after 2 ms, with a register-progress check** (`hle_thread.c:571`); later, a spin-loop rewriter (`99c3f91`: p95 29–37 → 18–22 ms in another game). | Ours: back-off exists but is off (`TOTK_YIELD_BACKOFF`). | low / high |
| 9 | **Vertex-binding cache** (`697df75`) and **GPU-masked indirect-count draws** (`f0ff511`). | Lower priority: Bonneton isn't submit-bound. | medium |
| 10 | **`cb_extent` partial uniform copies** (`f5646ef`) and **interval-proof `c1_guard`** (`d9e1f27`). | Ours bounds only literal indices (`c1_reads_bounded`); small memory and safety gains. | medium |

**Also check:** `vi::SetLayerScalingMode` (nxnative returns OK at `hle_vi.c:40`; we may have no handler); the movie end-of-stream drain; our guest heap size against their Odyssey `app_pool` 0xCD500000 and 4 GB reservation.

## Licences

- **nxnative and LumaDock: MIT.** Keep the copyright and permission notice with any copied code.
- **Bundled third-party code inside nxnative keeps its own licence:**
  - Ryujinx shader libraries: MIT;
  - NVIDIA FLIP: BSD-3;
  - libvpx: BSD-3;
  - `nvn_tex.c`, from deko3d: zlib.
- **GPL references:** its shader reference tool uses yuzu, and Atmosphère is cited as a reference; don't copy that tool.
- **Game-derived tables:** nxnative's TSV tables are derived from game files and must not enter Git (AGENTS.md rule 3).
