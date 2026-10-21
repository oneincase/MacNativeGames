# 音频为什么没声：根因报告（1.0.3 / SDK 12.3.9）

只读分析（反汇编 `rewritten/v103/main.patched.bin`、`sdk.patched.bin` + 对照 `NIER_build/logs/aud.log`）。结论与旧的"IAudioDevice HLE 初始化卡住"假设相反。

## 三条纠正

1. **`IAudioDevice` 的 HLE 没有失败。** guest 的音频设备对象构造成功，它的监视器已武装，并且每帧都在轮询我们给的事件（`aud.log:158 [audiodev] device event signalled once`）。"让 IAudioDevice 初始化成功"这条待办本来就已经成立。
2. **本作不使用 in-process renderer。** `main` 只导入 18 个 `nn::audio` 符号（公开 `AudioOut`、重采样器、设备查询）。渲染器 API 在 `sdk` 里有定义（`sdk+0x4ce40 / 0x4cd20 / 0x4d560 / 0x4d980`），但 `main`/`subsdk0` 都没有导入行，`[audren] cmd 0`（OpenAudioRenderer）从未出现 → 抄 nxnative 的 CPU+手动执行 + DeviceSink 没有挂点，会是死代码。
3. **guest 没有卡住。** 每帧轮询照跑，pulse 事件每次都送到它自己的引擎队列。

## 真正的根因

guest 里没有任何东西**请求输出**。`nn::audio::OpenDefaultAudioOut` 在 `main` 里只有**一个**调用点：`main+0xd8c628`（在 `main+0xd8c548` 里），而 `main+0xd8c548` 只能通过设备管理器 vtable（`0x12b4428` 槽 `+0xf8` = `0x12b4520`）进入，这个槽从未被调用：`main+0xd8c548` 零 BL 调用者、全 `main` 文本里扫描 116 处 `ldr xN,[xM,#0xf8]; blr xN` 无一处指向它、也没有它的 8 字节数据引用。于是 `audout:u` 从未被打开，`runtime/audio_public.c` 的覆盖函数从未进入（约 120 份日志里没有一行 `[audio]`）。**阻塞点在 guest 自己的声音引擎（Wwise 风格）里，而不在 IPC HLE。**

## 已重建的时序（供后续验证）

- `main+0xd692b4` → `main+0xd8ab8c` 音频设备构造函数（0x110 B）：`obj+0x108 = GetActiveAudioDeviceChannelCountForOutput()`（cmd 5）、`obj+8 = GetActiveAudioDeviceName()`（cmd 10，`b0=obj+8+0x100` 是 0x100 字节名字缓冲）、再 `AcquireAudioDeviceNotificationForOutput()`（cmd 12）。成功路径要求 `w0==0` 才置 `obj+0x10c=1`；轮询在没有 `0x10c` 时立即返回，而轮询在跑 → cmd 12 的"事件句柄放进 `rs->copy[]`"形状是对的。
- 每次查询都会重造 manager proxy：`sdk+0x54450/0x54170/0x542e0` 都走 `sdk+0x544d0 GetAudioDeviceService()` → manager vtable[0x40] → `[audren] cmd 4` → 新 `IAudioDevice` 会话。所以每条命令前都有一行新的 `opened service audren:u`（不是重试）。
- `sdk+0x397600` 是 CMIF 客户端的 `svc #0x21`（SendSyncRequest）跳板，不是音频逻辑（`sdk.gates.tsv` 行 `0xdb6c80 svc 33 0x397600`）；真正的打开点是 `sdk+0x4fe30 _ZN2nn5audio32CreateAudioRendererManagerByHipcEv`（`"audren:u"` 字符串唯一 xref）。
- 稳态：`main+0xcdc7e8` 主循环 → `main+0xd8ad0c` 轮询 → `if (n != obj+0x108) { ... bl 0xd21914; }` → vtable 链 → `main+0xcd4610` 往引擎队列投事件 id `0x2f`。`NIER_AUDIO_DEVICE_PULSE=1` 时就是每秒 10 次这个循环（这不是 bug，是 HLE 让事件反复触发）。

## 两处真实的 host 缺陷（`runtime/audio_public.c`）

1. **`AudioOutParameter` 解析错。** SDK 12.3.9 里 `sdk+0x3bb90 InitializeAudioOutParameter` 写 `{u32 rate@0=0; u16 channelCount@4=0; u16 magic 0xCAFE@6}`；游戏用 `strh w8,[x29,#0x1c]` 写通道数。现码把 `p[0]/p[1]` 当 u32 读 → 通道数读成 `0xCAFE0000`，静默回退到 2。应读 `*(const u16*)((const char*)param + 4)`。
2. **缺 `OpenAudioOut` 的具名重载覆盖**（`_ZN2nn5audio12OpenAudioOutEPNS0_8AudioOutEPNS_2os11SystemEventEPKcRKNS0_17AudioOutParameterE`，`sdk+0x555d0`）。不加它，guest 会静默落回 SDK 自带的 `audout:u` 客户端。

## 建议的最小步骤（行为中性，先取证）

1. `nier_audio_open_default` 入口无条件打一行（不受 `NIER_TRACE_AUDIO` 限制），并把 param 的 +0/+4/+6 字节按 u16 打出来。
2. `audio_device_handler` 的 case 5/10 也像 case 12 那样 dump guest 栈。
3. case 5 加一个"首次查询返回不同通道数"的开关（如 `NIER_AUDIO_DEVICE_COUNT_FIRST=6`，之后回到 2）：guest 唯一的状态分支是 `if (n != obj+0x108) bl 0xd21914`，这条分支至今从未走过；若 sink 是它创建的，这一步就能起音。
4. 用现成的 `runtime_observer` / `PROBE_PLT` 机制给 `main+0xd8c548`、`main+0xd8c628` 加计数：一整套流程下来仍为 0，就证明"guest 从不请求"，那么只有第 3 条还有戏。

## 一旦 guest 开始请求（P1 常量，已核对）

- `AudioOut`：rate@0、channelCount@4、sampleFormat@8、state@0xc。`AudioOutParameter` 8 字节。
- `AudioOutBuffer`（0x28）：next@0、sample_buffer@8、capacity@0x10、data_size@0x18、data_offset@0x20。
- sdk 入口：OpenDefaultAudioOut `0x55240`、OpenAudioOut `0x555d0`、Start `0x55810`、Stop `0x558a0`、Close `0x556f0`、GetState `0x55920`、Append `0x55a30`、GetReleased `0x55b20`、PlayedSampleCount `0x55e60`。
- 验收日志次序：`[audio] OpenDefaultAudioOut parameter <rate> Hz ch=<n>` → `[audio] AudioOut open 48000 Hz x2 PCM16 -> Core Audio (stereo)` → `[audio] AudioOut start` → `[audio] AudioOut appended 1 buffers …`。若出现 `opened service audout:u`，说明覆盖被绕过。`[totk-audio] output level:` 不会出现（AudioOut 不走 mixer）。
