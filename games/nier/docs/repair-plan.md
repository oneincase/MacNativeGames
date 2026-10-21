# NieR:Automata (Switch 1.0.3) 修复改进方案

基线：`games/nier` 可启动、标题/菜单/电影/序章文本正常；默认 `NIER_T2T_CONVERT` 关、GS 阶段默认丢弃。
所有条目都写"改哪里 / 怎么验 / 风险"，按 P0→P5 顺序做，每项独立可回滚。

> **2026-10-22 白屏根因重估（当前头号阻塞的执行方案已迁出）**：见 `docs/plan-2026-10-22.md`。
> 本轮从 `threads` 转储 + `main.patched.bin` 反汇编得出新证据链：白屏期间**没有 guest 线程在推进影片/装载状态机**
> （影片事件线程挂在 `svc 0x18 WaitSynchronization` 且 `waiting on 0 handle(s)`，装载线程挂在 `AcquireSemaphore(+0x68)`），
> 而宿主对 `count==0` 的语义与真实内核不同（真实内核立即返回错误，我们永久挂起）。
> **此前"影片池被填成 0xFF（白）"的证据无效**：`DM_NVN_UPLOG` 的 `nz8` 只统计"8 字节字非零"，
> 而解码输出 Y=16..65 的填充字 `0x1010101010101010` 同样非零 —— 推不出"池里是 0xFF"。
> 待办 1（白屏）改按该文件的 W1→W2 执行；待办 2（音频混音恒 0）判定为白屏的下游，不单独追。

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
4. **不是 alpha test 缺席**（2026-10-21 晚，已把 alpha test 真正实现掉，提交 `8e77128`）：实现前后各抓一帧标题画面，白块位置与形状完全一致；alpha test 的参考值实测为 0、函数为 `GREATER`，标题文字此前是整片四边形着色（现在是 `alpha > 0` 才保留，`Press Any Button` 等掩码恢复正常），白块不受影响。
5. **不是被跳过的三个 alpha-test 片元着色器**：`DM_NVN_SKIP_FS=c0ed2509…`、`=38576bae…,f7dfe9e1…` 两轮把可疑批次整批丢掉，白块仍在（只少了对应文字/描边）。

**下一步（按证据推进，不要再猜）**
1. 用 `TOTK_GPU_HILITE1=<fs key>` 逐个替换可疑片元着色器为纯色，定位真正画白块的批次；先用 `DM_NVN_PASSDUMP_ALL` 抓一帧标题画面的完整 `d` 行（vs/fs key + blend + cmask），按 pass 顺序取最后几个 UI 通道。
2. 白块是**整块矩形**、里面有字形轮廓，形状与「文字绘制的四边形被涂成纯白、字形来自上层另一遍绘制」一致：优先假设某个文字批次的片元输出 alpha 被写成 1（顶点色 / 图集 alpha 通道没进着色器）。定位手段：`NIER_LOG_KEYS=1` 列出该帧全部 (片元键, 顶点数)，再用 `TOTK_GPU_HILITE1` 一次染 4 个键看哪一组盖住白块区域；锁定后看该片元着色器的输入（顶点色属性、图集格式）与 `ps slot` 的纹理格式。
   另一条并行假设：字体图集被采样到低层 mip（整块取平均 → 白）。`NIER_FORCE_LOD0`（新增诊断，强制 `lodMaxClamp = 0`）一跑就能排除。
3. 若确认是某张贴图内容没上传（采样到白）：查 `tex_mtl()` 的上传条件与该贴图的 `has_layout`/`mem`。

**验收**：标题 logo 完整、无白块。

---

## P5 无声音 —— **已解决（2026-10-22，两个独立 bug 串起来）**

**结论**：不是 `audren`/`audout` 服务、不是 HLE 回复形状、不是 guest 状态机。是**两个宿主侧 bug**：
1. `29a4983`：`runtime/fs_host.c` 的 `dir_entry_count(int64_t*, const char *path)` 把 `nn::fs::GetDirectoryEntryCount(s64*, DirectoryHandle)` 的第二个参数**当路径**用。句柄 `0x300000000` 被当字符串读成垃圾（`raw=75a5f5121e0f0000`）→ 返回错误 → 引擎挂 `rom:/` 时"列举到 0 条"→ 整棵 `Hw::DvdSystem` 树空 → `sound/Init.bnk` 与三个 `.wai` 全部"見つかりません" → Wwise 无音库 → 音频输出从不初始化。改成按句柄计数后，`rom:/sound/ -> 9`、`rom:/sound/English(US)/ -> 19`，音库在 0.08–0.10 s 逐个打开（`BGM.bnk`/`WwiseVoiceInfo.wai`/`WwiseStreamInfo.wai`/`WwiseInfo.wai`/`Init.bnk`/`vo_core.bnk`），`[audstate] env h=` 从 `0,0,0` 变成三个非 0 句柄。
2. `0bfbd51`：`runtime/audio_public.c` 的 `nier_audio_append()` 把 `buffer->next`（游戏填 0）当释放 tag 存队列，播完交回 guest 的 `GetReleasedAudioOutBuffer` 永远是 0 → 引擎认为"没有缓冲播完"，**每秒把输出拆掉重建**（`hletrace` 8 s 窗口：`GetReleasedAudioOutBuffer` 1530 次、`open/start/stop/close` 各 8 次）。改存 guest 的 `AudioOutBuffer*` 本身后：250 s 全导航里 `AudioOut open` **只有 1 次**、`released: asks 54000 hits 11140 queue 4`、`AudioOut appended` ≥11000 次，0 崩。

**验收（已达标）**：`[audio] AudioOut open 48000 Hz x2 PCM16 -> Core Audio (stereo)` 出现一次并保持打开整场，Core Audio 回调持续（`played callback`/`guest took tag` 正常）；回归 `stability.sh 3 13` = 3/3 `faults=0`。**听感验收待人工确认**（宿主侧已证 PCM 缓冲在 48 kHz 双声道持续入队并被播放线程按时归还）。

**曾经排除的假设**（不要重走）：`audren` 三条命令回复形状、`NIER_AUDIO_DEVICE_COUNT_FIRST` 伪造通道数、vtable 槽 `+0xf8` 派发（`main+0xd8c548` 静态确认、动态 0 次）、"设备构造函数没跑"（`main+0xd8ab8c` 跑了，`ready=1`）、`cmd 10/12` 缺席（它们受 `NIER_TRACE_AUDIO` 门控，"没出现"从来不是证据）。

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

## P7 长会话 abort：CubeArray 深度超过 Metal 上限（341 立方体）· 完成（2026-10-21）

**现象**：`nier.log` 帧 13257（飞行场景之后的战斗段）被 Metal 断言杀进程：`MTLTextureDescriptor arrayLength (768) … max allowed size (341)`，栈 `pkg_upload ← tex_mtl ← prepare_draw_textures ← draw_inner`。

**根因**：`tex_desc()` 把 NVN `CUBEMAP_ARRAY` 的 `depth` 直接当立方体数写进 `arrayLength`；游戏声明 `16x16x768 级 5 格式 0x29`（4608 面 / 18 MB）。Metal 的 slice 预算是 2048，cube array 的上限是 `2048/6=341`（实测 342 也 abort；`MTLDevice` 没有可读的 `maxTextureArrayLayers`）。

**改法**
1. `tex_desc_fixup()`：宽高 1..16384、3D 深度 1..2048、级数 ≤15 且按尺寸、`arrayLength` ≤2048、cube array ≤341、MSAA 样本数规范化；超限打 `[tex]` 一行（前 6 次 + 每 500 次）。
2. `mt_slices()` + `pkg_upload_`/`wb_write_list`：逐 slice 拷贝与写回截到已建纹理的实际 slice 数；guest 内布局（`nvn_tex_layout`）不动。

**验收**：`[tex]` 修正行 + `[tex] 拷贝按 Metal 上限截断 … 2046 层` + `TOTK_TEX_UP_PROBE=1` 的 `[tex-probe]` 行；进程不再 abort。

**风险**：Metal 放不下的 341..767 立方体永远缺失（若游戏真用到那一段，画面会少反射/探针）；`mtl_read_texels()` 的 GPU 内容回读路径未截断（该贴图目前不回读）。

---

## P8 几何阶段的二选一：丢掉 60 笔/帧（世界模型不见）或开压缩（显存 1 分钟撞预算）

**现象**：`11111` 存档的飞行场景里，飞行的 6 个单元模型完全不出（只剩 IFF 铭牌、引擎光条、字幕，`logs/L7.png`）；同时 `[nvn] 回放丢弃的绘制 … shader-missing` 每 300 帧窗口 18125 笔（≈60 笔/帧）。开 `NIER_GS_COMPACT=1` 后同一段路在帧约 1868 被 `gpu_budget_check` 杀掉（`显卡已分配 5016 MB 超过预算 5000 MB`）。

**已定位**
1. `draw_inner()`（`nvn_mtl.m:10097`）对"stage 2 缺失"的程序只有两条路：走 `gs_capture_draw()` 压缩，或 `g_draw_skip[15]++` 丢掉。本作全部 stage-2 绘制都用同一支 NV passthrough 程序（`137b54dc…`），所以这两条路覆盖了**整个场景几何**。
2. 飞行场景一帧的 `[uw]`（F4565）里没有任何一笔把上千顶点画进 1920x1080：只有阴影图集（`2048x2048`，两个 `1024x1024` 视口）、后处理/泛光金字塔、文字 HUD → 世界几何确实是被丢掉的，不是画错。
3. 压缩路径的显存增长：帧 300/600/1800 = 2194/2274/3673 MB，驻留对象 80/722/4215；同场景不开压缩 2180 MB / 72 个对象。退出转储里 `行 6347`（`gs_compact_flush()` 里每次压缩 `newBufferWithLength:16` 的 `parb`）登记了 4558 个对象。
4. 压缩计数有垃圾值：`[gs] 上次压缩后计数=2335883264 / 2116306240 / 16777217`（正常是 0/104/117）→ 内核输出的计数缓冲对部分绘制不可信。

**改法（按顺序，每步单独可回滚）**
1. `parb` 改成开跑时预分配的环形缓冲（如 256 × 16 B，一次 `res_add`），不再每次压缩新建 → 复测驻留数是否回到两位数、显存斜率是否下来。
2. 若显存仍涨：在 `gs_compact_flush()` 前后打 `device.currentAllocatedSize` 差分，按"每次压缩新增字节"定位（缓冲 / 参数表 / 编码器 / 管线）。
3. 显存稳住后验证画面：飞行单元模型是否回来、`[ind]` count=0 比例、与关压缩的画面逐帧对比。
4. 单独查计数垃圾值：`S.ssbo[2][0].addr` 每笔绘制是否都指向**本帧**有效的计数缓冲（跨帧复用/未初始化都会读到上帧或垃圾）。

**怎么验**：同一段手工导航（标题 A → Load Game → 首槽 → F 左移到「是」→ A → START MENU A）到飞行场景，`shot` 截图 + `[gs]`/`内存 至帧` 两行数据；判定：显存 <3000 MB 且不 abort，画面里 6 个单元模型可见。

**风险**：压缩内核与它的计数语义是从 `docs/gs-137b54.glsl` 逐行照抄的，改分配方式不影响语义；但"计数由 GPU 读"依赖同帧内先编码压缩内核再编码间接绘制，任何插入的 flush/提交都会打断这个顺序（历史上中途提交会让 `rs_` 编码器 FATAL）。

**进展（2026-10-21 晚，两个提交）**
- `276ced5`：**带索引的绘制曾被当非索引捕获**（用 `[[vertex_id]]` 当槽号却发 `drawPrimitives:0:vcount`）→ 顶点阶段取游戏从未索引的顶点、越界读 → 场景装载时 `PageFault`，之后队列 `SubmissionsIgnored`、每帧 +19 MB 未登记分配、1 分钟撞预算。改成按索引缓冲捕获 + 内核按索引 gather 后：**页故障 8→0（`DROPIDX` 对照）、未登记段 84 MB 不涨、帧 3051 显卡 2339 MB**。
- `d2c442d`：内核按列表所在池的剩余条目限写、索引缓冲长度不足就跳过；撤销"host 写回 guest 计数记录"（c35 因此崩 `main+0x9bbebc`）。
- **仍未解**：压缩内核真跑时，帧 ~1936 / fence 1937-1939 的页故障**间歇**出现（c21/c26/c30/c31 各 8 次；c24/c25/c27/c29 各 0 次）；"野计数 → `lst[4*i]` 越界"假设**已证伪**（加界后 c30/c31 仍 8 次）。下一步：在捕获绘制与内核之间按 fence 二分（同一帧只编码捕获不编码内核 vs 反之），并把 `NIER_ALLOC_TRACE` 的 `[alloc]` 行与故障 fence 对齐；画面验证改用 `wshot`（`shot` 在帧 ~2200 后不再执行）。

**进展（2026-10-22，收尾核对）**
- 页故障：按映射池截断（`7569097`）之后实测 **9 轮 0 次**（c57/c59/c60/c62/c65/c66/c68/c76/c77），其中 c76/c77/c95/c96/c102 都是**跑满 7200 帧**的长跑、`fault=`/`signal=` 全 0；此前同脚本 6 轮里 4 轮各 8 次。**该项可判为已修**。
- 视觉：`logs/c68b.png`/`c68c.png` 与 `logs/c76c.png`（飞行场景）里世界几何/直升机敌人/弹幕/HUD 都在；`logs/L7.png` 的"纯绿底 + IFF 铭牌"不再复现。抓帧建议 `wshot`（`shot` 在帧 ~2200 后不再执行）。

---

## P9 音频：先证明 guest 会不会请求输出（P5 已按反汇编重写方向）

**已定位**：见 `docs/audio-root-cause.md`。HLE 回复形状正确、guest 未阻塞、本作不导入 renderer；`main` 里 `OpenDefaultAudioOut` 唯一调用点在未被调用的 vtable 槽 `+0xf8` 后面。

**改法（行为中性，一次启动就能定性）**
1. `nier_audio_open_default` 入口无条件打一行 + param 的 `+0/+4/+6` 按 u16 打。
2. `audio_device_handler` case 5/10 也 dump guest 栈。
3. `NIER_AUDIO_DEVICE_COUNT_FIRST=<n>`：首次查询给不同通道数，触发 guest 从未走过的 `if (n != obj+0x108) bl 0xd21914` 分支。
4. `runtime_observer` / `PROBE_PLT` 给 `main+0xd8c548`、`main+0xd8c628` 加计数（全程 0 即"guest 从不请求"成立）。
5. 同时修 `AudioOutParameter` 的 u16 解析、补 `OpenAudioOut` 具名重载（P1 常量见文档）。

**怎么验**：一次启动后看是否出现 `[audio] OpenDefaultAudioOut parameter …`；若出现 `opened service audout:u` 则说明覆盖被绕过。

**风险**：第 3 条会改变 guest 的状态机分支，仅诊断期开启，默认关。

---

## 里程碑

| 里程碑 | 内容 | 判定 |
|---|---|---|
| M1 | P0+P1 | **已达成 2026-10-21**：转换默认开（launcher 设 `NIER_T2T_CONVERT=1`）、`stability.sh 10 13` 全 0 fault、标题/序章/菜单画面恢复（截图 `docs/screens/07-composite-boot.png`、`/tmp/nier-shots/noforce1200.png`） |
| M2 | P2 | **机制已达成 2026-10-21**（捕获→内核→GPU 间接绘制全链路跑通、0 fault）：`[gs-rep] 保留顶点=72 有坐标三角形=760`、`[gs] 间接绘制：计数由 GPU 读`。**全场景 `count=0`<1% 与飞行场景验收未量**（进 gameplay 的 `TOTK_INPUT_SCRIPT` 盲按键仍不稳定） |
| M3 | P3+P4 | 无接缝、无白块 |
| M4 | P5 | **链路达成、听感未达成（2026-10-22 修正）**：音库装载 + `AudioOut` 持续打开 + 缓冲闭环已实测（`29a4983`、`0bfbd51`）；但 guest 混音输出恒 0（`NIER_AUDIO_MIXPROBE` 毒化实验证明写的是纯 0）。疑为白屏（逻辑未进发声状态）的下游，见 `docs/plan-2026-10-22.md` §4 |
| M5 | 打包验收 | `make app` + 30 分钟连续游玩无 abort/panic |
| M6 | P7 | **已达成 2026-10-21（描述符 + 截断验证）**：CubeArray 768 层按 341 建、拷贝 2046 层、不再 abort；场景内绑定画面未验证 |
| M7 | 白屏解除（新增 2026-10-22） | 判据：序章影片接力（出现第二台解码器 `configured`）+ 场景几何出现 + 装载线程不再等 `+0x68` 信号量 + 影片线程 `nsvc` 持续增长。方案 `docs/plan-2026-10-22.md` W1→W2 |
