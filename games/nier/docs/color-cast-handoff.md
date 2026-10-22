# 绿调偏色排查交接（2026-10-22，新会话从这里开始）

> 本文取代 `docs/color-cast-diagnosis.md` 的"下一步"部分（那份文档写到 c185，本文含 c188–c190 的突破）。
> 白屏已解除、P4 已关闭，本文只讲 3D 场景绿色偏色。

## 0. 工程与命令（照抄可用）

- 工程 `MacNativeGames/games/nier`（git 根 = `MacNativeGames`，分支 `feat/nier-latest`）
- `WORK=/Volumes/bit2t/switch/totk/NIER_build`，`PY=/Volumes/bit2t/switch/totk/TOTK_build/venv/bin/python`
- 构建：`cd games/nier && touch runtime/*.c third_party/d2r_gfx/gfx/nvn/nvn_mtl.m && make -j8 runtime WORK=$WORK PY=$PY`
- 跑批模板：抄 `NIER_build/logs/c165.sh`（含完整 env + 导航序列 + 收尾 `pkill`）。**改完 `runtime/*.c` 必须 `touch` 再 make**，否则拿旧二进制白跑。
- 回归：`sh scripts/stability.sh 3 13` 须 `STABLE`。
- 单实例锁：跑前 `pgrep -fl "nier-native|logs/c"` 必须为空。
- 导航（35s 到标题）：`key 38 150`×3（间隔 7-9s）→ `key 3 150` → `key 38 150`×2 → 约 250s 进序章飞行场景。
- 输入命令（`TOTK_INPUT_FILE`）：`shot`/`wshot <png>`、`passdump <n>`、`flag skipfs_<16hex> <0|1>`、`close`。

## 1. 已完成（勿重做）

- **白屏已解除**：`8d0e7c9`（svc 0x18 空句柄睡 1ms 返回 -2）+ `956aace`（release_due 在 `.usm` 全 `t=0` 时误丢帧，改为时间戳无信息时永不丢帧）。游戏现在能进标题→LOAD GAME→序章飞行场景。
- **P4 标题白块已关闭**：用户实机对照确认是 NieR 自身 glitch 美术设计，非缺陷（`1332c0f`）。
- **音频**：mix peak 0→4660→10480（BGM 渐入），只差听感人工确认。

## 2. 绿调偏色现状（量化）

- 序章飞行场景（`logs/c157_d.png`、`c182_base.png`、`c188_skip_sky.png`）：**平铺亮绿背景**；原版同场景（用户截图）= **中性灰白云海 + 橙色引擎光**。
- 标题（影片直连，不经 3D）：中性 ✓。→ **影片链色彩正确，只有 3D 渲染偏绿**。
- 偏色是**逐通道增益/内容错误，不是通道置换**（灰白→绿无法用任何单一 R/G/B 置换解释；引擎光橙→品红与海面灰白→绿方向相反）。

## 3. 已排除（勿重走，均有实测）

1. 通道置换（R↔B / G↔B）——反证见上。
2. mip 采低层：c162 `NIER_FORCE_LOD0=1` 与基线逐通道不变。
3. 静态采样贴图数据：c176（世界 pass 28 张）、c185（天空 pass 输入）全中性/正常。
4. 材质 tint：c174 世界 pass 实际用的材质 tint 是暖色 (29.15,9.87,3.65)，正常。
5. NVN swizzle：c172 全 HDR RT 恒等 `2,3,4,5`。
6. support buffer / is_bgra / bindless 句柄打包：ryujinx 对照全吻合（`vendor/ryujinx` 仅含 `Ryujinx.Graphics.Shader` 翻译层；完整 ryujinx 在 `/Volumes/bit2t/switch/totk/Ryujinx`）。
7. bindless 句柄当 float：`57290de6` 的 `_105` 分支恒 False，句柄值是死代码。
8. 移植层 bloom 捷径：未激活（"泛光 1/4 起点：生效"0 次）。
9. sRGB 格式表：完整（0x9A/0x9C 都在表内）。
10. 世界几何 shader `7c7dec85`：c182 skip 后绿底仍在（它只画细节几何，不是绿底）。

## 4. 绿源已锁定到一张纹理 ser=21（c188–c190 突破）

**c188 逐个 skipfs 二分**（飞行场景 base@250s）：
- skip 天空 `17654724` → 仍绿（天空不是源）
- skip 延迟光照 `915f419e` → **绿消失变紫**（5367 色）→ 915f419e 是"绿合成到屏幕"的 pass
- restore → 又绿

**c189 dump 915f419e 的输入**：HDR 纹理 **ser=21（1920x1080 fmt=92 RG11B10F）本身就绿**（R38 G70 B56）。

**c190 TEXRAW ser=21 原始内存位序判决**：按 Metal `RG11B10Float`（R=低11位/G=中11/B=高10）解码**正确**——亮部是暖色 (R54,G48,B43)≈橙（引擎光），暗部大面积绿主导 (0.051,0.061,0.024)。按 ryujinx 的 B10G11R11 解释会得到 G/B≈0.02 的退化垃圾值。**→ 位序不是 bug，ser=21 的数据本身就是绿的。**

**ser=21 关键事实**：在 dump 帧 F8741/F8742 **从不作为任何 render pass 目标**（`P ... ser=21`=0 次），只被采样（pass 342817/342818/342862/342863）。mem=`0x7dda642000`，sz=8847360。

## 5. 下一步（新会话从这里做）

**核心问题：ser=21 是谁产生的、绿从哪来。** 候选：
- (a) **CPU 上传的静态资源** → 绿 = 上传/deswizzle/解码错。查它的 `nvnfmt`/`flags`/`has_layout`（TEXPNG 路径会打，但 ser=21 在 c189 没进"被采样列表"所以没打 nvnfmt 行；用 `DM_NVN_TEXRAW=21` 已知能拿到 raw）。**若走 CPU deswizzle 回退路径，重点查块线性解包的通道序。**
- (b) **GPU compute/更早帧的 render pass 写的**（passdump 只采 2 帧，看不到生产者）→ 用 `DM_NVN_TILELOG=1920x1080` 看 ser=21 这块内存的完整事件序列（render/上传/解保护），或 `DM_NVN_SIZELOG` 看布局。

**判决实验（一次跑）**：`DM_NVN_TEXPNG=21 DM_NVN_TEXRAW=21` 同时拿 ser=21 的 PNG 与 raw，比对"GPU 采样结果"与"原始内存 Metal 解码"是否一致：
- 一致 → 数据本身绿，问题在生产者（走 a 或 b）。
- 不一致 → 上传/采样路径（view/deswizzle）有通道错。

**工具坑（本轮踩过）**：
- `DM_NVN_TEXRAW` 逐张 `cb_wait` 同步读回会**阻塞提交线程→驻留积压**，在飞行场景装载突发窗口（帧 2400 显卡 2312→6211MB）撞预算闸。对策：`TOTK_GPU_BUDGET_MB=13000`，且 dump 命令放在装载完成之后（250s+）。
- passdump/texpng 只在请求的那 1-2 帧生效，抓不到跨帧生产者。
- 导航到达飞行场景的时刻在 225–250s 抖动，wshot 太早会落在转场暗场（米色 201,196,172 = LOAD GAME，暗 40,44,45 = 转场）。

## 6. 诊断开关速查（全部默认关）

- `NIER_TRACE_SVC0=1`：`[svc0]` svc 0x18 句柄轨迹 + `[cvtrace]` 每条件键首次 wait/SIGNAL + `[taskpool]` 任务池 + `[osinit]` nn::os 初始化计数。
- `NIER_MOVIE_LOG=1`：`[movie]` 解码生命周期 + `register events` + `fire` + `post-EOS` + `release`。
- `DM_NVN_TEXPNG=<ser,...>` 或 `DM_NVN_TEXPNG_FS=<fskey前缀>`：dump 指定/被采样的贴图为 PNG（走采样 view，不撞预算）。
- `DM_NVN_TEXRAW=<ser>`：dump 原始字节（.raw，头 `W H D PF ROW`），**同步等待，避开突发窗口**。
- `DM_NVN_CBDUMP=<dir>`：随 passdump 存每笔绘制的 uniform/storage 块（`F<帧>_p<pass>_st<阶段>_cb<reg>_<地址>.bin`）。
- `DM_NVN_UPLOG=<WxH>`：上传源内存字节统计（min/max/mean/0xFF 占比/head16）。
- `flag skipfs_<16hex> <0|1>`：运行时丢弃某片元着色器的全部绘制（画面错，用于 A/B 定位）。
- `NIER_FORCE_LOD0=1`：强制 lodMaxClamp=0。

## 7. 给新会话的提示词（可直接粘贴）

```
继续排查 NieR:Automata (Switch 1.0.3) macOS 移植的"3D 场景绿色调偏色"。工程 MacNativeGames/games/nier，git 根 MacNativeGames，分支 feat/nier-latest。

先完整读 games/nier/docs/color-cast-handoff.md（绿源已锁定到 HDR 纹理 ser=21，位序已判决正确，下一步是查 ser=21 的生产者）；背景见 docs/plan-2026-10-22.md、docs/color-cast-diagnosis.md、CHANGELOG.md 顶部若干条。

环境：WORK=/Volumes/bit2t/switch/totk/NIER_build，PY=/Volumes/bit2t/switch/totk/TOTK_build/venv/bin/python。构建 cd games/nier && touch runtime/*.c third_party/d2r_gfx/gfx/nvn/nvn_mtl.m && make -j8 runtime WORK=$WORK PY=$PY。跑批抄 NIER_build/logs/c165.sh。单实例：跑前 pgrep -fl "nier-native|logs/c" 须空。回归 sh scripts/stability.sh 3 13 须 STABLE。

任务：确定 ser=21（1920x1080 fmt=92 RG11B10F，mem=0x7dda642000，dump 帧从不作为 render target）是 CPU 上传资源还是 GPU 早期/compute 写入，并定位绿色从哪注入。用 handoff 文档 §5 的判决实验（DM_NVN_TEXPNG=21 + DM_NVN_TEXRAW=21 比对采样结果与原始内存；DM_NVN_TILELOG=1920x1080 看内存事件序列）。

纪律：一轮一个可证伪假设 + 实测数字；每项改动独立提交并跑 stability；禁止用"返回成功"掩盖未实现；诊断开关默认关有上限；TEXRAW 撞预算就提 TOTK_GPU_BUDGET_MB=13000 并把 dump 放 250s 后。已排除的 10 条路（handoff §3）勿重走。
```
