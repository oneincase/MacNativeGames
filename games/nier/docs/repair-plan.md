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

## P2 几何阶段（P0 级画面缺陷）· 完成（`NIER_GS_COMPACT=1` 门控，默认关）

**根因（已证实）**：唯一 GS `137b54dc…` 是 NV passthrough + 三角形压缩：读 locn2/3/4，剔退化三角形，`atomicAdd` 写计数并把三个 `_37.x` 写进列表。计数器缓冲 == 间接绘制参数数组；GS 被丢弃时 61–97% 间接绘制 `count=0` → 整批物体不画 → 画面缺口。

**实现（三段，全部按 `docs/gs-137b54.glsl` 对齐）**
1. **捕获**：`gs_capture_pso()` 复制该程序的 VS：入口改 `vertex void main0(device float4 *totk_cap [[buffer(29)]], …)`（`rasterizationEnabled=NO` 的管线必须 void 返回；Metal 每级缓冲槽 0..30，29 是可用且不与程序自用槽（实测最大 0）、顶点流（16+n）、零缓冲（30）冲突的位置），每处 `return out;` 改成把 locn2/3/4 直接写进捕获缓冲（`totk_cap[vid*4+k]`）。缓冲 stride 64B。**教训**：写进 varying（`[[user(locn32..35)]]`）在关光栅化时被整体丢弃，捕获缓冲永远是 0，表现为「内核全丢、计数恒为 0」。
2. **压缩内核**：`k_gs_compact_msl` 逐条对齐 GLSL：`(b0&b1&b2)!=0` 丢；`((b0|b1|b2)&64)==64` 留；否则 `w1=(x2-x0)*(y1-y0)`、`w2=(y2-y0)*(x1-x0)`，`w2>=w1` 丢；再 +0.5 像素吸附（四个 max/min 都非整数且 floor(minX)==floor(maxX) 或 floor(minY)==floor(maxY)）丢。保留则 `atomic_fetch_add(cnt,3)` 并写 `lst[i..i+2] = as_type<uint>(c3.x)`。计数器 = `S.ssbo[2][0].addr`（gp_s0，20B 记录的第 0 字，与消费方间接参数同址），列表 = `S.ssbo[2][2].addr`（gp_s1）。
3. **消费端（关键）**：CPU 读不到 GPU 刚写的计数（游戏每帧重置记录，且回放流中途提交会让 rs_ 编码器失效 → `FATAL render stream: renderCommandEncoderWithDescriptor failed`），所以消费绘制改走 **GPU 间接绘制**：`nvn_render_stream.h` 新增 `RS_DRAW_INDIRECT / RS_INDEXED_INDIRECT`（`drawPrimitives:indirectBuffer:` / `drawIndexedPrimitives:indexType:indexBuffer:indexBufferLength:indirectBuffer:`），`nvn_mtl.m` 新增 `enc_r_draw_indirect / enc_r_draw_indexed_indirect`；`draw_resolve()` 命中 GS 计数器记录时先 `gs_compact_flush()`（把压缩内核编码在消费绘制之前的同一命令缓冲里，GPU 顺序即正确），再把绘制改派间接变体。NVN 的 5×uint32 记录与 `MTLDrawIndexedPrimitivesIndirectArguments` 逐字一致，直接喂。

**验证**
- `[gs-rep] 保留顶点=72 有坐标三角形=760/…`（`NIER_GS_REPORT=1`，内核统计缓冲）：捕获非零、判定链生效，保留 24 个三角形。
- `[gs] 间接绘制：计数由 GPU 读（已压 2401 次）`：消费端确实走间接路径。
- `NIER_GS_COMPACT=1` 55s 运行 0 fault；默认路径 14s×2 0 fault。
- 截图 `docs/screens/08-gs-compact-title.png`：标题画面在压缩链路打开后仍完整（`Press Any Button` 等文字笔画与之前不同，说明间接绘制确实画出了原先被丢弃的批次）。

**仍未达成**：验收要求的「`[ind]` count=0 比例 < 1%（全场景）」「序章飞行场景稳定」需要进 gameplay 才能量；当前只能确认机制与标题场景。白块（P4）与本项无关（三种 GS 设置下位置一致）。

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
5. 本轮新增/沿用的诊断开关（全部默认关，只用于定位，不改变默认画面）：
   - `NIER_T2T_CONVERT=1`：HDR→窗口换格式合成（P0 的修复本体，launcher 已默认打开）。
   - `NIER_GS_COMPACT=1`：GS 捕获+压缩内核（P2，默认关；打开后 0 fault，但消费端计数仍需 GPU 间接绘制）。
   - `NIER_GS_FORCE=<n>`：把 GS 计数记录强制成 n 个顶点（只作诊断，会画出错误几何，见 P2 的强制计数实验）。
   - `NIER_LOG_ALPHATEST`：记录 `logic op / alpha test` 命中的绘制与片元键（P4 排查用）。
   - `NIER_LOG_GSKEYS`、`NIER_GS_COUNTER_HINT`：记录唯一带 GS 的程序键与每次 GS 绘制的 ssbo2 绑定。
   - `TOTK_WINDOW_SHOT=<present>[,<present>]:<pattern>`：整窗截图（`%d` 换成 present 号），验证窗口画面的最快手段。
   - `DM_NVN_PASSDUMP[=<frame>]` + `DM_NVN_PASSDUMP_ALL=1`：抓一帧的全部 pass/绘制/采样器。

---

## 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| M1 | P0+P1 | **已达成 2026-10-21**：转换默认开（launcher 设 `NIER_T2T_CONVERT=1`）、`stability.sh 10 13` 全 0 fault、标题/序章/菜单画面恢复（截图 `docs/screens/07-composite-boot.png`、`/tmp/nier-shots/noforce1200.png`） |
| M2 | P2 | `count=0` <1%、飞行场景无缺口 |
| M3 | P3+P4 | 无接缝、无白块 |
| M4 | P5 | 有声 |
| M5 | 打包验收 | `make app` + 30 分钟连续游玩无 abort/panic |
