# Shader stutter: findings and ranked plan

Research from 22 October 2026 (session 72839a). Local measurements come from this Mac's runtime shader cache and logs, using local scripts that print counts only. Outside techniques come from emulator, recompilation and engine sources, linked at the end. Status of work done is in [`TECHNIQUES.md`](TECHNIQUES.md). This page is the plan.

## What the stutter actually is (measured locally)

1. **Variants barely multiply.** The runtime cache after play into Bonneton holds 844 unique shaders and only 862 compiled variants. Only 18 shaders have a second variant: 6 differ by depth mode, 6 by texture type. Variant explosion is not the problem, so variant-reduction techniques (function constants, flexible pipelines, dynamic libraries) are low priority.
2. **The cost is the first sighting of each new shader.**
   - Translation (NVN → SPIR-V → MSL in the external translator): median 86 ms, 90th percentile 405 ms, maximum 3.9 s, from 60 logged samples. **These figures are biased high.** The async path logs only the first 8 translations and then every 100th, so the sample is mostly fresh translator processes. At runtime the translators also compete with the game's higher-priority threads, and first sightings skew toward big material and effect programs. The chief engineer's offline seed batch ran at about 380 shaders/s on 8 services, about 21 ms per shader on idle P-cores.
   - Then the Metal library compile: upstream measured a median of 46 ms.
   - Then pipeline creation, which the pipeline archive now mostly removes after the first run.
3. **The game's whole shader set can be found offline. This is the key finding.**
   - The static inventory (`analysis/shader-keys.json` + `extra-shader-keys.json`, 28,284 keys) matched only 182 of the 844 runtime keys.
   - Of 80 sampled misses, 77 were inside archives the inventory had scanned (50 in `ShaderData`, 47 of those in `alRenderMaterial.szs`; 27 in `EffectData/EffectPtcl.szs`), and a few were embedded in `main`. The cause was a key mismatch, not missing archives.
   - The inventory scripts call TotK's extractor in Breath of the Wild mode (`'botw'`). For 0x848-byte control sections, that mode applies BotW's bind-time rewrite, and every one of Odyssey's 41,642 embedded BNSH program instances has a 0x848 section.
   - **Re-scanning the same archives in `'totk'` mode (no rewrite) gives 28,413 keys and matches 828 of the 844 runtime keys.** Odyssey binds its control sections unmodified.
   - The 16 still missing match the 16 runtime shaders whose code sits in the `main` module, which the inventory does not scan (TotK's `prepare_shader_seed.py --modules` does).
   - Reproduce with [`scripts/locate_runtime_shaders.py`](scripts/locate_runtime_shaders.py) and the comparison in the 22 October notes. Fix `scripts/inventory_shader_keys.py` and `scripts/inventory_extra_shader_keys.py` to pass `'totk'`.

## R1: the seed's 322 failed translations (answered 22 October)

The whole-game seed translated 28,131 of 28,453 keys; 322 failed (`Odyssey_build/shader-seed-odyssey/seed-manifest.json` → `skipped`). None of the 322 is among the 844 shaders the game has used so far.

Three samples were replayed offline through the translator with `DMSH_STACK=1` (CPU only, output in ignored `Odyssey_build/seed-failures-stack/`). The faults are all in **decoding the input**, not in translation, and the code files in `Odyssey_build/seed-failures/` explain why:

| Error (count) | Where it throws | Input |
| --- | --- | --- |
| IndexOutOfRange (288, vertex and pixel) | `Ryujinx.Graphics.Shader.Translation.ShaderHeader..ctor` | code median **0 bytes**; 214 shorter than the 0x50-byte shader program header. These are **empty program slots** in agl `.sharcb` packages (`alHdrCompose` 136, `alDeferredShading` 48, `alRenderFog` 45, `AglResource` 42, …), not real shaders. |
| ArgumentOutOfRange (32) | `Decoder.FillBlock` | code median 84 bytes (header plus one instruction), so decoding runs past the end. One outlier is 1.49 MB (`a87fc654…` vertex, `SystemData/AglResource.szs/agl_resource.Nin_NX_NVN.release.sarc/agl_technique_lght.sharcb`), likely a misparsed record length. |
| SPIRV-Cross `nullptr` exception (2, compute) | SPIRV-Cross, after translation | `AglResource` `agl_technique_pfx` utility programs. An offline probe without the seed's `local` workgroup field reported "compute shader without thread group size" instead; that was a probe artefact. |

**Risk:**
- In the default async mode a failed variant is skipped (its draws are dropped), so if the game ever bound one, an effect would go missing; it would not crash.
- In synchronous mode (`TOTK_SHADER_ASYNC=0`), `totk_rt_variant` calls `die()`.

**Recommendation (low priority):**
- Have the seed (and the inventory) skip records whose code is shorter than 0x50 bytes plus one instruction, and count them as "empty slots", not failures.
- Check the 1.49 MB outlier's `.sharcb` record parse.
- Upstream TotK saw the same classes (80 agl vertex shaders out of bounds, 2 compute nullptr) and reports the game never used them either.

## 0p: warm-up scope and memory (research design, 22 October)

**Situation.** Odyssey's warm-up loads the whole seed at every start: 28,131 shaders plus 22,604 per-slot rule variants, i.e. **51,618 Metal libraries** in the background. It took 21 s in the part B run and 70 s under memory pressure. The game had used about 860 variants by Bonneton.

Footprint in `boot-wb-partb2.log`:

| Frame | Footprint |
| --- | --- |
| 600 | 1.47 GB |
| 900 | 2.54 GB |
| end | 3.5 GB |

The +1.1 GB step falls inside the warm-up window. The Mac has 8 GB of RAM.

Upstream already solved this. Commit `671905c` (PR #84 part A, `docs/first-launch-shaders.md` "预热范围", the warm-up scope section) warms only the current version's shader keys plus the keys this Mac has **observed** in play (`observed/<RomFS SHA>.keys`, files `totk_shader_scope.h` and `totk_shader_observed.h`); `TOTK_WARM_ALL=1` restores the full warm-up. Our copy has none of it.

With precompiled `.metallib` files, a library missing from the warm-up costs about 0.8 ms to load at first use (upstream's measurement), and its pipeline comes from the binary archive after the first meeting. Warming everything therefore buys little and costs memory and CPU.

**Measurements** (GPU runs through the chief engineer; record `memory_pressure` before each run, as the board requires):

1. **Warm-up memory cost.** Same route, ABBA: full warm-up against warm-up off. Log footprint and `MTLDevice.currentAllocatedSize` every 300 frames; also time to first frame, long frames in play, and libraries loaded at draw time.
2. **Per-library cost.** Add the footprint to the existing `预热进度` (warm-up progress) line. It already prints every chunk; footprint delta divided by shaders loaded gives MB per library.
3. **Scoped warm-up.** Warm only the title/menu list plus the observed set (keys translated or drawn in previous runs, about 1–3k), and lazy-load the rest. Compare against the full warm-up on the same measurements. Port upstream `671905c`'s scope and observed files, or record `observed` keys from our runtime cache, where variants with a `.code` file plus seed variants that were drawn count as used.
4. **Rule-variant hit rate.** Count how many of the 22,604 rule variants are ever drawn. If few, ship them on disk but don't warm them.

**Port spec and criteria (sent to the chief engineer, 22 October).**

Port spec:
- **Mechanism.** Port upstream `671905c`'s scope and observed machinery (`totk_shader_scope.h`, `totk_shader_observed.h`, and the `totk_shader_rt.m` scope/observed functions), but in an **observed-only mode**.
- **Why a straight port won't help.** Upstream's scope is every key of this game version, which only cut TotK because its cache mixed versions; here every seed key is in scope.
- **Changes from upstream:**
  - record every used key, removing upstream's skip of static keys;
  - `TOTK_WARM_SCOPE=observed` makes the scope observed keys ∪ title-list keys;
  - title-list keys are always in scope, so the title never waits;
  - the file is `<cache>/observed/<RomFS SHA>.keys`: a JSON identity line, then one key per line.
- **Settled confound.** `DM_PSO_MANIFEST=off` warms nothing on the Mac, so measurement 1 is full vs none.

Criteria (ABBA, after one discard run, pairs within 10 points of free memory):

- **Memory gate:** at least 300 MB less footprint at frame 1200 and at the end, or 0p closes.
- **Play gate after 45 s:**
  - at most +3 long frames over 50 ms per 100 s, and at most +20 % time in long frames;
  - no new frame over 250 ms caused by library loads;
  - draw skips within ±5 %;
  - first frame at most +1 s;
  - no visual difference.
- **Scoped mode:** must keep at least 70 % of the "off" saving.

**Agreed order (chief engineer, 22 October):**

1. Run measurement 1 (full vs none; one discard run, then ABBA full/off/off/full, `memory_pressure` before each run) first, from about 22:20.
2. Port the observed-only scope only if the memory gate passes.
3. The observed-only mode can take its identity (chain, version 1.3.0, `source_romfs_sha256`) straight from our `seed-manifest.json`, so no `shader-keys.json` is needed.
4. The log fields go in after the 0m2 build: Metal allocated memory on the 300-frame present row (via `dm_mtl_l3_sample`), and footprint on the warm-up progress line (`预热进度`).

**Result of measurement 1 (chief engineer, 22 October):** the memory gate passed.

- **Memory:** footprint −650 MiB at frame 1200 and −550 MiB at the end. The cost is CPU-side library objects; Metal's own allocation changed by only −28 MiB.
- **Startup:** first frame 4.3 s vs 14.7 s.
- **Play:** smoothness after 45 s unchanged.
- **Only cost:** about 0.75 s extra on the kingdom load frame.

**Revised design: no port needed.** The observed set is already recorded. `pso_user_record` writes `S` (shader library used for drawing) and `R`/`C` (pipelines created) rows to each profile's `pso_user.tsv` and to the shared `<cache>/pso_collected.tsv`, which held 873 S, 2,818 R and 11 C rows on 22 October. Observed-only warm-up therefore means:

- `warm_start` keeps the title, user and shared lists;
- it drops the seed's `pso_manifest.tsv` (about 51k rows) and skips the trailing whole-cache scan;
- the switch is `TOTK_WARM_SCOPE=observed`, and `TOTK_WARM_ALL=1` keeps today's behaviour.

The details:

- **First frame:** it still waits for the title list (about 0.7 s). The 10 s gap came from the 51k background loads, not from that gate.
- **Fresh install:** title list only; the first kingdom entry pays about 0.75 s once.
- **Pipelines:** already included as `R` rows, which removes the load-frame pipeline creations.
- **Optional:** ship a starter collected list built from our test runs.

**Expected result** (to be verified): about 1 GB less footprint and most of the 21–70 s of background CPU at start removed, at the price of about 0.8 ms per first-use library load in play.

## Ranked plan

### 1. Translate and compile the whole game offline (removes most in-game shader stalls)

This is upstream TotK's "shader seed" (`docs/first-launch-shaders.md`, `games/totk/scripts/prepare_shader_seed.py`), now possible for Odyssey.

- **Inventory.** Inventory all shaders in `'totk'` mode across `ShaderData`, `EffectData`, `LayoutData`, `SystemData` and the `main` module (about 28.4k keys plus main's).
- **Translate.** Translate each with the default binding state, plus the recorded variant states, on every core. Upstream ran 24,032 translations in about 29 s on 10 cores. Odyssey has more keys, so expect minutes, not seconds.
- **Precompile.** Precompile each MSL into `.metallib` by MSL hash with the runtime's exact options (`-fmetal-math-mode=safe`, see `scripts/precompile_cached_shaders.py`). The runtime already loads `build/shader-libs/<sha>.metallib`. For the self-contained app, the libraries must sit next to its game binary (`exe_rel("shader-libs")`).
- **Warm up.** Prewarm only the title, menu and opening lists before the first frame; warm the rest in the background (upstream `warm_start` order, `TOTK_WARM_THREADS`).
- **Disk.** The current cache is about 166 MB for 862 variants. A whole-game seed may be several GB, so measure before choosing to store everything versus metallibs only.
- **What stays at runtime.** Only variants whose binding state the seed did not predict, the 18-style second variants. Async translation with draw-skipping already covers those.
- **Pipeline creation is not removed by the seed.** This corrects the 22 October first version of this page, after the chief engineer's challenge. In logged long frames with pipeline creation but no library compile (n = 53, all logs), each pipeline costs a median of **14 ms**, p90 134 ms, max 435 ms. The seed removes translation and library compile only. Pipelines still need the per-pipeline binary archive (ported, on by default), the warm lists, and ideally pipeline building at load time (plan items 3 and 4).
- **Coverage, checked offline.** The `main` module holds exactly 40 embedded shader keys in 2 BNSH containers; `sdk` and `subsdk0` hold none. The 16 runtime shaders absent from the archives are among those 40. Including `main`, the seed covers all 844 shaders seen so far. The material and effect archives are global, not per stage. A full scan found no shader containers (BNSH or BAHS) in any file of `ObjectData` (3,637), `StageData` (631), `LocalizedData` (57) or `EventData` (55). The whole-game set is therefore the four scanned archive folders plus `main`, and later kingdoms add no new shader sources. `main`'s eight `glslc*` imports are unresolved weak symbols that no run has called, so runtime-compiled shaders are not expected.

#### Binding state to translate with (answer for the implementer, measured 22 October)

The translator records in each variant's `deps` only what it asked about: `tex`, `img`, `attr` and `fout` per slot, plus `depth_minus_w` for vertex shaders. The runtime reuses a cached variant when those recorded answers match the bound state (`deps_match` in `totk_shader_rt.m`). Across the 862 recorded variants:

| Field | Observed answers (slot count) | Offline default |
| --- | --- | --- |
| `depth_minus_w` (vertex only) | true in 379 vertex variants; only 6 shaders also have a false one | **true**. Odyssey sets NVN's near-is-minus-w depth mode. Upstream's seed also passes `depth_minus_w=True`. |
| `fout` | float 1,325, nothing else | float (translator default; upstream `ANSWERS` leaves it `{}`) |
| `attr` | float 1,973, **uint 101** | float by default. Translate the recorded uint states as extra variants. |
| `tex` | 2d 1,344, **cube 300**, 2d_depth 18, 3d 13, 2darray 9, 1d 1 | translator default. **Check first:** translate one known cube-sampling shader with empty answers and confirm its recorded `deps` say `cube`. If they say `2d`, those 300 slots would always miss, and the cube variants must come from the replay list. |
| `img` | none recorded | `{}` |

**Recommended job list.** This follows upstream `prepare_shader_seed.py` `plan()`.

1. For every inventory key (`'totk'` mode, including the `main` module): `ANSWERS = dict(tex={}, img={}, attr={}, fout={})`, with `depth_minus_w=True` for vertex stages.
2. Then replay every recorded variant state. The runtime cache's `<key>.variants` files are exactly that list: 862 entries of (key, `deps`, `depth_minus_w`), the same role as upstream's `observed/<RomFS>.keys` and `shader_list.tsv`. The simplest route is to keep the existing cache files and import the seed alongside them; they never conflict, because variant names hash the answers.
3. Refresh the replay list after longer play. New states appear only as new `.variants` entries, which is task #5's "refresh after longer play".

### 2. Make the remaining first sightings cheap

- **Translator in process.** Run the translator in-process, or at least as persistent workers, which the async path already has. Measure process and IPC share first. Hydra (macOS) translates in-process with a direct Maxwell → MSL emitter.
- **Smaller SPIR-V.** Run `spirv-opt` before SPIRV-Cross: `--eliminate-dead-code-aggressive --eliminate-dead-branches --ccp --simplify-instructions --merge-blocks --eliminate-local-single-store`, or `-O`, but not loop unrolling or aggressive inlining. Smaller input makes SPIRV-Cross and the Metal front end faster. The effect is unmeasured.
- **The 3.9 s outlier.** Profile it and the large material shaders; a pathological pass is likely.
- **Separate caches.** Keep a guest-hash → SPIR-V cache apart from MSL, so a binding-state variant only re-runs SPIRV-Cross plus the Metal compile.

### 3. Compile at load time from what the stage loads (UnleashedRecomp model)

- UnleashedRecomp hooks asset loading, queues every pipeline a material can need, and holds the loading screen until those compiles finish (`PipelineTaskToken`). A recorded list covers post-effects and 2D.
- For Odyssey, prefetch on `nvnProgramSetShaders` is a first step (opt-in, `TOTK_SHADER_PREFETCH=1`). The fuller version would enqueue every program of `alRenderMaterial` / `EffectPtcl` when the stage loads, and gate the loading screen on it (upstream's `mtl_warm_wait` already holds the first world frame up to 15 s).
- Every engine reports a small leftover tail for effects and UI made at runtime. That matches `EffectPtcl` being among our misses.

### 4. Ship a GPU-code archive per machine (removes the Metal back-end compile)

- **AIR first.** Store compiled AIR (`.metallib`) rather than MSL. Metal binary archives hold GPU code, not front-end output, so only AIR removes the `newLibraryWithSource` cost.
- **Harvest pipelines (Metal 4).** Set `MTL4PipelineDataSetSerializerDescriptor.configuration = …CaptureDescriptors` on `MTL4CompilerDescriptor.pipelineDataSetSerializer`, and use `serializeAsPipelinesScriptWithError:` to write `pipelines.mtl4-json`. Then run `xcrun metal-tt game.metallib pipelines.mtl4-json -o game.mtl4archive -gpu-family <family> -macos-version-min 26.0` (from the local `metal-tt(1)` man page; the M3 family value is unverified).
- **Use the archive at runtime.** Load it with `-[MTLDevice newArchiveWithURL:error:]` and `MTL4CompilerTaskOptions.lookupArchives`, falling back to compiling.
- **No `metal-tt` alternative.** `…CaptureBinaries` with `serializeAsArchiveAndFlushToURL:` writes a ready archive on the device during a playthrough.
- **Overlap.** The per-pipeline `MTLBinaryArchive` already ported (TECHNIQUES.md) covers the same ground for legacy pipelines. Treat this as the Metal 4 successor, not an addition.

### 5. Cheap compile-option experiments (measure on the largest material shader)

- `MTLCompileOptions.optimizationLevel = MTLLibraryOptimizationLevelSize` may cut back-end time on huge shaders at some GPU cost. There are no published numbers. A possible pattern is Size first, then a default-level recompile of hot shaders in the background.
- Keep `mathMode` safe. Fast math is a correctness risk (upstream: it breaks a TotK integer-division idiom). Keep `enableLogging` off outside debug runs.
- An earlier benchmark attempt was killed before producing numbers. No compile-option numbers exist yet.

### 6. Low priority given 862 variants for 844 shaders

- **Metal 4 flexible pipelines.** Use `MTLPixelFormatUnspecialized` with `newRenderPipelineStateBySpecializationWithDescriptor:pipeline:`. They save back-end recompiles only when the same shader meets another render-target format or blend state. `MTL4RenderPipelineDescriptor` has no depth or stencil format, so depth-format pipeline variants disappear under Metal 4.
- **Mismatched-variant fallback.** For the 18 shaders with a second variant, draw with the compiled sibling while the exact one compiles, instead of skipping the draw. No Metal emulator documents this; validate visually.
- **Interpreters and ubershaders.** A Maxwell interpreter or ubershader on Metal (RPCS3 PR #8106, Dolphin ubershaders) is too large and slow for this ISA. Keep draw-skipping.
- **Keys.** yuzu and Ryujinx keep render-target formats and border colours out of shader keys (only the int/uint/float output type), and yuzu's portable cache is guest code plus queried environment. Revisit only if variants start to multiply.

## Sources

- **Local.** Shader cache `Odyssey_build/build/shader-cache/rt-totk-native-v4-unverified/`, `Odyssey_build/analysis/*.json`, boot logs.
- **Upstream MacNativeGames.**
  - `docs/first-launch-shaders.md` and `docs/pipeline-prediction-stalls.md`
  - `games/totk/scripts/prepare_shader_seed.py`: `control_key_len`, `bound_control`, `bnsh_shaders`, `--modules`
- **yuzu/Citron.** `src/video_core/shader_environment.cpp` and `renderer_vulkan/vk_pipeline_cache.cpp` (transferable cache: guest code plus queried environment).
- **Ryujinx.** `GpuChannelGraphicsState.cs` and `ShaderSpecializationState.cs`.
- **Hydra.** https://github.com/SamoZ256/hydra, `src/core/hw/tegra_x1/gpu/renderer/shader_cache.cpp` and its MSL emitter.
- **UnleashedRecomp.** https://github.com/hedge-dev/UnleashedRecomp: `gpu/video.cpp` and `gpu/cache/pipeline_state_cache.h`.
- **RPCS3 shader interpreter.** https://github.com/RPCS3/rpcs3/pull/8106
- **Godot pipeline compilation.** https://docs.godotengine.org/en/stable/tutorials/performance/pipeline_compilations.html
- **Apple.**
  - Metal 4 compilation: https://developer.apple.com/documentation/metal/using-the-metal-4-compilation-api
  - WWDC25 session 254: https://developer.apple.com/videos/play/wwdc2025/254/
  - Local man pages `metal-tt(1)` and `metal4-pipelines-script(5)`.
