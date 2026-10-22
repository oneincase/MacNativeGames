# NieR:Automata 3D 偏色：根因定位（2026-10-23 02:00）

## 结论

**3D 画面发绿不是后处理曲线、不是 bloom、不是色彩空间，而是 tonemap pass 的「场景」采样绑定到了 bloom 纹理上。**

tonemap 的像素着色器（key `b41adaf5…-1178a6955197_b`）把「场景色」声明在 **binding 2**，
而这一帧该 pass 实际绑定的 `reg2` 是 **ser=89（200x112 的 bloom 上采样结果，纯绿）**，
真正含画面的 **ser=17（1920x1080，延迟光照输出）** 被绑在 `reg0`。

于是 tonemap 的输入永远是 bloom 那张小绿图，输出的自然是一整幅绿。

## 证据链

### 1. 同帧原始字节（c204，帧 7000，`/tmp/raw11.py` 解码 RG11B10F）

| ser | 角色 | 线性均值 R/G/B | 8bit clamp 均值 |
|-----|------|----------------|-----------------|
| 17 | 延迟光照输出（场景） | 5.0233 / 4.2328 / 4.6382 | 253.5 / 251.8 / 253.4（≈白，G/R 0.99） |
| 19 | tonemap 输出（最终画面） | 0.2707 / 0.7211 / 0.2795 | G/R **2.66**、G/B **2.58**（纯绿） |
| 84 | bloom 50x28 | 0.0000 / 11.3415 / 0.0000 | R/B 全 inf（垃圾） |

场景本身是中性偏暖的白，绿只出现在 tonemap 之后。

### 2. tonemap 的 LUT 查表落在绿角（c206，帧 6500，`td_1616.png.raw`，256x16 RGBA8）

- 色域 LUT 的 `(255,15)` = 纯白 `(255,255,255)`；`(0,0)` = `(28,33,35)`。
- 最终画面 `(69,184,71)` 在 LUT 里的最近点 = **`(x=0, y=14)` = `(70,186,72)`（距离 6）**。
- 即 tonemap 的查表坐标是 **(R≈0, G≈0.93, B≈0)**，而不是场景的真实白。
- 这只能用「场景采样的返回值就是 (0, 0.83, 0)」解释。

### 3. MSL 直通判决（c210）

把 `b41adaf5…_b.metal` 的输出临时改成场景 sRGB 直通（`out.m_45.xyz = _158/_164/_170`）：

```
c210_pass.png   mean R=0.3 G=0.3 B=212.4   top1% 32/31/209   → 纯蓝
```

shader 里 `_158 = _112 = fma(_80, _110, _72)`、`_72 = _66.z`，`_164 = .x`，`_170 = .y`
—— 通道顺序是 `(z, x, y)`。输出纯蓝说明 **`_66 = sample(binding 2) = (R=0, G=0.83, B=0)`**，
即「场景采样」拿到的是一张纯绿纹理。

### 4. 绑定表（c195 passdump，帧 9548，pass 376221）

```
[pass] F9548 K 376221 st1 key=b41adaf5…-1178a6955197_b nb=12
[pass] F9548 B 376221 st1 tex reg0 tid=269 实 ser=17   nvnfmt=0x3f fmt=92 1920x1080
[pass] F9548 B 376221 st1 tex reg1 tid=562 实 ser=1616 nvnfmt=0x25 fmt=70 256x16   （LUT）
[pass] F9548 B 376221 st1 tex reg2 tid=341 实 ser=89   nvnfmt=0x3f fmt=92 200x112   （bloom）
[pass] F9548 B 376221 st1 tex reg3 tid=562 实 ser=1616 nvnfmt=0x25 fmt=70 256x16   （LUT）
```

shader 的 json 绑定：

```
binding 2 handle 0x1057805e   ← _66，场景色
binding 3 handle 0x1056805a   ← _74，bloom
binding 4 handle 0x1057005c   ← _260，LUT 上
binding 5 handle 0x10580060   ← _276，LUT 下
```

若 `binding N ↔ reg (N-2)`：binding 2→reg0（17，对）、binding 3→reg1（1616，**应为 89**）、
binding 4→reg2（89，**应为 1616**）、binding 5→reg3（1616，对）。

**reg1 与 reg2 的顺序颠倒**：bloom 与 LUT 换了位置。

### 5. 对照实验（都是同一二进制、同一导航）

| 实验 | 开关 | 结果 |
|------|------|------|
| c208 | 无 | G/R 2.61 / G/B 2.53（绿，对照） |
| c210 | 改 MSL 场景直通 | 纯蓝（证明采样拿到纯绿纹理） |
| c211 | `NIER_SWZ_RGBA=1`（全纹理强制 RGBA swizzle） | 世界全黑、HUD 正常 → swizzle 影响面很大但不是根因 |
| c212 | `NIER_SWZ_RGBA=17`（只强制 ser=17） | 仍然绿 G/R 2.60 → 不是 17 的 swizzle |

## 6. 反射表的 reg 与实际绑定相反（根因的根因）

`$WORK/build/shader-cache/rt-totk-native-v4-unverified/b41adaf5…-1178a6955197_b.json`
（`route: "B"`，`bindings` 12 条）的纹理槽号：

| msl_name | handle | json `reg` | 角色 |
|---|---|---|---|
| `fp_t_cb196610_1057805E` | 0x1057805E | **2** | 场景色（`_66`） |
| `fp_t_cb196610_1056805A` | 0x1056805A | **0** | bloom（`_74`） |
| `fp_t_cb196610_1057005C` | 0x1057005C | **1** | LUT 上（`_260`） |
| `fp_t_cb196610_10580060` | 0x10580060 | **3** | LUT 下（`_276`） |

而 pass 376221 的**实际绑定**（passdump）：

| NVN slot | 实际纹理 |
|---|---|
| reg0 | ser=17（1920x1080 场景） |
| reg1 | ser=1616（256x16 LUT） |
| reg2 | ser=89（200x112 bloom） |
| reg3 | ser=1616（256x16 LUT） |

**两者恰好把「场景」和「bloom」对调了**：shader 认为场景在 slot2、bloom 在 slot0；
游戏把场景绑在 slot0、bloom 绑在 slot2。

于是 `_66 = sample(slot2) = bloom(绿)`、`_74 = sample(slot0) = 场景(白)`，
tonemap 用 bloom 当场景 → 整幅绿。

`reg` 由外部翻译器（注释里提到的 `dmsh_build.py`，本仓库内没有源码）写进 `.json`，
运行时 `nvn_refl_from_json`（`third_party/d2r_gfx/gfx/nvn/nvn_refl.m:43-44`）直接读：
`int reg = [b[@"reg"] intValue]; r->reg = reg < 0 ? 0 : reg;`
—— 一旦 json 的 `reg` 与实际 slot 相反，查表 `S.tex[stage][reg]` 就取错纹理。

## 下一步（修法方向）

1. 查 `p_bind_buf` 的 `reg` 填充与 `S.tex[stage][reg]` 的赋值，确认「游戏给的 texture unit」是否被正确保存，
   以及「shader binding ↔ reg」的偏移是否对所有 pass 一致。
2. 若确认偏移错：改「绑定槽位的排序/映射」，让 binding 3 正确拿到 bloom、binding 4 拿到 LUT。
3. 修完的验收：`td_19.png.raw` 的 8bit clamp 均值 G/R 落在 **1.00–1.25**、G/B 落在 **0.90–1.10**（原版真值区间），
   且 `td_17`/`td_19` 同帧都正常、其余场景不回归。

## 复现命令

```sh
# 同一二进制、同一导航，只改一个 env
TOTK_GPU_BUDGET_MB=14000 NIER_GS_COMPACT=1 ./build/nier-native rewritten/v103 boot
# 帧 6500 落盘 17/19/1616 的原始字节
DM_NVN_TEXRAW=17,19,1616 DM_NVN_TEXDUMP=6500:$WORK/logs/tdcXXX/td_%d.png
```

> 注：`NIER_SWZ_RGBA`、`NIER_LUT_IDENTITY` 是本次定位新增的诊断开关，默认关闭，不改变默认路径行为。
