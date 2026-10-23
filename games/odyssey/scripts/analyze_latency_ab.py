#!/usr/bin/env python3
"""Summarise frame latency and frame time for bounded runs (task 0c): per log, the median of the runtime's
"game frame enqueued -> target present" p50 (游戏帧入队到目标上屏) and of the 300-frame window frame-time p50 / p95 / max,
over windows after the gameplay start (default: frames after 2400).
Also (Applier, 22 October): the display link's lead to the target present p50 (提交领先目标上屏) and the present cadence
interval p50 / p95 / max (上屏节奏), which shows 8.33 / 25 ms judder on a 120 Hz link.
Usage: analyze_latency_ab.py <log>... [--after FRAME]"""
import re
import statistics
import sys
from pathlib import Path

LAT = re.compile(r"至 (\d+)：.*游戏帧入队到目标上屏 p50 ([0-9.]+) ms / 最小 ([0-9.]+) / 最大 ([0-9.]+) ms")
LEAD = re.compile(r"至 (\d+)：.*提交领先目标上屏 p50 ([0-9.]+) ms")
CAD = re.compile(r"上屏节奏（近 \d+ 张，至帧 (\d+)，显示器 (\d+) Hz）：间隔 p50 ([0-9.]+) / p95 ([0-9.]+) / 最大 ([0-9.]+) ms")
TIME = re.compile(r"计时 至帧 (\d+)： 帧时间 p50 ([0-9.]+) / p95 ([0-9.]+) / 最大 ([0-9.]+) ms")


def summarise(path: Path, after: int):
    text = path.read_text(errors="replace")
    lat = [float(m[2]) for m in LAT.finditer(text) if int(m[1]) > after]
    frames = [(float(m[2]), float(m[3]), float(m[4])) for m in TIME.finditer(text) if int(m[1]) > after]
    lead = [float(m[2]) for m in LEAD.finditer(text) if int(m[1]) > after]
    cad = [(float(m[3]), float(m[4]), float(m[5]), int(m[2])) for m in CAD.finditer(text) if int(m[1]) > after]
    med = lambda xs: round(statistics.median(xs), 2) if xs else None
    return dict(windows=len(frames), latency_p50=med(lat), frame_p50=med([f[0] for f in frames]),
                frame_p95=med([f[1] for f in frames]), frame_max=max((f[2] for f in frames), default=None),
                lead_p50=med(lead), cadence_p50=med([c[0] for c in cad]), cadence_p95=med([c[1] for c in cad]),
                cadence_max=max((c[2] for c in cad), default=None), display_hz=cad[-1][3] if cad else None)


def main():
    args, after = sys.argv[1:], 2400
    if "--after" in args:
        i = args.index("--after"); after = int(args[i + 1]); del args[i:i + 2]
    for name in args:
        print(Path(name).name, summarise(Path(name), after))


if __name__ == "__main__":
    main()
