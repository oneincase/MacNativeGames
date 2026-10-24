"""``scenecap diff`` 命令行入口。

用法：

    scenecap diff <基线目录> <候选目录> [-o report.md] [阈值选项]
    scenecap diff --baseline <基线目录> <候选目录> [-o report.md]
    scenecap diff --baseline <基线目录> <候选目录> --update-baseline

退出码（与 t5 的整合脚本约定一致）：

* ``0`` 通过；或基线模式下成功写入/更新基线；
* ``1`` 渲染回归失败（存在不满足阈值的 pass）；
* ``2`` 输入/用法错误（目录缺失、schema 不符、基线损坏等）——此时不产生
  “回归通过/失败”的结论，避免把“工具用错”误报成“渲染正常”。
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from . import __version__
from .baseline import (
    baseline_state,
    load_baseline_replay,
    verify_baseline,
    write_baseline,
)
from .metrics import InputError, Thresholds
from .report import (
    render_baseline_report,
    render_json,
    render_report,
    write_report,
)
from .results import DiffResult, compare_dirs, compare_dirs_parallel

__all__ = ["main", "build_parser"]

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INPUT_ERROR = 2

DEFAULT_REPORT = "scenecap-diff-report.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scenecap diff",
        description=(
            "逐 pass 比较两次 scenecap 重放输出（index.json + pass_*.png），"
            "给出像素级差异、判定与可复核的中文 markdown 报告。"
        ),
        epilog=(
            "示例：scenecap diff out/base out/cand -o report.md；"
            "scenecap diff --baseline out/baseline out/cand -o report.md"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="目录",
        help="普通模式：<基线目录> <候选目录>；基线模式（--baseline）：仅 <候选目录>",
    )
    parser.add_argument(
        "-o",
        "--report",
        metavar="PATH",
        default=None,
        help=f"markdown 报告输出路径（默认 {DEFAULT_REPORT}）",
    )
    parser.add_argument(
        "--max-channel-delta",
        type=float,
        default=2.0,
        metavar="N",
        help="每通道最大误差阈值，单位为 8-bit 量化单位（默认 2）",
    )
    parser.add_argument(
        "--max-diff-pixel-ratio",
        type=float,
        default=0.001,
        metavar="P",
        help="差异像素比例阈值，0..1（默认 0.001，即 0.1%%）",
    )
    parser.add_argument(
        "--diff-dir",
        metavar="DIR",
        default=None,
        help="差异图输出目录（默认与报告同级的 <报告名>.diff/）",
    )
    parser.add_argument(
        "--no-diff-images",
        action="store_true",
        help="不生成差异图（只产出度量与报告）",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=0,
        metavar="N",
        help="并行进程数（默认按 CPU 核数；1 表示不用并行）",
    )
    parser.add_argument(
        "--all-diffs",
        action="store_true",
        help="对每个 pass 都输出差异图（默认只对失败的 pass 输出，避免干净的回归跑出成千上万张白图）",
    )
    parser.add_argument(
        "--baseline",
        metavar="DIR",
        default=None,
        help="基线模式：目录无基线时写入，有基线时做回归判定",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="配合 --baseline：覆盖已有基线",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="不写机器可读 JSON（默认在报告旁写同名 .json）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只在失败时打印摘要",
    )
    parser.add_argument("--version", action="version", version=f"scenecap-compare {__version__}")
    return parser


def _report_path(args) -> Path:
    return Path(args.report) if args.report else Path(DEFAULT_REPORT)


def _json_path(report_path: Path) -> Path:
    return report_path.with_suffix(".json")


def _diff_dir(args, report_path: Path) -> Path | None:
    if args.no_diff_images:
        return None
    if args.diff_dir:
        return Path(args.diff_dir)
    stem = report_path.stem if report_path.suffix else report_path.name
    return report_path.parent / f"{stem}.diff"


def _emit(command: str, result: DiffResult, args, stdout) -> int:
    report_path = _report_path(args)
    generated = render_report(
        result,
        command=command,
        tool_version=__version__,
        report_path=report_path,
    )
    write_report(report_path, generated)
    if not args.no_json:
        json_path = _json_path(report_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(render_json(result), encoding="utf-8")
    totals = result.totals()
    if result.passed:
        if not args.quiet:
            print(
                f"✅ 通过：{totals['passes']} 个 pass 全部满足阈值"
                f"（{result.thresholds.describe()}）",
                file=stdout,
            )
            print(f"   报告：{report_path}", file=stdout)
        return EXIT_OK
    first = result.first_failure
    print(
        f"❌ 失败：{totals['failed']}/{totals['passes']} 个 pass 未通过；"
        f"首个失败 pass #{first.index} {first.slug}",
        file=stdout,
    )
    for reason in first.reasons:
        print(f"   - {reason}", file=stdout)
    print(f"   报告：{report_path}", file=stdout)
    return EXIT_REGRESSION


def _normal_mode(args, command: str, stdout) -> int:
    if len(args.paths) != 2:
        raise InputError(
            "普通模式需要两个目录参数：<基线目录> <候选目录>（基线模式请加 --baseline）"
        )
    thresholds = Thresholds(args.max_channel_delta, args.max_diff_pixel_ratio)
    report_path = _report_path(args)
    result = compare_dirs_parallel(
        args.paths[0],
        args.paths[1],
        thresholds,
        diff_dir=_diff_dir(args, report_path),
        write_diff_images=not args.no_diff_images,
        all_diffs=args.all_diffs,
        jobs=args.jobs,
    )
    return _emit(command, result, args, stdout)


def _guard_outputs_outside(baseline_dir: Path, outputs: list[Path | None]) -> None:
    """基线目录必须自包含：报告/JSON/差异图写进去会被清单校验判为篡改。"""
    base = Path(baseline_dir).resolve()
    for path in outputs:
        if path is None:
            continue
        resolved = Path(path).resolve()
        if resolved == base or base in resolved.parents:
            raise InputError(
                f"输出路径 {path} 位于基线目录 {baseline_dir} 内；"
                "基线目录需保持自包含（清单会校验文件），请把报告/JSON/差异图写到基线目录之外"
            )


def _baseline_mode(args, command: str, stdout) -> int:
    if len(args.paths) != 1:
        raise InputError(
            "基线模式需要一个候选目录参数：--baseline <基线目录> <候选目录>"
        )
    candidate = args.paths[0]
    baseline_dir = Path(args.baseline)
    report_path = _report_path(args)
    _guard_outputs_outside(
        baseline_dir,
        [report_path, _json_path(report_path), _diff_dir(args, report_path)],
    )
    state = baseline_state(baseline_dir)
    if state in ("missing", "empty"):
        if args.update_baseline:
            raise InputError(f"{baseline_dir} 尚无基线，--update-baseline 无从更新")
        marker = write_baseline(baseline_dir, _replay_for_baseline(candidate), tool_version=__version__)
        text = render_baseline_report(
            baseline_dir=baseline_dir,
            replay_dir=Path(candidate),
            command=command,
            tool_version=__version__,
            pass_count=len(marker["snapshot"]) - 1,
        )
        write_report(report_path, text)
        if not args.quiet:
            print(f"📌 已写入基线：{baseline_dir}（{len(marker['snapshot']) - 1} 个 pass）", file=stdout)
            print(f"   报告：{report_path}", file=stdout)
        return EXIT_OK
    if state == "occupied":
        raise InputError(
            f"目录 {baseline_dir} 非空且不是基线目录；请换一个空目录作为基线位置"
        )
    # state == "baseline"
    if args.update_baseline:
        marker = write_baseline(
            baseline_dir, _replay_for_baseline(candidate), update=True, tool_version=__version__
        )
        text = render_baseline_report(
            baseline_dir=baseline_dir,
            replay_dir=Path(candidate),
            command=command,
            tool_version=__version__,
            pass_count=len(marker["snapshot"]) - 1,
            updated=True,
        )
        write_report(report_path, text)
        if not args.quiet:
            print(f"📌 已更新基线：{baseline_dir}（{len(marker['snapshot']) - 1} 个 pass）", file=stdout)
            print(f"   报告：{report_path}", file=stdout)
        return EXIT_OK
    verify_baseline(baseline_dir)
    thresholds = Thresholds(args.max_channel_delta, args.max_diff_pixel_ratio)
    result = compare_dirs_parallel(
        load_baseline_replay(baseline_dir),
        candidate,
        thresholds,
        diff_dir=_diff_dir(args, report_path),
        write_diff_images=not args.no_diff_images,
        jobs=args.jobs,
    )
    return _emit(command, result, args, stdout)


def _replay_for_baseline(candidate):
    from .replay_index import load_replay_dir

    return load_replay_dir(candidate)


def main(argv: list[str] | None = None, *, stdout=None, stderr=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if argv and argv[0] == "diff":
        argv = argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)
    command = "scenecap diff " + shlex.join(argv)
    try:
        if args.baseline:
            return _baseline_mode(args, command, stdout)
        return _normal_mode(args, command, stdout)
    except InputError as exc:
        print(f"错误：{exc}", file=stderr)
        return EXIT_INPUT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
