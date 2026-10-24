# scenecap 渲染回归对比（scenecap diff）

`scenecap diff` 逐 pass 比较两次重放的输出目录，给出像素级差异、通过/失败判定，
以及一份可复核的中文 markdown 报告。它是 scenecap 工具链的最后一环：

```
capture(t1/t2) --.nvs--> replay(t3) --pass_*.png--> diff(t4) --report.md--> verify(t6)
```

本目录只依赖 **Python 标准库**（`zlib`/`struct`/`json`/`argparse`），不引入
numpy/Pillow——`make check` 必须离线可跑。测试里若检测到 Pillow 会额外做一次
PNG 交叉验证（可选项，缺失时跳过），但工具本身从不依赖它。

## 用法

```bash
# 普通模式：比较基线与候选两次重放
python3 tools/scenecap/compare/scenecap_diff.py out/base out/cand -o report.md
# 或经 t5 的整合入口（转发到同一实现）
scenecap diff out/base out/cand -o report.md

# 基线模式：目录无基线时写入；有基线时做回归判定
scenecap diff --baseline out/baseline out/cand -o report.md
scenecap diff --baseline out/baseline out/cand --update-baseline -o report.md
```

常用选项：

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `-o/--report PATH` | `scenecap-diff-report.md` | markdown 报告路径 |
| `--max-channel-delta N` | `2` | 每通道最大误差，单位是“8-bit 量化单位”（见下） |
| `--max-diff-pixel-ratio P` | `0.001` | 差异像素比例上限（0.1%） |
| `--diff-dir DIR` | `<报告名>.diff/` | 差异图目录 |
| `--no-diff-images` | 关 | 不生成差异图 |
| `--baseline DIR` | 关 | 基线模式 |
| `--update-baseline` | 关 | 覆盖已有基线 |
| `--no-json` | 关 | 不写报告旁的机器可读 `.json` |

退出码（t5/t6 的脚本据此判定）：

| 码 | 含义 |
| --- | --- |
| `0` | 通过；或基线模式成功写入/更新基线 |
| `1` | 渲染回归失败（存在不满足阈值的 pass） |
| `2` | 输入/用法错误（目录缺失、schema 不符、基线损坏、参数非法）——**不**产生“渲染通过”的结论 |

## 输入契约

每个目录必须包含 `index.json`（`schema = "scenecap.replay/1"`，由 t3 生成）与被引用的
`pass_<NNN>_<slug>.png`。每个 `passes[]` 项含 `index/slug/width/height/pixel_format/encoding/png/status`。

* 按 `index` 配对（不按文件名），因此 pass 重命名不会造成假失败；
* 任一侧缺少该 `index`、或任一侧 `status != "ok"` → 该 pass 直接判失败并写明原因；
* `index.json` 的 `width/height` 会与 PNG 实际尺寸交叉校验；
* `pixel_format` 两侧不一致 → 直接判失败（这是契约违背，不是“像素差异小”）。

## 判定语义（可复算）

1. **解码**：按各侧自己的 `encoding` 把 8-bit 像素转为线性光——`srgb` 走反 sRGB
   传递函数，`linear` 直接用 `value/255`。两侧 encoding 不同是允许的（例如基线
   `srgb`、候选 `linear` 表示同一场景），此时分别解码后在线性空间比较，报告里会
   在“备注”说明。
2. **通道误差**：两线性值之差 ×255，单位即“8-bit 量化单位”。因此默认阈值 2 表示
   “允许 1~2 个码值的编码往返误差”，但在线性空间判定，暗部/亮部的感知差异不会被
   平方根曲线掩盖。
3. **差异像素**：R/G/B/A 任一通道误差 **严格大于** 阈值（等于阈值算通过）；
   差异像素比例 = 差异像素数 / 总像素数。
4. **PSNR**：峰值 1.0（线性归一化），`-10·log10(MSE)`；两图完全一致时为 `∞`。
5. **SSIM**：简化版单窗口实现（全图一个窗口，Rec.709 亮度，K1=0.01、K2=0.03），
   用于趋势判断，不等价于分块 SSIM（报告里明确标注）。
6. **NaN/Inf**：8-bit PNG 不可能携带非有限值，故 PNG 路径计数恒为 0；若将来直接
   比较 f32 缓冲，使用 `compare_float_buffers()`，它会真实统计 NaN/±Inf 像素并判失败。
7. **整体结论**：所有 pass 通过才通过；`first_failure` 给出首个失败 pass（按 index 升序）。

## 输出物

* `<report>.md`：中文报告——输入目录与包信息、阈值、结论、首个失败 pass、整体统计表、
  pass 明细表、失败详情、差异图索引、度量定义。
* `<report>.json`：机器可读结果（`schema = "scenecap.diff/1"`），字段含 `passed`、
  `first_failure`、`thresholds`、逐 pass `verdict/metrics/reasons` 与 `totals`。
  非有限 PSNR（完全一致）序列化为 `null`。
* `<report>.diff/diff_<NNN>_<slug>.png`：每 pass 一张差异图。未超阈值的像素为 25%
  灰度背景；超阈值像素按 **黄 → 红** 热力色显示，绿色通道按
  `e = clamp(log2(d8 / max(阈值, 1/255)) / 6, 0, 1)` 衰减（差异每放大 64 倍到纯红），
  便于肉眼定位“刚过阈值”的像素。差异图只供人工复核，不参与自动判定。

## 基线模式

`--baseline <dir>` 把首次运行写成一份自包含快照：

```
<baseline>/baseline.json      # 清单：schema、来源目录、每个文件的 SHA-256
<baseline>/index.json         # 来源 index.json 的副本
<baseline>/pass_000_main.png  # 被引用 PNG 的副本
```

* 目录不存在或为空 → 写入基线，退出码 0；
* 已有基线 → 先校验快照 SHA-256（被改动则退出码 2），再与候选做回归判定；
* 目录非空但不是基线 → 退出码 2（不猜测、不覆盖）；
* 需要换基线时显式 `--update-baseline`，旧版清单中不再需要的文件会被清理；
* 报告/JSON/差异图**不得**写进基线目录：会在写入前以退出码 2 拒绝，避免污染基线清单
  （基线目录会被逐文件校验 SHA-256）。

基线快照可以直接提交进 git（体积小、可审计），避免“基线被悄悄重跑覆盖”。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `scenecap_compare/png_codec.py` | 纯标准库 PNG 编解码（8-bit、非隔行；颜色类型 0/2/3/4/6→RGBA8；CRC 校验；超范围显式报错） |
| `scenecap_compare/replay_index.py` | 读取/校验 `scenecap.replay/1` 目录契约（含 png 路径防穿越） |
| `scenecap_compare/metrics.py` | 线性化解码、阈值、逐 pass 度量（max/MAE/ratio/PSNR/SSIM/NaN/Inf） |
| `scenecap_compare/diffimg.py` | 差异图（热力放大） |
| `scenecap_compare/results.py` | 按 index 配对、逐 pass 判定与整体汇总 |
| `scenecap_compare/report.py` | 中文 markdown 报告 + JSON |
| `scenecap_compare/baseline.py` | 基线快照写入/校验 |
| `scenecap_compare/cli.py` | `scenecap diff` 参数与退出码 |
| `scenecap_diff.py` | 独立入口脚本（t5 的 `scenecap diff` 转发到这里） |

## 测试

真实套件在 `scenecap_compare/tests/test_compare.py`（45 个用例），由仓库根目录的
`tests/test_scenecap_compare.py` 通过 `load_tests` 薄入口接入顶层 `make check`：

```bash
make check
# 或只跑本工具：
python3 -m unittest discover -s tests -p 'test_scenecap_compare.py' -v
```

覆盖：相同图通过、阈值内小改动通过、超通道阈值失败、超差异像素比例失败、尺寸不一致
失败（含 index 声明尺寸不符）、缺失/多余 pass 失败、`status != ok` 失败、
pixel_format 不一致失败、srgb/linear 混合正确解码（同一线性场景判通过；相同字节按
不同 encoding 解释判失败）、阈值边界（等于阈值通过）、PNG CRC/16-bit/隔行拒绝、
灰度/RGB 扩展、可选 Pillow 交叉验证、CLI 退出码 0/1/2、报告内容、差异图着色、
基线写入/回归/损坏检测/更新。

## 已知边界

* 仅接受 8-bit、非隔行 PNG；16-bit/Adam7 会显式报错（当前重放器输出为 RGBA8）。
* SSIM 为全局单窗口简化版，不用于与文献分块 SSIM 数值直接对比。
* 差异图为 8-bit 可视化，不保留原始浮点差异；需要精确定量请读 `<report>.json`。
* 尚未对接 `.nvs` 内未量化的 f32 缓冲——`compare_float_buffers()` 已就绪，待 t3
  提供浮点导出后接入。
