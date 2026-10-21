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

**完成（2026-10-21，渲染合成版）**：计算内核整体退役，`copy_t2t_convert()` 现在按游戏下发顺序**立即执行**渲染合成（不再挂起、无 `cv_flush`）：`rp_end` 落地挂起通道 → `rs_sync`（编码线程模式）→ 源纹理写进共享片元参数表纹理槽 0（游戏着色器是 argument-buffer ABI，不读该槽）→ `enc_r_begin` 开 `DontCare` 加载的 pass（尺寸=目标级尺寸），全屏三角形 `src.sample(s)`（`constexpr sampler`，不用采样器槽）直接写目标，`enc_r_end` 收尾带 `bar_end` 队列屏障；随后 `args_dirty_all()` 供游戏绘制继续用共享表。流式合成 pass 自动沿用 `enc_r_begin` 的编码线程路径。MSL：`cvv/cvf`（与 present 管线同款全屏三角形 UV 约定）。验证（M1 部分达成）：
- `NIER_T2T_CONVERT=1` + `ALLOW_SUBMIT=1`：stability.sh 13 秒 ×3 全干净（之前计算内核在此配置下第一次 flush 即 SIGTRAP）；100 秒实跑 0 fault。
- `TOTK_WINDOW_SHOT` 整窗截图：YoRHa 启动序列（LOADING/文本/logo）完整；序章飞行场景云海/天际线/字幕正常呈现（绿色调与缺口是 P2/P3 的已知残留，合成前就存在）。
- 不带 `NIER_T2T_CONVERT` 的默认路径 13 秒 ×2 依旧干净（改动不影响关闭分支）。
- launcher（`app/launcher.m`）现默认设 `NIER_T2T_CONVERT=1`（`NIER_build/` 里的 app 需重新 `make app` 打包后生效）。
- 后续注意：合成只覆盖整级 level-0、无偏移的形态（与原内核门一致）；出现 4x4 排布/部分区域形态仍会走报红路径，属预期。

**当前默认**：转换已默认开（launcher 设 `NIER_T2T_CONVERT=1`；想临时关掉可 `NIER_T2T_CONVERT=0` 启动 runtime 压过它）。

**风险**：`BLOCKRT/WIDEN` 是内核 panic 后隔离的实验路径，见 P1。

---

## P1 启动环境：去掉继承自 by3 的危险开关

**现状**：`app/launcher.m` 无条件设 `TOTK_GPU_BLOCKRT=1`、`TOTK_GPU_WIDEN=1`（`nvn_mtl.m:4049` 注释：这两个路径曾引发 macOS 内核 panic，被隔离待探针）。

**改法**：NieR 的 launcher 只保留 `TOTK_GPU_ALLOW_SUBMIT=1`、`TOTK_GPU_MIDCLEAR=1`、`TOTK_GPU_BUDGET_MB`；`BLOCKRT/WIDEN` 删除（NieR 的 RT 不含 BC，不需要）。若将来要开，先跑 `probe-gpu-blockrt` / `probe-gpu-widen`。

**验收**：App 启动 10 次无 fault；日志无 `rendering into a block-compressed texture`。

---

## P2 几何阶段（P0 级画面缺陷）· 部分完成（捕获 + 压缩已实现，消费端待 GPU 间接绘制）

**根因（已证实）**：唯一 GS `137b54dc…` 是 NV passthrough + 三角形压缩：读 locn2/3/4，剔退化三角形，`atomicAdd` 写计数并把三个 `_37.x` 写进列表。计数器缓冲 == 间接绘制参数数组；GS 被丢弃时 61–97% 间接绘制 `count=0` → 整批物体不画 → 画面缺口。手动把计数记录写成 900 时 `[ind]` 零值率降到 0%，标题画面完整、0 fault（实验开关 `NIER_GS_FORCE`，只作诊断）。

**已完成（`NIER_GS_COMPACT=1` 门控，默认关，写入未提交前验证 0 fault）**
1. **捕获**：`msl_vfield()` 从 `struct main0_out` 取 locn→字段名；`gs_capture_pso()` 在 `struct main0_out` 后插 `totk_cap2/cap3/cap4/capp [[user(locn32..35)]]`，顶点函数改成 `vertex void main0(`（`rasterizationEnabled=NO` 时 Metal 要求 void 返回，否则 `RasterizationEnabled is false but the vertex shader's return type is not void`），每处 `return out;` 改为写四个捕获输出后 `return;`。捕获缓冲 stride 64（c2/c3/c4/p 各 float4），走 vertex buffer 31。
2. **压缩内核**：`k_gs_compact_msl` 按 `docs/gs-137b54.glsl` 逐条对齐（`(b0&b1&b2)!=0` 丢；`((b0|b1|b2)&64)==64` 留；否则面积叉积 `w2>=w1` 丢；再 +0.5 像素吸附判定），保留则 `atomic_fetch_add_explicit` 取 3 个索引并写三个点。计数器 = `S.ssbo[2][0].addr`（gp_s0），列表 = `S.ssbo[2][2].addr`（gp_s1，实测 3600B = 900 顶点）。**注意**：早期写法 `c0+0x10C` 是错的。
3. **接线**：`draw_inner` 的 GS 分支改为 `gs_capture_draw()`（不再置 `pr->missing`），parked 工作在 `copy_t2t()` 开头由 `gs_compact_flush()` 在通道外提交。

**验证**：`[gs] 压缩内核就绪：300 三角形 → 计数 0x… 列表 0x…`，`NIER_GS_COMPACT=1` 14s×2 全 0 fault。

**卡点（唯一剩下的问题）**：消费端仍需 **GPU 间接绘制**。GPU 写进记录里的计数，CPU 回放在下一帧读到的是游戏重置后的 0；`NIER_GS_SYNC=1`（回放流中途 `mtl_sub_commit` + 等 GPU 读回）会让 rs_ 流式编码器失效并 abort（`FATAL render stream: renderCommandEncoderWithDescriptor failed`，栈在 `rs_worker`），已退役为显式诊断。正确路线：给 `nvn_render_stream.h` 的 `RS_DRAW/RS_INDEXED` 加间接变体（`drawIndexedPrimitives:indirectBuffer:indirectBufferOffset:`，5×uint32 布局与 `MTLDrawIndexedPrimitivesIndirectArguments` 一致），`draw_resolve` 命中 `g_gs_ring` 的记录改走该分支，并在 GS 通道与消费通道之间编码压缩内核（同一命令缓冲内 GPU 顺序即正确）。

**验收**（未达成）：`[ind]` 的 `count=0` 比例 < 1%；序章飞行场景稳定；截图与主机对比。

---

## P3 残留接缝 / 覆盖不全

**现状**：`[cover]` 诊断（22925 行）显示大量"1920x1080 目标 + 960x540 视口"的半分辨率通道；A/B 证明接缝与 GS 丢弃无关。

**改法**：P2 做完再复测；若仍有接缝，把 `[cover]` 诊断重打（做法记录在 CHANGELOG），确认是哪个 pass 没覆盖，再决定是补 pass 还是补全屏合成（P0 做法 3）。

**验收**：飞行场景三帧对比，接缝面积 0。

---

## P4 标题白块 · 未解（已排除三个假设，改用证据推进）

**现象**：标题画面 `NieR:Automata` logo 右侧、`The End of YoRHa Edition` 两侧、`Press Any Button` 附近出现亮白方块，方块内可见字形轮廓（像反白文字/低分辨率放大）。同一画面背景下城市/水面/其余文字都正常。

**已排除（2026-10-21 实测，截图与日志都可以复现）**
1. **与 GS 无关**：`NIER_GS_COMPACT=1` 前后、`NIER_GS_FORCE=900` 强制计数、完全不开 GS 三条路都出现同一白块；`NIER_GS_COMPACT=0` 与 `=1` 的标题截图逐像素差异只有 2.1%，白块位置不变。原计划把 P4 归因于 `NIER_KEEP_GS_DRAWS=1` 的放大三角形不成立。
2. **不是移植的泛光 1/4 叠加**：`DM_NVN_BLOOM_Q=0`（关掉移植自己的泛光捷径、走游戏原链）白块一模一样。
3. **不是 alpha test / logic op 那批绘制**：新增诊断 `NIER_LOG_ALPHATEST` 显示命中 `logic=3 afunc=5` 的绘制全是 4 顶点全屏四边形（`fs=f7dfe9e1…-1178a6955197_b`，`blend=15` 已开），不是文字批次，且只影响 1920x1080 全屏后处理。

**下一步（按证据推进，不要再猜）**
1. 用 `TOTK_GPU_HILITE1=<fs key>` 逐个替换可疑片元着色器为纯色，定位真正画白块的批次；先用 `DM_NVN_PASSDUMP_ALL` 抓一帧标题画面的完整 `d` 行（vs/fs key + blend + cmask），按 pass 顺序取最后几个 UI 通道。
2. 白块内的方块尺寸与「字体图集被按低层 mip / 错误行距采样」吻合，优先查该批次的纹理：`TOTK_TEX_CHECK_ONCE`、纹理 serial 与 format、以及 sampler 的 `mag/min`（`DM_NVN_PASSDUMP` 的 `B … samp` 行）。
3. 若确认是某张贴图内容没上传（采样到白）：查 `tex_mtl()` 的上传条件与该贴图的 `has_layout`/`mem`。

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
