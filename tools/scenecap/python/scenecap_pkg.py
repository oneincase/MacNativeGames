"""scenecap .nvs package reader, writer and verifier.

The byte layout is specified in ``tools/scenecap/format/nvs_format.h`` and documented in
``tools/scenecap/README.md``; the C writer/reader in ``tools/scenecap/format/`` and this module must stay in sync.
Only the Python standard library is used (``compression.zstd`` for the frames).
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass, field

try:  # Python 3.14+
    import compression.zstd as _zstd
except ImportError:  # pragma: no cover - exercised only on old interpreters
    _zstd = None

MAGIC = b"SCENECAP"
MAJOR = 1
MINOR = 0
VERSION = f"{MAJOR}.{MINOR}"

HEADER_SIZE = 128
CHUNK_ENTRY_SIZE = 128
CHUNK_ID_MAX = 64
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_BLOB_BYTES = 16 * 1024 * 1024 * 1024

HEADER_FLAG_MANIFEST_ZSTD = 0x1
HEADER_FLAG_CHUNKS_ZSTD = 0x2
CHUNK_FLAG_ZSTD = 0x1
BLOB_STORE_RAW = 0x1

HEADER_STRUCT = struct.Struct("<8sHHIIIIQQQQQ32s28s")
CHUNK_STRUCT = struct.Struct("<64sIIQQQ32s")

KINDS = {1: "json", 2: "msl", 3: "reflection", 4: "texture", 5: "buffer", 6: "binary"}
KIND_IDS = {name: number for number, name in KINDS.items()}

_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-")


class PackageError(Exception):
    """A package is corrupt, inconsistent or uses a feature this reader does not implement."""

    def __init__(self, message: str, code: str = "format", chunk: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code  # "format" | "unsupported" | "io" | "argument"
        self.chunk = chunk

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def _zstd_compress(data: bytes, level: int = 3) -> bytes:
    if _zstd is None:  # pragma: no cover
        raise PackageError("this Python lacks compression.zstd (need CPython 3.14 or a zstd module)", "unsupported")
    return _zstd.compress(data, level=level)


def _zstd_decompress(data: bytes, raw_size: int, chunk_id: str) -> bytes:
    """Decode one frame, refusing anything that does not expand to exactly raw_size bytes."""
    if _zstd is None:  # pragma: no cover
        raise PackageError("this Python lacks compression.zstd, cannot decode zstd frames", "unsupported", chunk_id)
    try:
        decoder = _zstd.ZstdDecompressor()
        try:
            raw = decoder.decompress(data, max_length=raw_size + 1)
        except TypeError:  # pragma: no cover - older signature without max_length
            raw = _zstd.decompress(data)
        if getattr(decoder, "unused_data", b""):
            raise PackageError(f"chunk {chunk_id!r}: trailing bytes after the zstd frame", "format", chunk_id)
    except PackageError:
        raise
    except Exception as exc:  # noqa: BLE001 - the message is what matters
        raise PackageError(f"chunk {chunk_id!r}: zstd decode failed: {exc}", "format", chunk_id) from exc
    if len(raw) != raw_size:
        raise PackageError(
            f"chunk {chunk_id!r}: frame expands to {len(raw)} bytes, the directory records {raw_size}",
            "format",
            chunk_id,
        )
    return raw


def _check_id(chunk_id: object) -> str:
    if not isinstance(chunk_id, str) or not chunk_id:
        raise PackageError(f"invalid chunk id {chunk_id!r}", "argument")
    encoded = chunk_id.encode("ascii", "strict") if chunk_id.isascii() else b""
    if not encoded or len(encoded) >= CHUNK_ID_MAX or not _ID_CHARS.issuperset(chunk_id):
        raise PackageError(
            f"invalid chunk id {chunk_id!r}: 1..63 bytes of [A-Za-z0-9._:/-]", "argument"
        )
    return chunk_id


def _check_kind(kind: object) -> int:
    if isinstance(kind, str):
        if kind not in KIND_IDS:
            raise PackageError(f"unknown chunk kind {kind!r}", "argument")
        return KIND_IDS[kind]
    if isinstance(kind, int) and kind in KINDS:
        return kind
    raise PackageError(f"unknown chunk kind {kind!r}", "argument")


@dataclass(frozen=True)
class Chunk:
    id: str
    kind: str
    flags: int
    offset: int
    stored_size: int
    raw_size: int
    sha256: bytes

    @property
    def compressed(self) -> bool:
        return bool(self.flags & CHUNK_FLAG_ZSTD)

    @property
    def kind_id(self) -> int:
        return KIND_IDS[self.kind]

    def sha256_hex(self) -> str:
        return self.sha256.hex()


@dataclass
class Blob:
    """One blob to store in a package."""

    id: str
    kind: str
    data: bytes
    flags: int = 0


@dataclass
class Package:
    path: str
    major: int
    minor: int
    flags: int
    chunk_table_offset: int
    manifest_offset: int
    manifest_stored_size: int
    manifest_raw_size: int
    file_size: int
    manifest_sha256: bytes
    chunks: list[Chunk]
    envelope: dict
    manifest: dict
    _payload: bytes = field(repr=False, default=b"")
    _cache: dict[str, bytes] = field(repr=False, default_factory=dict)

    def __enter__(self) -> "Package":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def chunk(self, chunk_id: str) -> Chunk:
        for chunk in self.chunks:
            if chunk.id == chunk_id:
                return chunk
        raise PackageError(f"no chunk named {chunk_id!r}", "format", chunk_id)

    def find(self, chunk_id: str) -> Chunk | None:
        for chunk in self.chunks:
            if chunk.id == chunk_id:
                return chunk
        return None

    def payload(self, chunk: Chunk | str) -> bytes:
        """Return the decompressed bytes of one chunk (hash-checked first)."""
        if isinstance(chunk, str):
            chunk = self.chunk(chunk)
        cached = self._cache.get(chunk.id)
        if cached is not None:
            return cached
        stored = self._payload[chunk.offset : chunk.offset + chunk.stored_size]
        digest = hashlib.sha256(stored).digest()
        if digest != chunk.sha256:
            raise PackageError(
                f"chunk {chunk.id!r} hash mismatch: directory records {chunk.sha256.hex()}, "
                f"payload hashes to {digest.hex()}",
                "format",
                chunk.id,
            )
        if chunk.compressed:
            raw = _zstd_decompress(stored, chunk.raw_size, chunk.id)
        else:
            raw = stored
        if len(raw) != chunk.raw_size:
            raise PackageError(
                f"chunk {chunk.id!r}: decoded {len(raw)} bytes, directory records {chunk.raw_size}",
                "format",
                chunk.id,
            )
        self._cache[chunk.id] = raw
        return raw

    def manifest_text(self) -> str:
        return json.dumps(self.manifest, indent=2, ensure_ascii=False) + "\n"

    def close(self) -> None:
        self._payload = b""
        self._cache.clear()


# ------------------------------------------------------------------------------------------------------------------
# reading
# ------------------------------------------------------------------------------------------------------------------


def _read_header(data: bytes) -> dict:
    if len(data) < HEADER_SIZE:
        raise PackageError(f"file is {len(data)} bytes, shorter than the {HEADER_SIZE}-byte header")
    (
        magic,
        major,
        minor,
        header_size,
        flags,
        chunk_count,
        entry_size,
        table_offset,
        manifest_offset,
        manifest_stored,
        manifest_raw,
        file_size,
        manifest_sha,
        _reserved,
    ) = HEADER_STRUCT.unpack_from(data, 0)
    if magic != MAGIC:
        raise PackageError(f"bad magic {magic!r} (expected {MAGIC!r})")
    if major != MAJOR:
        raise PackageError(
            f"format {major}.{minor} is not supported by this reader (it reads {VERSION}); "
            "the package was probably written by a newer scenecap",
            "unsupported",
        )
    if minor > MINOR:
        raise PackageError(
            f"format {major}.{minor} is newer than this reader ({VERSION}); upgrade scenecap", "unsupported"
        )
    if header_size != HEADER_SIZE:
        raise PackageError(f"header size {header_size} is not supported (expected {HEADER_SIZE})", "unsupported")
    if flags & ~(HEADER_FLAG_MANIFEST_ZSTD | HEADER_FLAG_CHUNKS_ZSTD):
        raise PackageError(f"unknown header flag bits 0x{flags:08x}")
    if any(_reserved):
        raise PackageError("the reserved header bytes are not zero")
    if entry_size != CHUNK_ENTRY_SIZE:
        raise PackageError(f"chunk entry size {entry_size} is not supported (expected {CHUNK_ENTRY_SIZE})", "unsupported")
    if file_size != len(data):
        raise PackageError(f"header records {file_size} bytes but the file is {len(data)} bytes")
    if table_offset < HEADER_SIZE or table_offset > len(data):
        raise PackageError(f"chunk table offset {table_offset} is outside the file")
    table_end = table_offset + chunk_count * CHUNK_ENTRY_SIZE
    if table_end > len(data):
        raise PackageError(f"chunk table ({chunk_count} entries) does not fit in the file")
    if manifest_offset < table_end or manifest_offset > len(data) or manifest_offset + manifest_stored > len(data):
        raise PackageError(
            f"manifest region ({manifest_offset} + {manifest_stored}) is outside the file or overlaps the table"
        )
    if manifest_raw > MAX_MANIFEST_BYTES:
        raise PackageError(f"manifest declares {manifest_raw} bytes, the limit is {MAX_MANIFEST_BYTES}", "unsupported")
    return {
        "major": major,
        "minor": minor,
        "header_size": header_size,
        "flags": flags,
        "chunk_count": chunk_count,
        "table_offset": table_offset,
        "table_end": table_end,
        "manifest_offset": manifest_offset,
        "manifest_stored": manifest_stored,
        "manifest_raw": manifest_raw,
        "file_size": file_size,
        "manifest_sha256": manifest_sha,
    }


def _read_chunks(data: bytes, header: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    seen: set[str] = set()
    for index in range(header["chunk_count"]):
        base = header["table_offset"] + index * CHUNK_ENTRY_SIZE
        raw_id, kind_id, flags, offset, stored_size, raw_size, sha = CHUNK_STRUCT.unpack_from(data, base)
        name = raw_id.split(b"\0", 1)[0]
        if not name or raw_id[: len(name)] != name or raw_id[len(name) :].strip(b"\0"):
            raise PackageError(f"chunk {index}: directory id is not NUL-terminated text")
        try:
            chunk_id = name.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PackageError(f"chunk {index}: id is not ASCII") from exc
        _check_id(chunk_id)
        if chunk_id in seen:
            raise PackageError(f"chunk id {chunk_id!r} appears twice in the directory", chunk=chunk_id)
        seen.add(chunk_id)
        if kind_id not in KINDS:
            raise PackageError(f"chunk {chunk_id!r}: unknown kind {kind_id}", chunk=chunk_id)
        if offset < HEADER_SIZE or offset > len(data) or offset + stored_size > len(data):
            raise PackageError(
                f"chunk {chunk_id!r} payload ({offset} + {stored_size}) is outside the file", chunk=chunk_id
            )
        if raw_size > MAX_BLOB_BYTES:
            raise PackageError(f"chunk {chunk_id!r} declares {raw_size} raw bytes", "unsupported", chunk_id)
        if not flags & CHUNK_FLAG_ZSTD and stored_size != raw_size:
            raise PackageError(
                f"chunk {chunk_id!r} is stored verbatim but its sizes differ ({stored_size} vs {raw_size})",
                chunk=chunk_id,
            )
        if offset % 8:
            raise PackageError(f"chunk {chunk_id!r} payload starts at {offset}, not on an 8-byte boundary", chunk=chunk_id)
        chunks.append(Chunk(chunk_id, KINDS[kind_id], flags, offset, stored_size, raw_size, sha))
    return chunks


def _check_layout(header: dict, chunks: list[Chunk]) -> None:
    """The writer lays blocks out back to back with at most 7 bytes of zero padding, in table order."""
    position = header["manifest_offset"] + header["manifest_stored"]
    for chunk in chunks:
        padding = chunk.offset - position
        if padding < 0:
            raise PackageError(
                f"chunk {chunk.id!r} at {chunk.offset} overlaps the previous block ending at {position}",
                chunk=chunk.id,
            )
        if padding > 7:
            raise PackageError(
                f"chunk {chunk.id!r} starts {padding} bytes after the previous block (expected <= 7 bytes of padding)",
                chunk=chunk.id,
            )
        position = chunk.offset + chunk.stored_size
    if position != header["file_size"]:
        raise PackageError(f"{header['file_size'] - position} trailing byte(s) after the last chunk")


def _check_envelope(header: dict, chunks: list[Chunk], envelope: object) -> dict:
    if not isinstance(envelope, dict):
        raise PackageError("manifest payload is not a JSON object")
    nvs = envelope.get("nvs")
    if not isinstance(nvs, dict):
        raise PackageError('manifest payload has no "nvs" envelope object')
    if nvs.get("format") != "scenecap":
        raise PackageError(f'envelope format is {nvs.get("format")!r}, expected "scenecap"')
    if nvs.get("version") != f"{header['major']}.{header['minor']}":
        raise PackageError(
            f"envelope version {nvs.get('version')!r} disagrees with the header ({header['major']}.{header['minor']})"
        )
    if not isinstance(nvs.get("generator"), str):
        raise PackageError("envelope has no generator string")
    if nvs.get("chunk_count") != len(chunks):
        raise PackageError(
            f"envelope lists {nvs.get('chunk_count')!r} chunks, the directory has {len(chunks)}"
        )
    if nvs.get("manifest_bytes") != header["manifest_raw"]:
        raise PackageError(
            f"envelope manifest_bytes {nvs.get('manifest_bytes')!r} disagrees with the header "
            f"({header['manifest_raw']})"
        )
    listed = envelope.get("chunks")
    if not isinstance(listed, list) or len(listed) != len(chunks):
        raise PackageError("envelope chunk list is missing or has the wrong length")
    for index, (entry, chunk) in enumerate(zip(listed, chunks)):
        where = f"envelope chunks[{index}]"
        if not isinstance(entry, dict):
            raise PackageError(f"{where} is not an object")
        if entry.get("id") != chunk.id:
            raise PackageError(f"{where}: id {entry.get('id')!r} disagrees with the directory ({chunk.id!r})")
        if entry.get("kind") != chunk.kind:
            raise PackageError(f"{where}: kind {entry.get('kind')!r} disagrees with the directory ({chunk.kind!r})")
        if entry.get("flags") != chunk.flags:
            raise PackageError(f"{where}: flags {entry.get('flags')!r} disagree with the directory ({chunk.flags})")
        if entry.get("compression") != ("zstd" if chunk.compressed else "none"):
            raise PackageError(f"{where}: compression {entry.get('compression')!r} disagrees with the directory")
        if entry.get("stored_bytes") != chunk.stored_size or entry.get("raw_bytes") != chunk.raw_size:
            raise PackageError(f"{where}: sizes disagree with the directory")
        if entry.get("sha256") != chunk.sha256.hex():
            raise PackageError(f"{where}: sha256 disagrees with the directory")
    manifest = envelope.get("manifest")
    if not isinstance(manifest, dict):
        raise PackageError('envelope has no "manifest" object')
    return manifest


def read_package(path: str) -> Package:
    """Read, structurally validate and hash-check a package. Raises PackageError."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise PackageError(f"cannot read {path}: {exc}", "io") from exc
    header = _read_header(data)
    chunks = _read_chunks(data, header)
    _check_layout(header, chunks)

    stored = data[header["manifest_offset"] : header["manifest_offset"] + header["manifest_stored"]]
    digest = hashlib.sha256(stored).digest()
    if digest != header["manifest_sha256"]:
        raise PackageError(
            f"manifest hash mismatch: header records {header['manifest_sha256'].hex()}, payload hashes to {digest.hex()}"
        )
    if header["flags"] & HEADER_FLAG_MANIFEST_ZSTD:
        raw = _zstd_decompress(stored, header["manifest_raw"], "manifest")
    else:
        raw = stored
    if len(raw) != header["manifest_raw"]:
        raise PackageError(
            f"manifest decoded to {len(raw)} bytes, the header records {header['manifest_raw']}"
        )
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"manifest payload is not UTF-8 JSON: {exc}") from exc
    manifest = _check_envelope(header, chunks, envelope)
    package = Package(
        path=path,
        major=header["major"],
        minor=header["minor"],
        flags=header["flags"],
        chunk_table_offset=header["table_offset"],
        manifest_offset=header["manifest_offset"],
        manifest_stored_size=header["manifest_stored"],
        manifest_raw_size=header["manifest_raw"],
        file_size=header["file_size"],
        manifest_sha256=header["manifest_sha256"],
        chunks=chunks,
        envelope=envelope,
        manifest=manifest,
        _payload=data,
    )
    for chunk in chunks:  # hash every blob up front: a package that fails here must never be replayed
        package.payload(chunk)
    return package


# ------------------------------------------------------------------------------------------------------------------
# writing
# ------------------------------------------------------------------------------------------------------------------


def _stored_block(data: bytes, level: int, compress: bool) -> tuple[bytes, int]:
    """Return (bytes to store, chunk flags); falls back to verbatim when zstd does not help."""
    if compress and data:
        frame = _zstd_compress(data, level=level)
        if len(frame) < len(data):
            return frame, CHUNK_FLAG_ZSTD
    return data, 0


def write_package(
    path: str,
    manifest: dict | str | bytes,
    blobs: list[Blob] | tuple[Blob, ...] = (),
    *,
    generator: str = "scenecap-python/1.0",
    compress: bool = True,
    level: int = 3,
) -> Package:
    """Write a package atomically, then read it back. The manifest may reference blob ids freely; use verify to
    check the references."""
    if isinstance(manifest, (str, bytes)):
        text = manifest.encode("utf-8") if isinstance(manifest, str) else manifest
        try:
            manifest_obj = json.loads(text.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError(f"manifest is not UTF-8 JSON: {exc}", "argument") from exc
        if not isinstance(manifest_obj, dict):
            raise PackageError("manifest must be a JSON object", "argument")
    elif isinstance(manifest, dict):
        manifest_obj = manifest
    else:
        raise PackageError("manifest must be a dict, str or bytes", "argument")

    stored_blobs: list[tuple[str, int, bytes, int, int, bytes]] = []
    seen: set[str] = set()
    for blob in blobs:
        chunk_id = _check_id(blob.id)
        if chunk_id in seen:
            raise PackageError(f"blob {chunk_id!r} was added twice", "argument")
        seen.add(chunk_id)
        kind_id = _check_kind(blob.kind)
        data = blob.data
        if not isinstance(data, (bytes, bytearray)):
            raise PackageError(f"blob {chunk_id!r} data must be bytes", "argument")
        data = bytes(data)
        if len(data) > MAX_BLOB_BYTES:
            raise PackageError(f"blob {chunk_id!r} is too large", "argument", chunk_id)
        stored, flags = _stored_block(data, level, compress and not blob.flags & BLOB_STORE_RAW)
        stored_blobs.append((chunk_id, kind_id, stored, flags, len(data), hashlib.sha256(stored).digest()))

    envelope = {
        "nvs": {
            "format": "scenecap",
            "version": VERSION,
            "generator": generator,
            "chunk_count": len(stored_blobs),
            "manifest_bytes": 0,  # patched below once the serialised size is known
        },
        "chunks": [
            {
                "id": chunk_id,
                "kind": KINDS[kind_id],
                "flags": flags,
                "compression": "zstd" if flags & CHUNK_FLAG_ZSTD else "none",
                "stored_bytes": len(stored),
                "raw_bytes": raw_size,
                "sha256": sha.hex(),
            }
            for chunk_id, kind_id, stored, flags, raw_size, sha in stored_blobs
        ],
        "manifest": manifest_obj,
    }
    # manifest_bytes is the length of this envelope; its decimal width feeds back into the length.
    def encode_envelope(value: int) -> bytes:
        envelope["nvs"]["manifest_bytes"] = value
        return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    size = len(encode_envelope(0))
    envelope_bytes = b""
    for _ in range(4):
        envelope_bytes = encode_envelope(size)
        if len(envelope_bytes) == size:
            break
        size = len(envelope_bytes)
    else:
        raise PackageError("the envelope length did not settle", "argument")

    manifest_stored, manifest_flags = _stored_block(envelope_bytes, level, compress)
    manifest_sha = hashlib.sha256(manifest_stored).digest()

    table_offset = HEADER_SIZE
    manifest_offset = table_offset + len(stored_blobs) * CHUNK_ENTRY_SIZE
    position = manifest_offset + len(manifest_stored)
    placements: list[tuple[int, str, int, bytes, int, int, bytes]] = []
    for chunk_id, kind_id, stored, flags, raw_size, sha in stored_blobs:
        position = (position + 7) & ~7
        placements.append((position, chunk_id, kind_id, stored, flags, raw_size, sha))
        position += len(stored)
    file_size = position

    flags_header = 0
    if manifest_flags & CHUNK_FLAG_ZSTD:
        flags_header |= HEADER_FLAG_MANIFEST_ZSTD
    if any(flags & CHUNK_FLAG_ZSTD for _, _, _, _, flags, _, _ in placements):
        flags_header |= HEADER_FLAG_CHUNKS_ZSTD

    header = HEADER_STRUCT.pack(
        MAGIC,
        MAJOR,
        MINOR,
        HEADER_SIZE,
        flags_header,
        len(placements),
        CHUNK_ENTRY_SIZE,
        table_offset,
        manifest_offset,
        len(manifest_stored),
        len(envelope_bytes),
        file_size,
        manifest_sha,
        b"\0" * 28,
    )
    table = bytearray()
    for offset, chunk_id, kind_id, stored, flags, raw_size, sha in placements:
        name = chunk_id.encode("ascii")
        table += CHUNK_STRUCT.pack(
            name + b"\0" * (CHUNK_ID_MAX - len(name)),
            kind_id,
            flags,
            offset,
            len(stored),
            raw_size,
            sha,
        )

    body = bytearray()
    body += header
    body += table
    body += manifest_stored
    for offset, _chunk_id, _kind, stored, _flags, _raw, _sha in placements:
        padding = offset - len(body)
        if not 0 <= padding <= 7:
            raise PackageError(f"internal layout error: offset {offset} is {padding} bytes past the body", "argument")
        body += b"\0" * padding
        body += stored
    assert len(body) == file_size, (len(body), file_size)

    directory = os.path.dirname(os.path.abspath(path))
    temporary = f"{path}.tmp.{os.getpid()}"
    try:
        with open(temporary, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise PackageError(f"cannot write {path}: {exc}", "io") from exc
    return read_package(path)


# ------------------------------------------------------------------------------------------------------------------
# manifest contract + verification
# ------------------------------------------------------------------------------------------------------------------

_TEXTURES = {"1d", "2d", "3d", "cube", "2d_array", "cube_array", "2d_multisample"}
_PASS_KINDS = {"render", "compute", "blit"}
_STAGES = {"vertex", "fragment", "kernel", "object", "mesh", "tile"}
_LOADS = {"load", "clear", "dontcare"}
_STORES = {"store", "dontcare"}
_COMPARE = {"never", "less", "equal", "less_equal", "greater", "not_equal", "greater_equal", "always"}
_CULL = {"none", "front", "back"}
_FILL = {"fill", "lines", "points"}
_FRONT = {"cw", "ccw"}
_PRIMITIVES = {"point", "line", "line_strip", "triangle", "triangle_strip"}
_FILTERS = {"nearest", "linear"}
_ADDRESSES = {"clamp_to_edge", "repeat", "mirror_repeat", "clamp_to_zero", "mirror_clamp_to_edge"}
_INDEX_TYPES = {"uint16", "uint32"}
_CAPTURE_STRINGS = (
    "game",
    "title_id",
    "game_version",
    "git_commit",
    "port_version",
    "build_version",
    "gpu",
    "macos",
    "metal",
    "captured_at",
)

MAX_ERRORS = 200


@dataclass
class VerifyReport:
    path: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    chunks: int = 0
    manifest_bytes: int = 0
    capture: dict = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"{'PASS' if self.ok else 'FAIL'} {self.path}"]
        if self.capture:
            capture = self.capture
            lines.append(
                "  capture: {game} {title} {version} frame {first}+{count} ({gpu}, macOS {macos}, Metal {metal})".format(
                    game=capture.get("game", "?"),
                    title=capture.get("title_id", "?"),
                    version=capture.get("game_version", "?"),
                    first=capture.get("frame_first", "?"),
                    count=capture.get("frame_count", "?"),
                    gpu=capture.get("gpu", "?"),
                    macos=capture.get("macos", "?"),
                    metal=capture.get("metal", "?"),
                )
            )
        if self.chunks:
            lines.append(f"  container: {self.chunks} chunk(s), manifest {self.manifest_bytes} bytes")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        for error in self.errors:
            lines.append(f"  error: {error}")
        return "\n".join(lines)


class _Checker:
    def __init__(self, package: Package):
        self.package = package
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.resources: dict[str, dict] = {}
        self.passes: dict[str, dict] = {}
        self.pipelines: dict[str, dict] = {}
        self.draws: dict[str, dict] = {}
        self.frame_map: dict[int, dict] = {}
        self.used_chunks: set[str] = set()
        self.frame_pass_refs: list[tuple[str, str]] = []
        self.pass_frame_refs: list[tuple[str, int]] = []

    def error(self, message: str) -> None:
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(message)
        elif len(self.errors) == MAX_ERRORS:
            self.errors.append(f"... more than {MAX_ERRORS} errors, stopping here")

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    # -- primitives ------------------------------------------------------------------------------------------------
    def need(self, obj: object, where: str, kind: type | tuple[type, ...]) -> bool:
        if not isinstance(obj, kind):
            expected = kind.__name__ if isinstance(kind, type) else "/".join(t.__name__ for t in kind)
            self.error(f"{where}: expected {expected}, got {type(obj).__name__}")
            return False
        return True

    def string(self, obj: dict, key: str, where: str, *, allow_empty: bool = False) -> str | None:
        value = obj.get(key)
        if not isinstance(value, str):
            self.error(f"{where}.{key}: missing or not a string")
            return None
        if not value and not allow_empty:
            self.error(f"{where}.{key}: must not be empty")
            return None
        return value

    def optional_string(self, obj: dict, key: str, where: str) -> str | None:
        """A string field that may be absent: the container is a general archive, not every record needs it."""
        if obj.get(key) is None:
            return None
        return self.string(obj, key, where)

    def integer(self, obj: dict, key: str, where: str, *, minimum: int = 0) -> int | None:
        value = obj.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            self.error(f"{where}.{key}: missing or not an integer")
            return None
        if value < minimum:
            self.error(f"{where}.{key}: {value} is below {minimum}")
            return None
        return value

    def number(self, obj: dict, key: str, where: str) -> float | int | None:
        value = obj.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            self.error(f"{where}.{key}: missing or not a number")
            return None
        return value

    def one_of(self, obj: dict, key: str, where: str, allowed: set[str]) -> str | None:
        value = obj.get(key)
        if value not in allowed:
            self.error(f"{where}.{key}: {value!r} is not one of {sorted(allowed)}")
            return None
        return value

    def string_list(self, obj: dict, key: str, where: str) -> list[str]:
        value = obj.get(key, [])
        if not isinstance(value, list):
            self.error(f"{where}.{key}: expected a list")
            return []
        result = []
        for index, item in enumerate(value):
            if not isinstance(item, str) or not item:
                self.error(f"{where}.{key}[{index}]: expected a non-empty string")
                continue
            result.append(item)
        return result

    def chunk_of(self, chunk_id: str, where: str, kinds: set[str]) -> Chunk | None:
        chunk = self.package.find(chunk_id)
        if chunk is None:
            self.error(f"{where}: no chunk named {chunk_id!r}")
            return None
        if chunk.kind not in kinds:
            self.error(f"{where}: chunk {chunk_id!r} is a {chunk.kind}, expected {'/'.join(sorted(kinds))}")
            return None
        self.used_chunks.add(chunk_id)
        return chunk

    def resource(self, resource_id: str, where: str, kind: str) -> dict | None:
        resource = self.resources.get(resource_id)
        if resource is None:
            self.error(f"{where}: unknown resource {resource_id!r}")
            return None
        if resource.get("type") != kind:
            self.error(f"{where}: resource {resource_id!r} is a {resource.get('type')}, expected {kind}")
            return None
        return resource

    # -- sections --------------------------------------------------------------------------------------------------
    def capture(self, manifest: dict) -> None:
        capture = manifest.get("capture")
        if not self.need(capture, "manifest.capture", dict):
            return
        for key in _CAPTURE_STRINGS:
            self.string(capture, key, "manifest.capture")
        self.integer(capture, "frame_first", "manifest.capture")
        count = self.integer(capture, "frame_count", "manifest.capture", minimum=1)
        if count is None:
            return
        if count > 1_000_000:
            self.error("manifest.capture.frame_count: more than 1,000,000 frames")

    def frames_section(self, manifest: dict) -> None:
        frames = manifest.get("frames")
        if not self.need(frames, "manifest.frames", list):
            return
        if not frames:
            self.error("manifest.frames: at least one frame is required")
            return
        capture = self.package.manifest.get("capture")
        expected_index = capture.get("frame_first", 0) if isinstance(capture, dict) else 0
        if not isinstance(expected_index, int):
            expected_index = 0
        for position, frame in enumerate(frames):
            where = f"manifest.frames[{position}]"
            if not self.need(frame, where, dict):
                continue
            index = self.integer(frame, "index", where)
            if index is not None:
                if index != expected_index:
                    self.error(f"{where}.index: expected {expected_index}, got {index}")
                expected_index = index + 1
                if index in self.frame_map:
                    self.error(f"{where}.index: duplicate frame {index}")
                self.frame_map[index] = frame
            for pass_id in self.string_list(frame, "passes", where):
                self.frame_pass_refs.append((f"{where}.passes", pass_id))
            if "presented" in frame and not isinstance(frame["presented"], bool):
                self.error(f"{where}.presented: expected a boolean")
            drawable = frame.get("drawable")
            if drawable is not None:
                if self.need(drawable, f"{where}.drawable", dict):
                    self.integer(drawable, "width", f"{where}.drawable", minimum=1)
                    self.integer(drawable, "height", f"{where}.drawable", minimum=1)
                    self.string(drawable, "pixel_format", f"{where}.drawable")
        count = capture.get("frame_count") if isinstance(capture, dict) else None
        if isinstance(count, int) and count != len(frames):
            self.error(f"manifest.frames: {len(frames)} frame(s) but capture.frame_count is {count}")

    def resources_section(self, manifest: dict) -> None:
        resources = manifest.get("resources")
        if not self.need(resources, "manifest.resources", list):
            return
        for position, resource in enumerate(resources):
            where = f"manifest.resources[{position}]"
            if not self.need(resource, where, dict):
                continue
            resource_id = self.string(resource, "id", where)
            if resource_id is None:
                continue
            if resource_id in self.resources:
                self.error(f"{where}.id: duplicate resource {resource_id!r}")
                continue
            kind = self.one_of(resource, "type", where, {"texture", "buffer"})
            if kind is None:
                continue
            blob_id = self.string(resource, "blob", where)
            chunk = None
            if blob_id is not None:
                chunk = self.chunk_of(blob_id, f"{where}.blob", {kind})
            byte_range = resource.get("bytes")
            if self.need(byte_range, f"{where}.bytes", dict) and chunk is not None:
                offset = self.integer(byte_range, "offset", f"{where}.bytes")
                size = self.integer(byte_range, "size", f"{where}.bytes")
                if offset is not None and size is not None:
                    if offset + size > chunk.raw_size:
                        self.error(
                            f"{where}.bytes: range {offset}+{size} exceeds the {chunk.raw_size}-byte chunk "
                            f"{blob_id!r}"
                        )
            self.string_list(resource, "usage", where)
            if kind == "texture":
                self.string(resource, "pixel_format", where)
                self.integer(resource, "width", where, minimum=1)
                self.integer(resource, "height", where, minimum=1)
                self.integer(resource, "depth", where, minimum=1)
                mip_levels = self.integer(resource, "mip_levels", where, minimum=1)
                layers = self.integer(resource, "array_layers", where, minimum=1)
                self.integer(resource, "sample_count", where, minimum=1)
                self.one_of(resource, "texture_type", where, _TEXTURES)
                mip_range = resource.get("mip_range")
                if self.need(mip_range, f"{where}.mip_range", dict):
                    first = self.integer(mip_range, "first", f"{where}.mip_range")
                    count = self.integer(mip_range, "count", f"{where}.mip_range", minimum=1)
                    if first is not None and count is not None and mip_levels is not None and first + count > mip_levels:
                        self.error(f"{where}.mip_range: {first}+{count} exceeds mip_levels {mip_levels}")
                layer_range = resource.get("layer_range")
                if self.need(layer_range, f"{where}.layer_range", dict):
                    first = self.integer(layer_range, "first", f"{where}.layer_range")
                    count = self.integer(layer_range, "count", f"{where}.layer_range", minimum=1)
                    if first is not None and count is not None and layers is not None and first + count > layers:
                        self.error(f"{where}.layer_range: {first}+{count} exceeds array_layers {layers}")
            self.resources[resource_id] = resource

    def pipelines_section(self, manifest: dict) -> None:
        pipelines = manifest.get("pipelines")
        if not self.need(pipelines, "manifest.pipelines", list):
            return
        for position, pipeline in enumerate(pipelines):
            where = f"manifest.pipelines[{position}]"
            if not self.need(pipeline, where, dict):
                continue
            pipeline_id = self.string(pipeline, "id", where)
            if pipeline_id is None:
                continue
            if pipeline_id in self.pipelines:
                self.error(f"{where}.id: duplicate pipeline {pipeline_id!r}")
                continue
            stages = pipeline.get("stages")
            if self.need(stages, f"{where}.stages", list):
                if not stages:
                    self.error(f"{where}.stages: at least one stage is required")
                shader_count = 0
                for index, stage in enumerate(stages):
                    stage_where = f"{where}.stages[{index}]"
                    if not self.need(stage, stage_where, dict):
                        continue
                    self.one_of(stage, "stage", stage_where, _STAGES)
                    msl = self.string(stage, "msl_blob", stage_where)
                    if msl is not None:
                        self.chunk_of(msl, f"{stage_where}.msl_blob", {"msl"})
                        shader_count += 1
                    reflection = self.optional_string(stage, "reflection_blob", stage_where)
                    if reflection is not None:
                        self.chunk_of(reflection, f"{stage_where}.reflection_blob", {"reflection"})
                if not shader_count:
                    self.error(f"{where}.stages: no stage references an MSL chunk")
            state = pipeline.get("state")
            if self.need(state, f"{where}.state", dict):
                self.pipeline_state(state, f"{where}.state")
            self.pipelines[pipeline_id] = pipeline

    def pipeline_state(self, state: dict, where: str) -> None:
        blend = state.get("blend")
        if self.need(blend, f"{where}.blend", dict):
            if not isinstance(blend.get("enabled"), bool):
                self.error(f"{where}.blend.enabled: expected a boolean")
            attachments = blend.get("attachments", [])
            if not isinstance(attachments, list):
                self.error(f"{where}.blend.attachments: expected a list")
            for index, attachment in enumerate(attachments if isinstance(attachments, list) else []):
                attachment_where = f"{where}.blend.attachments[{index}]"
                if not self.need(attachment, attachment_where, dict):
                    continue
                self.integer(attachment, "index", attachment_where)
                for key in ("rgb_operation", "alpha_operation"):
                    if key in attachment:
                        self.one_of(
                            attachment,
                            key,
                            attachment_where,
                            {"add", "subtract", "reverse_subtract", "min", "max"},
                        )
                for key in ("rgb_source", "rgb_destination", "alpha_source", "alpha_destination"):
                    if key in attachment and attachment[key] is not None:
                        self.one_of(
                            attachment,
                            key,
                            attachment_where,
                            {"zero", "one", "source_color", "one_minus_source_color", "destination_color",
                             "one_minus_destination_color", "source_alpha", "one_minus_source_alpha",
                             "destination_alpha", "one_minus_destination_alpha", "source_alpha_saturated",
                             "blend_color", "one_minus_blend_color", "blend_alpha", "one_minus_blend_alpha"},
                        )
        depth = state.get("depth_stencil")
        if self.need(depth, f"{where}.depth_stencil", dict):
            for key in ("depth_test", "depth_write"):
                if not isinstance(depth.get(key), bool):
                    self.error(f"{where}.depth_stencil.{key}: expected a boolean")
            self.one_of(depth, "depth_compare", f"{where}.depth_stencil", _COMPARE)
            stencil = depth.get("stencil")
            if stencil is not None and self.need(stencil, f"{where}.depth_stencil.stencil", dict):
                for key in ("front", "back"):
                    if key in stencil and stencil[key] is not None and self.need(
                        stencil[key], f"{where}.depth_stencil.stencil.{key}", dict
                    ):
                        self.one_of(stencil[key], "compare", f"{where}.depth_stencil.stencil.{key}", _COMPARE)
        raster = state.get("raster")
        if self.need(raster, f"{where}.raster", dict):
            self.one_of(raster, "cull", f"{where}.raster", _CULL)
            self.one_of(raster, "fill", f"{where}.raster", _FILL)
            if "depth_bias" in raster:
                self.number(raster, "depth_bias", f"{where}.raster")
            for key in ("depth_slope_scale", "depth_clamp"):
                if key in raster:
                    self.number(raster, key, f"{where}.raster")
        for key in ("viewport", "scissor"):
            value = state.get(key)
            if not isinstance(value, list) or len(value) != 4 or not all(
                isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
            ):
                self.error(f"{where}.{key}: expected four numbers")
        self.one_of(state, "frontface", where, _FRONT)
        self.one_of(state, "primitive_type", where, _PRIMITIVES)

    def passes_section(self, manifest: dict) -> None:
        passes = manifest.get("passes")
        if not self.need(passes, "manifest.passes", list):
            return
        if not passes:
            self.error("manifest.passes: at least one pass is required")
        for position, render_pass in enumerate(passes):
            where = f"manifest.passes[{position}]"
            if not self.need(render_pass, where, dict):
                continue
            pass_id = self.string(render_pass, "id", where)
            if pass_id is None:
                continue
            if pass_id in self.passes:
                self.error(f"{where}.id: duplicate pass {pass_id!r}")
                continue
            self.one_of(render_pass, "kind", where, _PASS_KINDS)
            self.integer(render_pass, "width", where, minimum=1)
            self.integer(render_pass, "height", where, minimum=1)
            self.string(render_pass, "pixel_format", where)
            if "frame" in render_pass:
                frame_index = self.integer(render_pass, "frame", where)
                if frame_index is not None:
                    self.pass_frame_refs.append((f"{where}.frame", frame_index))
            attachments = render_pass.get("attachments", [])
            if self.need(attachments, f"{where}.attachments", list):
                slots: set[int] = set()
                for index, attachment in enumerate(attachments):
                    attachment_where = f"{where}.attachments[{index}]"
                    if not self.need(attachment, attachment_where, dict):
                        continue
                    slot = self.integer(attachment, "slot", attachment_where)
                    if slot is not None:
                        if slot in slots:
                            self.error(f"{attachment_where}.slot: duplicate slot {slot}")
                        slots.add(slot)
                    texture_id = self.string(attachment, "texture", attachment_where)
                    resource = None
                    if texture_id is not None:
                        resource = self.resource(texture_id, f"{attachment_where}.texture", "texture")
                    level = self.integer(attachment, "level", attachment_where)
                    layer = self.integer(attachment, "layer", attachment_where)
                    if resource is not None:
                        mip_range = resource.get("mip_range") or {}
                        layer_range = resource.get("layer_range") or {}
                        if level is not None and isinstance(mip_range.get("count"), int):
                            if not mip_range.get("first", 0) <= level < mip_range["first"] + mip_range["count"]:
                                self.error(f"{attachment_where}.level: {level} is outside the resource mip range")
                        if layer is not None and isinstance(layer_range.get("count"), int):
                            if not layer_range.get("first", 0) <= layer < layer_range["first"] + layer_range["count"]:
                                self.error(f"{attachment_where}.layer: {layer} is outside the resource layer range")
                    load = self.one_of(attachment, "load", attachment_where, _LOADS)
                    self.one_of(attachment, "store", attachment_where, _STORES)
                    if load == "clear":
                        clear = attachment.get("clear")
                        if (
                            not isinstance(clear, list)
                            or not 1 <= len(clear) <= 4
                            or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in clear)
                        ):
                            self.error(f"{attachment_where}.clear: expected one to four numbers")
            pipeline_ids = self.string_list(render_pass, "pipelines", where)
            for pipeline_id in pipeline_ids:
                if pipeline_id not in self.pipelines:
                    self.error(f"{where}.pipelines: unknown pipeline {pipeline_id!r}")
            draw_ids = self.string_list(render_pass, "draws", where)
            if draw_ids and not pipeline_ids:
                self.error(f"{where}: lists draws but no pipeline to render them with")
            self.passes[pass_id] = render_pass

    def draws_section(self, manifest: dict) -> None:
        draws = manifest.get("draws")
        if not self.need(draws, "manifest.draws", list):
            return
        for position, draw in enumerate(draws):
            where = f"manifest.draws[{position}]"
            if not self.need(draw, where, dict):
                continue
            draw_id = self.string(draw, "id", where)
            if draw_id is None:
                continue
            if draw_id in self.draws:
                self.error(f"{where}.id: duplicate draw {draw_id!r}")
                continue
            pass_id = self.string(draw, "pass", where)
            if pass_id is not None:
                render_pass = self.passes.get(pass_id)
                if render_pass is None:
                    self.error(f"{where}.pass: unknown pass {pass_id!r}")
                elif draw_id not in render_pass.get("draws", []):
                    self.error(f"{where}.pass: pass {pass_id!r} does not list draw {draw_id!r}")
            pipeline_id = self.string(draw, "pipeline", where)
            if pipeline_id is not None and pipeline_id not in self.pipelines:
                self.error(f"{where}.pipeline: unknown pipeline {pipeline_id!r}")
            vertex = draw.get("vertex")
            if self.need(vertex, f"{where}.vertex", dict):
                self.integer(vertex, "start", f"{where}.vertex")
                self.integer(vertex, "count", f"{where}.vertex", minimum=1)
            index = draw.get("index")
            if index is not None and self.need(index, f"{where}.index", dict):
                self.one_of(index, "type", f"{where}.index", _INDEX_TYPES)
                if "start" in index:
                    self.integer(index, "start", f"{where}.index")
                self.integer(index, "count", f"{where}.index", minimum=1)
            self.integer(draw, "instances", where, minimum=1)
            self.one_of(draw, "primitive_type", where, _PRIMITIVES)
            table = draw.get("argument_table")
            if self.need(table, f"{where}.argument_table", dict):
                self.argument_table(table, f"{where}.argument_table")
            self.draws[draw_id] = draw

    def argument_table(self, table: dict, where: str) -> None:
        for key in ("vertex_buffers", "textures", "samplers", "uniform_buffers", "fragment_buffers"):
            if key not in table:
                continue
            entries = table[key]
            if not isinstance(entries, list):
                self.error(f"{where}.{key}: expected a list")
                continue
            slots: set[tuple[int, int]] = set()
            for index, entry in enumerate(entries):
                entry_where = f"{where}.{key}[{index}]"
                if not self.need(entry, entry_where, dict):
                    continue
                slot = self.integer(entry, "index", entry_where)
                stage = self.integer(entry, "stage", entry_where) if "stage" in entry else None
                if slot is not None:
                    # NVN keeps one slot space per shader stage, so the same index in two stages is legal.
                    slot_key = (stage if stage is not None else -1, slot)
                    if slot_key in slots:
                        self.error(f"{entry_where}.index: duplicate index {slot}"
                                   f" for stage {stage if stage is not None else 'unspecified'}")
                    slots.add(slot_key)
                if key in ("vertex_buffers", "uniform_buffers", "fragment_buffers"):
                    buffer_id = self.string(entry, "buffer", entry_where)
                    resource = None
                    if buffer_id is not None:
                        resource = self.resource(buffer_id, f"{entry_where}.buffer", "buffer")
                    offset = self.integer(entry, "offset", entry_where)
                    length = self.integer(entry, "length", entry_where)
                    if resource is not None and offset is not None and length is not None:
                        byte_range = resource.get("bytes") or {}
                        if isinstance(byte_range.get("size"), int) and offset + length > byte_range["size"]:
                            self.error(
                                f"{entry_where}: offset {offset} + length {length} exceeds the "
                                f"{byte_range['size']}-byte buffer {buffer_id!r}"
                            )
                elif key == "textures":
                    texture_id = self.string(entry, "texture", entry_where)
                    if texture_id is not None:
                        self.resource(texture_id, f"{entry_where}.texture", "texture")
                else:
                    for filter_key in ("min_filter", "mag_filter"):
                        if filter_key in entry:
                            self.one_of(entry, filter_key, entry_where, _FILTERS)
                    for address_key in ("address_u", "address_v", "address_w"):
                        if address_key in entry and entry[address_key] not in _ADDRESSES:
                            self.error(
                                f"{entry_where}.{address_key}: {entry[address_key]!r} is not one of "
                                f"{sorted(_ADDRESSES)}"
                            )

    def cross_references(self, manifest: dict) -> None:
        for where, pass_id in self.frame_pass_refs:
            if pass_id not in self.passes:
                self.error(f"{where}: unknown pass {pass_id!r}")
        for where, frame_index in self.pass_frame_refs:
            if frame_index not in self.frame_map:
                self.error(f"{where}: unknown frame {frame_index}")
        for pass_id, render_pass in self.passes.items():
            for draw_id in render_pass.get("draws", []) if isinstance(render_pass.get("draws"), list) else []:
                draw = self.draws.get(draw_id)
                if draw is None:
                    self.error(f"manifest.passes[{pass_id!r}].draws: unknown draw {draw_id!r}")
                elif draw.get("pass") != pass_id:
                    self.error(f"draw {draw_id!r} is listed by pass {pass_id!r} but points at {draw.get('pass')!r}")
            for pipeline_id in render_pass.get("pipelines", []) if isinstance(render_pass.get("pipelines"), list) else []:
                if pipeline_id not in self.pipelines:
                    self.error(f"manifest.passes[{pass_id!r}].pipelines: unknown pipeline {pipeline_id!r}")

    def coverage(self) -> None:
        for chunk in self.package.chunks:
            if chunk.id not in self.used_chunks:
                self.warn(f"chunk {chunk.id!r} is not referenced by the manifest")
        resources = [chunk for chunk in self.package.chunks if chunk.kind in ("texture", "buffer")]
        for chunk in resources:
            if chunk.id not in self.used_chunks:
                self.warn(f"{chunk.kind} chunk {chunk.id!r} is not bound to any draw or attachment")


def verify_package(path: str) -> VerifyReport:
    """Verify a package: container, hashes, envelope consistency and manifest reference integrity."""
    try:
        package = read_package(path)
    except PackageError as exc:
        return VerifyReport(path=path, ok=False, errors=[str(exc)])
    checker = _Checker(package)
    manifest = package.manifest
    checker.capture(manifest)
    checker.resources_section(manifest)
    checker.pipelines_section(manifest)
    checker.frames_section(manifest)
    checker.passes_section(manifest)
    checker.draws_section(manifest)
    checker.cross_references(manifest)
    checker.coverage()
    capture = manifest.get("capture") if isinstance(manifest.get("capture"), dict) else {}
    return VerifyReport(
        path=path,
        ok=not checker.errors,
        errors=checker.errors,
        warnings=checker.warnings,
        chunks=len(package.chunks),
        manifest_bytes=package.manifest_raw_size,
        capture=capture,
    )
