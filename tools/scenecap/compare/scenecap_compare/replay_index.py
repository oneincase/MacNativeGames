"""读取并校验 scenecap 重放输出目录（t3 契约：index.json + pass_<NNN>_<slug>.png）。

契约（与 tools/scenecap/replay 严格一致）：

    <dir>/index.json
    <dir>/pass_000_<slug>.png
    {
      "schema": "scenecap.replay/1",
      "package": {"path", "sha256", "captured_frame", "game", "build"},
      "device": {"name", "metal_version"},
      "passes": [{"index": 0, "kind": "render", "slug": "main",
                  "width": 1920, "height": 1080, "pixel_format": "RGBA8Unorm",
                  "encoding": "srgb", "png": "pass_000_main.png",
                  "draws": 12, "status": "ok"}],
      "unsupported": [{"pass": 3, "reason": "compute"}],
      "errors": []
    }

校验失败的输入一律抛 `InputError`，由 CLI 以退出码 2 报告（这是“输入不可用”，
不是“渲染回归”）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import InputError, normalize_encoding

__all__ = ["REPLAY_SCHEMA", "PassEntry", "ReplayDir", "load_replay_dir"]

REPLAY_SCHEMA = "scenecap.replay/1"


@dataclass(frozen=True)
class PassEntry:
    """index.json 中的一个 pass 条目。"""

    index: int
    slug: str
    png: str
    status: str
    encoding: str
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    kind: str | None = None
    draws: int | None = None
    raw: dict = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class ReplayDir:
    """一个重放输出目录的解析结果。"""

    path: Path
    index: dict
    passes: dict[int, PassEntry]
    unsupported: tuple[dict, ...]
    errors: tuple[dict, ...]

    @property
    def schema(self) -> str:
        return self.index.get("schema", "")

    def png_path(self, entry: PassEntry) -> Path:
        """pass 的 PNG 绝对路径，并确保没有跳出目录（防目录穿越）。"""
        candidate = (self.path / entry.png).resolve()
        root = self.path.resolve()
        if candidate != root and root not in candidate.parents:
            raise InputError(
                f"index.json 中 pass #{entry.index} 的 png 路径越出目录：{entry.png!r}"
            )
        return candidate


def _require_object(value, what: str) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{what} 必须是 JSON 对象，实际为 {type(value).__name__}")
    return value


def _optional_positive_int(raw: dict, key: str, where: str) -> int | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"{where} 的 {key} 必须是整数，实际为 {value!r}")
    if value <= 0:
        raise InputError(f"{where} 的 {key} 必须为正，实际为 {value}")
    return value


def _optional_str(raw: dict, key: str, where: str) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{where} 的 {key} 必须是非空字符串，实际为 {value!r}")
    return value


def _parse_pass(raw, position: int, source: Path) -> PassEntry:
    if not isinstance(raw, dict):
        raise InputError(f"{source} 的 passes[{position}] 必须是对象，实际为 {type(raw).__name__}")
    where = f"{source.name} 的 passes[{position}]"
    index = raw.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise InputError(f"{where} 缺少整数 index（实际 {index!r}）")
    if index < 0:
        raise InputError(f"{where} 的 index 不能为负：{index}")
    slug = raw.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise InputError(f"{where} 缺少非空 slug")
    png = raw.get("png")
    if not isinstance(png, str) or not png.strip():
        raise InputError(f"{where} 缺少非空 png 文件名")
    status = raw.get("status", "ok")
    if not isinstance(status, str) or not status.strip():
        raise InputError(f"{where} 的 status 必须是非空字符串")
    encoding = normalize_encoding(raw.get("encoding", "srgb"))
    return PassEntry(
        index=index,
        slug=slug.strip(),
        png=png.strip(),
        status=status.strip(),
        encoding=encoding,
        width=_optional_positive_int(raw, "width", where),
        height=_optional_positive_int(raw, "height", where),
        pixel_format=_optional_str(raw, "pixel_format", where),
        kind=_optional_str(raw, "kind", where),
        draws=raw.get("draws") if isinstance(raw.get("draws"), int) else None,
        raw=raw,
    )


def load_replay_dir(path) -> ReplayDir:
    """读取目录下的 index.json 并做契约校验。"""
    directory = Path(path)
    if not directory.exists():
        raise InputError(f"目录不存在：{directory}")
    if not directory.is_dir():
        raise InputError(f"不是目录：{directory}")
    index_path = directory / "index.json"
    if not index_path.is_file():
        raise InputError(f"{directory} 下缺少 index.json（不是 scenecap 重放输出目录）")
    try:
        text = index_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"无法读取 {index_path}：{exc}") from exc
    try:
        index = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{index_path} 不是合法 JSON：{exc}") from exc
    index = _require_object(index, str(index_path))
    schema = index.get("schema")
    if schema != REPLAY_SCHEMA:
        raise InputError(
            f"{index_path} 的 schema 为 {schema!r}，本工具只接受 {REPLAY_SCHEMA!r}"
        )
    raw_passes = index.get("passes")
    if not isinstance(raw_passes, list):
        raise InputError(f"{index_path} 缺少 passes 数组")
    passes: dict[int, PassEntry] = {}
    for position, raw in enumerate(raw_passes):
        entry = _parse_pass(raw, position, index_path)
        if entry.index in passes:
            raise InputError(f"{index_path} 中 pass #{entry.index} 重复出现")
        passes[entry.index] = entry
    unsupported = index.get("unsupported", [])
    if not isinstance(unsupported, list):
        raise InputError(f"{index_path} 的 unsupported 必须是数组")
    errors = index.get("errors", [])
    if not isinstance(errors, list):
        raise InputError(f"{index_path} 的 errors 必须是数组")
    return ReplayDir(
        path=directory,
        index=index,
        passes=passes,
        unsupported=tuple(_require_object(item, f"{index_path} 的 unsupported 项") for item in unsupported),
        errors=tuple(_require_object(item, f"{index_path} 的 errors 项") for item in errors),
    )
