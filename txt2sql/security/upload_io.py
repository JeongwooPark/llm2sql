"""업로드 크기 제한·안전한 ZIP 해제."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath

from txt2sql.security.errors import PublicError, UserInputError

_SHP_SIDECARS = frozenset(
    {".shp", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".xml", ".qix"}
)


class PayloadTooLarge(PublicError):
    def __init__(self, message: str = "업로드 크기가 제한을 초과했습니다.") -> None:
        super().__init__(message, status_code=413, code="payload_too_large")


async def read_upload_limited(
    stream: AsyncIterator[bytes],
    *,
    max_bytes: int,
) -> bytes:
    """Read upload in chunks; abort before buffering more than max_bytes."""
    limit = max(1, int(max_bytes))
    buf = bytearray()
    async for chunk in stream:
        if not chunk:
            continue
        if len(buf) + len(chunk) > limit:
            raise PayloadTooLarge()
        buf.extend(chunk)
    return bytes(buf)


def validate_zip_members(
    data: bytes,
    *,
    max_entries: int = 64,
    max_uncompressed_total: int = 200 * 1024 * 1024,
    max_entry_uncompressed: int = 100 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> list[zipfile.ZipInfo]:
    """Inspect ZIP central directory before extraction."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile as exc:
        raise UserInputError("잘못된 ZIP 파일입니다.") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise UserInputError("ZIP entry 수가 제한을 초과했습니다.")
        total_uncomp = 0
        seen_norm: set[str] = set()
        for info in infos:
            name = info.filename or ""
            if not name or name.endswith("/"):
                continue
            if info.flag_bits & 0x1:
                raise UserInputError("암호화된 ZIP entry는 허용되지 않습니다.")
            # Skip symlink / special (Unix external attrs)
            is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink:
                raise UserInputError("심볼릭 링크 ZIP entry는 허용되지 않습니다.")
            path = PurePosixPath(name.replace("\\", "/"))
            if path.is_absolute() or str(path).startswith("/") or ":" in path.parts[0]:
                raise UserInputError("ZIP 경로 탈출이 감지되었습니다.")
            if ".." in path.parts:
                raise UserInputError("ZIP 경로 탈출이 감지되었습니다.")
            norm = str(path).lower()
            if norm in seen_norm:
                raise UserInputError("중복 ZIP 경로가 있습니다.")
            seen_norm.add(norm)
            suffix = path.suffix.lower()
            if suffix and suffix not in _SHP_SIDECARS:
                raise UserInputError(f"허용되지 않은 ZIP 확장자입니다: {suffix}")
            uncomp = int(info.file_size)
            comp = max(int(info.compress_size), 1)
            if uncomp > max_entry_uncompressed:
                raise UserInputError("ZIP entry 비압축 크기가 제한을 초과했습니다.")
            if uncomp / comp > max_compression_ratio:
                raise UserInputError("ZIP 압축률이 비정상적으로 큽니다.")
            total_uncomp += uncomp
            if total_uncomp > max_uncompressed_total:
                raise UserInputError("ZIP 전체 비압축 크기가 제한을 초과했습니다.")
        return [i for i in infos if i.filename and not i.filename.endswith("/")]


def safe_extract_zip(data: bytes, dest: Path, *, members: list[zipfile.ZipInfo]) -> None:
    """Extract only pre-validated members; never extractall()."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        for info in members:
            rel = PurePosixPath(info.filename.replace("\\", "/"))
            target = (dest / Path(*rel.parts)).resolve()
            if not str(target).startswith(str(dest)):
                raise UserInputError("ZIP 경로 탈출이 감지되었습니다.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1024 * 64)
                    if not chunk:
                        break
                    out.write(chunk)


def find_single_shapefile(root: Path) -> Path:
    found: list[Path] = []
    for path in root.rglob("*.shp"):
        if path.name.startswith("."):
            continue
        found.append(path)
    if not found:
        raise UserInputError("ZIP 파일에 SHP 파일이 없습니다.")
    if len(found) > 1:
        raise UserInputError("ZIP에 SHP가 여러 개 있습니다. 하나만 포함하세요.")
    return found[0]
