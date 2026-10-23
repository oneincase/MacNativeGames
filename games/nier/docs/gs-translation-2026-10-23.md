# 几何着色器（GS）翻译链勘察与结论

日期：2026-10-23。目标：把 NieR 第一章节用的 `137b54dc…`（NVN stage 2）从「跳过」变成可翻译的着色器。

## 1. 根因回顾

- `nier.log`：`【红】TOTK shader 137b54dc…：NVN stage 2（几何/细分着色器）没有翻译链；这一级跳过，其余级照常绘制`。
- `nvn_mtl.m` 的 `prog_get` 里 `int st=(int)nvn_stage; if(st!=0&&st!=1&&st!=5){…}` 显式丢弃 stage 2/3/4，`NIER_KEEP_GS_DRAWS=1` 只让「原始三角形」照画（顶点由 VS 给，等效 passthrough），GS 的副作用（写 TFE 缓冲、视口掩码）从未执行。

## 2. 已打通：SPIR-V 能出

- 预编译翻译器 `$WORK/build/shader-service/totk_xlate` 支持**批处理模式**（`totk_xlate <job.json> <outdir>`），stage 字符串不校验，真正的阶段由代码里的 SPH 头决定。
- 作业（`stage: "gs"`，`code` 指向运行时的 `.code`，图形 = 0x30 头之后）→ **成功**产出 `gs137b54.spv`（11,648 B）与 `.res.json`。
- SPH 解析：`.code` 前 4 字节 `0x15071061`，按 `ShaderHeader.Stage = commonWord0.Extract(10,4)` 得 **4 = Geometry**；`Extract(24,1)` = **1 = GpPassthrough**；`Extract(24,8)` = 21（`ThreadsPerInputPrimitive`）。
- 用 d2r-mobile 的 `dmsh_xlate`（源码 `Program.cs`）试直接翻译：**SPIR-V 后端崩**（`InvalidCastException: AstOperation → AstOperand`，栈在 `CodeGen/Spirv/Instructions.GenerateCall`）；**GLSL 后端成功**（`137b54dc.glsl`，6,650 B），且 GLSL 明确显示 `layout (passthrough) in gl_PerVertex` + `GL_NV_geometry_shader_passthrough`，主函数**没有 `EmitVertex`/`EndPrimitive`**，只写 `gp_s1` 与 `gl_ViewportMask[0]`。

## 3. 卡点：SPIR-V → MSL

```
$ spirv-cross gs137b54.spv --output gs137b54.metal
SPIRV-Cross threw an exception: This subgroup operation is only supported in Vulkan semantics.
```

- GS 里用了 subgroup 操作（`gl_SubGroupInvocationARB` / `readInvocationARB`，GLSL 里是 `ShuffleUp()`），spirv-cross 的 **MSL 后端拒绝** subgroup 语义。
- 语义本身不复杂：`ShuffleUp(a0,a1,a2,out a3)` = 按 lane 偏移读相邻 invocation 的值，用于把三角形顶点按 wave 压缩后写进 `gp_s1`（TFE / transform feedback 缓冲）。

## 4. 两条可行路径

**(a) 手写 MSL 的 compute 版本（推荐）**
- Metal 的 `simd_shuffle_*` / `simdgroup` 能表达同样的 lane 语义；把 GS 主函数改写成 `kernel`（输入：顶点属性缓冲 + 三角形索引；输出：`gp_s1` 写入 + 视口掩码表）。
- 绘制本身仍走 VS（passthrough，顶点位置不变），GS 的副作用由一次 compute dispatch 完成。
- 这与 Ryujinx 的 `VtgAsCompute` 思路一致（`src/Ryujinx.Graphics.Gpu/Shader/ShaderCache.cs:506-508`、`VtgAsCompute*.cs`）。

**(b) 先跳过 subgroup，验证 GS 形状**
- 把 `ShuffleUp` 换成常量/串行近似，只为看 TFE 写入的地址轨迹是否正确；不用于正式画面。

## 5. 运行时的接线点（已完成勘察，未改）

- `totk_shader_rt.m` 已有实验钩子：`stage == 2 && getenv("NIER_GS_TRANSLATE") → stage_name @"gs"`。
- `nvn_mtl.m:5331` 的 `if(st!=0&&st!=1&&st!=5)` 是丢弃 GS 的唯一实现点；改成放行 `st==2` 后，`totk_rt_variant(@(key), 2, …)` 会走翻译服务。
- 翻译服务：`service_start()` 优先用 `build/shader-service/totk_xlate`（native），否则 `scripts/shader_service.py` + `build/totk_xlate/totk_xlate.dll`。
- `dmsh_build.py` 的离线批量映射表 `stage_of[c[3]] = {'顶点':'vs','像素':'ps'}[c[2]]` 只有两档，需要补 `'几何':'gs'`（当前 GS 不在映射表里，`137b54dc` 是运行时新发现的）。

## 6. 建议的落地顺序

1. `nvn_mtl.m`：`stage_of()` 补 `几何 → 2`；`prog_get` 放行 `st == 2`（其余 3/4 仍跳过）。
2. `totk_shader_rt.m`：把 `NIER_GS_TRANSLATE` 从「只申请 SPIR-V」升级为「申请并接受 gs 变体」。
3. 手写 GS 的 MSL compute（`k_gs_tfe_msl`），在 `draw` 的 GS 路径里：VS 直通绘制 + dispatch 该 kernel。
4. 用 c255/c256 的读档脚本验证：人物/场景是否恢复，`gp_s1` 是否被写入，`[gs]` 日志是否还有「没有翻译链」。
