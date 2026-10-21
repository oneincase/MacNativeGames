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
