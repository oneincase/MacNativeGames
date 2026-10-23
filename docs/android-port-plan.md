# 《王国之泪》安卓版移植适配方案

> 基于 MacNativeGames 既有 Mac / iPhone / iPad 原生移植(TOTK 1.0.0 / 1.1.2 / 1.2.1 / 1.4.3 已在 Apple 平台可玩),规划同一移植体系向 Android 的扩展。
> 本方案只做规划,不包含游戏内容;游戏包、密钥仍由用户自备,仅限自用。

## 0. 结论与路线

沿用项目既定路线:**非模拟器,原游戏 ARM64 机器码在目标芯片直接执行**,离线改写指令(平台寄存器 x18 / 线程指针 / `svc` / 计数寄存器),C 编写的 host 回答 Horizon 系统服务,NVN 命令录制后回放到目标图形 API。

| 维度 | Apple 平台现状 | Android 方案 | 差异级别 |
| --- | --- | --- | --- |
| CPU | ARM64 直接执行 | ARM64 直接执行,复用现有改写器 | 低 |
| 图形 API | NVN → Metal 4 | NVN → **Vulkan 1.3**(Android 12+) | **高(全新后端)** |
| Shader | Ryujinx→SPIR-V→SPIRV-Cross→MSL | Ryujinx→**SPIR-V 直接喂 Vulkan**(省 SPIRV-Cross) | 中(删一步、加补丁) |
| 纹理 | Apple GPU 支持 BC 解压 | **BC→ASTC 离线转码**(仓库已有 bc1astc/bctc 探针) | 高 |
| OS ABI | macOS / iOS dyld,Apple 布局 | Android linker(`android_dlopen_ext`),自定义布局 | 中 |
| 启动器 | AppKit / UIKit | Kotlin + Jetpack Compose(NDK 原生入口) | 中 |
| 音频 | Core Audio | **AAudio**(低延迟)/ Oboe | 低 |
| 输入 | GameController / 键鼠 | android_gamepad API + 触屏虚拟手柄 + 外设 | 低 |
| 构建 | Python + Xcode | **Gradle + NDK r28+**,Clang | 中 |

**首版目标:** 1.0.0 → 标题界面 → 开局场景 → 可操作 → 可保存 → 退出重进读档;真机验证设备建议 Snapdragon 8 Gen 2/3 + 16 GB 内存。

**版本策略:** 与仓库一致,不做版本推测;新增 `games/totk/assets/expected-v*.json` 对应校验,改写产物与 build ID 绑定。1.2.1 / 1.4.3 在安卓跑通 1.0.0 后按 Apple 侧相同流程扩展。

---

## 1. 前置输入矩阵

| 项 | 要求 |
| --- | --- |
| 游戏 | TOTK 1.0.0 基础(XCI/NSP),升级可选;密钥 `prod.keys` |
| Title ID | `0100F2C0115B6000`,按文件身份识别,不按文件名 |
| 改写输入 | `main.npdm` ACID 签名校验 + ExeFS 三模块(main / sdk / subsdk0) |
| 构建机 | macOS 或 Linux;NDK r28+(支持 16 KB 页),Python 3.14,Gradle 8.x |
| 目标设备 | Android 12 (API 31) 以上;**arm64-v8a only**(x86_64/32 位明确拒绝);建议 Android 14+ |
| 最低内存 | 16 GB 真机(游戏 guest 常驻约 3–4 GB,另需系统余量) |
| 图形驱动 | Vulkan 1.3 + `VK_KHR_depth_stencil_resolve` + 时间戳 query;Adreno 740+/Mali-G715+ 首批 |

---

## 2. 模块级适配方案

### 2.1 离线改写(nxpatch / patch_nso.py)

现有改写目标是 macOS dyld + Apple ABI。安卓改写目标不同,**复用同一改写器、新增安卓规则集**:

| 现有改写项 | Apple 处理 | Android 处理 |
| --- | --- | --- |
| x18 平台寄存器 | 改为跳板到 host 桩 | **Android x18 对用户空间无保留义务**,但仍改写,防止未来内核策略变化,并统一走 host 桩便于诊断 |
| 线程指针(TPIDR) | 改写到 host TLS | 同样改写;安卓 `gettid`/TLS 由 host 桩回答,保持 SDK 布局 |
| `svc` | 跳板到 host HLE | 同样跳板;新增 Android 版 HLE 桩地址表(与 Apple 侧同一套 svc 语义,不同跳板目标) |
| 计数寄存器 | 改写为 host 时间源 | 同样改写为 host 时间源 |
| 新增:Android 特有 | — | `arm64-v8a` 的 `shadow call stack`(仅当启用 PAC/BTI 时需禁用或适配);`SVE` 指令集不依赖 |
| 新增:页面映射 | — | 改写产物 ELF `PT_LOAD` 对齐从 16 KB(4K 不行);**`max-page-size=16384` 链接参数必须设置** |

**验证:** 沿用 Apple 侧"逐改写点在模拟器中对拍随机输入 + 负控"方法;安卓复用同一套测试用例,仅替换跳板目标。

### 2.2 装载与布局(runtime/loader.c → loader_android.c)

这是安卓侧**最大的新工程**之一,难点在地址布局与内存上限:

- **Loader 机制:** 用 `android_dlopen_ext` 加载改写后的三模块是不现实的(安卓 linker 不支持任意地址 + 相互符号解析 + 自定义 relro)。改用 **`memfd_create` + 手写映射**:复用 Apple 侧"按原始布局映射模块、互相解析导入、从 `nn::init::Start` 启动"的逻辑,改为 `mmap(MAP_ANONYMOUS|MAP_FIXED)` 到预留地址;`.text` 映射为 RX,`.data` 为 RW,RELR/GNU hash 重定位逻辑照搬现有实现。
- **布局:** guest 内存必须 < 2^39(IPC 缓冲 39 位编码)。安卓用户空间可分配虚拟地址远大于此,但**进程 RSS 受 LMK/ARC 限制**。策略:与 iOS 侧相同,栈 + 堆 + alias 区在连续空洞中并排;alias 区从 64 GiB 起步,按 4 GiB 步长收缩到 8 GiB(游戏实际用不到 3 GiB)。用 `/proc/self/maps` 打印 `guest layout` 日志行。
- **内存上报与保命:** 注册 `ActivityManager.onTrimMemory`,在 `TRIM_RUNNING_MODERATE` 时收缩 host 缓存(shader 缓存 LRU、音频队列、RomFS 读缓冲);`TRIM_COMPLETE` 时向 guest 发停顿信号。**目标是把 guest 常驻压到 3 GB 以下**,给系统留 4 GB 余量,避免 Android 14+ 的 16 GB 设备上被 LMK 杀(参考 iOS 侧 iPhone 14 内存瓶颈教训)。
- **16 KB 页对齐:** NDK r28 起默认支持,但自写 loader 需要按 16 KB 对齐 mmap;`getpagesize()` 读取运行时页大小,不假设 4 KB。
- **线程:** 复用 `thread_enter.S`(AArch64 入口跳板);用 `pthread_create` + `clone` 语义对齐 Horizon 线程优先级(`SCHED_FIFO` 需要 `android.permission.WAKE_LOCK` 之外还需 `THREAD_PRIORITY_*` 调用,或通过 `sched_setscheduler` 降级处理)。

### 2.3 Horizon 服务层(runtime/*.c)

**绝大部分代码直接复用**(C 语言,无平台依赖):`fs_host.c`、`settings_service.c`、`time_host.c`、`game_slot.c`、`ipc.h`、`hle_stack.c`、`romfs.c`、`aoc_romfs.c`、`shared_font.c`、`game_patches*.c`、`audio_effects.c`、`audio_reverb.c`、`audio_mixer.h`、`movie_host.h`、`controller_map.h`、`gamepad_layout.h`。

需要替换的平台绑定层:

| 原文件 | Apple 实现 | Android 实现 |
| --- | --- | --- |
| `frontend.m` | AppKit 窗口 | 删除;入口改为 JNI `nativeMain()`,由 Kotlin Activity 承载 Surface |
| `gamepad.m` / `gamepad_feedback.m` | GameController framework | `android/input.h`(AInputQueue)+ `AInputEvent_getSource`,手柄震动用 `InputManager.vibrate`(API 31)或 `InputDevice#supportsRumble`(需 JNI 侧 Kotlin 辅助);体感用 `SensorManager` 类型 `TYPE_GAME_ROTATION_VECTOR` |
| `audio_renderer.c` | Core Audio | **AAudio** `AAudioStreamBuilder` 低延迟输出;采样率 48000,`AAUDIO_PERFORMANCE_MODE_LOW_LATENCY`;缓冲区策略先 `setBufferSizeInFrames(256)` 探底 |
| `movie_host.c` + libvpx | libvpx VP9 | libvpx **交叉编译 arm64-v8a**,或切换到 Android `MediaCodec`(首版用 libvpx 保持一致,后续评估 MediaCodec) |
| `swkbd_host.c` | Mac 键盘 | Android 软键盘:JNI 回调 Kotlin 弹出 `TextField`,或首版用固定字符串桩 + 明确失败信息 |
| `input.c` | 键鼠 + 手柄 | Android 输入事件流;**触屏虚拟手柄**(SwiftUI/Compose 布局迁移为 Compose,沿用 Switch 手柄布局设计) |

### 2.4 图形后端(NVN → Vulkan)—— 全新子项目

这是**本方案工作量最大、风险最高**的部分。现有 `d2r_gfx/gfx/nvn` 是 Metal 4 实现,需要在同一 NVN 层下新增 Vulkan 后端:

**分层原则:** NVN API 层(游戏调用的接口)保持不变,底下抽象出 `gfx_backend` 接口,Metal 和 Vulkan 各自实现。抽象面包括:
- 资源管理:内存池(从 guest 内存映射来的 `VkBuffer`/`VkImage`,带脏页跟踪)、交换链、描述符;
- 命令回放:`VkCommandBuffer` 对应现有 MTLCommandBuffer;render pass / 深度附件 / EQUAL 深度测试等逐条映射;
- 提交与完成信号:`VkFence`/`VkSemaphore` 对应 present 与完成回调;
- Pipeline 缓存:`VkPipelineCache` 对应现有 MSL 预编译产物。

**关键难点与对策:**

| 难点 | 对策 |
| --- | --- |
| **16 KB/4 KB 页面差异** | Android 物理页 4 KB(或 16 KB),Apple GPU 16 KB。NVN GOB 布局逻辑保持不变,但 `VKMemoryAllocate` 的对齐要求用 `vkGetBufferMemoryRequirements` 查询,不硬编码 |
| **内存别名(仓库已修的 `MTL4VisibilityOptionResourceAlias`)** | Vulkan 天然用 `vkBindBufferMemory2`/`VK_KHR_bind_memory2` 绑定同一物理内存到多 VkBuffer;**barrier 语义由 Vulkan 明确,不需要 Apple 的 alias 修复**;但需要验证 Vulkan 的 `VK_ACCESS_MEMORY_READ/WRITE` 与现有 copy barrier 语义一致 |
| **深度 D16 classification clear(仓库已修的 0.5 精度问题)** | Vulkan 深度语义与 Metal 不同:用 `VkClearDepthStencilValue` 时需同样选择"截断与四舍五入两 bin 都不落"的四分位余量;`probe-depth16-clear` 探针改写为 Vulkan 版本并跑同一组 777 值 fixture |
| **顶点未写输出默认 (0,0,0,1)(仓库 patch 0005)** | 这是 shader 翻译器层补丁,在安卓复用同一 totk_xlate 产物;若改为直接 SPIR-V 路径,需验证 SPIRV-Cross 的清零行为是否被绕过 |
| **MetalFX 上采样/插值** | Android 无对应;**首版砍掉**,提供降级路径:原生分辨率 + Vulkan 的 `VK_KHR_present_wait` 控制呈现间隔。后续可引入 AMD FSR 2 或开源的 temporal upscaler |
| **纹理 BC 压缩** | Adreno/Mali 均不支持 BC 格式采样;**离线转码为 ASTC**(仓库已有 bc1astc/bctc 探针,说明转换路径已原型验证)。转码在构建期做,产物进 app(或者首版用 `VK_EXT_host_query_reset` + CPU 解压到 RGBA8,但内存爆炸风险高,不推荐)。ASTC 压缩比与 BC 接近,RomFS 里 BC 纹理总量约 10 GB,ASTC 6x6 转码后约 12–14 GB,需评估 APK/存储 |
| **呈现与 Surface** | `VK_KHR_android_surface`,Surface 由 Kotlin `SurfaceView` 提供;Swapchain 用 `FIFO_RELAXED`(与 60 Hz 匹配)或 `MAILBOX`(高刷设备) |
| **Shader 特性** | SPIR-V 直接喂 Vulkan,省 SPIRV-Cross;但需要检查 Ryujinx 翻译器输出的 SPIR-V 是否符合 Vulkan 1.3 规范(如 GL_ARB_separate_shader_objects 风格 layout、`gl_FragDepth` 语义);有不符合处需在 totk_xlate 加 Android 后端补丁 |

**渲染分辨率:** 首版 720p(1280×720)固定,与 Switch 掌机一致;后续通过 `TOTK_RENDER_RES` 等环境变量对应 Android 的 SharedPreferences 可调。

### 2.5 Shader 翻译器(totk_xlate)

现状:.NET NativeAOT 编译,`totk_xlate --service` 以子进程方式供 host 调用(Ryujinx→SPIR-V→SPIRV-Cross→MSL)。

**Android 路径:** NativeAOT 本身支持 android-arm64 目标(`dotnet publish -r linux-bionic-arm64`),但**首版推荐把翻译器并入 host 进程**,原因:
- 安卓上 fork 子进程成本高(zygote 关系);进程间通信需要 Binder/socket;
- totk_xlate 的 .NET 运行时镜像在 Android 上仍需用 bionic libc,NativeAOT 支持,但体积大(~30 MB)且冷启动慢;
- 备选:把翻译器代码编译为静态库(`libtotk_xlate.so`)直接 dlopen 到 host 进程,通过 C ABI 调用,而非子进程。

**缓存:** 现有 shader 缓存按 build ID + 翻译链版本失效;Android 侧缓存目录用 `context.getExternalFilesDir(null)/shader-cache`,与 iOS 侧同样的"设备不能运行时翻译"约束 → **构建期必须把全部预编译 SPIR-V 进 APK**(BNSH 解析 → SPIR-V 编译产物);播放中遇到缺失 variant 时的策略与 iOS 相同(跳过绘制 + 记录到 shader-list-contribution)。

**APK 体积控制:** 预编译 SPIR-V 可能数百 MB,超出 APK 上限;改用 **Play Asset Delivery(不适用于 sideload)** → 用**自定义 OBB/拆分包**,或构建期产出 split APK(`install --split`)。首版直接输出一个 `.apk` + 外部数据目录(构建脚本把 shader 数据放到 `Android/data/<pkg>/files/`),避免体积问题。

### 2.6 启动器与 UI(Kotlin)

- **入口:** `MainActivity`(Compose)→ 显示版本/存储权限请求/选包界面 → `System.loadLibrary("totk_host")` → `nativeMain()`。
- **Surface:** `SurfaceView` + `setFixedSize(1280, 720)`;渲染分辨率切换通过重建 swapchain 实现。
- **触控布局:** Compose 实现左右摇杆 + ABXY + 肩键/扳机 + 十字键;多点触控用 `PointerInputScope` 收集并发送到原生层。**布局 JSON 化**,让用户可调。
- **运行时权限:** 存储权限仅用于游戏包选择(READ_MEDIA_VIDEO 不适用;用 SAF `OpenDocument` 选 XCI/NSP,无需权限)。
- **悬浮 HUD:** 现有 Mac 的 FPS/缓存角落框,Android 用 Compose overlay 实现,不需要额外权限。
- **后台/前台切换:** `onPause` → host 暂停 guest(音频停、交换链持),`onResume` → 恢复;音频焦点管理用 `AudioManager.requestAudioFocus`。
- **崩溃与诊断:** 原生 crash 用 `libunwind` + tombstone;日志统一到 `logcat`,按 tag `TOTK_HOST` 过滤;首启布局、改写、缓存过程日志与 Apple 侧语义对齐,便于诊断。

### 2.7 构建管线(build_app.py → 新增 --platform android)

```
python3 build_app.py <totk.xci|nsp> -k prod.keys --platform android [-o out.apk] [--work dir]
```

步骤(在现有 1–4 步之后扩展):
1. 复用:容器识别 → NCA/ExeFS 提取 → patch_nso.py 安卓规则集改写 → RomFS 解密与 IVFC 校验 → 字体处理 → mods 处理 → 纹理 BC→ASTC 转码(新增)。
2. **NDK 编译:** `cmake -B build-android -G Ninja -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-31 -DANDROID_PAGE_SIZE_16KB=ON` 构建 `libtotk_host.so`(runtime/*.c + loader_android.c + gfx_vulkan/*.c + 桥接 jni.c);**关键 LDFLAGS: `-Wl,-z,max-page-size=16384`**。
3. **totk_xlate:** NativeAOT android-arm64 或静态库方式产出,SHA-256 校验(对应现有 prebuilt 校验逻辑)。
4. **shader seed:** `prepare_shader_seed.py` 安卓分支:产出 SPIR-V(不是 MSL)+ 依赖索引,进 APK assets 或外部数据目录。
5. **Gradle 打包:** `games/totk/android/` 工程(新),`xcodegen` 换成 Gradle;签名用 `apksigner`(构建产出未签名 APK,由用户自行签名安装,或提供 `--keystore` 选项用 debug key 签名方便自用)。
6. **校验:** APK 内 `assets/expected-v*.json` 校验记录与 Apple 侧一致。

**首版 CI/测试:** 无实机时至少跑:(a) `unittest discover` 全部容器测试(Linux/Android 路径无游戏数据用例);(b) 交叉编译通过 + `llvm-readelf -l` 确认 16 KB 对齐;(c) Vulkan 后端单元测试(需要 VK 运行时,Linux CI 用 lavapipe/lavapipe software renderer + SwiftShader 对拍)。**实机验收路径:** 启动 → 标题 → 开局场景 → 操作 → 保存 → 退出重进读档,每步截图 + logcat 留证。

---

## 3. 风险与依赖

| 风险 | 等级 | 说明 | 缓解 |
| --- | --- | --- | --- |
| Vulkan 后端完整性 | **高** | NVN→Metal 是多年打磨;Vulkan 重写工作量大 | 分阶段:先跑通 `gfx_probe` 级别(清屏 + 单三角形),再逐个 NVN 子系统迁移;Metal 实现作为语义参考,不并行开发 |
| 内存上限 | 高 | 安卓厂商激进 LMK;16 GB 设备可用 RSS 也可能 < 8 GB | `onTrimMemory` 分级收缩;alias 区缩到 8 GiB;shader 缓存上限 + LRU;实测真机走查 |
| 驱动碎片化 | 高 | Adreno/Mali/PowerVR/Xclipse 行为差异大;Vulkan 驱动 bug 多 | 首批白名单设备(SD 8G2/8G3/Tensor G4,均 Vulkan 1.3);构建期 `vkGetPhysicalDeviceProperties` 白名单匹配,不匹配给明确提示;bug 列表持续积累 |
| BC→ASTC 体积 | 中 | 转码后数据量增大、构建时间变长 | 转码放独立缓存目录,增量构建;若 APK 超 4 GB 走外部数据目录方案 |
| NativeAOT on Android | 中 | totk_xlate 在 Android 上的可行性未验证 | 首选静态库方案(dlopen),若仍受阻,回退到"构建机翻译,产物打包"模式,运行时不翻译 |
| MediaCodec vs libvpx | 低 | libvpx 交叉编译成熟;MediaCodec 有格式兼容风险 | 首版 libvpx,问题再多也不急于换 |
| 页面对齐/Linker | 低 | NDK r28 已规范 16 KB 支持 | 构建脚本检查 `llvm-readelf -l` 输出 |
| 厂商定制行为 | 低 | 部分厂商劫持 Surface/分配内存 | 白名单设备先行,再扩展 |

**明确非目标(首版):** MetalFX / 2K / 超宽 UI;DLC;BotW 安卓版;x86_64/32 位;Google Play 上架(含 shader-list 贡献机制在 Android 侧的适配问题另行讨论)。

---

## 4. 分阶段里程碑

### M1 · 构建管线与装载打通(预计 3–5 周)
- [ ] `--platform android` 构建入口:容器识别 / 提取 / 改写(安卓规则集)/ RomFS / 字体 全流程在 Linux/ macOS 构建机跑通
- [ ] `libtotk_host.so` 交叉编译通过,16 KB 页对齐验证
- [ ] 最小 APK:Kotlin 启动器 + Surface 创建 + `loader_android.c` 映射改写模块 + 跳到 `nn::init::Start`(此时无图形,预期卡在第一次 NVN 调用)
- 验收:logcat 能看到 guest 布局日志行 + 首个 svc 调用命中 host 桩

### M2 · 服务层与音频跑通(预计 3–4 周)
- [ ] 复用 fs/settings/time/game_slot/ipc/hle_stack/romfs 等,Android 输入/AAudio 输出替换平台绑定
- [ ] `nn::init::Start` 前的初始化 svc 全部回答,不返回伪成功
- 验收:guest 线程进入游戏主循环前不因服务缺失而失败(诊断日志与 Apple 侧同结构)

### M3 · Vulkan 图形纵向贯通(预计 6–10 周,关键路径)
- [ ] gfx_backend 抽象层 + Vulkan 最小实现(设备/swapchain/命令池/内存池)
- [ ] NVN API 层最小子集映射(清屏/单 draw/纹理绑定),`gfx_probe` 级别画面
- [ ] totk_xlate Android 产物(SPIR-V 直接路径),pipeline 缓存预热
- 验收:标题界面出现(即使卡顿/缺元素);截图留证

### M4 · 标题到开局(预计 4–6 周)
- [ ] NVN 子系统逐个补齐(深度/混合/多点渲染/计算 shader);BC→ASTC 纹理进画面
- [ ] 触屏虚拟手柄 + 外接手柄输入;AAudio 音频
- [ ] 保存/读档走通
- 验收:开局场景可操作、可存、可读;新进程读档成功

### M5 · 稳定性与多版本(持续)
- [ ] 1.2.1 / 1.4.3 安卓版支持(与 Apple 侧相同流程)
- [ ] 内存压缩策略(`onTrimMemory`)与真机长会话走查
- [ ] Vulkan 驱动差异修复;shader 缺失贡献回传机制适配
- [ ] 性能:分辨率选项、高刷设备 present 模式、CPU 线程优先级调优

---

## 5. 立即可做清单(下一步)

1. 在 `build_app.py` 增加 `--platform android` 分支与 APK 输出骨架(不实现,先跑通到"NDK 编译前"的现有步骤)。
2. 新建 `games/totk/runtime/loader_android.c` 与 `games/totk/runtime/jni_bridge.c` 空实现 + 头文件接口,与 `loader.c` 对照设计。
3. 新建 `games/totk/android/` Gradle 工程骨架(MainActivity + Surface + Compose 空手柄布局),能安装到真机并显示"数据未就绪"界面。
4. `games/totk/third_party/d2r_gfx/gfx/` 下梳理 NVN 接口清单,输出"NVN API 调用面 + Vulkan 对应表"文档,作为 M3 的输入。
5. `games/totk/tests/bc1astc/probe.m`、`bctc/probe.m` 的转换逻辑抽成可独立运行的 Python/原生工具,离线批量转码测试用例跑通。

## 6. 许可与合规

- 游戏内容、密钥不进仓库,与 Apple 侧一致;构建产物仅自用,不分发。
- `THIRD_PARTY_NOTICES.md` 需补:AIEEE/vulkan headers、libvpx(交叉编译)、AAudio 属系统库、Compose/Gradle 构建工具(不随产物分发可忽略)、libunwind 等。
- shader-list 贡献机制在 Android 侧需重新设计(无 Mac 常驻连接时的上报路径),涉及用户数据上传需单独讨论,不在首版范围。
