# 结论：GS 不是「人物黑」的原因

`137b54dc` 的 GLSL（`dmsh_xlate` GLSL 后端产出）语义已经完全确认：

```glsl
layout (passthrough) in gl_PerVertex { vec4 gl_Position; ... };   // 顶点直通，位置由 VS 给
...
if (temp_35) {
    temp_54 = temp_53 << 2;
    gl_ViewportMask[0] = 1;                                       // 只写视口掩码
    gp_s1.data[temp_54>>2]      = floatBitsToUint(in_attr3[v7].x); // 写 3 个 float 进 TFE 缓冲
    gp_s1.data[(temp_54+4)>>2]  = floatBitsToUint(in_attr3[v8].x);
    gp_s1.data[(temp_54+8)>>2]  = floatBitsToUint(in_attr3[v10].x);
    return;
}
gl_ViewportMask[0] = 0;
```

## 推论

1. **顶点位置完全直通**——`NIER_KEEP_GS_DRAWS=1` 画出的三角形就是 GS 会输出的三角形。GS 缺失不会让几何「少画」。
2. GS 只做两件事：把 3 个顶点的一个属性写进 `gp_s1`（TFE/transform feedback），设置 `gl_ViewportMask`。
3. `NIER_KEEP_GS_DRAWS=1` 反而会**多画** `gl_ViewportMask[0] = 0` 那批三角形（本该被 GS 丢掉）。
4. 所以「人物模型黑色」**不是 GS 翻译链缺失导致的几何丢失**，而与用户直觉一致：是**贴图/材质/光照**。

## 下一步的方向（按成本排序）

1. **验证人物贴图**：`DM_NVN_TEXPNG_FS=<人物 FS key>` 抓人物 draw 的贴图，看是黑的还是正常（c257 抓的是 eaf2df47 主几何，全是 ASTC 6x6 sRGB，`fmt=190` 映射正确）。
2. **验证材质/光照常量**：人物用哪套 FS，是否走 `49bd7a78`（那个输出恒 0 的空壳）。c258 证明它写 ser=27 不是最终画面。
3. **视口掩码**：把 `gl_ViewportMask=0` 的语义补上（用 CPU 侧三角形剔除近似），可以消掉「多画」的伪影。
4. GS 的 `gp_s1` 写入是否被消费：查 `gp_s1` 的 sbuf slot（res.json: binding 1, slot 0, c0[260]）在别的 pass 里有没有被读。

## GS 翻译链本身（已完成勘察）

- SPIR-V：`build/shader-service/totk_xlate` 批处理成功出 `gs137b54.spv`（11648 B）。
- MSL：`spirv-cross --msl` 报 `Subgroup builtins are not available in this type of function`（Metal 的几何着色器没有 simdgroup）；`--vulkan-semantics` 能过检查但输出仍是 GLSL。
- 因此完整 GS 只能走 compute 模拟（Ryujinx 的 `VtgAsCompute` 同因），而它的**唯一产出是 TFE 缓冲 + 视口掩码**，价值远低于「先修人物贴图」。
