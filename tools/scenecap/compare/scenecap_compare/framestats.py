"""逐帧稳定性统计：按 pass slug 匹配相邻帧，输出 PSNR 与差异像素比例。

和 ``diff`` 不同，这里比的是同一个重放目录内部相邻帧，用来确认动画/粒子
逐帧是稳定的（没有某一帧突然跳变），而不是拿两次重放做回归。
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import MetricError, Thresholds, compare_rasters
from .png_codec import PngError, decode_png
from .replay_index import InputError, PassEntry, ReplayDir, load_replay_dir


@dataclass(frozen=True)
class FramePair:
    """相邻两帧里同一个 pass 的比较结论。"""

    base_frame: int
    cand_frame: int
    slug: str
    psnr_db: float | None
    diff_pixel_ratio: float
    max_channel_error: int
    identical: bool


@dataclass(frozen=True)
class FrameStats:
    """整个重放目录的逐帧统计。"""

    frames: tuple[int, ...]
    pairs: tuple[FramePair, ...]
    identical_pairs: int

    @property
    def psnr_values(self) -> list[float]:
        return [item.psnr_db for item in self.pairs if item.psnr_db is not None]

    def worst(self) -> FramePair | None:
        values = [item for item in self.pairs if item.psnr_db is not None]
        return min(values, key=lambda item: item.psnr_db or 0.0) if values else None


def _frame_of(entry: PassEntry) -> int | None:
    raw = entry.raw.get("frame")
    return raw if isinstance(raw, int) else None


def _group_by_frame(replay: ReplayDir) -> dict[int, dict[str, PassEntry]]:
    by_frame: dict[int, dict[str, PassEntry]] = {}
    for entry in replay.passes.values():
        frame = _frame_of(entry)
        if frame is None:
            continue
        by_frame.setdefault(frame, {})[entry.slug] = entry
    return by_frame


def frame_stats(replay_dir, jobs: int = 0) -> FrameStats:
    """统计同一重放目录里相邻帧的稳定性（jobs>1 时按帧对切分到多进程）。"""
    replay = replay_dir if isinstance(replay_dir, ReplayDir) else load_replay_dir(replay_dir)
    by_frame = _group_by_frame(replay)
    if len(by_frame) < 2:
        raise InputError("该重放目录里带 frame 标记的帧不足 2 帧，无法做逐帧统计")

    order = sorted(by_frame)
    thresholds = Thresholds()

    # 先把要比较的 PNG 对算出来，渲染/解码都按对切分
    tasks: list[tuple[str, str, str, int, int, int]] = []
    for base_frame, cand_frame in zip(order, order[1:]):
        base_list = sorted(by_frame[base_frame].values(), key=lambda entry: entry.index)
        cand_list = sorted(by_frame[cand_frame].values(), key=lambda entry: entry.index)
        for position, (base_entry, cand_entry) in enumerate(zip(base_list, cand_list)):
            tasks.append((str(replay.png_path(base_entry)), str(replay.png_path(cand_entry)),
                          base_entry.encoding or "srgb", base_frame, cand_frame, position))
    workers = jobs if jobs and jobs > 0 else 0
    if workers > 1 and len(tasks) > 64:
        import concurrent.futures
        import multiprocessing

        workers = min(workers, multiprocessing.cpu_count() or 1)
        slices = [tasks[i::workers] for i in range(workers)]
        slices = [chunk for chunk in slices if chunk]
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            gathered = [item for batch in pool.map(_stats_slice, slices) for item in batch]
        gathered.sort(key=lambda item: (item.base_frame, int(item.slug.lstrip("#"))))
        return FrameStats(tuple(order), tuple(gathered),
                          sum(1 for item in gathered if item.identical))

    decoded: dict[tuple[int, str], object] = {}
    pairs: list[FramePair] = []
    identical = 0

    def raster(frame: int, slug: str):
        key = (frame, slug)
        if key not in decoded:
            try:
                decoded[key] = decode_png(replay.png_path(by_frame[frame][slug]).read_bytes())
            except (PngError, OSError):
                decoded[key] = None
        return decoded[key]

    for base_frame, cand_frame in zip(order, order[1:]):
        # Slugs are globally unique (pass_<index>), so the only stable way to pair two frames is
        # by the pass position inside each frame.
        base_list = sorted(by_frame[base_frame].values(), key=lambda entry: entry.index)
        cand_list = sorted(by_frame[cand_frame].values(), key=lambda entry: entry.index)
        for position, (base_entry, cand_entry) in enumerate(zip(base_list, cand_list)):
            a = raster(base_frame, base_entry.slug)
            b = raster(cand_frame, cand_entry.slug)
            if a is None or b is None:
                continue
            if a.width != b.width or a.height != b.height:
                continue
            try:
                metrics = compare_rasters(a, b, thresholds, base_entry.encoding or "srgb")
            except MetricError:
                continue
            same = a.data == b.data
            if same:
                identical += 1
            pairs.append(
                FramePair(base_frame, cand_frame, f"#{position}", metrics.psnr_db,
                          metrics.diff_pixel_ratio, metrics.max_channel_error, same)
            )
    return FrameStats(tuple(order), tuple(pairs), identical)


def _fmt(value: float) -> str:
    return "∞" if value == float("inf") else f"{value:.2f}"


def _stats_slice(tasks: list[tuple]) -> list[FramePair]:
    """Worker: decode and compare one slice of frame pairs."""
    out: list[FramePair] = []
    thresholds = Thresholds()
    for base_png, cand_png, encoding, base_frame, cand_frame, position in tasks:
        try:
            a = decode_png(open(base_png, "rb").read())
            b = decode_png(open(cand_png, "rb").read())
        except (PngError, OSError):
            continue
        if a.width != b.width or a.height != b.height:
            continue
        try:
            metrics = compare_rasters(a, b, thresholds, encoding)
        except MetricError:
            continue
        out.append(FramePair(base_frame, cand_frame, f"#{position}", metrics.psnr_db,
                             metrics.diff_pixel_ratio, metrics.max_channel_error, a.data == b.data))
    return out


def render_report(stats: FrameStats) -> str:
    """把逐帧统计渲染成一段中文文本报告。"""
    values = stats.psnr_values
    lines = [
        "# 逐帧稳定性报告",
        "",
        f"- 帧数：{len(stats.frames)}（{stats.frames[0]}..{stats.frames[-1]}）",
        f"- 相邻帧 pass 对数：{len(stats.pairs)}",
        f"- 完全相同帧对：{stats.identical_pairs}",
    ]
    if values:
        avg = sum(values) / len(values)
        lines += [
            f"- PSNR：min {_fmt(min(values))} / mean {_fmt(avg)} / max {_fmt(max(values))} dB",
        ]
    worst = stats.worst()
    if worst is not None:
        lines.append(
            f"- 最差：帧 {worst.base_frame}→{worst.cand_frame} {worst.slug}，"
            f"PSNR {_fmt(worst.psnr_db)} dB，差异像素 {worst.diff_pixel_ratio * 100:.2f}%"
        )
    if stats.identical_pairs == len(stats.pairs) and stats.pairs:
        lines.append("")
        lines.append("⚠️ 所有相邻帧完全相同——如果这应当是一段动画，说明录制窗口内画面没有推进。")
    return "\n".join(lines) + "\n"
