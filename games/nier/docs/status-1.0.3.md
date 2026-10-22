# NieR:Automata (Switch) port: status 1.0.3

Experimental native Apple Silicon compatibility layer for the Nintendo Switch build of
*NieR:Automata The End of YoRHa Edition* (base 0100B8E016F76000 1.0.0 + update 0100B8E016F76800 1.0.3).
It is a copy of the [Bayonetta 3 port](../by3) skeleton with the game-specific parts (build IDs, module rewrite,
RomFS merge, runtime layout, overrides) redone for this title; the graphics layer is the shared
`third_party/d2r_gfx` NVN → Metal 4 replay.

Status as of 2026-10-20/21 on macOS 27.0.1 (26A434), MacBook Pro M5 (16 GB), built with the command-line tools only
(no full Xcode). Evidence: screenshots in `docs/screens/`, logs in `<work>/logs/`.

## What works (verified with the packaged app)

| Stage | Result |
| --- | --- |
| Container handling | base NSP (10.9 GiB, 4 NCA) and the update NSZ→NSP (23.7 MiB) parse; update Program = ExeFS + sparse BKTR RomFS; common tickets decrypt both without `title.keys` entries |
| Code extraction | base and update ExeFS verified (FS header, hash table, every block); update ships new `main` + `main.npdm` only, `rtld`/`sdk`/`subsdk0` identical to base |
| Offline rewrite | `third_party/d2r/patch_nso.py` (`DM_RULESET=native DM_PRIVATE_GATES=0`) rewrote `main` (8260 sites), `sdk` (11153 sites, 109 svc gates), `subsdk0` (10325 sites) with no unsupported instruction; SDK 12.3.9 modules |
| RomFS | update RomFS merged onto the base: 660 files, 11,644,591,488 bytes, all six IVFC levels verified (SHA-256 pinned in `assets/expected-v1.0.3.json`) |
| Boot | loader → module ctors → `nnMain` → SDK 12.3.9 threads/events/memory → RomFS mounts → bsd:u, HID, audren:u, saves, account → first presented frame at 0.348 s |
| Picture | opening VP9 movie (3692 frames, 1.10 ms/frame), the real title screen (ruined city with vegetation and water), start/load/new-game menus, difficulty select, prologue text and the prologue cutscene render at 30 fps (2982×1670 drawable, display-link paced 33.33 ms) with correct Chinese glyphs. Two NVN replay bugs were fixed on 2026-10-21: a geometry-only stage no longer drops the whole draw, and the 1920×1080 RG11B10Float → RGBA8Unorm copy (HDR scene → window) is now converted through a scratch texture instead of being refused. Remaining: the `NieR:Automata` logo on the title screen draws as ~4×4-texel white blocks (suspected BC decode, see gaps) |
| Input | keyboard → HID mapped (J = A, Return = Plus); the start menu reacted to a synthetic A press |
| Audio | **silent.** `audren:u` is opened three times at start-up, the SDK queries the output device (channel count, name, notification event) and then never opens `audout:u` / never calls `nn::audio::OpenDefaultAudioOut`; the host-side public-AudioOut implementation (`runtime/audio_public.c`) is therefore never reached. See gap 10 |
| Gameplay | the prologue runs on past the cutscene into the first combat scene: HUD, item use ("已自动使用回复药：小（持有数量：10）"), damage numbers, level display all update, and the game keeps running at 30 fps. The 2026-10-20 run showed the 3D picture badly corrupted (`docs/screens/06-gameplay-corrupt.png`); after the two replay fixes the same prologue scene renders correctly: the six flight units fly as lit 3D models with green engine glows over the landscape, `docs/screens/09-gameplay-fixed.png` and `10-flight-unit-fixed.png` |
| Saves | host save storage mounted at `<work>/user-data/save/<profile>`; no save/load round-trip tested yet |
| Packaging | `make app` builds `NieR Automata.app` (launcher + runtime, ad-hoc signed, icon from the game's own control data); the launcher sets the GPU switches, redirects logs to `<work>/logs/nier.log` and execs the runtime |

## Known gaps (they decide what the picture looks like)

1. **Geometry/tessellation shaders are not translated** (Metal has no geometry stage; this translator covers vs/ps/cs).
   A program with an untranslated stage 2 is logged once and its draw is skipped (`NIER_KEEP_GS_DRAWS=1` draws it
   anyway — tested: keeping it smears unexpanded triangles, e.g. the blocky title logo and green washes, because
   this engine's overlay/logo shaders rely on the geometry stage). The geometry *amplification* itself is therefore
   still absent: GS-generated copies (grass, particles, some silhouettes, HUD quads) are missing. Proper fix: emulate GS (vertex amplification
   / compute expansion) or translate the specific NVN GS programs this engine uses.
2. ~~**Driver constant `c2+0x160` is read as 0.**~~ **已澄清（2026-10-23）**：这是误报。该片元程序实际只读 `c2+0x168`
   （`.z`），那里已经填了句柄；`c2+0x160` 不是它读的字（`docs/color-cast-diagnosis.md:42`、`docs/port-plan-2026-10-23.md`
   的偏色章节）。可以整条删掉，留在这里只是标记它曾被记录。
3. **Mid-pass / partial-mask / scissored colour clears** run through the isolated `TOTK_GPU_MIDCLEAR=1` path (now
   defaulted for this game in `runtime/loader.c`); that path has not been compared against the console's clears.
4. **`nvnCommandBufferReportValue`** was implemented as a 4-byte uniform write (`OP_UPDATE_UB`); the game's use of it
   looks like a progress/fence value, not yet cross-checked.
5. **Not implemented / warned at runtime:** `logic op 3`, `alpha test 5` (drawn without), `nvn` mid-clear correctness
   as above. The texture→texture copy `1920×1080 format 92 (RG11B10Float) → 70 (RGBA8Unorm)` is **no longer skipped**:
   `copy_t2t_convert` runs an 8×8 compute kernel and, because the window swapchain is render-target-only, converts into
   a cached scratch texture and blits that into the destination. That copy was the reason the scene never reached the
   window (the menus drew straight into the window and looked fine while the 3D scene was stale garbage).
   The title-screen logo still draws as white 4×4 blocks, which points at the BC decode path or the logic-op/alpha-test
   fallbacks; not diagnosed yet.
6. **`fsp-srv` is not implemented.** The SDK's profanity filter (`nn::ngc`) mounts system data through it; the port
   overrides `nn::ngc::ProfanityFilter::Initialize` to succeed with an empty filter. Player-name/profanity filtering is
   therefore a no-op. Any later `fsp-srv` user will abort the same way.
7. **GPU submission is a switch, not a proof.** The shared replay layer keeps submission off for a game unless
   `TOTK_GPU_ALLOW_SUBMIT=1` (another title's unproven GPU work kernel-panicked macOS). The launcher sets it for
   NieR; treat long sessions as experimental.
8. **No performance tuning, no preset UI, no iOS path.** The launcher only starts the game. Frame pacing is 30 fps
   (the game's own), GPU keep-alive dominates the GPU budget (93 % of the budget is the keep-alive spin, 7–10 % the
   game's own work).
10. **Audio is blocked in the guest, not in the host.** The SDK's audio-device code opens `audren:u` (three times),
    asks for the active output device channel count, its name and a device-change notification event, and then stops:
    no `audout:u` open, no `nn::audio::OpenDefaultAudioOut` call, so the host's public-AudioOut implementation
    (`runtime/audio_public.c`, Core Audio `AudioQueue`, PCM16 48 kHz stereo, `BY3_AUDIO=0` silent sink) stays unreached.
    Found and fixed on the way: `device_event_pulse()` used to signal the device-change event every 100 ms, which made
    the guest re-open `audren:u` 8261 times; the event is now created and left unsignalled (`NIER_AUDIO_DEVICE_PULSE=1`
    restores the pulse). Neither signalling it once nor returning 6 channels changes the outcome, and no guest thread is
    wedged (audio workers wait on `nn::os::WaitConditionVariable`). Next steps: log the reply shapes of device commands
    5/10/12 and every `smGetService` result, check that the notification event handle satisfies the guest's
    `nn::os::WaitSystemEvent` expectations, and instrument the SDK's audio-device entry points to see which check fails.

11. **Colour cast and a stale-rectangle seam in the 3D scene.** The prologue flight scene shows the geometry,
    water and lighting but tinted green, with occasional rectangular stale regions, and the user reported the colours
    flashing (m02052). Measured so far: the one converted copy per frame is `1920×1080 92→70 offsets 0,0→0,0` and its
    dumped destination (`cvdump` input command → `NIER_DUMP_CV=<dir>`) holds the **title screen in the top ~540 rows
    only**, even while the window shows the load menu, i.e. that buffer is a lazy/stale scene buffer and *not* the
    displayed composite — the visible seam does not come from the conversion itself. The draw accounting on the same
    build is `TOTK 回放丢弃的绘制 至帧 1800: cond-unknown=900 shader-missing=900`, so the remaining holes line up with
    dropped geometry-stage draws (gap 1). Next step: dump the *render targets* of the scene passes instead of the
    converted copy, and compare the sampled HDR values (RG11B10Float view) against the expected colour space.
    A/B on 2026-10-21 00:54: the same prologue frame was captured with the draw dropped
    (`docs/screens/12-flight-holes-drop.png`: two stale boxes at the top-left) and with `NIER_KEEP_GS_DRAWS=1`
    (`docs/screens/13-flight-holes-keep.png`: one much larger stale box across the top-left quadrant). **The stale
    region appears in both modes**, so it is not caused by dropping the geometry-stage draws; it is a coverage problem —
    some pass leaves part of its target unwritten each frame (the unwritten part still holds an older frame). Next
    experiment: log every pass whose viewport/scissor does not cover its target and whose load action does not clear,
    and check the window texture's clear-on-acquire path.

9. **Untested areas:** gameplay past the prologue (combat, scene streaming, physics), audio output quality, save/load,
   New Game+/DLC (DLC NSP present but no add-on content mounted), the 1.0.0 base ExeFS as an alternative code source.

## Build and run

```sh
cd games/nier
WORK=/Volumes/bit2t/switch/totk/NIER_build           # extracted data, rewritten modules, caches, saves
VENV=/Volumes/bit2t/switch/totk/TOTK_build/venv/bin/python
BASE="/Volumes/bit2t/switch/games/NieR Automata The End of YoRHa Edition/NieRAutomata The End of YoRHa Edition [0100B8E016F76000][v0][Base].nsp"
UPDATE=<update.nsp>                                  # the update NSZ decompressed with nsz

# once: containers → exefs → rewrite → romfs merge → runtime tables
make prepare patches mods app PY=$VENV WORK=$WORK GAME="$BASE" KEYS=/Volumes/bit2t/switch/totk/key+firmware/prod.keys
open "NieR Automata.app"
```

Dev loop without the bundle: `make runtime WORK=$WORK` then `cd $WORK && ./build/nier-native rewritten/v103 boot`
with `TOTK_GPU_ALLOW_SUBMIT=1 TOTK_GPU_MIDCLEAR=1`. Screenshots without the window manager:
`echo "shot $WORK/logs/shot.png" >> $WORK/logs/input.txt` (the launcher creates that file).

## Next steps, in order of what limits the picture

1. Translate or emulate NVN geometry-shader programs (biggest visual gap; 564 skipped draws at the title menu).
2. Identify `c2+0x160` in the NVN driver constant bank and fill it from the game's `nvnCommandBuffer` state.
3. Probe the mid-pass clear path against the console (a scene with partial clear) instead of defaulting the switch.
4. Run gameplay past the prologue and record what breaks first (scene streaming, physics, audio, saves).
5. Then performance: keep-alive spin (93 % GPU budget), shader warm-up, resolution presets.

## 2026-10-21 复检：接缝与声音（第二轮）

**接缝（画面缺口 / 灰绿分界）**：在同一个序章飞行段分别用默认（丢掉几何着色器绘制）和 `NIER_KEEP_GS_DRAWS=1` 抓帧，两种模式都有接缝 → 与「丢不丢几何着色器」无关；打包后的 app 同样复现。
- 换格式拷贝不是原因：整轮只发生一次 `1920x1080 格式 92→70 偏移 0,0→0,0`，且把它 dump 出来（`cv_dump`，输入命令 `cvdump`，`NIER_DUMP_CV=<目录>`）看到的是**上一帧的标题画面**、只填了上面约一半 —— 这张缓冲并不是上屏合成图。
- 缩放拷贝也不存在：`copy_t2t` 里尺寸不一致会走 `RED_ONCE(33, "贴图→贴图拷贝未实现的形态…")`，整轮红行里没有这一条 → 放大是一笔绘制，不是拷贝。
- 逐趟日志（`TOTK_LOG_PASS=1`）与覆盖日志（`TOTK_LOG_COVER=1`）显示：场景是**按半分辨率画进整张目标的四分之一**（`1920x1080` 目标 + `960x540` 视口，每帧都有），另有 `800x450→400x225→200x112→100x56`、`2048x2048`（1024 视口）等链；所有视口原点都是 `(0,0)`。没画的区域留着上一帧，合成后就是灰绿接缝。
- 还查了驱动常量：那支读 `c2+0x160` 的片段着色器读到的位置正落在回放层故意不填的空洞 `[0x120,0x168)`（`drv_fillable`，`nvn_mtl.m:4908`）里，回放层按 0 读 —— 怀疑合成用的缩放/偏移就在这里，但没有依据先不动。
- 更正一条：`cond-unknown`（条件渲染读不到计数，`nvn_mtl.m:1758`）**仍然会画**（`enabled=1`，只记数），不是缺口来源；真正不画的是 `g_draw_skip[15]`（阶段没翻译 / `prog_fit` 失败）。所以默认模式的 18,744 笔少画全部来自几何着色器；而保留模式（0 少画）也还有缺口 → 与「趟没盖住」一致。
- 新增实验开关 `TOTK_FULL_VIEWPORT=1`：把「视口正好是目标一半」的趟放大到整张目标再画（日志 `【实验】把 WxH 的视口放大到整张目标`）。默认关闭。
- **这个实验一次都没命中**，原因查清楚了：`[pass]` 里那些 `1920x1080` 目标 + `960x540` 视口的趟，在编码前就被丢掉了（`prog_fit` / 阶段没翻译，`g_draw_skip[15]` 那条路），所以它们根本走不到设视口那一步 —— 在飞行段实测 2,225 条这种趟、0 次命中，`NIER_KEEP_GS_DRAWS=1` 时同样 0 次。结论：**场景那半分辨率趟 = 几何着色器程序那一类**，缺口的根因和几何着色器翻译是同一个问题，不是合成系数写错。
- 好消息是这类程序很少：把所有日志里「NVN stage」红行按 shader 键去重，**只有 1 支**（`137b54dc5462e5bbbf8aac39ee488d25e85f5b6737b5d6649a34fc1c046b9371`，stage 2），却占掉帧 4500 前 18,744 笔绘制。下一步值得为这一支单独做「顶点放大」模拟：先让翻译服务把 stage 2 的 SPIR-V 也生出来、看清它 EmitVertex 的次数与偏移来源，再用 `instance_id`/`base_instance` 放大成 N 份绘制。

**声音**：仍然没有音频输出。整轮 `audren:u` 只开 3 次、设备查询（5 通道数 / 10 名字 / 12 事件）各一次就停，`audout:u` 一次都没开，`nn::audio::OpenDefaultAudioOut` 从未被调用（`runtime/audio_public.c` 的公有 API 覆盖因此走不到）。设备变更事件：客人从不等待它（`WaitSynchronization` 加了日志但一次没触发），一次性 signal 只会让它重新枚举一次。下一步要把 SDK 音频初始化那段（`nn::audio` 的 `AudioOut` open 之前）跟到调用点 —— 现在只走到 sdk 的 gate 蹦床，`runtime_trace_guest` 在那里拿不到栈。

## 2026-10-21 几何着色器本体（已拿到 SPIR-V）

- `--serve` 的包装层只放行 `vs|ps|cs`（`games/totk/tools/totk_xlate/Service.cs:287`），但同一份预编译二进制有**批处理模式**：`build/shader-service/totk_xlate <job.json> <outdir>`，stage 字符串不校验、真正的阶段由代码里的 SPH 头决定。于是把运行时写下的 `.code`（`build/shader-cache/rt-totk-native-v4-unverified/137b54…-b7530f698e76.code`，图形 = 30 字节头之后的 SPH+指令）喂给批处理，成功出 `gs137b54.spv`（11,648 B）与 `.res.json`。
- 用 `build/spirv-cross/spirv-cross --version 460 --vulkan-semantics` 转成 GLSL（存 `docs/gs-137b54.glsl`，223 行），结构看清楚：
  - `layout(triangles) in;` + `layout(passthrough) in gl_PerVertex` + `#extension GL_NV_geometry_shader_passthrough` —— 这是**直通（passthrough）几何着色器，不做顶点放大**，只决定「这个三角形过不过」。
  - 判定：读三个顶点上的 `_25`（location 4，位运算做 early-out）、`_27`（location 2，屏幕空间叉积算面积）、`_37`（location 3，要写进缓冲的值）；叉积为 0 / 面积为零 / 边界都落在同一像素格时判为退化三角形。
  - 副作用：保留的三角形用 `subgroupBallot` + 自写的 `_54()` 前缀和加 `atomicAdd(gp_s0._m0[0])` 求紧凑下标，把三个顶点的 `_37` 写进 `gp_s1` 存储缓冲（**流压缩/可见性列表**）；被剔除的写 `gl_BuiltIn_5253[0]=0` 后 return。
- 含义：视觉上它不改顶点位置，所以「不画」并不等于画面正确 —— 它同时承担「剔除退化三角形 + 把顶点写进紧凑缓冲」，后者是后面某个 pass 的输入。丢掉这一整笔绘制会连带丢掉那个缓冲的写入；画出来又少了剔除，于是出现大片退化三角形（标题 logo 的方块/绿色涂抹）。
- 下一步的实现路线：**用计算着色器按三角形模拟这一支**（每三角形一个线程：同样的退化判定 → `atomicAdd` 求下标 → 写三个顶点的 `_37`），视觉绘制仍走 VS+PS 直通；这样既恢复缓冲写入，又不需要 Metal 有几何阶段。

### 几何着色器那支：其实是「剔除 + 压缩」，不是视觉绘制

`137b54dc…`（唯一一支 stage 2）的 SPIR-V 用批处理模式取到（`docs/gs-137b54.glsl`），结构是
`layout(triangles) in` + `NV_geometry_shader_passthrough`：不移动顶点，只做两件事——

1. 读逐顶点 varying location 2（屏幕空间叉积，用来算面积）、3、4；
2. 退化/零面积的三角形写 `gl_BuiltIn_5253[0] = 0` 后 `return`；保留的三角形用 subgroup 前缀和 + `atomicAdd` 求紧凑下标，把三个顶点的 location 3 值写进存储缓冲（c0+0x104 计数、c0+0x10C 列表）。

由此两条实测现象都解释得通：

- **丢弃这支绘制**（当前默认）：标题 logo、菜单、飞行单位都正常——说明这支 pass 的三角形在主机上本来就被它自己全部剔掉，视觉输出为空；它真正的产出是那份紧凑列表。
- **硬画出来**（`NIER_KEEP_GS_DRAWS=1`）：退化三角形被光栅化，标题 logo 变成大块白方块，飞行场景出现大片绿/灰涂抹。

所以「画面缺口」不是丢了这一支的像素，而是缺了它写进存储缓冲的三角形列表——后续 pass 读这份列表来画东西，列表空着就什么都画不出来（留下上一帧的矩形残影）。正确的做法是：视觉上不画（保持默认），另外跑一个按三角形的计算 pass 复现「退化判定 + atomicAdd + 写 location 3」。这需要先把这条程序的 VS 输出（location 2/3/4）落到缓冲里（Metal 没有 transform feedback），工作量大于前几轮的任何单项改动。

### 缺口的两层原因（2026-10-21 实测）

1. **几何着色器是 GPU 驱动绘制的生产者**。`137b54dc…` 把可见三角形压进存储缓冲（计数 `c0+0x104`、列表 `c0+0x10C`）。默认丢弃这支绘制，等于让这份列表永远为空。
2. **间接绘制的参数在 CPU 侧解析**（`nvn_mtl.m:10295` `draw_resolve` 的注释就写着这是近似：同一次提交里由 GPU 写入的参数「通常是上一帧的值」）。实测 `TOTK_LOG_INDIRECT=1` 一局记录 7,965 次间接绘制，其中 **4,830 次读到 `count=0` 而被跳过**（61%）。GPU 驱动绘制的计数本来就该由 GPU 写，CPU 读到的 0 直接变成「不画」。

所以只补几何着色器还不够：就算 gp_s0/gp_s1 被正确写入，参数仍可能被 CPU 提前读成 0。完整做法是两步——(a) 复现这支 GS 的剔除 + 压缩（需要把该程序的 VS 输出落进缓冲：Metal 没有 transform feedback，得给这条 VS 出一个「带输出缓冲」的变体）；(b) 间接绘制改为把参数缓冲交给 Metal（`drawIndexedPrimitives:...indirectBuffer:`），让 GPU 在执执行时读到同帧算好的计数，而不是 CPU 在回放时读内存。

诊断开关：`TOTK_LOG_INDIRECT=1`（记录每次间接绘制读到的 count/instances 与累计零值比例）、`TOTK_LOG_SBUF=1`（记录每程序每槽的存储缓冲地址）、`NIER_KEEP_GS_DRAWS=1`（画出 stage 2，用于对照观察涂抹）、`NIER_LOG_GSKEYS=1`（打印带 stage 2 的程序的 vs/ps/gs 键）。

### 关键定位：几何着色器的「计数缓冲」就是间接绘制的参数数组

`NIER_GS_COUNTER_HINT=1` 的诊断把几何阶段的存储缓冲打印出来：

```
[gs-hint] 顶点 900 ssbo2[0]=0x7c23c12c60 ssbo2[1]=0 ubo2[0]=0/0
[gs-hint] 几何阶段计数器写入 900（顶点 900，计数缓冲 0x7c23c12c60）
```

- `S.ssbo[2][0].addr` 与 `draw_resolve` 里那些「读到 count=0」的间接参数地址是同一处内存（同一次启动内地址一致，ASLR 只改基址）——也就是说这支 GS 的 `gp_s0`（`atomicAdd` 的计数器）**就是间接绘制的参数数组**。
- 这次绘制 900 个顶点 = 300 个三角形，计数器按 `3 × 保留三角形数` 递增；参数数组的 `count` 因此等于「紧凑列表里的顶点数」，消费方直接用它发起间接绘制。
- `gp_s1`（列表，地址在 c0 里、`ssbo2[1]` 未绑）承载每个顶点的一个 float（`_37.x`），即紧凑后的顶点数据。

于是缺口链条完整了：GS 被丢弃 → 参数数组 `count` 保持 0 → 6,336 次间接绘制被跳过（实测 61%）→ 那些物体所在的屏幕区域留在上一帧。

**还差的一步**：参数数组的 `count` 可以直接按「顶点数」推算（诊断已能做到），但列表里的每个顶点的 `_37.x` 必须来自该程序 VS 的 location 3 输出，这个值我们目前拿不到（Metal 没有 transform feedback）。要么给这条 VS 生成一个「把 location 2/3/4 写进缓冲」的变体再跑一个精简的三角形压缩内核，要么在 CPU 上复现这条 VS 的数学（该 VS 只读一个 float4 顶点属性和两张 uniform 数组）——前者是正确路线，后者是权宜。
