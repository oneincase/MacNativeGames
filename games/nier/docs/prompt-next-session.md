# 新会话开场提示词（直接粘贴）

---

我在 `/Volumes/bit2t/switch/totk/MacNativeGames` 做 NieR:Automata（Switch 1.0.3）的 macOS 原生移植，
工程在 `games/nier`（AArch64 原生执行 + NVN→Metal 4 重放）。分支 `feat/nier-latest`，HEAD `1732e8e`。

**先读这两个文件，不要跳过：**
- `games/nier/docs/handoff-2026-10-24.md`（现状、已确认事实、黑屏定界、下一步、工具与陷阱）
- `games/nier/docs/ch1-black-screen-2026-10-23.md`（黑屏排查的完整过程）

**当前唯一阻塞：第一章节（废墟/花草那段）的 3D 世界是黑的。**
已经定界到：LUT、SSAO、环境探针、贴图都是好的；场景 RT（ser=16/17）和 G-Buffer（ser=24/25/26/27/30）实测全 0；
写场景的 `0ff2b337` 改输出无效（被 `140663` 的 9 个 FS 覆盖），而 `140663` 不读场景 RT。

**你要做的第一件事**（handoff §5 P0）：确认 G-Buffer 到底谁写——
抓 `149537`–`149539`（clear）和 `149547`–`149549`（load）的完整 `P`/`B`/`K` 行，
确认颜色附件是 `c0` 还是 `c0`–`c3`（MRT 是否被截断），再决定是改 `rp_begin_ex` 的附件绑定，还是查几何 FS 的 `[[color(N)]]`。

**硬约束：**
- 每次改动后必须跑 `sh scripts/stability.sh 3 13`，输出必须 `STABLE`。
- 截图必须量化（`python3 /tmp/pngdark.py <png>` 看整体亮度），不能凭肉眼说"好了"。
- 不要再用 `NIER_GAMMA` 之类曲线提亮来"让画面能看"，那是掩盖不是修复；用户已经明确要求恢复默认。
- 改任何 MSL 之前先 `.bak`，改完若无效必须还原。
- 用户会在实机上测（把 `.app` 拿去跑），所以每次给用户的包都必须是「想让它测什么」明确的版本。

**用户的操作路径**（复现黑屏用）：
读档 → 等 30 s → `key 124 2500` + `key 124 1500`（右转 160 度）→ 花草/废墟位置。
输入文件 `$WORK/logs/*.in`，命令 `key <code> <ms>`、`wshot <path>`、`passdump <n>`、`close`。

**构建：**
```sh
cd /Volumes/bit2t/switch/totk/MacNativeGames/games/nier
make -j8 runtime WORK=/Volumes/bit2t/switch/totk/NIER_build PY=/Volumes/bit2t/switch/totk/TOTK_build/venv/bin/python
make -j8 app
```

先给我一句话确认你读完了这两个文档、并复述你打算做的第一个实验，然后开工。

---

## 附：如果用户只想先玩

把 `games/nier/NieR Automata.app` 拷到别处双击即可（game data 在 `/Volumes/bit2t/switch/totk/NIER_build`）。
当前是默认亮度，第一章节会黑；序章飞行场景、战斗、UI、音频正常。
