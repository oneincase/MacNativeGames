# scenecap：可搬运的场景包工具链

scenecap 把「一帧画面的完整渲染场景」从游戏里抽出来，写成一个自包含、版本化、带哈希校验的
`.nvs` 包；再由一个不依赖游戏本体的独立 Metal 重放器离线重建、逐 pass 出图；最后用对比工具做
像素级回归。它服务于两类验证：

- **渲染验证**：改了 NVN→Metal 的翻译、着色器转换或某个 pipeline 状态后，画面是否仍然一致。
- **移植验证**：同一个包在另一台机器、另一套 Metal 驱动上重放，输出是否仍然一致；包本身不引用
  游戏文件，因此可以随 bug 报告、CI 产物一起搬运。

首期用合成场景自举（`scenecap synth`，完全不依赖游戏数据），并以 `nier` 作为第一个真实目标游戏；
`totk`、`odyssey`、`by3` 已按同样的钩子接入。

> 字节级格式、C/Python API 与逐字段契约见 [`tools/scenecap/README.md`](../tools/scenecap/README.md)。
> 本文只讲怎么用。

---

## 1. 快速闭环（合成场景）

四个命令即可在本地走完 `synth → verify → replay → diff`，不需要游戏、不需要 Linux 构建环境，
只需要 macOS（Metal）与可用的 Python：

```sh
# 1. 合成一个包：64x64 bgra8unorm_srgb 附件 + 4x4 纹理 + 单三角形
tools/scenecap/scenecap synth /tmp/demo.nvs --raw

# 2. 校验容器、哈希与 manifest 交叉引用
tools/scenecap/scenecap verify /tmp/demo.nvs

# 3. 重放两次（重放器需要先构建，见第 4 节）
tools/scenecap/scenecap replay /tmp/demo.nvs -o /tmp/a
tools/scenecap/scenecap replay /tmp/demo.nvs -o /tmp/b

# 4. 像素级对比，写 Markdown 报告
tools/scenecap/scenecap diff /tmp/a /tmp/b -o /tmp/report.md
```

实测输出（macOS，Apple Silicon）：

```
$ tools/scenecap/scenecap synth /tmp/demo.nvs --raw
wrote /tmp/demo.nvs (6 chunk(s), raw storage)
$ tools/scenecap/scenecap verify /tmp/demo.nvs
PASS /tmp/demo.nvs
  capture: scenecap-synth 0000000000000000 1.0.0 frame 0+1 (synthetic, macOS synthetic, Metal synthetic)
  container: 6 chunk(s), manifest 3600 bytes
$ tools/scenecap/scenecap replay /tmp/demo.nvs -o /tmp/a
replayed 1 pass(es) from /tmp/demo.nvs into /tmp/a
$ tools/scenecap/scenecap diff /tmp/a /tmp/b -o /tmp/report.md
✅ 通过：1 个 pass 全部满足阈值（每通道 ≤2/255（8-bit 量化单位），差异像素比例 ≤0.1%）
   报告：/tmp/report.md
```

`diff` 的退出码：`0` 通过、`1` 回归、`2` 输入或用法错误，可直接用于 CI。

不带任何参数运行 `tools/scenecap/scenecap` 会打印用法（退出码 `0`）：

```
usage: scenecap [-h] [--version]
                {verify,info,cat,manifest,synth,replay,diff} ...
```

---

## 2. 包格式摘要（`.nvs`）

一个 `.nvs` 文件是「固定头 + 块目录 + JSON 清单 + 对齐的数据块」：

| 区段 | 内容 |
| --- | --- |
| 文件头（128 B） | 魔数 `SCENECAP`、`major.minor`（当前 `1.0`）、flags、chunk 数量、块表/manifest/文件大小偏移、manifest 的 SHA-256 |
| 块目录（每项 128 B） | 块 id、kind、flags、文件偏移、存储字节数、原始字节数、SHA-256 |
| JSON 信封 | 压缩后的 manifest（场景清单）以及完整块目录的副本 |
| 数据块 | 8 字节对齐的 MSL 源码、反射 JSON、纹理像素、buffer 字节 |

块 kind 有 `json`、`msl`、`reflection`、`texture`、`buffer`、`binary`。压缩按块自适应：能压小就用
Zstandard 帧（Python 写端用标准库 `compression.zstd`；C 写端内置 raw/RLE 编码器，也可接 libzstd /
Apple `libcompression`），否则原样存储，保证「读不出来」永远不会被误当成「没压缩」。

manifest 是包的核心，主要小节：

| 小节 | 含义 |
| --- | --- |
| `capture` | 游戏名、title id、游戏/移植版本、git commit、GPU/macOS/Metal、捕获帧窗口 |
| `frames` | 帧序号与时间戳（多帧时每个被捕获帧一条） |
| `passes` | pass 结构：`render`/`compute`/`blit`、尺寸、像素格式、附件（颜色 slot 0–7、深度 slot 8，含 load/store/clear）、引用的 pipeline 与 draw |
| `pipelines` | `key`（捕获侧缓存键）、各 stage 的 `msl_blob`/`reflection_blob`、完整的 `state`（混合、深度/模板、图元、光栅化等） |
| `draws` | `vertex{start,count}`、`index{type,start,count,buffer}`、`instances`、`primitive_type`、`argument_table`、draw 时刻的 `state` |
| `dispatches` | compute 的 kernel、`grid`、绑定 |
| `resources` | 纹理/buffer 元信息与快照范围（每张纹理只快照一个 mip level 与一个 array layer） |

校验分三层，全部通过才算 `PASS`：

1. 容器层：魔数/版本门、头与块表偏移、长度重叠、重复 id、未知 flags；
2. 哈希层：整包 SHA-256、manifest SHA-256、每个块的 SHA-256；
3. 语义层：manifest 内部引用（pass↔draw↔pipeline↔resource↔chunk）与必填字段，错误会定位到
   具体 chunk id 或 manifest 路径。

版本策略：`major` 变化表示旧读端会读错；`minor` 只加可选字段/新块 kind，读端拒绝比自己新的
`minor`，避免静默丢数据。

---

## 3. 在游戏里捕获

### 3.1 一次性接入

捕获分三层，游戏侧只需要两个文件里的少量调用点：

- `tools/scenecap/capture/scenecap_capture.{h,c}`：纯 C11 记录内核（不依赖 Metal / Objective-C）；
- `tools/scenecap/capture/scenecap_nvn.{h,inc}`：NVN→Metal 层的桥接，读回纹理、物化 guest buffer、
  从着色器缓存取 MSL 与反射；
- `tools/scenecap/sync.py`：把上述文件（连同 `format/nvs_format.{h,c}`，共 6 个）同步进每个游戏的
  `third_party/d2r_gfx/gfx/nvn/`，供游戏自己的 `$(wildcard …nvn/*.c)` 构建编译。

`nier`、`totk`、`odyssey`、`by3` 已完成接入；`botw` 目前没有 `third_party/d2r_gfx` 树，同步时会跳过
并明确报告（见第 7 节）。

接入 `games/<game>/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` 的五个位置：

```c
/* 1. 文件头附近：钩子声明（同目录副本，由 tools/scenecap/sync.py 从源码树同步进来） */
#include "scenecap_nvn.h"

/* 2. mtl_set_frame()：告诉记录器当前帧号 */
scenecap_nvn_frame(f);

/* 3. 渲染 pass 边界 */
scenecap_nvn_pass_begin((__bridge void *)pd, (uint32_t)g_pass.tw, (uint32_t)g_pass.th);
scenecap_nvn_pass_end();          /* 放在 rp_end 中、enc_r_end 之前 */

/* 4. draw_inner()：状态与绑定都已最终确定、真正提交之前 */
scenecap_nvn_draw(dw, di);

/* 5. dispatch()：compute 提交处 */
scenecap_nvn_dispatch(dp);

/* 文件末尾：引入桥接实现；SCENECAP_NVN_TOTK 选择纹理句柄查找方式 */
#define SCENECAP_NVN_TOTK 0                     /* nier=0；totk/odyssey/by3=1 */
#define SCENECAP_NVN_DEFAULT_GAME "nier"        /* 可选，缺省按上面的宏取 nier/totk */
#include "scenecap_nvn.inc"
```

再给 `games/<game>/runtime/input.c` 的调试命令行加一个分支（`nier`、`totk`、`odyssey`、`by3` 已加）：

```c
else if (!strncmp(line, "scenecap", 9) && (line[9] == 0 || line[9] == ' ' || line[9] == '\n' || line[9] == '\r')) {
    extern void dm_gfx_scenecap_next(const char *path);
    line[strcspn(line, "\r\n")] = 0;
    dm_gfx_scenecap_next(line[9] ? line + 10 : NULL);
}
```

新游戏接入就是：建好 `third_party/d2r_gfx` → `tools/scenecap/sync.py` → 按上面改 `nvn_mtl.m` 与
`input.c` → 构建。`sync.py` 只写 scenecap 自有文件，绝不覆盖游戏既有文件。

### 3.2 环境变量

| 变量 | 作用 |
| --- | --- |
| `SCENECAP=1` | 总开关；未设置或为 `0` 时所有钩子立即返回，不读回、不写包、不发 Metal 命令 |
| `SCENECAP_PKG` | 输出路径；`%llu` 或 `#` 会被替换成起始帧号（默认 `scenecap-%llu.nvs`） |
| `SCENECAP_FRAMES` | `<count>` 或 `<first>:<count>`，默认 `1` |
| `SCENECAP_MAX_BYTES` | 资源快照总预算，默认 512 MiB |
| `SCENECAP_COMPRESS=1` | 允许写端压缩块；默认原样存储 |
| `SCENECAP_GAME`、`SCENECAP_TITLE_ID`、`SCENECAP_GAME_VERSION`、`SCENECAP_GIT_COMMIT`、`SCENECAP_PORT_VERSION`、`SCENECAP_BUILD_VERSION` | 元信息覆盖；GPU、macOS、Metal 版本自动查询 |

单张纹理/单个 buffer/单个着色器的内部上限分别为 64 MiB、16 MiB、8 MiB（编译期常量）。

### 3.3 操作步骤（以 nier 为例）

```sh
# 1. 构建并启动（以 nier 为例；make boot 会先构建 runtime，再从工作目录启动）
#    WORK 必须指向已解出游戏数据的构建目录：默认是相对 games/<game> 的 NIER_build，
#    如果那里没有 runtime_tables.h 和 rewritten/，用绝对路径显式指定（下面就是本机的实际位置）。
#    GNU make 会把命令行变量带进 recipe 的环境，所以这几个变量能直接传给游戏进程。
make -C games/nier boot \
  WORK=/Volumes/bit2t/switch/totk/NIER_build \
  SCENECAP=1 \
  SCENECAP_PKG=/tmp/nier-scene.nvs \
  SCENECAP_FRAMES=1
# 等同：在环境里带上这些变量运行 $WORK/build/nier-native rewritten/v103 boot

# 2. 进入想验证的画面（标题、某个房间、某个特效），打开调试输入，输入：
scenecap /tmp/nier-scene.nvs

# 3. 捕获从下一帧边界开始；录满 SCENECAP_FRAMES 帧后自动写包并打印落盘路径
#    然后退出游戏，或直接对包做校验
tools/scenecap/scenecap verify /tmp/nier-scene.nvs
```

其他游戏把 `games/nier` 换成对应目录（`games/totk`、`games/odyssey`、`games/by3`），启动方式各自
`Makefile` 的 `boot` 目标为准。

说明：

- `scenecap <path>` 只是「武装」记录器，真正的窗口从下一帧开始；参数省略时用 `SCENECAP_PKG`。
- 窗口结束（`frame >= first + count`）时自动 `finish` 写包，无需再输入命令；进程退出时也会尝试落盘。
- 捕获线程是回放层的决策线程；编码线程不调用钩子。
- 生成的 cache 目录（`<pkg>.scenecap-cache/`）属于重放器，可随时删除。

### 3.4 窗口内快捷键（F9）

不想开终端、也不想接输入脚本时，直接在游戏窗口里按 **F9**：下一帧边界起写出一个包，路径形如
`/tmp/scenecap-20261024-183500.nvs`（时间戳命名）。

- 不需要设任何环境变量；`SCENECAP_DIR` 可换目录（例如 `SCENECAP_DIR=$HOME/scenecap`）。
- 与 `scenecap <path>` 命令走同一条路径（`dm_gfx_scenecap_next`），只是路径自动生成。
- 窗口右上角的快捷键说明会多列两行：`F9  抓帧（生成 scenecap 场景包）` 与 `F10 录制（默认 60 帧）`。
- 落盘后照常 `scenecap verify` / `scenecap replay`。

### 3.5 场景录制（F10 / 多帧）

单帧（F9）之外，可以录一段连续帧，用来验证动画、粒子、相机运动和逐帧稳定性。

- 游戏窗口里按 **F10**：从下一帧边界起录 **60 帧**（约 1 秒 @60fps），同样写到 `SCENECAP_DIR`，时间戳命名。
  `SCENECAP_RECORD_FRAMES=120` 改默认帧数。
- 输入文件里也可以带帧数：`scenecap /tmp/clip.nvs 120`（不写数字 = 1 帧，等同 F9）。
- 包内 `capture.frame_count` 是这段的帧数，`frames[]` 列出每帧的 pass；`scenecap info` 会显示 `frame 2249+10`。
- 重放时逐帧导出 PNG（文件名 `pass_<序号>_<slug>.png`），`index.json` 每个 pass 带 `frame` 字段，
  `scenecap diff` 按 pass 顺序逐帧对比，可直接做动画回归基线。
- 录制窗口内每帧的 pass/draw 全部保留，所以包比单帧大得多（10 帧 nier 实测约 240 MB）；按帧数预算磁盘。

### 3.6 记录什么、丢弃什么

每个被保留的帧记录：全部 render pass（附件 level/layer、load/store、clear 值、尺寸与像素格式）、
每个保留 draw（pipeline、顶点/索引范围、实例数、图元、draw 时刻状态、完整 argument table）、每个
compute dispatch（kernel、grid、绑定）。资源每包快照一次：附件与绑定引用到的纹理（各取一个 mip level
与一个 array layer）、argument table 与索引数据用到的 buffer；MSL 与反射来自着色器缓存。

以下情况会**丢弃记录**并在 manifest 的 `warnings` 中计数（`dropped_draws`/`dropped_resources`）：

- 拿不到 MSL 源码的 draw；
- 一张纹理都不可用、也没有任何 kept draw/dispatch 的 pass；
- 超出单纹理或总预算的纹理（引用它的绑定被省略，包不会引用不存在的块）。

MSAA 目标暂不快照（`texture_read` 直接拒绝），因此这类 pass 会丢掉附件、只剩 draw；重放时它会作为
`unsupported` 报出（退出码 `3`），而同包内的普通 render pass 仍然出图。捕获不修改游戏的渲染路径：
`SCENECAP` 未设置时，钩子只是一次函数调用。

---

## 4. 重放

### 4.1 构建

重放器只依赖 `.nvs`、Metal、Foundation 与 zlib，不链接游戏：

```sh
make tools/scenecap/replay/build/scenecap_replay
```

`WITH_LIBZSTD=0` 也能构建，但只能读未压缩或 raw/RLE 块；遇到真正的 zstd 帧会明确报
`the installed zstd decoder returned 0 of N bytes` 并以 `2` 退出，不会猜测。

### 4.2 运行与输出

```sh
tools/scenecap/scenecap replay scene.nvs -o out/
# 等价于：tools/scenecap/replay/build/scenecap_replay scene.nvs -o out/
```

产物：

- `out/index.json`（`schema: scenecap.replay/1`）：包信息、设备信息、逐 pass 的尺寸/像素格式/PNG/
  draw 数/状态/像素指标（`metrics.pixels/min/max/mean`）、`unsupported` 与 `errors`；
- `out/pass_NNN_<slug>.png`：每个 pass 一张，RGBA8、非预乘、左上原点、sRGB 语义。

退出码：`0` 全部重放成功；`1` 校验或渲染失败；`2` 用法错误或包不可读；`3` 有无法执行的 pass
（已执行的 pass 仍然出图）。`--validate-only` 只做包与 manifest 校验、不创建 Metal 设备；
`--no-cache` 禁用 pipeline 缓存；`--verbose` 打印细节。

### 4.3 重放约定

- **pipeline 状态**取自 `pipelines[].state`；`draws[].state` 只用于对比调试。
- **绑定映射**：`vertex_buffers[k]`→vertex slot k；`uniform_buffers[k]`→vertex+fragment slot k；
  `fragment_buffers[k]`→fragment slot k；`textures[k]`/`samplers[k]`→两个 stage 的 slot k。
- **入口点**：`stages[].entry` → 库里唯一的函数 → `vs_main`/`vertex_main`/`main_vs`/`main0`/`main`；
  找不到时列出库内全部函数。
- **索引 draw** 必须带 `index.buffer`；缺失时重放报错，绝不猜测。
- **缺反射**的 stage：容器层允许（`.nvs` 是通用归档），但重放无法在没有反射时映射 argument table，
  会在 `errors[]` 里逐条报「…has no reflection_blob; the argument table cannot be mapped without guessing」
  并以 `1` 退出，不崩溃、不猜。
- **Pixel format**：支持 19 种常用格式（rgba8/bgra8 的 unorm 与 srgb、rgba8snorm、r8/rg8、rgba16unorm、
  rgba16float、rgba32float、r32float、rg32float、rgb10a2/bgr10a2、depth16unorm、depth32float、
  depth24_stencil8、depth32float_stencil8、stencil8）。未知格式报 pass + 资源 + 已知格式表。
- **视图**：viewport/scissor 与 pass 求交；cull/winding/fill/depth-bias、primitive/index type/instances
  均按包还原；附件 load/clear/dontcare 按声明处理。

### 4.4 pipeline 缓存

编译好的 pipeline 缓存在包旁边：`<pkg-dir>/<stem>.scenecap-cache/{pipelines.metallib,cache.json}`。
包 SHA-256 或 Metal 设备不匹配时自动重建；缓存损坏只告警，不影响像素结果。

---

## 5. 对比（像素级回归）

```sh
# 两次实时重放互比
tools/scenecap/scenecap diff out-a out-b -o report.md

# 与已提交的基线比
tools/scenecap/scenecap diff --baseline baselines/nier-title out -o report.md

# 确认无回归后把当前结果写为新基线
tools/scenecap/scenecap diff --baseline baselines/nier-title out --update-baseline -o report.md
```

- 输入必须是含 `index.json` 与 `pass_NNN_<slug>.png` 的重放目录；按 index 配对，`status != ok` 或两侧
  `pixel_format` 不一致直接判失败。
- 阈值默认每通道 ≤2（8-bit 量化单位）、差异像素比例 ≤0.1%，可调：
  `--max-channel-delta N`、`--max-diff-pixel-ratio R`。
- 报告是 Markdown（含基线/候选的包 SHA-256、逐 pass 指标与结论），同时写同名 `.json`
  （`schema: scenecap.diff/1`）与差异图目录（`--diff-dir` 可改，`--no-diff-images` 关闭）。
- 退出码：`0` 通过（或基线写入成功）、`1` 回归、`2` 输入/用法错误。`--quiet` 只输出结论。

---

## 6. 支持范围与已知未支持

| 类别 | 现状 |
| --- | --- |
| render pass | 支持颜色附件 slot 0–7、深度附件 slot 8，load/clear/dontcare，viewport/scissor，cull/winding/fill/depth-bias，primitive/index/instances |
| 混合与深度 | 按 `pipeline.state` 重建；模板（stencil）字段虽被记录，重放尚未实现 |
| 像素格式 | 19 种（见 4.3）；MSAA、stencil-only、MTLHeap 未实现 |
| compute | 只记录不重放；重放报 `unsupported`（退出码 `3`），不影响同包内 render pass |
| blit / copy | 不记录、不重放 |
| 多帧 | 一个包可装多帧（`SCENECAP_FRAMES`），重放按 pass 顺序全部出图；但帧间呈现/同步时序不在包里，多帧目前只用于静态画面验证 |
| 输出附件 | 只导出颜色附件 slot 0 |
| 反射 | 只做校验与透传，不参与绑定决策；缺反射会被重放明确拒绝（见 4.3） |
| 索引格式 | 捕获端把 8-bit 索引放宽为 `uint16`（便于统一重放） |
| 间接绘制 | 不记录（当前捕获点只覆盖直接 draw/dispatch） |
| 裁剪 | 纹理快照每资源只含一个 mip level / array layer；单纹理 64 MiB、单 buffer 16 MiB、总预算 512 MiB，超限即丢弃 |
| 平台 | 捕获端在游戏内（macOS / Metal）；重放端要求本机有 Metal 设备 |

---

## 7. 多游戏接入与同步

`tools/scenecap/` 是唯一源码树；`sync.py` 负责把 6 个文件复制进各游戏树并校验一致性：

```sh
tools/scenecap/sync.py                     # 同步全部游戏
tools/scenecap/sync.py --check             # 只校验；不一致时列出文件并非零退出
tools/scenecap/sync.py --game nier         # 只处理 nier（可重复）
tools/scenecap/sync.py --list              # 列出规范文件与目标游戏树
tools/scenecap/sync.py --force             # 允许覆盖同名但不像 scenecap 的文件（慎用）
```

`--check` 的输出形如：

```
ok      games/by3
ok      games/nier
ok      games/odyssey
ok      games/totk
skipped games/botw (no third_party/d2r_gfx/gfx/nvn)
all 4 game tree(s) carry the same 6 scenecap file(s)
```

任何一份副本被改动，`--check` 会变成 `drift   games/<g>: 1 file(s) differ…` 并逐条列出相对路径，
退出码 `1`；`make check` 已把 `sync.py --check` 作为第一项，因此副本落后会直接让构建失败。

当前状态：

| 游戏 | 钩子 | 输入命令 | 编译验证 |
| --- | --- | --- | --- |
| `nier` | 已接（`SCENECAP_NVN_TOTK 0`） | `scenecap [<path>]` | `nvn_mtl.m` 通过 |
| `totk` | 已接（`SCENECAP_NVN_TOTK 1`） | `scenecap [<path>]` | `nvn_mtl.m` 通过 |
| `odyssey` | 已接（`1`） | `scenecap [<path>]` | `nvn_mtl.m` + 同步副本的 `.c` 通过 |
| `by3` | 已接（`1`） | `scenecap [<path>]` | `nvn_mtl.m` + 同步副本的 `.c` 通过 |
| `botw` | 未接：仓库里还没有 `third_party/d2r_gfx` 树 | — | — |

`SCENECAP_NVN_TOTK` 只决定「纹理句柄→元数据」的查找方式：`0` 用 nier 的全局 `g_treg` 表，`1` 用
totk/odyssey/by3 共同的 `bound_tex()` 注册表。接入新游戏时先看它的 `bound_tex` 实现，再决定宏值。

---

## 8. 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 重放 exit `2`，`the installed zstd decoder returned 0 of N bytes` | 重放器以 `WITH_LIBZSTD=0` 构建却遇到 zstd 帧；重建重放器，或用 `scenecap synth --raw` 生成未压缩包 |
| 重放 exit `1`，`… has no reflection_blob` | 捕获端没写到反射（着色器缓存缺失）；重新捕获该帧，或检查该 pipeline 的反射读取 |
| 重放 exit `1`，`index buffer … is missing` | 捕获端没写 `index.buffer`；这是包缺陷，重放不会猜 |
| 重放 exit `1`，`unknown pixel format "…"` | 该格式不在 19 种支持列表中；先转成 `rgba8unorm` 再捕获，或扩展重放格式表 |
| 重放 exit `1`，MSL 编译失败 | 报错含 pipeline key、stage、blob id 与 Metal 诊断，并列出库内函数；多为入口点或着色器转换问题 |
| 重放 exit `3` | 包里含 compute/blit 等无法执行的 pass；render pass 的 PNG 仍然可用 |
| `verify` 失败且报块哈希不符 | 包被截断或改动；用 `scenecap cat <pkg> <chunk>` 取出原块核对 |
| `scenecap replay` 提示 `the replayer is not built` | 先 `make tools/scenecap/replay/build/scenecap_replay`，或用 `SCENECAP_REPLAY` 指定二进制 |
| `sync.py --check` 报 drift | 有副本被改动；跑一次 `tools/scenecap/sync.py` 重新同步 |

---

## 8.1 真实游戏实测（nier，2026-10-24）

第一次打通「真实游戏 → 离线重放」的完整链路，记录当时的条件与结论，后续排查可对照。

**捕获**

```sh
WORK=/Volumes/bit2t/switch/totk/NIER_build
make -C games/nier runtime WORK=$WORK -j8          # 钩子是运行时开关，不需要编译期宏
caffeinate -d -u -t 600 &                          # 显示器休眠时 CVDisplayLink 初始化失败
cd $WORK && TOTK_INPUT_FILE=/tmp/sc-in.txt ./build/nier-native rewritten/v103 boot > /tmp/sc.log 2>&1 &
sleep 22 && printf 'scenecap /tmp/nier.nvs\n' >> /tmp/sc-in.txt
```

`nier-native` 必须在**窗口模式**下运行：`games/nier/runtime/loader.c` 的 headless 分支会直接报
`VI requires native macOS window`。

**结果（一帧，frame 615）**：37 个 pass、30 次 draw、89 个资源、包体 130,868,592 B；`verify` PASS（152 chunk）。

**重放**：31 个 pass 输出 PNG（1920×1080 与 960×540），6 个 `unsupported` —— 1 个 pass 没有颜色附件、
5 个是 compute（按契约明确列出，不静默跳过），`errors` 为空，退出码 `3`（部分成功）。

**这次暴露并修掉的捕获/重放缺陷**：

| 现象 | 根因 | 处理 |
| --- | --- | --- |
| `scenecap <path>` 报 `invalid diagnostic input command` | `games/*/runtime/input.c` 用 `strncmp(line, "scenecap", 9)`，命令词只有 8 字符 | 四个游戏统一改为 8 字符比较并跳过后续空格 |
| 命令到达后永不开始捕获 | `scenecap_frame_begin` 只认 `frame == first_frame` | 改为 `frame >= first_frame` 并把窗口对齐到到达帧 |
| 第二帧起钩子全部早退 | `scenecap_nvn_frame` 的守卫漏了「命令已请求」 | 守卫补 `!g_sc_cap` |
| `invalid blob id … (1..63 bytes)` | 真实 shader key 是 64 位十六进制，`msl/<key>/<stage>` 超长 | 改用 FNV-1a 摘要命名 blob，完整 key 保留在 manifest |
| `blob … was added twice` | 同一 shader 服务多个 pipeline | `add_blob` 按 id 去重 |
| 改 `scenecap_nvn.inc` 不生效 | 四个游戏 `GFX_HEADERS` 只通配 `*.h` | 补 `*.inc`；`tools/scenecap/sync.py` 负责把副本同步进游戏树 |

**当前边界（真实包会遇到，合成包不会）**：

- **单层快照**：捕获端每个纹理只取 `mip_range.first` / `layer_range.first` 那一层。cube 与带 mip 链的纹理
  因此短于逻辑布局（例如 64×64 cube 只存 1 面）。重放器对「短于布局」的快照打印
  `[scenecap] note: texture … zero-filling` 并按可用字节上传，其余置零；**内容不完整是可观察的**。
- **depth32float_stencil8 的实际行宽**：本机 M5 上报的 GPU 行是 5 字节/像素（格式表按 8 字节记），
  所以深度/模板目标总是短快照。渲染目标在 pass 开始会清屏，因此不影响这些 pass 的输出。
- **未命名像素格式**：捕获端把不认识的 `MTLPixelFormat` 记成 `"unknown"`，重放按 `rgba8unorm` 兜底并
  在 stderr 打出同名 note。
- **BC1（`bc1_rgba`）**：捕获端仍按 4 字节/像素记录（未做块布局计算），重放按 4×4 块布局读前 N 字节；
  修正需要捕获端按块计算，见待办。
- **顶点输入**：属性表来自 `vs->rf`，布局来自 `S.stream`。反射里没有顶点输入的 pipeline 会得到一个
  空属性表（重放不设 `MTLVertexDescriptor`，若 MSL 声明了 `[[attribute]]` 则 Metal 拒绝该 pipeline）。
- **同一帧的 compute 链**：compute 不重放，依赖它的后处理 pass 会读到未计算的内容。

**验证窗口**：`SCENECAP_FIRST_FRAME` 默认 0（立即），`SCENECAP_FRAMES` 默认 1。要在特定画面抓，
先用 `scenecap` 命令在目标时刻触发（本页命令示例即此方式），不要依赖启动即捕获。

---

## 9. 相关文件

- 格式/API/逐字段契约：[`tools/scenecap/README.md`](../tools/scenecap/README.md)
- 记录内核：`tools/scenecap/capture/`；容器：`tools/scenecap/format/`
- 重放器与合成包：`tools/scenecap/replay/`（`scenecap_replay`、`scenecap_synth.py`）
- 对比工具：`tools/scenecap/compare/`（`scenecap_diff.py`）
- 多游戏同步：`tools/scenecap/sync.py`
- 测试：`tools/scenecap/tests/`、`tools/scenecap/replay/tests/`、`tools/scenecap/compare/tests/`
  与顶层 `make check`

### 9.4 compute pass 重放

重放器现在会**执行** compute pass（此前一律记为 unsupported）：

- `tools/scenecap/replay/replay_model.m` 解析 manifest 顶层的 `dispatches[]`（`id`/`pass`/`pipeline`/`grid`/`argument_table`）。
- `replay_metal.m` 的 `dispatchPass:error:` 建 compute pipeline（按 `stage.mslBlobId` 缓存）、绑 buffer/texture/sampler、`dispatchThreadgroups:` 后 `waitUntilCompleted`；threadgroup 大小固定 64×1×1。
- `index.json` 新增顶层 `dispatches[]`：每项 `{pass, dispatch, grid, status, reason?}`；compute pass 仍列入 `unsupported`，原因统一为 `no colour attachment to export`（它没有可导出的颜色附件）。

实测（`/tmp/scenecap-20261024-232638.nvs`，60 帧）：1860 pass 里 120 个 compute 全部 `status=ok`，`unsupported` 180 条（全部是 `no colour attachment`），`errors` 空。

**为什么画面看起来没变**：本次包里的 10 个 compute kernel 只声明了 buffer 参数（`constant` / `device` 的 SSBO），没有 `texture2d` / `access::write` 之类的纹理写入；抽查 MSL 源码确认它们写的是 `cp_sN` 这类 storage buffer（例如把可见性列表清零），因此执行它们不会改变任何纹理，导出 PNG 与不执行时逐像素一致是预期行为，不是重放缺陷。只有 kernel 真正写纹理时，compute 结果才会反映到后续 pass 的采样上。

## 9. 开发构建与指令式抓帧（推荐用法）

发布产物里不应该带抓帧钩子：默认 `SCENECAP=0` 时 `scenecap_capture.c` / `nvs_format.c` 不进编译，`nvn_mtl.m` 的 7 处钩子、`input.c` 的 `scenecap` 命令、`frontend.m` 的 F9/F10 全部由 `#if SCENECAP_HOOKS` 排除。实测发布构建 `strings nier-native | grep -c scenecap` 为 0。

### 9.1 编一个能抓帧的开发版

```sh
make -C games/nier runtime WORK=/Volumes/bit2t/switch/totk/NIER_build -j8 SCENECAP=1
```

`SCENECAP` 变化不会自动触发 `nvn_mtl.m` 重编，切换开关后先 `touch games/nier/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m`。开发版启动后会在 `/tmp/scenecap-<pid>.sock` 上监听，日志里有一行 `scenecap: control socket /tmp/scenecap-<pid>.sock`。

### 9.2 用 CLI 下发抓帧

```sh
# 抓 3 帧连续画面（默认写到 /tmp/scene.nvs）
./tools/scenecap/scenecap capture --name nier-native --frames 3 --out /tmp/scene.nvs --wait

# 只抓一帧
./tools/scenecap/scenecap capture --name nier-native --out /tmp/one.nvs --wait

# 按 pid 指定（多开时）
./tools/scenecap/scenecap capture --pid 12345 --frames 10 --out /tmp/ten.nvs
```

`--name` 按进程名找（`nier-native`），`--wait` 轮询文件大小直到稳定再返回。指令只设置请求状态，真正的捕获仍由渲染线程在帧边界开始/结束，所以不会打断 pass。

### 9.3 重放与对比

```sh
./tools/scenecap/scenecap verify /tmp/scene.nvs
./tools/scenecap/scenecap replay /tmp/scene.nvs -o /tmp/out
./tools/scenecap/scenecap diff --baseline /tmp/base /tmp/out
```

### 9.4 开发版崩溃排查记录

`scenecap_capture.c` 的 `sc_ready()` 不能在编码线程里启动捕获：它会被 `scenecap_nvn_pass_begin/draw/dispatch` 调用，而那些钩子跑在 Metal 编码线程上（`TOTK_RENDER_STREAM=1`），在里面做 host 读取会触发 `FATAL render stream: sync inside a streamed pass`。现在 `sc_ready()` 只查状态，启动统一由 `scenecap_nvn_frame`（游戏线程的 `mtl_set_frame`）负责。

另一个坑：`#if SCENECAP_HOOKS` 包裹 `nvn_mtl.m` 的钩子时 `#endif` 后面不能跟代码（`#endif enc_r_end(g_enc);` 这种写法会静默改变语义），必须让每个 `#endif` 独占一行。
