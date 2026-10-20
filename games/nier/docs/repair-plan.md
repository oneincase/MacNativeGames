# NieR:Automata (Switch 1.0.3) 修复改进方案

基线：`games/nier` 可启动、标题/菜单/电影/序章文本正常；默认 `NIER_T2T_CONVERT` 关、GS 阶段默认丢弃。
所有条目都写"改哪里 / 怎么验 / 风险"，按 P0→P5 顺序做，每项独立可回滚。

---

## P0 稳定性：换格式拷贝（窗口合成）会踩坏 guest 内存

**现象**：`NIER_T2T_CONVERT=1` 时 13 秒内 guest fault（`signal=5/10`，fault 落在 guest 地址），关闭则 3/3 干净。

**已定位**：计算派发原本插在渲染通道编码中途 → 已改为 `copy_t2t_convert()` 入队、`rp_begin_ex()` 开头 `cv_flush()` 执行；开发路径曾 3/3 干净，复测又 3/3 崩 → 剩余可疑点是**参数缓冲生命周期**，其次是屏障声明。

**改法**
1. `cv_flush()` 里 `arena_alloc(32,&pb)` 换成模块级持久缓冲：`static id<MTLBuffer> g_cvb;`（`storageModeShared`，16 B，`res_add`），每次 CPU 直接写 `contents`，不参与帧回收。
2. 屏障统一：dispatch→blit 保留 `MTLStageDispatch→MTLStageBlit`；blit 后若同帧还有渲染写同一 `d`，补 `MTLStageBlit→MTLStageRender`（只在 `d` 上有后续绘制时加，避免全局 All）。
3. 仍崩则**放弃计算内核**，改"全屏渲染 pass 合成"：在 present 前开一个 `1920x1080` pass，用一条片元 `return src.sample(sampler, uv)` 把 HDR 源写进窗口目标（复用现有 `blit()`/present 路径，不碰计算编码器）。

**进展（2026-10-21）**：参数缓冲改成持久 `MTLBuffer` 后，`NIER_T2T_CONVERT=1 TOTK_GPU_MIDCLEAR=1` 的 13 秒 ×5 全干净——说明"参数缓冲被 arena 回收"确实是原因之一。但打开 `TOTK_GPU_ALLOW_SUBMIT=1`（真正提交 GPU）后仍在第一次 `cv_flush()` 处以 SIGTRAP（`pc` 在系统库，堆/断言）崩溃；此前所有"干净"的测试都没真正提交 GPU。因此计算内核这条路线在真实提交下不可用，**改为"全屏渲染 pass 合成"**：用 MTL4 渲染编码器在目标上开一个 pass，画一个三角形，片元里 `texture2d<float> src` 采样，写入目标（目标 usage 5 = shaderRead|renderTarget，正好可写）。这一段需要复用 `draw_inner` 的 argument-table 绑定方式（`setArgumentTable` + `setTexture:gpuResourceID` + `setRenderPipelineState` + `drawPrimitives`），不能再用计算编码器。
**当前默认**：转换仍关（`NIER_T2T_CONVERT`）；关掉后 App 环境（含 `ALLOW_SUBMIT`）启动 22 秒无 fault，但窗口是黑的——说明**这张窗口合成就是画面呈现的唯一通道**，所以 P0 是画面的前置项。

**风险**：`BLOCKRT/WIDEN` 是内核 panic 后隔离的实验路径，见 P1。

---

## P1 启动环境：去掉继承自 by3 的危险开关

**现状**：`app/launcher.m` 无条件设 `TOTK_GPU_BLOCKRT=1`、`TOTK_GPU_WIDEN=1`（`nvn_mtl.m:4049` 注释：这两个路径曾引发 macOS 内核 panic，被隔离待探针）。

**改法**：NieR 的 launcher 只保留 `TOTK_GPU_ALLOW_SUBMIT=1`、`TOTK_GPU_MIDCLEAR=1`、`TOTK_GPU_BUDGET_MB`；`BLOCKRT/WIDEN` 删除（NieR 的 RT 不含 BC，不需要）。若将来要开，先跑 `probe-gpu-blockrt` / `probe-gpu-widen`。

**验收**：App 启动 10 次无 fault；日志无 `rendering into a block-compressed texture`。

---

## P2 几何阶段（P0 级画面缺陷）

**根因（已证实）**：唯一 GS `137b54dc…` 是 NV passthrough + 三角形压缩：读 locn2/3/4，剔退化三角形，`atomicAdd` 写计数并把三个 `_37.x` 写进列表。计数器缓冲 == 间接绘制参数数组；GS 被丢弃时 61% 间接绘制 `count=0` → 整批物体不画 → 画面缺口。手动写计数器可把 0% 归零（已验证）。

**改法（三步，全部新增、默认关）**
1. **捕获**：用 `vs_fn_for` 的 MSL 打补丁机制给该 VS 生成一个变体，额外 `device float4 *cap [[buffer(21)]]`，把 `m_36/m_34/m_38` 按 `gl_VertexIndex` 写出；绑定用 `MTL4ArgumentTable`（`maxBufferBindCount` 足够），缓冲挂在 `res_add` 上。
2. **压缩内核**：`kernel void gs_compact(device float4 *cap, device float4 *lst, device atomic_uint *cnt, constant uint&n)`，每线程一个三角形：退化则写 `gl_BuiltIn_5253=0`；否则 `i=atomicAdd(cnt,3)` 后写三个点。计数器地址取 `S.ssbo[2][0].addr`，列表取 `c0+0x10C`。
3. **接线**：在 `draw_inner` 的 GS 分支里，先跑 capture(draw 前) → compact(draw 后，同一通道外 flush 点)，`pr->missing` 不再置位。

**验收**：`[ind]` 的 `count=0` 比例 < 1%；序章飞行场景稳定；截图与主机对比（云/水/机体齐全）。

**风险**：共享层（TotK/by3 也用同一 `nvn_mtl.m`），新代码必须全部 env 门控（`NIER_GS_COMPACT=1`），默认不影响其它游戏。

---

## P3 残留接缝 / 覆盖不全

**现状**：`[cover]` 诊断（22925 行）显示大量"1920x1080 目标 + 960x540 视口"的半分辨率通道；A/B 证明接缝与 GS 丢弃无关。

**改法**：P2 做完再复测；若仍有接缝，把 `[cover]` 诊断重打（做法记录在 CHANGELOG），确认是哪个 pass 没覆盖，再决定是补 pass 还是补全屏合成（P0 做法 3）。

**验收**：飞行场景三帧对比，接缝面积 0。

---

## P4 标题白块（GS 放大三角形）

**根因**：`NIER_KEEP_GS_DRAWS=1` 时把 GS 程序按 VS+PS 画，顶点未放大 → 大三角形糊成白块（标题 logo 最明显）。

**改法**：P2 完成后该开关应只剩"draw 是否包含压缩后的列表"的区别；届时删掉 `NIER_KEEP_GS_DRAWS`，统一走 P2 路径。若 P2 未完成，保持默认丢弃。

**验收**：标题 logo 完整、无白块。

---

## P5 无声音

**现状**：guest 只开 `audren:u` ×3，查设备（cmd 5 通道数 / 10 名字 / 12 事件）后不再前进；从不打开 `audout:u`、从不调 `nn::audio::OpenDefaultAudioOut`；`audio_public.c` 的覆盖从未被调用；线程无阻塞。

**改法（逐条试，每条单独验）**
1. 给 `smGetService` 失败与 `audren` 三条设备命令的**回复形状**（`ncopy/nmove/nxout`）加日志，对照 SDK 12.3.9 的 `IAudioDevice` 期望；重点查 cmd 12 返回的事件是否要求 `nn::os::SystemEvent` 而非普通 event。
2. `AcquireAudioDeviceNotificationForOutput` 的返回值/事件类型按 SDK 反汇编核对（`sdk.patched.bin` 里该符号已定位）。
3. 若仍是"查完就停"，在 `nn::audio::Initialize`/`OpenDefaultAudioOut` 入口下断点式日志，确认 guest 是否在等某个 HID/Audio 设备的初始化完成事件（与 `hid` 的初始化顺序耦合）。

**验收**：`[audio] AudioOut open 48000 Hz x2 PCM16` 出现，且能听到序章音效/音乐。

---

## P6 工程保障（防止再出事）

1. `games/nier` 纳入 git（当前 `?? games/nier/`），每个 P 项一个提交；`NIER_build/` 继续忽略。
2. 脚本化补丁必须带 `assert old in s` 与"替换后行数变化上限"检查（截断事故的教训）。
3. 稳定性测试固化：`scripts/stability.sh`（13 秒 ×N，统计 `fault=`），每个改动前后各跑一次。
4. 诊断重打清单（丢失项）：`pass_log`、`cover_log`、`TOTK_LOG_SBUF`、`TOTK_LOG_INDIRECT`、`NIER_GS_COUNTER_HINT`、`TOTK_FULL_VIEWPORT` —— 做法在 `CHANGELOG.md`。

---

## 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| M1 | P0+P1 | 转换默认开、App ×10 无 fault、标题/序章画面恢复 |
| M2 | P2 | `count=0` <1%、飞行场景无缺口 |
| M3 | P3+P4 | 无接缝、无白块 |
| M4 | P5 | 有声 |
| M5 | 打包验收 | `make app` + 30 分钟连续游玩无 abort/panic |
