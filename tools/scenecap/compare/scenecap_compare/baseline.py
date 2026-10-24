"""基线模式：把一个重放输出目录固化成可比对的基线快照，并校验其完整性。

基线目录布局（自包含、可提交进 git）：

    <baseline>/baseline.json      # 清单：schema、来源、每个文件的 SHA-256
    <baseline>/index.json         # 来源 index.json 的副本
    <baseline>/pass_000_main.png  # 来源中被引用的 PNG 副本
    ...

为什么快照而不是原地引用：重放产物常被覆盖（同一目录反复重跑），
基线必须是一份不可变的参照物；同时记录 SHA-256 让“基线被改动”可被发现。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .metrics import InputError
from .replay_index import ReplayDir, load_replay_dir

__all__ = [
    "BASELINE_SCHEMA",
    "BASELINE_MARKER",
    "load_baseline",
    "verify_baseline",
    "write_baseline",
    "baseline_exists",
    "baseline_state",
]

BASELINE_SCHEMA = "scenecap.baseline/1"
BASELINE_MARKER = "baseline.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _marker_path(directory: Path) -> Path:
    return directory / BASELINE_MARKER


def baseline_exists(directory) -> bool:
    return _marker_path(Path(directory)).is_file()


def baseline_state(directory) -> str:
    """描述目录状态：``missing`` / ``empty`` / ``baseline`` / ``occupied``。"""
    directory = Path(directory)
    if not directory.exists():
        return "missing"
    if not directory.is_dir():
        raise InputError(f"基线路径已存在但不是目录：{directory}")
    if baseline_exists(directory):
        return "baseline"
    entries = list(directory.iterdir())
    return "occupied" if entries else "empty"


def load_baseline(directory) -> dict | None:
    """读取基线清单；不存在返回 ``None``，格式错误抛 ``InputError``。"""
    directory = Path(directory)
    path = _marker_path(directory)
    if not path.is_file():
        return None
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"基线清单 {path} 无法读取或不是合法 JSON：{exc}") from exc
    if not isinstance(marker, dict):
        raise InputError(f"基线清单 {path} 必须是 JSON 对象")
    if marker.get("schema") != BASELINE_SCHEMA:
        raise InputError(
            f"基线清单 {path} 的 schema 为 {marker.get('schema')!r}，"
            f"本工具只接受 {BASELINE_SCHEMA!r}"
        )
    snapshot = marker.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise InputError(f"基线清单 {path} 缺少非空 snapshot 映射")
    for name, digest in snapshot.items():
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise InputError(f"基线清单 {path} 含非法文件名 {name!r}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise InputError(f"基线清单 {path} 中 {name} 的 sha256 非法：{digest!r}")
    if "index.json" not in snapshot:
        raise InputError(f"基线清单 {path} 未记录 index.json")
    return marker


def verify_baseline(directory) -> dict:
    """校验基线快照文件与清单哈希一致；不一致抛 ``InputError``（退出码 2）。"""
    directory = Path(directory)
    marker = load_baseline(directory)
    if marker is None:
        raise InputError(f"{directory} 下没有 {BASELINE_MARKER}，不是基线目录")
    snapshot = marker["snapshot"]
    for name, expected in snapshot.items():
        path = directory / name
        if not path.is_file():
            raise InputError(f"基线快照缺少文件 {name}（{path}），基线不完整")
        actual = _sha256(path)
        if actual != expected:
            raise InputError(
                f"基线快照文件 {name} 的 SHA-256 与清单不符（期望 {expected[:12]}…，"
                f"实际 {actual[:12]}…），基线已被修改"
            )
    unexpected = [
        child.name
        for child in directory.iterdir()
        if child.is_file() and child.name not in snapshot and child.name != BASELINE_MARKER
    ]
    if unexpected:
        raise InputError(
            f"基线目录 {directory} 含清单外的文件：{', '.join(sorted(unexpected))}"
        )
    return marker


def write_baseline(
    directory,
    replay: ReplayDir,
    *,
    update: bool = False,
    tool_version: str = "",
) -> dict:
    """把 ``replay`` 快照为基线。返回新清单。"""
    directory = Path(directory)
    state = baseline_state(directory)
    if state == "occupied" and not update:
        raise InputError(
            f"目录 {directory} 非空且不是基线目录；请换一个空目录，或确认后用 --update-baseline"
        )
    if state == "baseline" and not update:
        raise InputError(
            f"基线已存在：{directory}。如需覆盖，请显式加 --update-baseline"
        )
    directory.mkdir(parents=True, exist_ok=True)

    # 先校验来源完整：index.json 引用的 PNG 必须都在。
    sources: dict[str, Path] = {}
    if not replay.passes:
        raise InputError(f"来源目录 {replay.path} 的 index.json 没有任何 pass")
    for index, entry in sorted(replay.passes.items()):
        png = replay.png_path(entry)
        if not png.is_file():
            raise InputError(
                f"来源目录 {replay.path} 缺少 pass #{index} 的 PNG：{entry.png}"
            )
        sources[entry.png] = png
    sources["index.json"] = replay.path / "index.json"

    old_marker = load_baseline(directory) if state == "baseline" else None
    old_snapshot = old_marker["snapshot"] if old_marker else {}

    snapshot: dict[str, str] = {}
    for name, source in sorted(sources.items()):
        target = directory / name
        if target.resolve() != source.resolve():
            shutil.copyfile(source, target)
        snapshot[name] = _sha256(target)

    # 清理上一版基线中不再需要的文件（仅限清单里列出的名字，绝不递归删除）。
    for stale in sorted(set(old_snapshot) - set(snapshot)):
        stale_path = directory / stale
        if stale_path.is_file():
            stale_path.unlink()

    marker = {
        "schema": BASELINE_SCHEMA,
        "tool_version": tool_version,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": str(replay.path),
        "updated": bool(update and state == "baseline"),
        "snapshot": snapshot,
    }
    marker_path = _marker_path(directory)
    temporary = marker_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(marker_path)
    return marker


def load_baseline_replay(directory) -> ReplayDir:
    """校验完整性后把基线目录当作普通重放目录加载。"""
    verify_baseline(directory)
    return load_replay_dir(directory)
