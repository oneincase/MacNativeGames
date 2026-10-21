## 2026-10-21 (later) — P2: the geometry-stage capture and the compaction kernel are implemented; the consumer draw still needs a GPU indirect draw. P4's white blocks are not the geometry stage.

- **P2, implemented and verified (default off, `NIER_GS_COMPACT=1`).** `nvn_mtl.m` now has three pieces:
  (1) `msl_vfield()` reads the varying *field names* out of `struct main0_out` (the old code reused `msl_varyings()`,
  which returns the *types*, so the patch produced `out.float4` and the capture variant failed to compile with
  "no member named 'float4' in 'main0_out'"); `gs_capture_pso()` appends `totk_cap2/cap3/cap4/capp [[user(locn32..35)]]`
  after the struct, turns `vertex main0_out main0(` into `vertex void main0(` (Metal refuses a non-void vertex function
  when `rasterizationEnabled` is NO) and rewrites every `return out;` to store the four capture outputs.
  (2) `k_gs_compact_msl` follows `docs/gs-137b54.glsl` line by line: drop when `(b0&b1&b2)!=0`, keep when
  `((b0|b1|b2)&64)==64`, otherwise the area cross product `w2>=w1` drops it, then the +0.5 pixel snap test; a kept
  triangle takes three indices from `atomic_fetch_add_explicit` and writes the three `_37.x` words. The counter is
  `S.ssbo[2][0].addr` and the list is `S.ssbo[2][2].addr` — the earlier `c0+0x10C` was simply the wrong address.
  (3) `draw_inner` parks the capture instead of skipping the draw, and `copy_t2t()` runs `gs_compact_flush()` outside
  any render pass. Evidence: `[gs] 压缩内核就绪：300 三角形 → 计数 0x… 列表 0x…`, 0 faults over 14 s ×2.
- **P2, the remaining blocker is the consumer draw.** The count our kernel writes into the game's record is reset by
  the game before the next frame's CPU replay reads it, so the indirect draw still resolves to 0. Reading it back on
  the CPU is not possible: committing from inside the replay of the render stream (old `NIER_GS_SYNC=1`) breaks the
  stream's encoder and aborts in `rs_worker` with "renderCommandEncoderWithDescriptor failed"; that switch now reports
  itself as retired instead. The correct route is a GPU indirect draw (`drawIndexedPrimitives:indirectBuffer:…`, the
  5×uint32 record already matches `MTLDrawIndexedPrimitivesIndirectArguments`) plus the compaction encoded between the
  two passes — the plan file carries the exact steps.
- **P4: the title-screen white blocks are not the geometry stage.** They look identical with the compaction on, with
  the counter forced to 900, and with the geometry stage skipped entirely (`NIER_GS_COMPACT=0`); the two title
  screenshots differ by 2.1% of the pixels and the blocks are in the same place. The port's bloom shortcut
  (`DM_NVN_BLOOM_Q=0`) makes no difference either, and the `logic op 3 / alpha test 5` draws (new switch
  `NIER_LOG_ALPHATEST`) are 4-vertex full-screen post quads, not text batches. The earlier changelog entry blamed
  `NIER_KEEP_GS_DRAWS=1` for them; that attribution is wrong and is corrected here. The block size and the glyph
  outlines inside them match a font atlas sampled from a wrong mip / stale upload, which is the next probe.
- **Not committed as a behaviour change:** everything above is behind `NIER_GS_COMPACT=1`; default runs (and the
  packaged app) are unchanged apart from the retired-drain diagnostic.

## 2026-10-21 — picture: geometry-stage draws kept, HDR scene copy converted; audio blocked in the guest

- **Replay: geometry-stage programs are diagnosed and skipped (default), with an opt-in draw-anyway mode.**
  `nvn_mtl.m` `prog_get()` marks stage 2 as missing (one red line per program) so the draw is skipped, as before;
  `NIER_KEEP_GS_DRAWS=1` keeps the translated stages and draws with unexpanded vertices. Tested both ways on the
  title screen and the prologue: keeping the draw smears huge triangles (blocky white logo, green washes), so the
  default is dropping the draw. The 2026-10-21 experiment that flipped the default also showed the prologue scene
  reaching the screen (3D flight units over the landscape), so the remaining picture work is not in this switch.
- **Replay: format-changing texture→texture copies are converted instead of refused.** The 1920×1080
  `RG11B10Float → RGBA8Unorm` copy (HDR scene → window) was dropped with "贴图→贴图拷贝未实现的形态". `nvn_mtl.m`
  now converts with an 8×8 compute kernel (`k_cv_msl`); when the destination is not shader-writable — the window
  swapchain is `ShaderRead|RenderTarget` only — the kernel writes a cached scratch texture and a blit copies it into
  the destination. The real title screen (ruined city, vegetation, water) now reaches the window where it used to be
  white/stale. Evidence: `docs/screens/07-title-screen-fixed.png`.
- **Audio: found the 8261× `audren:u` re-open loop and fixed it.** `runtime/audio_mixer.c` `device_event_pulse()`
  signalled the device-change event every 100 ms, so the client re-enumerated for ever. The event is now created and
  left unsignalled (`NIER_AUDIO_DEVICE_PULSE=1` for A/B; `NIER_AUDIO_DEVICE_QUIET=1` disables the one-shot signal),
  and the device handler honours `NIER_AUDIO_CHANNELS` (default 2). Result: three `audren:u` opens, three device
  queries, then silence.
- **Audio: added a public `nn::audio::AudioOut` implementation that the guest never reaches.**
  `runtime/audio_public.c` implements `OpenDefaultAudioOut` / `Start` / `Stop` / `Close` / `Append` /
  `GetReleased` / buffer-info and state accessors over Core Audio `AudioQueue` (PCM16, 48 kHz, stereo downmix,
  `BY3_AUDIO=0` silent sink), registered through `runtime_audio_override()` in `runtime/services.c`. Every run shows
  **zero** calls: the guest queries the `audren` output device (channel count, name, notification event) and then never
  opens `audout:u`. Not a host bug and no thread is wedged; the blocker is inside the guest's audio-device init.
- **Coverage diagnostic `TOTK_LOG_COVER=1`** (`nvn_mtl.m` `cover_log()`): on the first draw of a pass without a colour
  clear it reports a viewport/scissor that does not cover the attachment. In the packaged-app run the scene passes come
  out as a 1920×1080 target drawn with a 960×540 viewport (and 800×450 / 400×225 / 200×112 / 100×56 chains), i.e. the
  game renders the scene at half resolution into part of the target and relies on a later upscale/composite; the visible
  seams (`docs/screens/14-flight-app-seams.png`) are regions that the later pass did not cover. Next: find that
  upscale/composite pass (likely the copy or the fullscreen quad whose draw is skipped) and check its region math.
- **A/B on the stale rectangle (2026-10-21 00:54).** The same prologue frame was captured with the geometry draws
  dropped (`docs/screens/12-flight-holes-drop.png`, two boxes at the top-left) and with `NIER_KEEP_GS_DRAWS=1`
  (`docs/screens/13-flight-holes-keep.png`, one larger box over the top-left quadrant). The stale region appears in
  both modes, so it is not the dropped geometry-stage draws; it is a coverage problem in the replay (part of a target
  is left unwritten for a frame). Recorded as status gap 11 with the next experiment (log passes whose viewport/scissor
  does not cover a non-cleared target, and check the window texture's clear-on-acquire).
- **Diagnostic: `cvdump` input command dumps the converted destination.** `nvn_mtl.m` keeps `cv_dump()` behind a
  one-shot `nvn_cv_dump_request()`; `input.c` exposes it as `cvdump <dir via NIER_DUMP_CV>`, writing `cv-<frame>.rgba`
  (`RGBA8 <w> <h>` header). Used it to show that the converted buffer still holds the title screen in its top ~540 rows
  while the window shows a menu: the visible seam is not produced by this conversion (see status gap 11).
- **Verified on the prologue flight scene**: after loading the prologue save the six flight units render as lit 3D
  models with engine glows over the landscape, HUD and subtitles correct (`docs/screens/09-gameplay-fixed.png`,
  `10-flight-unit-fixed.png`), where the same scene was a rainbow smear before the two replay fixes.
- Diagnostics added: `[totk-ipc] opened service <name> (from <module>+0x<offset>)`, `NIER_TRACE_AUDIO` device-command
  traces, and a wait check that logs (and would signal) any guest wait on the device-change event.

# NieR:Automata (Switch) port: work log

A record of how this port is being made, for anyone who continues it or tries the same with another game. Each entry
says what was found, what was done and why, what went wrong on the way, and how it was checked. Newest entries go at
the bottom.

Conventions: dates are absolute; "verified" means a check with a recorded result, never "it looked right". Game data,
keys and saves are never committed. Status lives in [docs/status-1.0.3.md](docs/status-1.0.3.md).

## Log

### 2026-10-20 — Stage 0: containers, modules, engine identity

- Base NSP `0100B8E016F76000` is a plain PFS0 (6 entries, 4 NCA): Program (11,146 MiB; ExeFS 20,037,632 B with
  `fs_type=1`, RomFS 11,667,406,848 B **not** BKTR), Control, Data, plus a common ticket for
  `0100b8e016f76000000000000000000e`. `title.keys` has no NieR rows; the containers decrypt anyway.
- Update is delivered as an NSZ; `nsz -D` with the TotK venv produced a 23.7 MiB NSP whose Program NCA holds the
  same-size ExeFS and a 3.6 MiB **sparse BKTR** RomFS (the patch). Update main is
  `98397a10419894652f372d4dc1b6fc2c672765cd`; `rtld`/`sdk`/`subsdk0` are byte-identical to base, so only
  `main` + `main.npdm` change.
- The modules are NSO **v2 layout**: everything the existing loader reads (flags 0x0C, segments 0x10, bss 0x3C,
  compressed sizes 0x60) still applies, but the first segment starts at file offset **0x101**, not 0x100. The
  extractor therefore kept the file verbatim and let `nsoimg.py` decompress; no header change was needed.
- `main.npdm`: ACI0 with program id `0100B8E016F76000` and 47 services (`nvdrv`, `fsp-srv`, `hid`, `audren:u`,
  `vi:u`, `set`, …). Imports name
  `C:/Nintendo/NativeSDK12.3.9/NintendoSDK/Libraries/NX-NXFP2-a64/Release/multimedia.nss`, i.e. **SDK 12.3.9**,
  older than TotK's/by3's 13.3.2. Engine is Platinum's (exports `nninitStartup`, `nnMain`).
- Because `build_app.py` is hard-wired to TotK/BotW titles, NieR gets its own `games/nier` layer:
  `scripts/survey_container.py` (inventory), `scripts/extract_exefs.py` (generic title/update extraction with BKTR
  check), `scripts/merge_romfs.py` (base RomFS is a plain FS, not a sparse one), `scripts/prepare_runtime.py` (module
  order, ctor flags, PLT probe, RomFS index).

### 2026-10-20 — Stage 1: rewrite and RomFS merge

- `patch_nso.py` with `DM_RULESET=native DM_PRIVATE_GATES=0` rewrote all three modules with no "unsupported" hit:
  main 8260 sites, sdk 11153 (+109 svc gates), subsdk0 10325. That the generic rewriter handles Platinum's engine is
  a result, not an assumption: it would have stopped at the first unhandled instruction class.
- `merge_romfs.py` needed a `PlainBase` class: the base RomFS header has `generation == 0`, which the by3 code read as
  "sparse" and then failed hashing. With `PlainBase` (AES-CTR with the header IV) the merge produced 660 files,
  11,644,591,488 bytes and all six IVFC levels verified; SHA-256 is pinned in `assets/expected-v1.0.3.json`.
- `prepare_runtime.py` printed DT_INIT flags (main 0x12ce0d0, subsdk0 0x57f0d0, sdk 0xc910d0), 132103 relocations and
  the `GetSystemTick` PLT slot `0xd9a6d0`.

### 2026-10-20 — Stage 2: boot to `nnMain`, then a thread panic

- First boot reached module constructors, `nnMain`, memory pools, `bsd:u`, HID, one guest thread and a 34-entry
  `rom:/` listing, then `guest Break reason=0 result=0` inside `nn::os::detail::ThreadManager::CreateThread`. The
  break came from the SDK's own `AbortImpl` after `bl 0x584190` returned bit 0 clear.
- Root cause (found by instrumenting svc 0x04/0x05): NieR's SDK passes **thread stacks that live in the main module's
  data/bss**, not in the guest heap, and `stack_move()` only accepted heap/alias sources, so `MapMemory` of the alias
  failed with `0xdc01` 64 times and `MapAliasStack` gave up. Fixed by teaching `stack_move()` a third source class
  (`runtime_module_query` covering the whole range): copy module pages to the alias destination, never copy back,
  never `mprotect` the module pages, and report the module state for the source in `QueryMemory`. After that all game
  threads were created and the alias region held their stacks.
- Next: the game imports the **public** `nn::hid` six-axis wrappers (`StartSixAxisSensor`,
  `GetSixAxisSensorState`, `IsSixAxisSensorAtRest`, …), while the by3 table only named the `detail` ones, so they
  resolved to the SDK and opened the `hid` IPC service (not implemented). Added an always-active public table in
  `runtime/hid_feedback.c`.
- The following fatal was our own: `totk_shader_rt.m` died when `NSDataWritingAtomic` failed with "You don't have
  permission to save the file … in the folder rt-totk-native-v4-unverified" even though `fopen` in the same folder
  worked. The writer now retries without the atomic option and only then dies with the Foundation message.
- The shader service did not start (`python3 -I <work>/scripts/shader_service.py` missing because the runtime
  resolves its root two levels above the executable). Instead of teaching it about the work folder, the native
  translator path is used: `make shader-tools` copies `prebuilt/macos-arm64/totk_xlate` and builds `spirv-cross` from
  the pinned sources (symlinked from the TotK checkout at `aa217ae`), so no network clone is needed.

### 2026-10-20 — Stage 3: NVN commands, then first picture

- `nvnCommandBufferMidPassColorClear` family: the layer isolates mid-pass/partial-mask/scissored clears behind
  `TOTK_GPU_MIDCLEAR=1` pending a standalone probe. NieR issues them during boot, so the port defaults the switch in
  `runtime/loader.c` (opt out with `TOTK_GPU_MIDCLEAR=0`).
- `nvnCommandBufferReportValue` was unimplemented; implemented as a 4-byte uniform write (`OP_UPDATE_UB`) so the value
  lands in submission order.
- Programs using NVN stage 2 (geometry) were fatal ("unsupported NVN stage 2"). Metal has no geometry stage and the
  translator has no GS chain, so such programs are now marked missing and their draws skipped with one red log line
  per program. This is a real picture gap, not a fix — see status.
- Then the GPU-submission containment showed itself: `nogpu()` in `nvn_mtl.m` keeps submission off for a game unless
  `TOTK_GPU_ALLOW_SUBMIT=1` (a full run on another title kernel-panicked macOS). With the launcher's switches
  (`ALLOW_SUBMIT`/`WIDEN`/`BLOCKRT`/`MIDCLEAR`) the first frame was presented at 0.348 s and the opening movie decoded
  (3692 VP9 frames at ~1.1 ms/frame).
- The SDK profanity filter (`nn::ngc`) mounts system data through `fsp-srv`, which the port does not implement, so boot
  aborted there. Overrode `nn::ngc::ProfanityFilter::Initialize(void*, size_t)` to succeed with an empty filter
  (`host_ngc_init` in `runtime/services.c`); the game only filters player-authored text.
- A fragment program read driver constant `c2+0x160`, which the replay layer does not fill; the translator's JSON says
  `drv_unfillable` and the loader refused the whole program. `drv_scan()` already reads 0 for such words and only
  reports them, so the port now does the same (log + draw). TotK keeps the strict check (the change is in
  `games/nier/third_party`, not in TotK's copy).

### 2026-10-20/21 — Stage 4/5: packaging and the app

- `make app` needed two fixes: `extract_app_icon.py` imports `cryptography` (use the TotK venv's Python), and
  `iconutil` refuses to write the `.icns` straight onto this volume ("Failed to generate ICNS") while converting into
  a temp file works — the script now converts in the temp dir and copies the bytes.
- The launcher was by3's settings window (fps patches, resolution presets, mods) and its Play button stayed disabled
  for a game it did not know. Replaced with a small NieR launcher: title/version/data folder, an experimental-status
  note, Play/Quit; it sets the GPU switches, redirects to `<work>/logs/nier.log`, creates the diagnostic input file
  and execs `build/nier-native rewritten/v103 boot`. `NIER_AUTOSTART=1` starts without a click for unattended checks.
- Verified with the packaged app: start menu ("开始游戏 / 选项 / 按键设置"), difficulty select, prologue narration and
  the prologue cutscene (flight units 7E/2B/12H/11B/4B/1D, "IFF: MODE 2") render with correct glyphs at 30 fps,
  window title "NieR:Automata Mac — Ver. 1.0.3"; keyboard input reaches the game (a synthetic A press started the
  game).
- Screenshots: `docs/screens/00-launcher.png` … `05-hud-first-frame.png`.

### 2026-10-21 — Gameplay: logic runs, the picture does not

- Left running with no further input, the game left the prologue and entered the first combat scene: HUD, damage
  numbers, level and item-use messages update, the game stays at 30 fps. The 3D picture is heavily corrupted (rainbow
  smearing, garbage geometry), which matches the three known gaps: geometry-shader draws skipped, the driver constant
  `c2+0x160` read as 0, and unproved mid-pass clear handling. Screenshot: `docs/screens/06-gameplay-corrupt.png`.

### 2026-10-21 — 接缝定位 + 诊断开关（第二轮）
- 接缝与几何着色器无关（两种模式都有，app 也复现）；换格式拷贝不是原因（dump 出来是上一帧的标题、只填半张）；也没有缩放拷贝（`copy_t2t` 的尺寸不一致分支整轮没报过）。
- 新增诊断：`TOTK_LOG_PASS=1` 逐趟日志（目标尺寸 / 视口 / 剪裁 / 清屏 / 程序）、`TOTK_LOG_COVER=1` 覆盖日志（没清屏又没盖满目标）、`cvdump` 输入命令 + `NIER_DUMP_CV=<目录>` 把换格式拷贝的目标落盘（`RGBA8 W H` 头 + 原始像素）。
- 发现场景按半分辨率画进整张目标的四分之一（`1920x1080`+`960x540` 视口），另有 1/2、1/4 链；新增实验开关 `TOTK_FULL_VIEWPORT=1`（把这种视口放大到整张目标），默认关闭。
- 驱动常量：读 `c2+0x160` 的片段着色器落在回放层不填的空洞 `[0x120,0x168)`，按 0 读（未改）。
- 声音仍未解决：`audren:u` 开 3 次、设备查询后停止，`audout:u` 从未打开，公有 API 覆盖走不到。

### 2026-10-21 — 几何着色器 SPIR-V 解剖
- 发现 `totk_xlate` 的批处理模式可绕过 `--serve` 的 `vs|ps|cs` 限制，把运行时写下的 `.code` 直接译成几何阶段的 SPIR-V；出图转 GLSL 后确认 NieR 这支 GS 是 **NV passthrough**：只做退化三角形剔除 + 用 subgroup 前缀和/atomics 把顶点的 location 3 数据写进紧凑缓冲（流压缩），不做顶点放大。
- 解释了两个旧现象：丢掉它 → 紧凑缓冲没写入（后续 pass 读到旧内容，画面缺口）；画出它 → 少了剔除（退化三角形铺满，标题 logo 方块/绿色涂抹）。
- 新增实验开关 `NIER_GS_TRANSLATE=1`（向翻译服务申请 stage=`gs`，服务端会拒绝，仅用于记录请求）与文档 `docs/gs-137b54.glsl`。
- 2026-10-21 geometry stage decoded: `137b54dc…` is a passthrough GS that only culls degenerate triangles and compacts per-vertex location-3 data into a storage buffer (counter c0+0x104, list c0+0x10C); its visual output is empty on hardware, so dropping the draw keeps menus/logo correct, and the missing scene content is the empty compaction list. GLSL saved as `docs/gs-137b54.glsl`; `TOTK_LOG_SBUF=1` added to list storage-buffer addresses per program.
- 2026-10-21 GPU-driven draws: `TOTK_LOG_INDIRECT=1` on one run logged 7,965 indirect draws, 4,830 of them (61%) read `count=0` on the host and were skipped; the host resolves indirect arguments on the CPU by design (`nvn_mtl.m:10295`), so arguments a GPU pass of the same submission writes are read one frame late or as zero. Together with the GS compaction pass this explains the flight-scene holes: the producer list is missing (GS dropped) and the consumer's counts are read as zero.

2026-10-21 几何阶段：定位到「计数缓冲 = 间接绘制参数数组」
- 诊断开关 NIER_GS_COUNTER_HINT=1 打印出 `S.ssbo[2][0].addr`，与 draw_resolve 读到 count=0 的间接参数是同一段内存（同一次启动同一地址），说明这支 passthrough GS 的 atomicAdd 计数器就是间接绘制的 count 字段。
- 打开该开关后，`[ind]` 日志里 count=0 的比例从 61%（6,336/10,480）变成 0（0/5,245）：缺口链条闭合 —— 丢掉 GS → count 恒为 0 → GPU 驱动绘制全部跳过 → 屏幕区域停在上一帧。
- 仍然缺的一步：紧凑列表（c0+0x10C）里每个顶点的 _37.x 来自该程序 VS 的 location 3 输出，Metal 没有 transform feedback，需要给这条 VS 生成写缓冲的变体 + 一个三角形压缩内核（诊断开关默认关闭，仅用于验证）。

2026-10-21 P0 完成：换格式拷贝改渲染合成，窗口画面恢复
- 计算内核路线退役（真实提交下第一次 flush 即 SIGTRAP，前两轮"干净"都没提交 GPU）：`copy_t2t_convert()` 改为按游戏下发顺序立即执行的渲染合成；`rp_begin_ex` 里的 `cv_flush` 挂起机制删除。
- 合成 pass：`rp_end` → `rs_sync` → 源纹理写进共享片元参数表纹理槽 0（游戏着色器 argument-buffer ABI 不读该槽）→ `enc_r_begin`（DontCare 加载、尺寸=目标级尺寸）→ 全屏三角形 `src.sample(s)`（`constexpr sampler`，不占采样器槽）直接写目标 → `enc_r_end`（`bar_end` 队列屏障）→ `args_dirty_all()`。MSL `cvv/cvf` 与 present 管线同款 UV 约定。流式合成沿用编码线程路径。
- 门条件不变：仅 HDR 源→LDR 目标、整级 level-0、无偏移、非 3D、非压缩；块压缩目标直接回退（BLOCKRT 关闭下不会出现）。
- 验证：`NIER_T2T_CONVERT=1` + `TOTK_GPU_ALLOW_SUBMIT=1` stability.sh 13 秒 ×3 与 100 秒实跑全干净（0 fault）；整窗截图 YoRHa 启动序列完整、序章云海/字幕正常（绿色调与场景缺口为 P2/P3 已知残留，与本改动无关）；不带转换的默认路径 13 秒 ×2 干净。
- launcher 默认 `NIER_T2T_CONVERT=1`（`make app` 重打包生效）；`docs/screens/07-composite-boot.png` 为合成路径下的启动画面。
