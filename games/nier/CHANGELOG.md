## 2026-10-22 绿源锁定：c188 逐个 skip 判决——延迟光照 shader `915f419e` 是绿色来源

- **c188（飞行场景稳定窗口 base@250s，逐个 skipfs）**：
  | 帧 | 最亮 5% | 颜色数 | 结论 |
  |---|---|---|---|
  | base | (40,44,45) 暗 | 217 | 时序：base 仍落转场 |
  | skip 天空 17654724 | **(72,185,74) 绿** | 608 | **天空不是绿源** |
  | skip **915f419e** | **(154,93,160) 紫** | **5367** | **绿消失 → 915f419e 是绿源** |
  | restore | (70,185,72) 绿 | 1711 | 绿回来 → 确认 |
- skip `915f419e` 后画面变**紫红网格**（`c188_skip_915.png`，5367 色——最丰富的一帧）：去掉这个 pass 叠加的绿色后，底层是偏品红的阴影/探针网格。`915f419e` 是**延迟光照 pass**（c165 里 pass 342817/342823，采 G-buffer ser=103/147/148/149/1582 + 探针 + 光源缓冲 ser=3187/3272/3273）。
- **uniform 解码（c174 数据，pass 364071）**：`915f419e` 高频读 `fp_c4[2]=(0.8,0,2,6)`、`fp_c4[6]=(0,0.83,0,4)`、`fp_c1[1]=(-0.465,1.624,0.417,1.5)`、`fp_c1[6]=(24.558,7.122,1.719,0)`——逐通道不对称的光照/色调参数。**语义未知**（无真值无法判断这些值对不对）。
- **下一步（c189 进行中）**：dump `915f419e` 采样的 13 张输入（G-buffer/探针/光源缓冲），判定"输入已绿"（上游污染）还是"输入正常、光照计算产出绿"（uniform/公式问题）。这是绿源定位后必须分的最后一叉。


## 2026-10-22 偏色排查（续）：swizzle 假设排除（c172 全 HDR RT 恒等）；TEXRAW 路径不可用（同步等待阻塞提交线程 → 驻留积压撞预算，c170/c173 两次复现）

- **c172（`DM_NVN_TEXPNG_FS=b41adaf5,…` tonemap 系 shader 采样的贴图）**：世界 HDR `ser=16` 与全部泛光 RT（24/25/26/82/83/84/89）的 NVN swizzle 都是 **`2,3,4,5` = R,G,B,A 恒等**，`flags=0x8`（render-target）；`nvnfmt=0x3f`（R11G11B10F）→ Metal 92 精确映射。**游戏没有声明任何通道重排，我们也没有丢**——swizzle 假设排除。
- 逻辑裁决（不需要再跑位域实验）：ser=16 是 GPU 写 GPU 读的 RT，写入与采样走同一个 Metal 纹理对象——Metal 的位域解释对读写天然自洽，**RT 上不可能发生位域错位**；静态贴图（法线图蓝 (127,127,243) 未交换）与影片链都正常，进一步排除采样期交换。
- 视觉签名再核对：海面蓝→绿（B 跑到 G）与引擎光橙→品红（G 跑到 B）**同向 = 纯 G↔B 交换**；G/B 交换世界 HDR 后引擎光均值 (254,242,153) ≈ 原版橙白。但 shader 输出三通道对称、swizzle 恒等、blend/colorState 无通道映射字段（`color_st` 只有 blend/logic/alpha）——**剩下的不对称输入只有 uniform**：世界 shader 读 `fp_c13/c10/c16` 等常量组做光照/色彩分级，若某个"通道增益/矩阵"常量块绑定错位（槽位错、读到别的块），恰好能产生场景相关的通道失衡。
- **c170/c173 教训（工具性）**：`DM_NVN_TEXRAW` 的逐张贴图 `cb_wait` 同步读回会阻塞提交线程 → 驻留积压（装载突发窗口内 10.9 GB/13.9 GB 两次撞闸）。飞行场景 RT 判决改用 **CBDUMP（随 passdump 存 uniform 块，c174 进行中）**；TEXRAW 只适合无突发窗口。


## 2026-10-22 偏色调查开局：P4 关闭（用户实机对照）、LOD0 排除、量化锁定"3D 场景坏、影片链好"

- **P4 关闭**（`1332c0f`）：用户对照 Switch 原版实机确认标题白块 = 游戏自身 glitch 设计（NieR 数据损坏美学），非移植缺陷。
- **偏色量化**（Pillow 通道统计，最亮 5% 近白区 = 中性色检验）：
  | 画面 | 最亮 5% | 判定 |
  |---|---|---|
  | 标题（影片直连，不经 HDR 链） | R237 G234 B226 | **中性 ✓** |
  | 序章飞行场景（c157_d） | R78 **G184** B78 | 偏绿 ✗（R=B 严格相等） |
  | 飞行 300 s（c158_scene） | R78 **G184** B78 | 同上 |
  | 对话场景（c160_t8 城市黄昏） | R182 G153 B152 | 偏暖但可解释（黄昏） |
  用户原版参考图证实：飞行场景天空应为**中性灰白 + 橙色引擎光**，我们的是深绿海面 + 绿色光条——**橙色旋转到绿色**。
- **LOD0 判决（c162）**：`NIER_FORCE_LOD0=1` 与基线标题逐通道不变（236,234,226 vs 237,234,226）→ **mip 假设排除**（历史 plan 说"一跑就能排除"，这次跑了）。
- **c164 崩溃（新发现，非偏色调查回归）**：同 c157 导航 + 多一次 A + 在帧 ~2400（飞行场景装载窗口）发 `passdump 2` → 显卡 2312→**6211 MB**（+3.9 GB 突发、第 24 个内存池）撞 `TOTK_GPU_BUDGET_MB=6500` 预算闸 abort。c157 的 dump 在装载完成后（峰值 2354 MB）故无事。**= P8 遗留的"场景装载突发"仍在**（2026-10-21 记录：帧 1800→2100 一次跳 4649 MB，归因"贴图重传/池分配突发"未解决）；教训：**passdump 必须避开装载峰值窗口**。
- 当前主假设（待飞行场景 passdump/texdump 判决）：**HDR 目标 fmt=92 RG11B10Float 的通道位域解读或 deswizzle 错误**——飞行场景 R=B 严格相等 + 橙→绿旋转是通道级错误的形态（tonemap 曲线错不会产生 R=B 严格等）；"预排布贴图存储不在内存池里，回退 CPU"红警每轮 1 次提示 CPU deswizzle 路径被触发。


## 2026-10-22 c158：场景内 300 s 长跑——音频持续流动（mix peak→10480）、0 崩溃、画面推进（飞行场景→暗场）

- 导航（c157 校准版序列）进场景后原地跑 300 s（`NIER_TRACE_AUDIO=1`）：
  - **音频**：`mix peak 0→2822→9831/10480（max 22579）`——BGM 随场景推进渐入，缓冲流 `appended 14000+`、`hits 10267 queue 3-4` 稳定。**M4 数据层面达成**（听感只差人工确认）。
  - **画面**：`c158_scene.png` 757 色 top (69,184,72) = 飞行场景绿色海面；`c158_late.png` 173 色 top (28,33,35) = 场景推进到暗场/内景过渡。
  - 至帧 9059、`GPU 93% (11%)`、`FATAL/fault=` **0**。
- 白屏战役（W0→W1c→W2 节拍修复）至此完整收口：影片链、装载泵、音频混音、场景渲染、稳定性五项全绿。


## 2026-10-22 M7 达成：序章飞行场景完整上屏（c157，白屏修复后的首次进游戏）

- 导航校准（c154→c157）：c154/c155/c156 停在 LOAD GAME 对话框（`key 3` 左移发在对话框弹出之前被吞）；c157 把每步间隔从 2-3 s 拉长到 7-9 s（影片链现在真的在播，界面推进比白屏时代慢），序列 35s/7/7 三下 A → 左 150ms → A（确认「是」）→ 60s 后 A（跳过引子）→ Plus(36) → A。
- **实测（c157，250 s）**：dlg/a/b 走对话框→START MENU，**c 帧 = 整屏品红（装载瞬间，正是已知的品红块场景，非回归）**，**d 帧 = 序章飞行场景完整上屏**（`logs/c157_d.png`：海面波纹、黑色飞行机体+引擎光条、绿色弹幕、IFF 铭牌、MANUAL「ZR 闪避」浮窗）；至帧 7500、`GPU 93% (11%)`（游戏自身 GPU 工作，白屏时代是 1%）、`FATAL/fault=` **0**。
- 与 P8 里程碑时代（c68）同场景同色调——绿色调/品红是已知 P2/P3/P-品红残留，与白屏无关。
- **M7 判据全中**：影片接力（#1 74 帧 + #2 81 帧 + 装载后继续）✓ 场景几何出现 ✓ 装载线程恢复推进（泵 nsvc 20.8 万）✓。


## 2026-10-22 白屏解除：影片节拍在"全部 t=0"的 .usm 上误丢帧并释放游戏没见过的槽 —— 播放器扣满 8 槽饿死在 53/74 帧

- **判决实验（c150，`TOTK_MOVIE_PACE=0`）**：影片链**首次接力**——decoder #1 消费全部 74 帧 + EOS → destroyed → **decoder #2 创建 → 81 帧 → destroyed**；c151（PACE=0 + 截图）里**标题画面完整上屏**（NieR:Automata logo/副标题/Press Any Button/版权行，见 `logs/c151_2.png`）——"停在 53/74 帧"不是游戏状态机玄学，是**我方节拍逻辑**。
- **根因（`movie_host.c:release_due`）**：NieR 的 `.usm` 播放器每次 `SendInputBufferForDecode` 都送 `time=0`（c122 send #1..#5 全部 `t=0`）→ 所有帧的 `due_at` 相等 → 丢帧分支 `now - due <= (next-due)*3/2` 在 `next==due` 时恒真 → 每个 present 把 `decoded` 队列裁到 1 帧，并且**把游戏从未 acquire 过的槽 `out_held=0`**——这些槽随即被解码器复用写新帧，而游戏那边还按"我拿了 53 帧、还欠 53 次 release"记账。游戏对 EOS 前最后几帧的 release 落到已被复用的槽上 → `h_release_output` 的 `queued` 判定失败返回 ST_ERROR → 8 个输出槽全部被游戏扣住 → `AcquireOutputBufferIndex` 永 `ST_NO_OUTPUT`（c149：acquire 停在 total=53、EOS 从未取走、release 计数还在流动）。
- **修复**：`release_due` 的丢帧条件加 `next == due` 守卫——**时间戳无信息时永不丢帧**（每 present 至多交 1 帧，游戏本来就是一帧一画）；带真实递增 pts 的流（TOTK `.webm`）行为逐字节不变。
- **实测（c152，默认节拍 + 修复）**：decoder #1 74 帧 → destroyed → #2 81 帧 → destroyed → **#3 创建**（logo_00/01/02 三部连播）；90 s 内三张 `wshot` 互不相同（黑场→标题 8428 色→暗场过渡）——**白屏解除**。回归 `stability.sh 3 13` = STABLE。
- 本轮同时验证了上一轮的三处判读：c149"停在 53 帧 = 槽饿死"方向对、归因错（不是游戏状态机等外部条件，是我方节拍）；h=0x1026/装载泵两个卡点随影片链恢复自然解除（c152 未再出现 nsvc 定格）；"白 = 影片末帧真实内容"成立（修复后第一张图就是黑场淡入，不再是静止白）。


## 2026-10-22 W2 判定（c149）：播放器停在影片第 53/74 帧——排除槽耗尽，是游戏状态机等一个不来的条件

- 仪器：`h_release_output` 加 release 计数（`NIER_MOVIE_LOG`，前 3 条 + 每 20 条）。
- **实测（c149，22 s）**：`release #1..#40`（idx 轮换）持续流动；解码 74 帧全部完成（能完成即证明槽在归还）；播放器 acquire 到 total≈53 后**不再进入解码器**，EOS 标志始终没被取走（所有 acquire 行 `eos=0`）。→ **"8 槽被扣光而饿死"排除**。
- 结合 c148（hletrace 17 s 窗口 92503 调用：nvn/音频/hid 正常流动，无 `movie::*`、无 `nn::codec`、无第二台解码器尝试、无 "left to the library"）：**游戏播放器在等一个与解码器无关的条件**（候选：呈现完成 fence / 上层状态机触发 / 音频时钟同步——USM 是 CRI 容器含音轨，但游戏从未请求音频解码器，故音频同步候选减弱）。
- **subagent 终报修正（已采纳）**：h=0x1026（纯 CV-wait 叶子，`main+0xb2fa9c` 醒来只会 `ret` 结束线程）与装载泵 h=0x1027 **互不相干**——"worker 睡→不 release task+0x38"闭环作废；泵 nsvc 89/s 增长、其键有 SIGNAL（c147）也独立证伪了"泵永等"。两个卡点是同一批未推进的上游的平行症状。**不要对 h=0x1026 的 CV 硬放行**（只会让一个线程退出，不解决任何东西）。
- **当前画像（供下一轮）**：W1c 解除了"整进程线程挂起"这一层（这是历史上第一次游戏逻辑在装载阶段持续运行：泵 89 任务/秒、混音出数据、GPU 每帧合成）。剩余 = 播放器状态机停在 logo 影片第 53 帧（≈1.8 s 处，池内容定格 mean 228 = 白场）。下一步假设 H-X：查第 53 帧之后播放器在等的具体条件——从 `main+0x240266c`（每帧画影片平面的函数）的调用者状态机入手，或给 `nvnSyncWait`/呈现完成计数加轨迹，判"等 GPU 完成"vs"等上层事件"。


## 2026-10-22 W5 仪器（cvtrace）：修正"装载泵永等"误判——它在正常泵任务；全进程唯一"只 wait 无 SIGNAL"的条件键 = movie worker

- 仪器：`threads.c` 新增 `cv_trace()`，`NIER_TRACE_SVC0=1` 下对**每个条件键的首次 wait（0x1C）与首次 SIGNAL（0x1D）**各打一行（含符号化 lr），48 键/64 行封顶，默认关。
- **实测（c147，25 s）**：
  - 装载泵 `h=0x1027` 的键 `…641560` **wait 与 SIGNAL 成对出现**，nsvc 1687/25 s（≈67 次/秒）；c145 全程 nsvc 42764/480 s —— **它不是"永等 task+0x38"，而是一边等一边被唤醒、持续处理任务**。此前"装载线程永等→白屏"的表述作废（W1c 提交信息里的"装载泵恢复推进"是对的，"永等"是错的）。
  - 全日志唯一"只有 wait、从未 SIGNAL"的键 = `…38efa4`（worker `h=0x1026`，main+0xb2fb58 的 WaitConditionVariable）。两种读法：(a) 空闲 worker 等下一份活（正常，游戏没派活给它）；(b) 该派活的宿主/游戏路径没跑（真缺陷）。区分要靠它的**唤醒者身份**（subagent 反汇编中）。
  - taskpool 探针 F=1/F=600 都采到 holder=0（池瞬时为空，与"泵活着、任务随到随走"一致）。
- 事件流复核：`fire #1..12` 正常（封顶），播放器轮询 785/s 正常。`mix peak 4660`（c146）证明游戏逻辑在跑。**"白屏"当前画像 = 游戏逻辑活着、在等一个永远不会来的东西**，头号嫌疑仍是 movie worker 的缺位生产者。


## 2026-10-22 音频复测（方案 §4 预言应验）：W1c 后 guest 混音不再恒 0 —— `mix peak 4660`

- 假设（方案 §4）：混音恒 0 是白屏的下游（逻辑没进发声状态），W1c 修好后复测。
- **实测（c146，45 s，`NIER_TRACE_AUDIO=1 NIER_AUDIO_MIXPROBE=1`）**：`[audio] mix peak 4660 max 4660 (buffer 1/2, frames 1024)` —— 与修复前的 `mix peak 0 max 0` 对照，**guest 混音器在输出真实 PCM**（峰值 4660/32767）。宿主链路保持健康：`AudioOut appended 2000 buffers`、`released: asks 10000 hits 2035 queue 4`（稳定 4 缓冲在环）、`AudioOut open` 1 次。
- mixprobe 的 `poison=0 zero=2048` 行与 `mix peak` 行不矛盾：毒化实验检查的是"宿主写入的毒值有没有被 guest 覆盖"（guest 每周期全量重写 → poison=0 正常），`zero=2048` 是探针采样窗与混音写入的相位错位；`mix peak` 直接读混音缓冲，非零即真声音。
- **M4 判定推进**：链路 + 混音数据都在了；"听感"只差人工确认（或音量/路由问题）。白屏的下游效应按预期解除。


## 2026-10-22 W1 后续取证（c143/c144）：宿主事件流正常，白屏收窄到 subsdk0 movie worker 卡条件变量

- **480 s 长观察（c145，无导航）**：三张 `wshot`（120/300/480 s）md5 全同（`a8138bb2…`），worker `h=0x1026` 全程 `nsvc=2` 不动、装载泵 `h=0x1027` nsvc 42764 仍在推进但仍等 `task+0x38`、播放器 `h=0x1025` nsvc 376924 正常轮询。→ **白屏是硬阻塞，不是慢自愈**；根因锁定 movie worker 的条件变量永睡（等一个从未运行的生产者）。

- 探针两处（默认关）：`threads.c` 的 `threads` 转储对 kind-2 等待者补打**条件键字**（`word=`，此前只读 kind-1 的锁字，条件变量等待者的关键判据一直是瞎的）；`movie_host.c` 的 `fire()` 加宿主→客户事件 signal 计数（`NIER_MOVIE_LOG`，前 12 条）。
- **c143（双次 dump，间隔 4 s）**：全部 kind-2 等待者 `word=0x1` —— 这是 0x1C 的设计（先置键再挂链），不是丢唤醒形态；装载泵 `h=0x1027` 的 nsvc 1248→1510 在推进。**唯独 movie worker `h=0x1026`（entry 在 subsdk0，卡 `main+0xb2fb5c` 的 WaitConditionVariable，key=`…38efa4`）nsvc=2 两次都不动** —— 线程创建后只做过 2 次 svc，第二次就是这次永睡。
- **c144（fire 计数）**：宿主 decoder 的事件在**持续发出**（`fire #1..#12`，ev0 主要是 EV_INPUT=`…0eb8`），播放器线程 `h=0x1025` 轮询消费正常（nsvc 13780 在涨）。post-EOS acquire total=42/43：播放器拿到 EOS 后只再调 2 次就停手 —— 与"播放器进入影片结束路径"一致。
- **排除与结论**：W1c 之后，VP9 视频路径（宿主 decoder + 事件 + 播放器轮询）已闭环；卡死点移到 **subsdk0 movie 库自己的 worker 线程**：它等的条件变量没人 signal。它是 `DecoderImpl`/NvMM 时代的异步工作线程（`movie_host.c` 头注释：该库 = Android MediaCodec over NvMM，经 nvdrv 等 NvHostSync fence）——宿主只 override 了 `movie::Decoder` 的 17 个 vtable 方法，**没有接管这个 worker 的唤醒源**。下一手（W5）：定位该条件变量的 signal 方（subsdk0 里 `SignalConditionVariable` 的调用点归因），判断它等的是 fence 还是队列；若属 NvHostSync 等待，需给该 worker 的等待点做宿主语义（这是"未知服务保留明确失败"与"实现有语义的降级"的边界，先取证再动）。
- 本轮不动行为；探针默认关不影响。加长回归：W1c+W3+W4 叠加后 `stability.sh 10 13` = **10/10 faults=0 STABLE**（`logs/stab10.log`）。


## 2026-10-22 W4：uplog 升级为字节统计，裁决"池里是 0xFF"——**错**，池里就是解码数据

- 改动：`nvn_mtl.m` 的 `DM_NVN_UPLOG` 块单遍统计 `wff`（全 FF 字）、`ff/n`（FF 字节占比）、`min/max/mean`、前 16 字节 hex。默认关不变。
- **实测（c142，`DM_NVN_UPLOG=1600x904`，30 s）**：`ser=1502 … wff=0 ff=0/1446400 mean=16.0 min=16 max=16 head=10101010…`（= Y 平面 0x10 填充 = 解码 frame 0 的 mean 16.0，逐帧吻合）；随后 `mean=184.2 head=baba…`、`mean=226.7 head=e5e5…` 是淡入曲线的第 2、3 帧。**影片平面池子里就是解码结果，不是 0xFF**。c118 的"nz8 全非零 → 池被填白"正式作废（nz8 区分不了 0x10 重复与 0xFF——W4 就是为了堵这个）。
- 因此白屏的剩余成因收窄为：(a) 画面静止 = 播放器停在 EOS 后的最后一帧（淡入到亮场的 logo 结尾），(b) 真正没推进的是**任务完成方**：`h=0x1026`（movie 库 worker，entry 在 subsdk0）卡在条件变量 `key=…38efa4`，`nsvc=2` 后再没醒过（c139/c140 一致）——装载泵 `h=0x1027` 等的 `task+0x38` 信号量正该由它 release。下一轮假设：查谁该 signal 这个条件变量（`svc 0x1D` 丢唤醒的又一形态，或 worker 在等宿主 IO 完成回调）。

- **再延伸（同一份 c142 日志）**：`ser=1502` 的 mean 时间序列 16.0 → 184.2 → 226.7 → **228.1 并定格到 frame 697+**。Y=228 在 YUV 里就是白场——**「白屏」的白就是影片（`movie_logo/logo_01.usm`）最后一帧的真实内容**（淡入到白场后播放器定格），不是任何填充/渲染错误。「白屏」一词从此的准确含义 = **「影片停在末帧 + 装载泵永等 worker」**，画面侧无缺陷。


## 2026-10-22 W3：REUP_MAX_BYTES 1 MB→4 MB，影片平面不再每帧新建纹理

- 缺陷：`reup_offer()` 的 1 MB 门槛恰好挡住影片 Y 平面（1600x904 R8，Metal `allocatedSize` 1728 KB）——每帧 `nvnTextureInitialize` 换新 serial、`reup_take` 永不命中、`pkg_upload_` 每帧新建一张纹理。实测（c115，修复前）：至帧 10212 累计 **10200 次 / 17208 MB** 分配流水。
- 改动：`nvn_mtl.m:3303` `REUP_MAX_BYTES` 1 MB → 4 MB。安全性（subagent 审计 + 本次复看）：复用键只含描述符、无内容哈希，但 `pkg_upload_` 每次**全量覆写**所有 level×layer，复用不会读到旧内容；上限滞留 = 256 槽 × 4 MB，`REUP_IDLE=600` 提交后退。
- **实测（c141，40 s，W3 判据）**：`[tex-new] 1728 KB 1600x904` 累计 **8 次 / 10 MB**（此前同窗口约 1200 次）；`重传贴图复用 522 次（留存 524 张，现存 2 张）`；`显卡已分配 2070 MB` 平稳。回归 `stability.sh 3 13` = STABLE。


## 2026-10-22 W1 实施：白屏的线程挂起解除（svc 0x18 空句柄轮询化）+ W0/osinit 取证探针

**实测链（c130→c138，同脚本只差开关）**
- W0 判据（c130）：影片事件线程 `h=0x1025` 的**第一次** svc 0x18 就 `n==0`（`n -1->0`，不是中途掉句柄）→ 排除"上游丢 holder"的 H-B 原始形态。
- W1a 第一版（c131/c132：`n==0` 立即返回错误）：线程不再冻结，但 `nsvc≈1.5 亿/30 s`（约 5M 次/秒）空转，霸住虚拟核（`core 0 owner 0x1025 for 11710 ms`），其余线程饿死 → 错误码方向对、节奏错。
- osinit 计数（c136，`NIER_TRACE_SVC0` 下挂 `InitializeEvent`/`InitializeMultiWaitHolder(Event)`/`InitializeMultiWait`/`LinkMultiWaitHolder`/`WaitAny` 五个转发桩）：SDK 初始化链**完整跑过**——InitializeEvent ≥16 次、5 个 holder 全部 Link 进同一个 mw、WaitAny 在同一 mw 上反复进入。`CreateEvent`（svc 0x45）全程 0 次是**正常的**：这个 SDK 版本的 `EventType` 是纯用户态实现（`sdk+0x33e200 InitializeEvent` 只写对象字节、不调内核；`sdk+0x33e270 SignalEvent` 的唤醒 = 写事件字节 + 遍历 holder 写各自 MW 的 front+0x10，也全是内存写）。
- 因此 `n==0` 的真实语义：这 5 个 holder 没有内核句柄可交（GetSyncHandle 返回 false），SDK 自己就是"count=0 + MW 无限超时"的**轮询等待者**，两次 WaitSynchronizationNE 之间检查 front+0x10。宿主立即返回错误 = 把轮询变成 5M/s 忙等。
- **W1c（本版）**：`n==0` 时 `block(t,4,min(ns,1ms))` 睡最多 1 ms（让出虚拟核），再返回 `R_CANCELLED` + 下标 -2（`sdk+0x3482c8` 把 0xec01 映射成 -2；`sdk+0x342af0` 的 -2 路径检查 front+0x10，有 signal 就返回 holder，否则重新收集再等）。返回 -1（R_TIMEOUT）会让 SDK 读 `handles[-1]`（栈垃圾），已排除。
- **实测（c137，30 s）**：轮询率 5M/s → **590/s**；`[负载] T 99% → 2%`，CPU P 38%→4%；**EOS 正常到达**（`end of stream after 74 frames`，c131/c132 里从未到过）；装载泵 `h=0x1027` 的 `nsvc 158 → 2133`（在推进，不再定格）。回归 `stability.sh 3 13` = `STABLE`。
- **仍未解除**：画面仍是同一张静止近白（`c137_a.png` md5 与 c122 全同）。事件线程活着、EOS 已交付，但游戏不 `stop/configure` 第二部影片 —— 转入 W2：查 EOS 之后影片播放器状态机卡在哪一步（c138 已证明 4 个 DecoderEvents 指针注册到位：`register events obj=… ev=…eb8,…ee0,…f08,…f30`，与 InitializeEvent #13-16 逐一对应；宿主 fire() 有处可发）。

**新增诊断（全部默认关、有上限）**：`NIER_TRACE_SVC0=1`：`[svc0]` svc 0x18 句柄数轨迹 + n==0 计数（含 lr/参数）、`[svc0] CreateEvent` 计数、`[taskpool]` 任务池状态（`game_patches.c:nier_taskpool_tick`，由 `runtime_watch_tick` 驱动）；`[osinit]` 五个 nn::os 初始化转发桩（`game_patches.c:nier_os_init_override`，接入 `runtime_override` 链）。`NIER_MOVIE_LOG=1` 增加 `register events` 行。

**修正一条历史误判**：c132/c133 的"mw dump 显示 holder 链表非空/vptr=0"作废——svc 0x18 的 x0 是 `WaitSynchronizationNE` 的**栈上出参槽**（`sdk+0x348298 add x0,x29,#0x1c`），不是 MultiWait 指针，当时读的是栈垃圾。教训：探针先核对参数的真实含义再下结论。


# 2026-10-22 白屏根因重估：不是渲染，是 guest 线程挂起（`svc 0x18` 等 0 个句柄 + `AcquireSemaphore` 等任务完成）

本轮不改行为，只把"白屏"的病因从"影片池被填 0xFF"改判为"推进影片/装载状态机的线程根本没在跑"。执行方案迁到 `docs/plan-2026-10-22.md`。

- **推翻上一条的证据**：`DM_NVN_UPLOG` 的 `nz8=180800/180800` 统计的是"8 字节字非零"。解码输出 Y=16..65 时填充字是 `0x1010101010101010`，**同样非零**——nz8 全非零与"池里是 0xFF（白）"之间没有推理关系。上一条"游戏侧池子被填成 0xFF"作废。
- **线程转储（c122，白屏 30 s，`logs/c122.log:296` 起）**：
  - 影片事件线程 `h=0x1025`：`wait=4`（WaitSynchronization）、**`waiting on 0 handle(s)`**、`last_svc=0x18`、`nsvc=105` 后不再动。栈：`sdk+0xdb6b68（gates 表：svc 24=0x18）← sdk+0x342a74 ← sdk+0x342ed0 ← sdk+0x340054 ← main+0xa68aa8`。
  - `ExecStartupShader` 线程 `h=0x1027`：`sdk+0xdb6c08(svc 28) ← main+0x9a62a8 ← main+0x58ff30 ← main+0x58fa70`。
- **反汇编定名（`main.patched.bin` + `analysis/runtime-layout.json` 的 relocs 把 GOT stub 还原成符号）**：
  - `main+0x9a62a4 = bl main+0xd9a840`，GOT 槽 `0x12b6148` = **`nn::os::AcquireSemaphore(SemaphoreType*)`**；`0x9a62a8` 是它的返回地址。该线程是 `ExecStartupShader`（线程名串 `0xf8bcca`，入口 `main+0x58fa10` 注册于线程表 `0xf3da28`）。pump 函数 `main+0x9a6234` 的结构：置任务状态 `[task+0x28]=3` → `ReleaseSemaphore(task+0x68)`（`0x9a629c`，唤醒 worker）→ `AcquireSemaphore(task+0x38)`（`0x9a62a4`，**等完成**）→ 返回后复查状态 `==5` 才跑完成回调；完成方 `Release(+0x38)` 站点在 `main+0x9a6af8/0x9a6bc8`。**这条链是 core 装载的消费半段（等任务完成），不是此前文档说的"注册 bnk 那条链"**（生产半段 `0x58f7ec→0x8c9870→0x983b80→0x9974d4`，两者经同一任务池 `[0x12c7df0]` 耦合）。
  - `main+0xa68aa4 = bl main+0xd9afa0` = **`nn::os::WaitAny(MultiWaitType*)`**；其前是 `movie::MediaData::FindInt32/FindRect/Clear/D1`，其后 `cmp x0,x22` + `cbnz w0, #0xa68aa0` 重试循环，且 `0xa68aa8` 起用返回值与 `x21/x22/x23/[sp+0x10]/[sp+8]` 五个值逐一比较派发——**游戏代码自己就在处理 WaitAny 的多种非成功结果**。
  - 我们的 `runtime/threads.c:523` 对 `n==0` 的校验是 `(!hs&&n)` → 放行，随后 `block(t,4,-1)` 且 `wh[]` 为空 ⇒ 任何 signal 都扫不到它 ⇒ **永久挂起**。
- **两种解释，本轮不下结论**（承重论断"真实内核对 `count==0` 立即返回错误"我未能在本机验证 Atmosphère 实现，故只作假设）：**H-A** 宿主语义与内核不同；**H-B** `count==0` 是症状，上游本该挂进来的事件句柄没进来（旁证：`nsvc=105` 不是 1，说明该线程此前正常等待过，后来才变空）。区分手段见方案 W0（默认关的 `n` 值轨迹 + `MultiWaitType` 对象 dump，一次跑定性）。
- **`h=0x1025` 的 `nsvc=105` 定格与 hletrace 里没有任何 `movie::*` 互相印证**：挂起的线程不产生宿主调用。上一条"游戏此刻不再调解码器"描述成立、因果反了。
- **画面静止的实测**：`c121_1/2/3.png` 与 `c122_a.png` md5 全同（`a8138bb2…`，跨 222 s），主色 `(250,248,241)` 占 94.4%；主线程每帧重放同一帧（`passdump` 的 2 次绘制）。
- **顺带核实（W3 依据）**：`reup_offer()` 复用键只含描述符、无内容哈希，但 `pkg_upload_` 每次全量覆写所有 level×layer ⇒ `REUP_MAX_BYTES` 从 1 MB 提到 4 MB 安全（1728 KB 影片平面因此可进缓存，消掉每帧新建）。
- 待办排序改为：**W0** 先取证区分 H-A/H-B（零行为改动）→ **W1a/W1b** 按结论二选一 → **W2** 影片 EOS 后推进 → **W3** REUP 提额 → **W4** uplog 统计修正（nz8 升级为字节 min/max/mean + 0xFF 占比，防再次误判）。音频混音恒 0 判为白屏下游，不单独追；M4 改回"链路达成、听感未达成"。

# 白屏：解码输出正确，白来自游戏自己每帧重传的 YUV 影片平面

本次只加诊断，不改行为，为了把"白屏"从"我们的解码器给了白帧"里摘出去。

- `runtime/movie_host.c`：`NIER_MOVIE_LOG=1` 下，前 3 帧解码后打 Y 平面统计。实测（c121，无导航）：
  `[movie] frame 0 Y plane: mean 16.0 min 16 max 16`、`frame 1 mean 36.8 min 15 max 42`、`frame 2 mean 58.5 min 16 max 65`
  （全部 1446400 px）→ 是真画面，且是"从黑淡入"的正常开头，**解码路径没有问题**。
- 同一轮：`decoder #1 created` → `configured 1600x904 VP9, 4 input buffers of 2048 KiB, 8 output frames of 2169600 bytes` → `start` → `send #1..#5…` → 第 74 帧 `end of stream`。此后（c116，150 s）**再没有 configure/start/stop/reset/flush，也没有第二台解码器**。
- `passdump 1`（c117，帧 3846，白屏中）：整帧只有 **2 次绘制** —— ①全屏四边形，目标 1920x1080，采样 `ser=26`；②全屏四边形 `blend=0xf`，采样 `ser=1502`（1600x904，`nvnfmt=0x1` = Y 平面 R8）与 `ser=1503`（800x452，`nvnfmt=0xd` = UV 平面 RG8）→ 这就是 **YUV420 影片平面的全屏合成**，每帧一次。
- `DM_NVN_UPLOG=1600x904`（c118）：`[uplog] ser=1502 fmt=0x1 1600x904 … size=1446400 nz8=180800/180800` → 上传源内存每个 8 字节字都非零；结合解码输出实测是 16…65 的亮度，说明游戏侧那池子被填的是 **0xFF（白）**，不是解码结果。
- `hletrace 1`（c119，EOS 修复后，白屏窗口 6 s）：32447 次调用 / 50 个函数，**排行里没有任何 `movie::*`**；`DrawElements 358`（≈60/s，与每帧 2 次绘制一致）→ 白屏期间游戏**根本不再调解码器**，只是反复画自己的影片平面。
- 另外给 `[tex-new]` 加了尺寸/格式：重复新建的是 `1728 KB 1600x904 pf=10 lv=1`，每帧 1 张（c115 至帧 10212 累计 10200 次 / 17208 MB）；`reup_offer()` 的 `REUP_MAX_BYTES` 是 1 MB，1728 KB 永不进回收缓存，所以每帧新分配。

下一手：查"谁把 0xFF 填进那个影片平面池 / 为什么游戏不重新 configure 解码器（真实 MediaCodec 在 EOS 之后的行为差异？）"，以及 `h_get_output` 的返回值/flags 是否被游戏判成"没拿到帧"。

## 2026-10-22 白屏排查（1/2）：movie 解码器 EOS 之后仍在发放输入缓冲 —— 游戏线程每秒空转 3500 次；修掉后**白屏不变**，但空转消失

**背景**：修好 `GetDirectoryEntryCount`（上一提交）后，游戏开始真正枚举并装载全部资源，随后的序章阶段表现为**长时间整屏纯白**（用户报告"一直白屏"）。本轮先判断"是卡住还是画错"。

**证据一（不是挂起）**：`logs/c110.log` 的 `threads` dump —— guest 主线程在 `mtl_link_wait_tick <- h_nvnWindowAcquireTexture <- main+0x9bbae8`（正常的等上屏），其余线程 `nanosleep`/`svc_thread`；`[负载] … GPU 94% (1%)` 里**游戏自己的 GPU 工作只有 1%**；帧号仍在推进（帧 300→3600，约 55 fps），同一时刻两张 `wshot` 的 md5 完全相同（`c110_a.png` = `c110_b.png`）→ 游戏在做同一件事、画同一个白画面，不是卡死。

**证据二（`hletrace 2` 8 s 窗口）**：`movie::Decoder::AcquireInputBufferIndex` **28045 次**、`SendInputBufferForDecode` **28044 次**、`GetInputBuffer` **28044 次** —— 每秒约 3500 次，全在影片解码的**输入侧**。

**根治点**：`runtime/movie_host.c` 的 `h_acquire_input()` 在 `m->eos_in` 置位后**仍然继续发放输入缓冲**（`send` 的 EOS 分支只是把 buffer 塞回空闲表）。真实 MediaCodec 在 EOS 之后不再交出输入缓冲，于是游戏（`size=0, flags=4`）反复"拿到缓冲→提交 EOS"，永不终止。修法是 `if (m->started && !m->eos_in && m->nin_free > 0)`。

**结果**：`logs/c113.log` 里 `[movie] send #` 的滚动计数停在第 5 次之后再没有 2000 次行，空转消失；但 **`c113_a.png` 仍是 99% 纯白** → 该死循环**不是**白屏的原因（它是一个独立缺陷，每秒 3500 次空转白烧 CPU）。

**白屏留下的线索**：同一轮 `[tex-new]` 一路涨到 **3400 次 / 5733 MB**（每帧新建一张 1728 KB 纹理、几乎没有回收），GPU 94% 而游戏 GPU 工作 1%。下一轮从"为什么每帧新建一张 1728 KB 纹理、谁该回收"入手。

新增诊断（默认关，`NIER_MOVIE_LOG=1` 打开，只打前 5 次与每 2000 次）：`[movie] send #n idx/off/size/t/flags/…`、`[movie] acquire #n ok/eos/none/…`、解码器创建计数 `decoder #n created`。回归 `sh scripts/stability.sh 3 13` = `run 1/2/3: faults=0` → `STABLE`。

## 2026-10-22 音频 P0：`GetReleasedAudioOutBuffer` 返还的 tag 用了 `buffer->next`（恒为 0）—— 引擎以为"没有空闲缓冲"，每秒把输出拆掉重建，改返缓冲区对象本身后输出变成一条持续流

**一句话**：`runtime/audio_public.c` 的 `nier_audio_append()` 把 `(Queued){b->next, bytes}` 存进队列，播完再把这个 `tag` 交给 guest 的 `GetReleasedAudioOutBuffer`。但 guest 的 `AudioOutBuffer::next` 是 SDK 自己的链指针、游戏填 0，于是**每次"释放"都报告 tag=0**。引擎据此认定没有缓冲区播完 → 立刻 stop/close 再重开，形成每秒一次的重试循环，声音永远只播几毫秒就被掐断。

**怎么定位的（`hletrace 2` 窗口普查，`logs/c97.log`）**：一个 8 s 窗口里 `GetReleasedAudioOutBuffer` 被调 **1530 次**，而 `OpenDefaultAudioOut`/`StartAudioOut`/`StopAudioOut`/`CloseAudioOut` 各 **8 次**（每 8 s 一个 open→start→stop→close 周期）、`AppendAudioOutBuffer` **32 次**（每周期 4 个 1024 帧缓冲）。也就是说游戏确实每周期提交了 4 个缓冲、然后 24 次/周期地等释放 —— 只是永远等不到。

**逐层证据**：
- `[audio] played callback 1 q=0x… running=1 samples 1024` → 1,2,3,4 次回调、`samples` 1024→4096，Core Audio 真在播（不是静音旁路）。
- `[audio] callback released tag 0 host 0x…` ×4 → 播放线程推给 FIFO 的 tag 全是 **0**。
- `[audio] guest took tag` 在修复前一次都不出现（`hits 0`），修复后立刻出现：`guest took tag 0x71c55d0080 … (ask 9)`。

**修复**（`runtime/audio_public.c`）：队列里存 guest 传进来的 `AudioOutBuffer*` 本身（`(Queued){(uint64_t)(uintptr_t)buf_raw, bytes}`），播完把它作为 tag 返还 —— 这正是 SDK 语义（`GetReleasedAudioOutBuffer(AudioOut*)` 返回可复用的缓冲区对象指针）。

**修复后实测（c101 标题 26 s / c102 全导航 250 s）**：
- c101：`AudioOut open` **1 次**（修复前每秒 1 次）、`guest took tag` 正常、`released: asks 2000 hits **424** queue 4`。
- c102（250 s 到飞行场景）：`AudioOut open` 仍**只有 1 次**（输出保持打开整场），`AudioOut appended` 日志行 23（每 1/500 次打点 ⇒ ≥11000 次提交），`released: asks 54000 hits **11140** queue **4**`（稳定 4 缓冲在环），0 崩、0 `signal=`。
- 回归 `sh scripts/stability.sh 3 13` = `run 1/2/3: faults=0` → `STABLE`。

**顺带记录**：本轮前一步的 `GetDirectoryEntryCount` 句柄 bug（`29a4983`）让音库能装载、输出能打开；这一步让输出能真正持续跑。两个 bug 串起来才是"完全没声音"的完整原因。

**诊断开关**（都受 `NIER_TRACE_AUDIO` 门控，默认关）：`[audio] queue created ok`、`[audio] AudioQueueStart rc=%d running=%u`、`[audio] played callback %u q=%p running=%u samples %llu`（前 4 次）、`[audio] callback released tag %#llx host %p`（前 4 次）、`[audio] guest took tag %#llx host %p (ask %u)`（前 3 次）、`[audio] released: asks %u hits %u queue %u`（每 2000 次）、`[audio] enqueue failed rc=%d`（前 3 次与每 200 次）。

## 2026-10-22 音频 P0：根因是 `GetDirectoryEntryCount` 把句柄当路径 —— 一句签名错误让整张文件表为空，音库和音频输出全没了

**一句话**：`runtime/fs_host.c` 的 `dir_entry_count(int64_t *out, const char *path)` 把 `nn::fs::GetDirectoryEntryCount(s64*, DirectoryHandle)` 的**第二个参数当成路径**。guest 传进来的是句柄（`0x300000000`+i），我们把它当 `char *` 去 `strlen/opendir` → 垃圾路径 → 返回错误 → 引擎挂载 `rom:/` 时"列举到 0 个条目"，于是整棵 `Hw::DvdSystem` 路径树是空的，`sound/Init.bnk` 与三个 `.wai` 全部"見つかりません"，Wwise 无音库、音频输出也从不初始化。

**证据（`TOTK_FS_LOG=1` 实测，c91 → c93 对照）**
- 修复前 c91：`[totk-fs] OpenDirectory rom:/ mode 3 raw=726f6d3a2f000000` 成功并列出 34 条，随后第二条 `OpenDirectory u�� mode 3 raw=75a5f5121e0f0000`（就是句柄 `0x300000000` 被当字符串读出的字节）失败 → 之后**没有任何** `GetDirectoryEntryCount`/`ReadDirectory` 调用。
- 修复后 c93：
```
[totk-fs] GetDirectoryEntryCount rom:/ handle=12884901888 -> 34
[totk-fs] ReadDirectory rom:/ pos=0 n=34 max=34
[totk-fs] GetDirectoryEntryCount rom:/Enlighten/ -> 364
[totk-fs] GetDirectoryEntryCount rom:/movie/ -> 158
[totk-fs] GetDirectoryEntryCount rom:/sound/ -> 9
[totk-fs] GetDirectoryEntryCount rom:/sound/English(US)/ -> 19
…
```
- 修复后首次真正打开音库：`0.083 rom rom:/sound/bgm/BGM.bnk`、`0.097 WwiseVoiceInfo.wai`、`0.100 WwiseStreamInfo.wai`、`0.101 WwiseInfo.wai`、`0.102 Init.bnk`、`5.870 sound/Japanese/vo_core.bnk`。
- `[audstate] env` 从 `h=0,0,0` 变成 **`h=0x75f8005a00,0x75f803ee00,0x75f8072800`**（三个音库句柄非 0），`dvdB0` 根节点从空变成有子项（`Enlighten/`、`movie/` …）。
- 音频输出首次被真正打开（c94，`NIER_TRACE_AUDIO=1`）：
```
[audio] OpenDefaultAudioOut parameter u32(+0) 48000 u16(+4) 2 u16(+6) 51966 -> using 48000 Hz x2
[audio] AudioOut open 48000 Hz x2 PCM16 -> Core Audio (stereo)
[audio] AudioOut appended 1 buffers, 1024 frames each
[audio] AudioOut start / stop / close
```
启动阶段这种"开→塞 1 个 1024 帧缓冲→start→stop→close"的探测**重复 38 次**（c93 里 `OpenDefaultAudioOut` 38 次，全在启动窗口内），之后不再有音频调用。

**修复**：`dir_entry_count(int64_t *out, uint64_t h)` 改成按**句柄**计数 —— RomFS 句柄直接返回 `d->nrom`，宿主目录在旁路 `opendir` 重数列出（不移动句柄读位置），并按 `TOTK_FS_LOG` 打 `GetDirectoryEntryCount <path> handle=<n> -> <count>`。

**回归**：`sh scripts/stability.sh 3 13` = `run 1/2/3: faults=0` → `STABLE`；c93/c94 均 40 s 到标题、0 崩、无 `signal=`。

**下一手**：①跑完整导航（飞行场景 + BGM/SFX）看 `AppendAudioOutBuffer` 是否持续流动（现在只有启动探测的 38 次）；②若启动探测后仍不持续，检查 Wwise 的缓冲数/延迟要求（探测里每次只塞 1 个 1024 帧缓冲就 stop/close，可能是 Wwise 在测延迟后放弃）。**注意**：这条修复同时也解释了为什么"路径树是空的"和"guest 从不 open sound/"——它们是同一个 bug 的下游。

## 2026-10-22 音频 P0：声音资源一个都没进 `Hw::DvdSystem`（`sound/Init.bnk` + 3 个 `.wai` 全失败；表结构已 dump）

**工具**：`NIER_AUDIO_STATE_PROBE=1`（只读，`runtime/game_patches.c`，`mach_vm_read_overwrite` 读宿主堆）新增两段：①`SoundSystem::Env` 状态 `[audstate] env y=… h=… obj=… st=…`；②DvdSystem 表 dump `[audstate] dvdA/dvdB/dvdC …`。`logs/c83.sh`（40 s 到标题、帧 900、0 崩）与 `logs/c89.sh`（帧 918）为实测轮。

**实测（c83，F=60 与 F=600 完全一致）**
```
[audstate] env y=0x10a349658,0x10a349668,0x10a349678 h=0,0,0 obj=0x10a349680 st=0,0x3e3a8bf9,0x6c2a37cc
```
- `y` 三个对象确实在堆上分配过（0x10 步长相邻），但三者的**首字段（文件句柄）= 0**，四个 `registData` 返回槽 `st=0,0,0,0`。
- **语义纠正（推翻上一轮 f920685 的判断）**：`main+0x983b80` 的返回值 **0 = 失败**。成功路径 `0x983c08 str x24,[x20]` + `0x9834a0` 非零；失败 `0x983c34/c68 mov w0,wzr` + `str xzr,[x20]`。上一轮把 `st=0,0,0,0` 读成"四个全成功"是错的，实际是 **`sound/Init.bnk` 与 `sound/WwiseInfo.wai`/`WwiseStreamInfo.wai`/`WwiseVoiceInfo.wai` 四个全部没装进来**。
- `0x3e3a8bf9`/`0x6c2a37cc` 不是状态码，是 `SE_ListenerPreset`/`SE_ListenerPresetFadeTime` 的 AkUniqueID（`0x9ae220`/`0x9ad7c0` 查表结果）；`Volume_Master` 查到 0。

**装载链路（静态，已核实）**：`main+0x8c9870`（唯一调用者 `0x58f814` ← `0x58f7ec` ← `0x58f75c`）先用 `0x9aca90` 把 `sound/Init.bnk` 格式化进 `*(0x12c6768)` 的 0x200 缓冲，再四次 `0x983b80(obj, 0, path, allocator, flag, 0)`；`0x983b80` 内部是 `0x982330(path)`（找不到就走 `0xf94827` 的 `[Hw::DvdSystem] %sは見つかりません`）→ `0x9974d4`（读入）→ `0x9834a0`（解析）。**卡在第一步 `0x982330` 返回 0**。

**RomFS 侧无问题**：`sound/Init.bnk` 22621 B、`sound/WwiseInfo.wai` 234336 B、`sound/WwiseStreamInfo.wai` 211280 B、`sound/WwiseVoiceInfo.wai` 80064 B 都真实存在于 `runtime-index.bin`（660 条）；`runtime/fs_host.c` 的 `open_rom_directory()` 每次列举都无条件打 `[totk] RomFS directory rom:/: 34 entries`，说明 guest 确实拿到了 34 个条目（只在启动时列举一次，从不列举子目录，也从不 open `sound/` 路径）。

**DvdSystem 表结构（本轮 dump 出来）**：`main+0x982330` 依次查三个来源——
1. 记录列表：`base=*(*(0x12c7890))`、`count=*(u32*)*(0x12c7898)`，记录 0x70 步长，**flag 在记录 +0（循环用 `ldur w8,[x20,#-8]`，payload 在 +8）**，对 flag≠0 的每条调 `0x981788(rec+8, path)`；
2. 内联列表 `*(0x12c7888)`（`{u32 count; payload @+8}`）；
3. 哈希桶表 `*(0x12c78c8)`：`w20=0x7e0` 起每 0x20 一块向下扫 64 桶，桶非空就调 `0x982460`；桶里 `[bucket]` 的 word0 = 种类，**1 = 记录列表**（回到来源 1/2 的链表，0x70 步长）、**2 = cpk 条目**（`x0=[bucket+8]` 再调 `0xaa5780`）。
`0x981788` 是**路径树匹配**：`strchr(path,'/')`，节点 `{char *name; …; dirs @+0x10; files @+0x18}`，子项 `{char *name; child *next}`，用 `0xd9a260`（定长比较）/`0xd9a270`（整串比较）后递归剩余路径。

**实测 dump（c84–c89，F=60 与 F=600 两次都一样）**
```
[audstate] dvdA base=0x7544a7bb98 n=1
[audstate] dvdA0 node=0x7544a7bba0 words=0,0,0,0,0,0 name=""
[audstate] dvdC n=0 node=0
[audstate] dvdB n=1
[audstate] dvdB0 node=0x10a392cd8 words=0x7544a0e540,0,0,0,0x75449bbc20,0x40 name="rom:/"
```
即：`rom:/` 的根节点存在且名字正确，但 **dirs（+0x10）与 files（+0x18）都是 0**，另有一个 64 槽（word5=0x40）数组（word4）**全 0**；来源 1 的唯一记录 flag=0（被跳过）；来源 3 计数为 0。**整棵树是空的** —— 与 `sound/*` 查不到完全一致。

**为什么前面看不到任何 guest 报错**：游戏自己的日志函数 `main+0x9802b4` 是**空实现**（只把 varargs `stp` 进栈就 `ret`），release 版把日志编译掉了；`0x8c9b40` 区域那三条 `[SoundSystem::Env] … 読み込みエラー` 同样不会输出。所以失败是静默的，只能靠探针读状态。

**排除项**：`data200.cpk`…`data207.cpk`、`data300.cpk` 九个 cpk 在 t≈0.000 就被逐个试探（`TOTK_FS_LOG` 行 85–101），RomFS 索引里没有这些名字，格式化串 `data%03d.cpk` 在 main 里也不存在字面量 → 属"按编号范围试探、缺失可容忍"，**不是本轮病因**。

**下一手**：找**谁往树里插节点**（mount 侧），判断是 guest 的 mount 逻辑因我们返回的 `DirectoryEntry` 属性（`type`/`name`/`size`，`runtime/fs_host.c:284` 的 0x310 结构、`type` 0=dir/1=file）而整段跳过，还是根本走了另一条 mount 路径；同时可 dump 来源 3 的 64 桶看有没有 live 条目。**注意**：`/Volumes/bit2t` 卷上改完 `runtime/*.c` 必须 `touch` 再 `make`，否则 make 会因为 mtime 粒度输出 `Nothing to be done for 'runtime'` 拿旧二进制跑（c82 就这么白跑一轮）。

## 2026-10-22 — 音频 P0 第 3 步：**修正上一条**——四个音库装载其实**全失败**，卡点在更上游的 guest `Hw::DvdSystem` 路径查找

- 探针再扩一组（仍 `NIER_AUDIO_STATE_PROBE=1`、只读）：游戏自己的 `SoundSystem::Env` 状态。对象三层读法 —— 槽 `main+0x12c66c8/+0x12c6788/+0x12c66d0` 是**对象指针**（堆上 0x10 字节结构，首字段才是文件句柄），槽 `main+0x12c66d8` 是引擎对象，`main+0x12c66e8/+0x12c67a8/+0x12c67b0` 存 GameSync 的返回；新增输出行 `[audstate] env y=…, h=…, obj=…, st=…`（`y` = 三个对象指针，便于区分"指针在/不在"与"字段为 0"）。
- **c83 实测（40 s 到标题，帧 900，0 崩，`logs/c83.log`）**：`y=0x10a349658,0x10a349668,0x10a349678`（三个对象 0x10 相邻、确实分配过）、`h=0,0,0`、`obj=0x10a349680`、`st=0,0x3e3a8bf9,0x6c2a37cc`。`F=60` 与 `F=600` 完全一致 → 状态从启动起就定格。
- **修正上一条（`f920685`）的语义错误**：`0x983b80` 的返回值 **0 = 失败**，不是成功。静态读法（`main+0x983b80`）：成功路径 `0x983c08 str x24,[x20]`（把读到的缓冲写回**对象首字段**）+ `0x983c10 str x23,[x19]`（文件对象写回出参），返回 `0x9834a0` 的非零值；失败路径 `0x983c34/c68 mov w0,wzr` + `str xzr,[x20]`。c80/c83 的 `st=0,0,0,0` 与 `h=0,0,0` 因此是同一条事实的两面：**`sound/Init.bnk` 与三个 `.wai` 全部没装进来**。
- 失败时会打的两条串（`0x983b80` 内）：`[Hw::DvdSystem] %sは見つかりません`（`0xf94827`，来自 `0x982330` 返回 0）与 `[Hw::DvdSystem] メモリ不足が発生(%s)`（`0xfb7c29`，来自 `0x9974d4` 返回 0）——两者都带路径，指向"查表不到"而非读失败。
- 更上游的 `SoundSystem::Env` 初始化（`main+0x8c9b40` 区域，已反汇编）：先 `0x9acad0(*(0x12c6768))`，再逐个检查那三个对象首字段，为 0 就打 `[SoundSystem::Env] Wwise Info / Stream Info / Voice Info 読み込みエラー`（`0xfd6787`/`0xf78bc4`/`0xf78bff`）；随后用 `0x9ae220`/`0x9ad7c0` 取 `Volume_Master`/`SE_ListenerPreset`/`SE_ListenerPresetFadeTime` 的 ID —— 实测后两个是 `0x3e3a8bf9`/`0x6c2a37cc`（AkUniqueID 哈希，不是状态码），第一个是 0。另有 `main+0x8c8570`/`0x8c85cc` 一组会把这三个句柄**清零**的复位路径。
- **因此本轮把结论上移一层**：音频卡点不是"vtable+0x98/+0xc8 没人派发"，而是**声音资源一个都没进 guest 的 `Hw::DvdSystem` 表**（bank + 3 个索引全失败）→ 没有音库 → Wwise 引擎不初始化 → 自然不会去要输出设备。上一轮"缺的只是输出设备那一跳"作废。
- 旁证（`TOTK_FS_LOG=1` 的 c77，帧 7200）：整个启动只枚举过**一次目录**（`rom:/` → 34 项），**从未 open 任何 `sound/` 路径**；另有 9 个 cpk 打开失败（`data200.cpk`…`data207.cpk`、`data300.cpk`）—— 这 9 个名字在 660 条 RomFS 索引里**不存在**，而 `data%03d.cpk` 是格式化出来的（main 里没有字面串）。这三个 `sound/*.wai` 与 `sound/Init.bnk` 在索引里**有真实条目**（`sound/WwiseInfo.wai` size 234336 @0x272df1a20 等）→ 端口侧能供，guest 侧没查到。
- **环境坑（本轮白跑一次）**：这个卷（`/Volumes/bit2t`）上编辑 `runtime/*.c` 后**必须 `touch`** 再 `make runtime`，否则 mtime 粒度让 make 判 `Nothing to be done for 'runtime'`，会拿旧二进制白跑一轮（本轮就白跑了 c82）。
- 回归：`stability.sh 3 13` = `STABLE`（探针默认关）。
- **下一手**：把 `Hw::DvdSystem` 的注册表读出来 —— `*(main+0x12c7890)` 指向 `{计数; 记录[0x70]…}`、另有一个计数在 `*(main+0x12c78b0)`/`*(main+0x12c7898)`（`0x982330` 的两级查找），打前 N 条记录的路径名即可判定"表里到底有没有 sound/*"；若没有，再顺着往"谁负责注册"（多半是 core 资源清单/`data000.cpk` 的表）追。这条路一旦通了，音频很可能直接跨过前面所有猜测。

## 2026-10-22 — 音频 P0 第 2 步：guest 的 **Wwise 文件层已初始化成功**（Init.bnk + 3 个 .wai 全返回 0），缺的只是输出设备那一跳

- 探针扩展（默认关，仍是 `NIER_AUDIO_STATE_PROBE=1`）：`runtime/game_patches.c` 的 `nier_audio_state_tick()` 多读一组 Wwise 静态 —— 缓冲指针槽 `main+0x12c6768`、四个注册结果指针槽 `0x12c6778/+0x6780/+0x6790/+0x6798`，打一行 `[audstate] wwise buf=… text="…" st=…,…,…,…`（同样只读、`mach_vm_read_overwrite` 取宿主/镜像外的目标，读不到不会崩）。
- **c80 实测（40 s 到标题，帧 900，0 崩）**：`[audstate] wwise buf=0x10af71434 text="sound/Init.bnk" st=0,0,0,0`，`F=60` 与 `F=600` 两行完全一致 → **guest 的 Wwise 音库注册在启动早期就跑完且全部成功**。
- 静态定位（本轮新增，偏移均相对 `main`）：
  - `0x8c9870` 把 `sound/Init.bnk`（字符串 `0xf85fa8`）格式化进 `*(0x12c6768)` 的 0x200 缓冲（`0x9aca90`），再四次 `0x983b80(handle, 0, path, allocator, 0, 0)` 注册 `sound/Init.bnk` / `sound/WwiseInfo.wai`（`0xf90c3a`）/ `sound/WwiseStreamInfo.wai`（`0xfcf7b0`）/ `sound/WwiseVoiceInfo.wai`（`0xf9e826`），返回值写进 `*(0x12c6778/80/90/98)`；实测四个都是 0 = 成功。
  - 唯一调用者 `0x58f814`，属 core 数据装载函数 `0x58f7ec`（同函数还 snprintf `core/core.dat`、`core/core.dtt`），再上一层 `0x58f75c`。→ 声音的文件层是随 **core 资源装载**一起初始化的，不依赖音频设备。
  - 因此阻塞点收窄为**唯一一跳**：引擎对 `AudioOut::Initialize`（vtable `+0x98` = `0xd8c508`；open 在 `+0xc8` = `0xd8c548`）的派发。静态上 `0xd8c508`/`0xd8c548`/`0xd8c8b0`/`0xd8cc4c`/`0xd8ccc8`/`0xd8ccf4` 的 BL 调用者全为 0（只能经 vtable 派发），动态上 c73 的 vtable+0xc8 桩 0 次调用 → **guest 从不请求输出**，与文件层无关。
- **工具修正（重要，之前白花时间）**：历史上三轮"adrp+add 字符串 xref"全 0 命中是**扫描器 bug** —— 掩码写成 `w & 0x9F00001F == 0x90000000`，把 Rd 位也纳入比较，于是所有 **Rd≠0 的 adrp 都被漏检**（编译器几乎总用 x8/x19 等）。改为 `(w & 0x9F000000) == 0x90000000` 后立刻在 `0x8c9888` 命中 `sound/Init.bnk` 的唯一引用。脚本留在 `/tmp/xref.py`（`imm` 模式 = adrp+add 取精确地址；`slot` 模式 = adrp+ldr 取指针槽）。
- **P4 数据点（顺带）**：`logs/c801.png` 在 **40 s** 就复现标题副标题行白块；而且不止"两端"：`The End of YoRHa Edition` 行内有两段被白块吞掉、`Automata™` 右侧也有块状碎片 —— 形态更像"某张纹理的取用/步长错位"，不是纯矩形遮挡。这个 40 s 短跑脚本（`logs/c80.sh`，无导航、只 wshot 一张）可直接当 P4 的快速迭代夹具。
- **c78（`hletrace 2` 宿主调用普查）的另一发现**：帧 2734 被**我们自己的 GPU 预算闸**中止 —— `FATAL 提交驻留集：显卡已分配 8418 MB（零拷贝 guest 池 1913 MB，实际 6505 MB）超过预算 6500 MB`，之前连续多帧 `ser=1388` 反复重传（累计 1539 张）、Metal 报 `kIOGPUCommandBufferCallbackErrorSubmissionsIgnored`。同一帧段（2730–2780）也是 c73/c74 SIGSEGV（`main+0x996d84`）的位置，而 c76/c77 能过 → **该帧段的不稳定是共性问题**（重传/提交压力），不是音频探针专属。普查本身：启动期宿主调用 228 项，`nn::codec::GetHardwareOpusDecoderWorkBufferSize` 在 **0.338 s** 就被调用（编解码层启动过），全程**没有任何 `nn::audio` AudioOut 条目**，与"输出设备初始化没走"一致。
- 回归：`stability.sh 3 13` = `STABLE`（探针默认关）。
- **下一手**：沿 `0x8c9870 ← 0x58f7ec ← 0x58f75c` 向上找 Wwise 引擎 `Init` 的入口，用同样的"读结果静态"手法钉死它停在哪一步返回；或给 vtable `+0x98`/`+0xa8` 加 SINK 式转发行桩、在桩里打 `x30`，直接抓到派发者。

## 2026-10-22 — 音频 P0：设备对象**建好了、ready=1**，卡在「open 输出」这一步没人调用（探针 + 修正上一条结论）

- 新增诊断（默认关）`NIER_AUDIO_STATE_PROBE=1`：`runtime/game_patches.c` 的 `nier_audio_state_tick()` 由 `runtime/gfx_bridge.c` 的 `runtime_watch_tick()` 每帧驱动，每 60 帧读一次 guest 静态内存并打一行 `[audstate]`（值变化时打、每 600 帧再打一次证明活着，总行数封顶 64）；对象指针/门控字都顺着 `*(base+got)` 的 `R_AARCH64_RELATIVE` 重定位值取，不写 guest、不改文本。
- **两个实现要点（都吃过亏）**：
  - 设备对象不在镜像里：guest `operator new` 由宿主堆兑现，槽 `main+0x12cc9d8` 里是**宿主指针**（c74 实测 `0x7d453e0080`），所以按镜像偏移取字段会全读成 0 —— 改用 `mach_vm_read_overwrite` 拷 0x110 字节，读不到就报 `ok=0`，不会把探针自己搞崩。
  - 上一版 c74 用 `vo` 边界检查把宿主指针判成无效，`ch/ready` 被打成占位 0 —— **那两行数字不可用**，以 c75 为准。
- **c75 实测（同一脚本，帧 60 起就稳定，直到帧 600）**：
  - `[audstate] F=60 obj=0x71c53e0080 ok=1 vt=0x10e81c458 ch=2 ready=1 a7=0x1 at2c=0 acq=1 flag=0`
  - `vt = main+0x12b4458`（对象真带上了设备类 vtable）、`ch = 2`（obj+0x108）、`ready = 1`（obj+0x10c）；
  - `F=600` 同一行 —— 状态从头到尾没变过（不是"来不及"，是**稳态停在这里**）；
  - 0x110 字节 dump 显示 obj+8 起是设备名 `"AudioTvOutput"`（`cmd 10` 的产物），其余字段全 0，obj+0x110 之后是堆邻居 —— 对象就 0x110 字节、没有别的状态。
- **修正上一条（`7542f44`）的表述**：`main+0xd8c548` 不是构造函数，它是**设备类的 vtable+0xc8 = `OpenDefaultAudioOut` 包装**（同一 vtable 从 `0x12b4458` 起共有 27 个方法槽，`+0x00` 析构 `0xd8aec0`、`+0x18` 轮询用 `0xd8add4`、`+0xc8` 就是 `0xd8c548`）；真正的构造函数是 `main+0xd8ab8c`，它**跑了**（c72 的两次 `cmd 5` 就是它造成的），对象建出来并置了 `ready=1`。
- **修正"cmd 10/12 从不出现"这条证据**：`runtime/audio_mixer.c:749/759` 的 cmd 10/12 trace 只在 `NIER_TRACE_AUDIO` 下才打（`:755` 的 cmd 5 才是无条件打印），c72 没开这个开关 —— 所以"缺席"不成立；`acq=1`（`main+0x2bc8858`）说明 `AcquireAudioDeviceNotificationForOutput`（cmd 12）**曾经成功过**，设备事件早就在手。
- **因此本轮结的论**：guest 声音引擎的**设备层已经走完**（构造 → vtable → 设备名 → 通道数 2 → 拿事件 → ready=1），稳态轮询 `main+0xd8ad0c` 每帧由引擎 tick `main+0xcdc7e8` 调用；真正没走的是**下一步：对 vtable+0xc8 的派发**（即 `OpenDefaultAudioOut` 包装 `main+0xd8c548`，c73 动态 0 次）。这也解释了为什么在 `audout:u`/HLE 侧怎么改都不会出声。
- **补充事实（下一手要用）**：`nn::os::TryWaitSystemEvent` 在本工程里**不是宿主 HLE 符号**（`runtime/` 里 `grep TryWaitSystemEvent` 为空，`threads.c:597` 的 SystemEvent 支持是走 `guest_sym()` 找 guest 内的 sdk 实现），所以"轮询有没有在跑"不能靠 import observer，只能靠改入口或读状态。设备槽 `0x12cc9d8` 的静态读取点只有 4 处：`0xcd687c`（引擎侧 getter）、`0xd8ac24`（构造尾部写）、`0xd8acd0`（销毁：`[vt](obj)` + `operator delete` + 清槽）、`0xd8ad18`（轮询）。
- **顺带发现一个真阻塞**：c73/c74 都在**帧 ≈2770（约 104 s）**崩在同一处 `main+0x996d84`（`str x8,[x9,#0x20]!`，x9=0，guest 链表摘除；`fault=0x77`、`region=main` `prot=0x505`），崩前正在翻新着色器 + `CreateThread`；而同样输入脚本的 c68/c71/c72 分别跑到 4800/7200/7200 帧无崩。下一轮用**同一二进制、只差探针开关**做 A/B，先判是不是探针引入。另：c75 只跑到帧 600 是因为上一轮残留的 `logs/c74.sh` 末尾 `pkill -f build/nier-native` 把新实例杀了（环境坑复现），不是崩溃。
- 回归：`stability.sh 3 13` = `STABLE`（探针默认关）。
- **下一手**：给 vtable+0xc8 的**派发点**定位 —— 沿 `0xcd687c`（getter）与设备类的 `+0x78/+0x90…+0x110` 方法找"该调 open"的引擎路径；同时开 `NIER_TRACE_AUDIO=1` 跑一轮拿完整音频命令序列（cmd 4/5/10/12 的时间线），把"引擎走到哪一步就不再有音频服务调用"钉死。

## 2026-10-22 — 音频 P0 收口：**动态**证明设备构造函数从未被调用（槽指向确认 + 零调用）

- 新增诊断（默认关）`NIER_AUDIO_SINK_PROBE=1`：`runtime/game_patches.c` 在重定位之后、`main` 封页之前，把设备管理器 vtable 槽 `main+0x12b4428+0xf8 = main+0x12b4520` 改写成宿主桩 `nier_audio_sink_stub`，桩打 `[audiosink] main+0x12b4520 called this=… a1=… a2=… a3=…` 再转发给原函数。
- **c73 实测（帧 2700，越过装载瞬间）**：
  - `[audiosink] patched slot main+0x12b4520: main+0xd8c548 -> host stub` —— **槽里原本就是 `main+0xd8c548`**，静态分析里"这个槽指向构造函数"当场成立，偏移没算错；
  - `grep -c "called this="` = **0**，`audout` 仍 **0** —— 真实游戏流程里这个构造函数**一次都没被调用**。
- **意义**：这是"guest 从不请求输出"的**第一条动态证据**（此前只有静态的零 BL 调用者 + 日志里没有 `[audio]`/`audout:u`）。结论坐实：在 `audout:u`/HLE 侧做任何事都不会出声，阻塞点在 guest 自己的声音引擎初始化路径里。
- **下一手**：查"谁该创建这个 manager 对象/走这条 vtable" —— 该槽是 `.data.rel.ro` 里静态初始化的（重定位直接指向 `0xd8c548`），所以不是"对象没建"，而是**调用点所在的引擎状态机没走到**。下一步用 `NIER_AUDIO_SINK_PROBE` 常开 + `passdump`/`runtime_trace_guest` 之外的办法：在 `main+0xd8ad0c`（稳态轮询，文档已定位）入口加同样形式的桩，比较"轮询在跑但没有 sink"这件事的两侧状态。
- 回归：`stability.sh 3 13` = `STABLE`（探针默认关，不影响运行）。

## 2026-10-22 — 音频 P0：设备通道数变化**不能**让 guest 请求输出（`COUNT_FIRST` 假设被证伪）

- 前置证据（现成日志，三次运行 c66/c68/c70 一致）：`audout=0` 行、`[audio]=0` 行、`audren=5` 行、`audiodev=2` 行 —— guest 从未打开 `audout:u`，我们的 `nier_audio_open_default` 从未进入（与 `docs/audio-root-cause.md` 的根因一致）。
- **c72（唯一改动 = `NIER_AUDIO_DEVICE_COUNT_FIRST=6`，其余同 c68；跑到帧 5700、0 故障）**：
  - 全日志只有 **两次** `[audiodev] cmd 5 GetActiveChannelCount -> 6` / `-> 2`（第 90、148 行），
  - `audout=0`、`[audio]=0` —— **仍然没有任何输出请求**；
  - 且 **`cmd 10`（GetActiveAudioDeviceName）与 `cmd 12`（AcquireAudioDeviceNotificationForOutput）一次都没出现**。
- **结论**：`docs/audio-root-cause.md` 里"第 3 条还有戏"这条**被证伪** —— 设备通道数不一致不会让游戏创建音频输出。而 cmd 10/12 缺席说明设备构造函数（`main+0xd8c548`）确实**从未执行**（那两次 cmd 5 来自 manager proxy 路径），与"零 BL 调用者"的静态结论互相印证。
- **下一手（已想定，未做）**：`runtime_observer` 只能挂 guest 的**导入符号**，挂不了内部地址 —— 改为在加载期把 vtable 槽（`main+0x12b4428 + 0xf8` = `main+0x12b4520`）**改写成一个会记日志再跳到原函数的宿主桩**。槽被调到时立刻留下调用者（LR/FP）与参数，一次启动即可判定"这条 vtable 到底有没有被走过、是谁该走它"。

## 2026-10-22 — 品红块排查（两条假设已排除）

- **`passdump 2` 可用且有用**：c70（帧 7200、0 故障）拿到 14686 行 `[pass] F<n> P/D/R/X/Q/Y`（序列号 / 格式 / 是否 clear / clear 色）。对 F4447、F4448 两帧统计 clear 色只有 `clear=0,0,0,0`（78 次）与 `clear=0,0,0,1`（2 次），**没有 `clear=1,0,1`** → 品红块**不是**任何被记录的清屏色。
- **不是"读入被省掉"**：`nvn_mtl.m:4145-4160` 的覆盖证明（`cov` → `MTLLoadActionDontCare`）只在 `tbdr_on()` 时生效；c71 用 `DM_NVN_LOADPROBE=1` 跑满（帧 7200）**一条探针行都没有** → 本平台没有任何附件被省掉读入，品红与此无关。
- 另有 `DM_EXP_CLEAR_SER=<serial>` 这个现成诊断（把该序列号的读入改成品红，专门让"没被任何绘制覆盖的像素"显形），我们的脚本没开；要定位就沿序列号枚举，而不是改代码。
- **品红块的现像**：`c68b.png` 左上出现、`c68c.png` 同一轮里没有 → 暂时性残留（很可能是某张贴图未清 / 复用未初始化），且面积小（约 170×195 逻辑像素）。**降级为非阻塞项**，接下来按用户给的顺序转音频 P0。

## 2026-10-22 — 记录：`TOTK_GPU_MIDCLEAR=0` 会直接崩（不是可用开关）

- c69（只把 c68 的 `TOTK_GPU_MIDCLEAR=1` 改成 `0`，其余不变）：启动约 30 s 内 guest 崩栈（`observed_main + 60` / `boot + 204` / `guest_main + 28`，日志里 6 条红行，未到帧 300）。
- 结论：`TOTK_GPU_MIDCLEAR=1` 是本作运行的必要条件 —— 用它来验证"品红块是不是我们的中途清屏色"这条路**不可用**，品红排查改走 `passdump`（对同一帧取 pass 列表，看哪一级没覆盖左上角）。
- 上一条 `c68b.png` 左上的品红矩形仍待定位。

## 2026-10-22 — P8 视觉里程碑：几何压缩路径**出画**了，飞行场景的世界几何回来了

- **c68（`NIER_GS_COMPACT=1`，225 s 长跑 + 三张晚 shot，跑到帧 4800、0 次页故障）**：
  - `c68c.png`：暗色通道内景 —— 墙面的棱条、绿色灯带、两架带**实体轮廓**的飞行机体（此前只剩引擎光条），准星正常。
  - `c68b.png`：同一段的更广视角 —— 通道脊状墙、多束引擎光、底部机体结构、字幕「自动修正系统没有问题」都出现。
  - 对照 `logs/L7.png`（P8 未做时的丢弃路径）：只有一张平铺绿底 + IFF 铭牌 + 光条，**没有任何墙体/机体几何**。
- **结论（可作为 P8 的验收证据）**：stage-2 缺失的那支 NV passthrough 几何程序，经"按索引缓冲捕获 + 内核按索引 gather + 消费端读 GPU 计数"之后，压缩结果确实进了画面；页故障在 5 连轮 + 本轮 = 6 轮里 0 次（c57/c59/c60/c62/c65/c66/c68）。
- **遗留**：`c68b.png` 左上出现一块品红矩形（约 260×300 像素，压在画面左上角外框上）—— 与引子整屏品红同源（该区域无绘制覆盖，品红是宿主清屏色），下一步用 `passdump` 对同一帧取 pass 列表定位是哪一级没画。
- **回归**：`scripts/stability.sh 3 13` = 3 轮 0 故障 `STABLE`。

## 2026-10-21 — P8：把捕获窗口按**顶点流所在的映射池**截断（不是按声明大小），并连跑 5 轮 0 故障

- **第一版截断是错的（已改）**：用 `S.vbuf[st].size / stride` 取最小 → c62 报 `跨度截断 620 → 顶点流覆盖 4 槽`，把正常绘制也砍到 4 个槽（声明大小可能只有一两个元素，而数据其实躺在同一大池里）。
- **改法**：截断边界取**映射池**的剩余字节：对每条绑定的顶点流 `mtl_gpu_addr(S.vbuf[st].address,…)` 拿 `(mb.length − mo) / stride`，取最小；捕获窗口 `capn_verts = min(max(index)+1+bv, 池覆盖)`，内核把 `i >= q.capn` 的索引丢进 `越界` 计数。声明大小不再参与。
- **实测**：c65（同脚本、开到帧 6000，越过装载瞬间）→ **0 次页故障、截断 0 次**（池边界远大于跨度，说明日常绘制的跨度本来就在池内）；c62（旧版按声明大小截断）也是 0 故障、帧 3300，但那版把几何砍掉了，不可用。
- **当前连跑记录**：c57(0)/c59(0)/c60(0)/c62(0)/c65(0) 五轮 0 故障，而更早同代码的 c56 是 8 次、c37/c38/c43 各 8 次 —— 归因尚不确定（可能是 base vertex 修正 + 消费端守卫一起把故障窗口缩小了，也可能只是运气），所以下面这条仍是**待验**而不是结论。
- **下一步**：拿到飞行场景的实拍（`wshot` 在帧 >2500），确认"大跨度/大池"那几笔绘制现在是被压缩而不是被丢弃；若世界模型仍缺，就用 `NIER_GS_MAXCAP` 做 A/B 看缺的是不是正好那 96～102 笔。

## 2026-10-21 — P8 定位：页故障来自"大索引跨度的捕获"（`NIER_GS_MAXCAP` 3/3 轮 0 故障）

- **开关**：`NIER_GS_MAXCAP=<槽数>`，在 `capn_verts` 算完后比较 —— 装载期那几笔只用几百个三角形、却命名到 57344/61440/65536 号顶点的绘制正是"大跨度捕获"。
- **实测（每轮 235 s，同脚本同存档，都跑到帧 ≥2700，也就是都越过了帧 ~1936 的装载瞬间）**：
  - `NIER_GS_MAXCAP=8192`：c57 **0 故障**（跳过 96 笔，帧 5700）、c59 **0 故障**（跳过 102 笔，帧 6000）、c60 **0 故障**（跳过 25 笔，帧 2700）。
  - 对照（不开该开关、同脚本）：c37/c38/c43/c56 各 **8 次**故障，c36/c40/c41 各 0 次 → 基准故障率约 50%，**3 次连续 0 的概率约 12.5%**。
- **含义**：故障锁定在"捕获绘制本身，且只在索引跨度巨大的那几笔"。跨度大时 `capn = round_up(max(index)+1, 4096)`（65536 槽 × 64 B = 4 MB 捕获缓冲），捕获顶点着色器要访问到 65535 号顶点的属性 —— 这些顶点在游戏自己的绘制里从不被同一 base/instance 组合访问，裸地址绑定下越界即页故障。
- **下一步（可证伪）**：不用跳过，而是**把大跨度捕获降到顶点流真正覆盖的范围**：捕获前用 `S.vbuf[st].size`（顶点流字节数 / stride）算出 `max_valid_vertex`，`capn_verts = min(max(index)+1+bv, max_valid+1)`，内核里 `i >= q.capn` 的索引直接丢（已有 `越界` 计数可看丢了多少），这样大跨度绘制也能被压缩而不是被跳过。
- **回归**：`scripts/stability.sh 3 13` = 3 轮 0 故障 `STABLE`。

## 2026-10-21 — P8 续：base vertex 折进索引地址是错的（已改），但**不是**残余页故障的来源；消费端垃圾计数也不是

- **改动一（正确性）**：捕获带索引的绘制时把 `base_vertex` 折进索引地址（`gpu_idx += bv*isz`）是错的 —— base vertex 是"每个索引值加 bv"，不是"索引源整体挪位"。后果：读的是**别的索引条目**，而顶点取数用的是 `index` 而不是 `index+bv`，地址比游戏自己的低 `bv*stride`（顶点流从池底开始时就是池外）。现在改成游戏语义：索引地址不挪、`idx_len = 池剩余`、捕获绘制传 `baseVertex: bv`、捕获槽数 `capn_verts = max(index)+1+bv`、内核 gain `q.bv`（`struct Q` 加第 6 个 uint，`g_parb` 32 B 槽仍够）。新增 `[gs] 捕获：base vertex %lu 的绘制…` 计数行。
- **改动二（安全网）**：`draw_resolve` 里如果指向 GS 计数的间接记录值 `> 64M`，直接不画并打 `【红】GS 消费端：计数记录不可信 %u`（原来是让 GPU 读 `lst + 8.5 GB`；c37 实测过 2144566592）。
- **实测（决定性）**：c56 单跑 225 s（跑到帧 2100、捕获缓冲涨到 57344/61440 顶点）→ **8 次页故障，而 `base vertex` 捕获 0 笔**；`计数记录不可信` 也 0 次。→ 两个假设**都被这一轮证伪**（改动保留：一个修正确性，一个防野读）。
- **顺带确认的可靠性事实**：`NIER_GS_MAXTRIS` 从未触发（大缓冲来自大索引跨度）；`MTL_DEBUG_LAYER=1 MTL_SHADER_VALIDATION=1` 不报任何越界（c17，但那轮没到场景）；跑批脚本**互相踩**（两个 run 同时活着会抢单实例锁，第二个实例每轮只跑到帧 600；父脚本被杀后它的 `sh logs/cNN.sh` 子进程仍会在结尾 `pkill` 掉下一次跑批）——现在起跑前必须 `pgrep -fl "nier-native|logs/c"` 确认为空。
- **回归**：`scripts/stability.sh 3 13` = 3 轮 0 故障 `STABLE`。
- **下一步（可证伪）**：加 `NIER_GS_MAXCAP=<槽数>`，只跳过"索引跨度 > N"的捕获（装载期正是 57344/61440/65536 槽），2 臂 × 3 轮：若 8 次 → 0 次，则故障锁定在大跨度捕获；否则转查同一提交里的其它 37 笔绘制。

## 2026-10-21 — P8 取证：页故障是**间歇**的（每轮 0～8 次），上"出错即打印地址"的常驻台账

- **先说结论**：索引捕获（`276ced5`）之后，"开压缩就 1 分钟撞预算"不再复现，但同一份代码、同一段导航，页故障的**次数在 0～8 之间乱跳**：c36 = 0、c37 = 8、c38 = 8、c39 = 1、c40 = 0、c41 = 0（c40/c41 各 12289 笔压缩），故障帧都在 ~1936 的装载瞬间。→ **单轮二分不作数**（前面 `NOKERN`/`NOIND` 各只跑一轮，结论只能当线索），必须有"出错即打印现场"的仪器。
- **一次被证伪的二分**：新增 `NIER_GS_MAXTRIS=<三角形>`（捕获绘制三角形数超过上限就跳过压缩），c38 对照 8 次故障、c39（上限 8192）1 次，但 `捕获跳过` **一条都没有** → 开关从未生效。原因：捕获缓冲大小由 `capn = max(index)+1` 决定，装载期那笔 300 三角形的绘制要 65536 个槽（`捕获缓冲 65536 顶点（4096 KB）`）→ "大缓冲 = 大索引跨度"，不是"大绘制"。该开关保留（默认关），但它不是定位工具。
- **常驻台账**（`nvn_mtl.m`，默认常开但只记指针，64 条上限）：每次提交重置 `g_gs_bindn`，几何阶段把实际绑给 GPU 的条目记进 `g_gs_bind[]` —— 1 索引缓冲 `(guest, gpu, len)`、2 捕获缓冲 `(0, gpu, capn*64)`、3 计数 `(guest, gpu, 4)`、4 列表 `(guest, gpu, ntri*12+16)`、5 消费端间接记录 `(ind.args, ind.gpu, ind.len)`。`mtl_sub_split` 的反馈错误分支调 `gs_bind_dump("分段")`（最多 3 次），逐条用 `mtl_gpu_addr` 回查 guest 地址是否**仍在映射池里**，打 `池在 / 池已不在 / 无 guest 地址`。这样下次故障能直接看到"哪条绑定的底池在执行时已经不在"。
- **实测（台账版）**：c40、c41 均 0 故障（c40 跑到 `window closed`，`捕获绘制：非索引 0 次 / 索引 12289 次`），台账未被触发 → 暂无"池已不在"的证据，仪器留着等下一次。
- **另一条画像**：c37 的 `计数记录可疑 2144566592（列表池余量 4720070 条目）` 说明计数记录有时读到**垃圾/复用值**；我们只报红、不写回（c35 曾因 host 写回 guest 记录崩在 `main+0x9bbebc`，已撤销）。消费端 `draw_indexed_indirect` 的 `indexCount` 由 GPU 读该记录，若真是垃圾字，最坏是让它读 `lst + 8.5 GB` —— 下一条要验的就是"CPU 侧看到可疑计数时，这一笔改用 guest 自己的 indexCount 而不是 GPU 计数"。
- **回归**：`scripts/stability.sh 3 13` = 3 轮 0 故障 `STABLE`。

## 2026-10-21 — P8 根因（一半）：几何阶段把**带索引**的绘制当非索引捕获 → GPU 页故障 → 每帧 +19 MB 未登记分配

- **故障链**：`NIER_GS_COMPACT=1` 进入飞行场景装载（帧 ~1936）时一次 `kIOGPUCommandBufferCallbackErrorPageFault`，随后同队列所有提交变成 `SubmissionsIgnored`，从那帧起 `device.currentAllocatedSize` 每帧稳定 +19～20 MB，1500 帧后撞预算（`FATAL 提交驻留集：显卡已分配 8415 MB（零拷贝 guest 池 1913 MB，实际 6502 MB）超过预算 6500 MB`）。
- **逐帧探针**（新增 `mtl_alloc_probe`，`NIER_ALLOC_TRACE=1`，300 行封顶）：把 `显卡已分配` 拆成 `零拷贝池 + 登记 + 未登记`（未登记 = 显卡 − 池 − (登记 − 在集池)，在集池取 `mtl_pool_res_stats()[3]`，否则重叠会被算成负数）。故障前 2371 MB；故障后零拷贝池 1913 MB 与登记 1747 MB 全程 **+0**，增长全在未登记段（命令缓冲/编码器/表等无 `res_add` 的对象）→ 2.4 GB 是**故障的下游症状**，不是压缩本身在分配。
- **二分**（同脚本同存档，预算 6500 MB）：`NIER_GS_NOKERN`（跳过压缩内核）8 次故障、`NIER_GS_NOIND`（不消费压缩结果）8 次 → 故障在捕获绘制本身；`NIER_GS_DROPIDX`（整笔丢弃带索引的压缩绘制）**0 次**故障、且一条"捕获绘制"日志都没有 → 被压缩的绘制 **100% 带索引**（`捕获绘制：非索引 0 次 / 索引 8193 次`）。
- **根因**：捕获 pass 用 `[[vertex_id]]` 当槽号写 `totk_cap`，却把带索引的绘制按 `drawPrimitives:0:vcount` 发 → 顶点阶段取 0…vcount−1 号顶点（游戏从未索引到的那些），越过顶点缓冲读取。本移植所有绑定都是裸 GPU 地址（Metal 无从边界检查），装载大网格时踩到池外页面。
- **改法**（`276ced5`）：带索引的绘制用同一份索引缓冲捕获（base vertex 折进索引地址，槽号即索引值），压缩内核按索引 gather（`itype 0/1/2`），捕获管线按"顶点程序 + pass 附件格式/样本数"缓存（原来全局单例，跨 pass 复用会 `Fragment input user(locn2) mismatching`），捕获缓冲按 `max(index)+1` 上取整分配。
- **实测**：`DROPIDX` 帧 2887 显卡 2304 MB；索引捕获 + 压缩被复验 bug 全放弃时帧 2749 显卡 2333 MB（池 1913 + 登记 1769 + 未登记 84）；压缩真跑（c27）帧 3051 显卡 2339 MB、未登记 84 MB 平、`[gs-rep] 保留顶点=192…5826`、0 次故障。**"开压缩就 1 分钟撞预算"已消失，未登记段不再逐帧涨。**
- **残留（仍未解释，但已排除一个假设）**：同一份代码里，压缩内核真跑时页故障**间歇**出现（c21/c26/c30/c31 = 8 次；c24/c25/c27/c29 = 0 次），故障点都是帧 ~1936、fence 1937-1939。`[gs-rep] 计数=3424665216（窗口 717）`、`计数=0` 说明计数记录有时是垃圾/复用值 → 曾假设"野计数让 `lst[4*i]` 写到池外"，于是给内核加 CAS 上界（按列表所在 guest 池的剩余条目 `lst_cap`）、派发前把不可能的计数复位 0、并校验索引缓冲长度。**实测该假设被证伪**：加界后 c30/c31 仍是 8 次故障（c29 0 次）。三个守卫保留为安全网（垃圾计数复位 8～13 次/轮，说明垃圾值确实存在），但故障另有来源，下一步在捕获绘制与内核之间按 fence 定位。
- **一次自伤**：为了压住"野计数"，曾把可疑的计数记录在 host 侧写回 0；c35 因此崩在 `main+0x9bbebc`（`signal=0xb`、`fault=0xccfd28`，被写坏的指针）。**该写回已撤销**（guest 记录不归 host 写），只保留"内核按池余量限写 + 只报红不写回"和"索引缓冲长度不足就跳过捕获"两条守卫（都会打红行）。回滞后 `scripts/stability.sh 3 13` = 3 轮 0 故障 `STABLE`。
- **工具**：`NIER_ALLOC_TRACE=1`（逐帧三段分解）、`NIER_GS_NOKERN/NOIND/DROPIDX`、`[gs] 捕获绘制`、`[gs] 捕获管线`、`[pool] 保留已销毁的池`（`NIER_POOL_NODEL=1`），全部默认关、有上限。另记一条环境坑：`shot` 在帧 ~2200 之后不再执行（多条只产出第一条），改用 `wshot`；两个跑批脚本不能同时活着（单实例锁 + 收尾 `pkill` 会互杀）。

## 2026-10-21 — P8 续：`parb` 改持久环（驻留对象 4215 → 207），但场景装载那一刻的 2.3 GB 突发仍在

- **改法**：`gs_compact_flush()` 里每次压缩 `newBufferWithLength:16` + `res_add` + `defer_obj_unref` 的 `parb`，换成一个开跑时分配的 4096 槽（64 KB）持久环，CPU 每次重写槽内容、`setAddress:…+poff`，不再逐次分配/登记。GPU 只读到编码该帧的提交完成，2–3 帧在飞远小于 4096 槽的余量。
- **实测（`NIER_GS_COMPACT=1`，同一段手工导航到飞行场景）**：驻留集对象 80/158/170 个（帧 300/600/1800；改前同点是 80/722/4215），说明逐次 `parb` 登记确实消失；`[gs-rep] 保留顶点=72 有坐标三角形=760/230`、`[gs] 间接绘制：计数由 GPU 读（已压 4801 次）` 与改前一致 → 压缩语义没变。
- **但显存没解决**：帧 1800 是 2294 MB（改前 3673 MB），随后到帧 2100 一次跳到 4649 MB，在 5015 MB 撞 `gpu_budget_check`。跳变伴随 `内存池 26 个`（多出第 26 个池）与 `贴图重传累计 2551 → 2845 次`。结论：**`parb` 不是主因**（记一次被证伪的假设），主因是场景完整装载时的贴图重传/池分配突发，下一步按 `贴图重传` 与"第 26 个池是谁建的"继续。
- **未验证**：压缩开着时飞行单元模型是否回来（本次两次采样都落在引子白屏/装载阶段，第 3 张截图前进程已 abort）。这是 P8 剩下的核心问题。

## 2026-10-21 — P5 根因：guest 从不请求音频输出（HLE 无罪）

- 反汇编 + 日志对照（详见 `docs/audio-root-cause.md`）：`IAudioDevice` 的 HLE 形状全对（cmd 5/10/12 都被接受，轮询每帧在跑）；本作**不导入** in-process renderer（无 `[audren] cmd 0`，抄 nxnative 的 DeviceSink 是死代码）；`main` 里 `OpenDefaultAudioOut` 唯一调用点 `main+0xd8c628` 只能在从未被调用的 vtable 槽里到达，`audout:u` 从未打开 → `runtime/audio_public.c` 从未进入。
- 顺带发现两处真实 host 缺陷：`AudioOutParameter` 被当 u32 读（通道数读成 `0xCAFE0000`），以及缺 `OpenAudioOut` 具名重载覆盖。
- 下一步是行为中性的取证（无条件入口日志、case 5/10 栈、首次通道数变化探针、`main+0xd8c548` 观测计数），先证明"guest 到底会不会请求"。

## 2026-10-21 — P8 现场诊断：飞行场景的世界几何没有编码，几何阶段压缩开着会把显存顶到预算

- **做了什么**：用 `TOTK_INPUT_FILE` 手工驱动（标题 `A` → Load Game → 选 `11111` 槽 → `F` 左移到「是」→ `A`；再 `A` 开始游戏）进到 `11111` 存档的飞行场景，抓现场证据而不猜：
  - `flag draw_log N` + `passdump N`：一帧的 pass 清单（`[uw]`/`[pass]`）；
  - `NIER_GS_COMPACT=1` 复跑同一段路，观察 GC 与显存。
- **观察 1（世界几何不在编码里）**：飞行场景一帧（F4565）编码进 `[uw]` 的 pass 只有：阴影图（`2048x2048`，两个 `1024x1024` 视口，`fs 49bd7a78`，数千顶点 ×2）、后处理链（`1920x1080`，`fs 216aa095/fe25a5ab/869fc247/b41adaf5`）、泛光金字塔（`800x450→400x225→200x112→100x56→50x28`）、文字/HUD（`fs 9e275b94/9a2be07b/c0ed2509/38576bae`）。**没有一笔把上千顶点画进 1920x1080 的世界几何 pass**。同一时刻 `[nvn] 回放丢弃的绘制 … shader-missing=18125`（每 300 帧窗口，≈60 笔/帧）——这些正是被 `draw_inner()` 里 `pr->missing & (1<<2)`（几何阶段缺失）丢掉的绘制。画面（`logs/L7.png`）与此一致：只有 IFF 铭牌（2B/7E/12H/1D/4B/11B）、绿色引擎光条与字幕，飞行单元模型本身没有。
- **观察 2（压缩开着会顶爆显存）**：`NIER_GS_COMPACT=1` 同一段路，帧 300/600/1800 的 `显卡已分配` 是 2194/2274/3673 MB、驻留集对象 80/722/4215 个，随后 `present_now` 的 `gpu_budget_check` 在 5016 MB > 5000 MB 处 `FATAL 提交命令缓冲`（≈帧 1868，进游戏后约 1 分钟；同帧窗口里还有 `MTL4CommandQueueErrorDomain Code=5 … kIOGPUCommandBufferCallbackErrorSubmissionsIgnored`，即先前已积累 GPU 错误）。同场景**不开压缩**只有 2180 MB / 72 个对象。退出转储（`内存 至帧 1800`）显示注册对象按登记行分布：`行 664` 15 个 1431 MB（贴图池）、`行 3341` 116 个 62 MB、**`行 6347` 4558 个 0 MB**——`行 6347` 就是 `gs_compact_flush()` 里每次压缩新建的 16 B 参数缓冲 `parb`（`res_add(parb)` + `defer_obj_unref(parb)`）。
- **结论**：几何阶段的处理仍是一个**二选一都不可接受**的状态——丢掉这 60 笔/帧 → 世界模型不见（观察 1）；开 `NIER_GS_COMPACT=1` → 显存 1 分钟内撞预算（观察 2）。这两条必须同时解决才能谈 gameplay 画面。
- **下一步（按证据排序）**：1) 把 `parb` 改成开跑时预分配的环形缓冲（如 256 × 16 B，`res_add` 一次），确认 `行 6347` 的驻留数与 `显卡已分配` 斜率随之消失或下降；2) 若显存仍涨，给 `gs_compact_flush()` 前后加 `device.currentAllocatedSize` 差分打印，按"每次压缩新增多少字节"定位是缓冲 vs 参数表 vs 编码器；3) 显存稳住后再验证观察 1 的画面（飞行单元模型是否回来、`[ind]` 零值率），并逐帧对比开关压缩的画面差异。
- **顺带修正旧结论**：`docs/status-1.0.3.md:121` 把"`1920x1080` 目标 + `960x540` 视口 / `2048x2048` + `1024x1024`"解释为"场景按半分辨率画进四分之一目标导致接缝"。本期实测：`2048x2048` + 两个 `1024x1024` 视口是同一张图里左右两半各画一次，顶点数上千、`blend 0`、`fs 49bd7a78`（标题界面也画），更像**阴影图集**，不是场景色；`800x450→…→50x28` 是泛光金字塔。旧解释不再作为排查方向。

## 2026-10-21 — 长会话 abort：CubeArray 768 立方体超过 Metal 上限（341）

- **现象**：`NIER_build/logs/nier.log`（14:03，帧 13257，进度在飞行场景之后的战斗段）进程被 Metal 断言杀掉：`MTLTextureDescriptor arrayLength (768) is set to zero or greater than the max allowed size (341)`；栈 `pkg_upload ← tex_mtl ← tex_by_id ← prepare_draw_textures ← draw_inner`，即"游戏第一次把某张贴图绑到绘制上"时建纹理失败，不是 guest fault。
- **实测限制**：新增 `scripts/diag/metal_tex_limits_probe.m`（个案 fork 子进程，避免断言吞输出；`MTLDevice` 没有 `maxTextureArrayLayers` 属性可读）在 M5 上量到：1D/2D/2DMS 阵列最多 2048 slice；**CubeArray 的 `arrayLength` 按立方体数计、共用 2048 slice 预算 → 最多 341 立方体（2046 面），342 与 768 都被断言杀掉**。三个参考工程（nier / nxnative / d2r-mobile）都没有这条上限保护，nxnative 只有 `depth>2048` 的静默丢纹理。
- **根因**：`tex_desc()` 把 NVN `CUBEMAP_ARRAY` 的 `depth` 直接当立方体数写进 `arrayLength`。游戏声明 `16x16x768 级 5 格式 0x29`（= 4608 面 / 18 MB），Metal 一张纹理放不下。
- **改法**：新增 `tex_desc_fixup()`（宽高 1..16384、3D 深度 1..2048、级数按尺寸上限且 ≤15、`arrayLength` ≤2048、**cube array ≤2048/6=341**、MSAA 样本数规范化，超限打一行 `[tex]` 日志，前 6 次 + 每 500 次）；新增 `mt_slices()`，`pkg_upload_` 的逐 slice 拷贝与 GPU 写回都按已建纹理的实际 slice 数截断（4608→2046）。guest 内存里的布局（`nvn_tex_layout`）不变，只是拷贝/写回不越界。
- **为什么不猜语义**：NVN 的 cube-array `depth` 到底是立方体数还是面数没有文档可证；但无论哪种解释，截断到 2046 面后"index < 2046 的 slice 内容"都与游戏着色器的索引一致（face 顺序本来就是 cube-major），丢掉的只是 Metal 无论如何都放不下的层，因此不需要先证明语义。
- **验收**：`[tex] 按 Metal 上限修正 ser=1619 声明 16x16x768 级 5 阵列 768 → 实用 16x16x1 级 5 阵列 341`、`[tex] 拷贝按 Metal 上限截断 ser=1619 声明 4608 层 实用 2046 层`、`[tex-probe] … Metal 建成 16x16x1 类型 6 / slice 2046`，进程不再 abort（`TOTK_TEX_UP_PROBE=1` 在创建时就验证描述符，不必推进到那个场景）。
- **未验证**：该场景真正绑定时（及 341 层之后若被读写时的 Metal clamp 行为）的画面；另外 `mtl_read_texels()` 的 GPU 内容回读路径未加同样的截断（那张贴图目前不回读）。

## 2026-10-21 — P2 收尾：几何阶段压缩走 GPU 间接绘制

- **捕获改直写缓冲**：`gs_capture_pso()` 早前把 locn2/3/4 写进 varying（`[[user(locn32..35)]]`），而捕获管线 `rasterizationEnabled=NO`，顶点输出整体丢弃 → 捕获缓冲恒为 0 → 内核全丢、计数恒 0。改成 `vertex void main0(device float4 *totk_cap [[buffer(29)]], …)` 直接写缓冲：槽 29 避开程序自用（实测最大 0）、顶点流 16+n、零缓冲 30；Metal 每级缓冲槽上限 0..30，31 会报 `'buffer' attribute parameter is out of bounds`。
- **消费端改 GPU 间接绘制**：`nvn_render_stream.h` 增 `RS_DRAW_INDIRECT`/`RS_INDEXED_INDIRECT`；`nvn_mtl.m` 增 `enc_r_draw_indirect`/`enc_r_draw_indexed_indirect`；`draw_resolve()` 命中 GS 计数器时先编码压缩内核（`gs_compact_flush()`）再用间接变体绘制，计数由 GPU 读，不再需要 CPU 同步（`NIER_GS_SYNC` 仍保留为显式诊断，会 abort）。
- 诊断：`NIER_GS_REPORT=1` 在提交完成回调里读内核统计缓冲（`保留顶点/有坐标三角形`），区分「捕获为空」与「判定全丢」。
- 实测：`[gs-rep] 保留顶点=72 有坐标三角形=760/300`、`[gs] 间接绘制：计数由 GPU 读（已压 2401 次）`、`NIER_GS_COMPACT=1` 55s 0 fault、默认路径 14s×2 0 fault。
- 仍未达成：全场景 `[ind]` 零值率与序章场景验收需要进 gameplay（`TOTK_INPUT_SCRIPT` 盲按键仍不稳定）。
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

## 2026-10-22 偏色定位（修正版）：绿色不在世界几何 shader，在天空/环境 pass

- **c182 判决**：skipfs 世界几何 shader `7c7dec85`（40 笔）后，飞行场景**基调仍是绿**（78,182,78 → 78,185,78），只是颜色数从 1473 降到 418。→ `7c7dec85` 画的是有细节的几何（机体/弹幕，1055 色），**不是那片平铺绿底**。绿底另有来源。
- **撤回上一条的"cb10[0]=纯绿"**：复核 `7c7dec85` 的 MSL，它只读 `fp_c13` 的元素 [1][4][5][6][7][14][15]，**从不读 [0]**——"cb10[0]=(0,1,0,1)"是无关字节，误标为绿色来源，作废。教训：uniform 块里"看到纯绿"不等于"shader 用它"，必须回到读取点。
- **绿底来源收窄**：ser=16 的 `clear=0,0,0,1`（黑）→ 天空 pass 342815（load，shader `17654724`，采样 cube 探针 ser=3236-3270）先写入 → 世界几何叠加。skip `7c7dec85` 后绿底仍在 ⇒ 绿来自 **342815 天空 pass** 或更早。c183 正在 skip `66ba9531`/`ac309751` 二分，下一步 skip `17654724`。
- **探针嫌疑**（与 P7 直接相关）：`17654724` 声明 `texturecube_array`（id 30/32）——正是 P7 把 768 立方体截断到 341 的光照探针 atlas。海面反射色来自探针，探针 slice 错位/缺失可造成环境光偏色。飞行场景日志里**没有**"按 Metal 上限修正"行（c165/c176 grep=0），说明该场景未触发截断——需确认探针到底建了几层、天空 pass 读到的是不是有效数据。
