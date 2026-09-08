from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from txt2sql.security.errors import UserInputError
from txt2sql.security.upload_io import (
    PayloadTooLarge,
    read_upload_limited,
    validate_zip_members,
)


def test_read_upload_limited_aborts() -> None:
    async def gen():
        yield b"a" * 100
        yield b"b" * 100

    async def run() -> None:
        with pytest.raises(PayloadTooLarge):
            await read_upload_limited(gen(), max_bytes=150)

    asyncio.run(run())


def test_zip_slip_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.shp", b"x")
    with pytest.raises(UserInputError):
        validate_zip_members(buf.getvalue())


def test_zip_symlink_rejected() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link.shp")
        info.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(info, b"x")
    with pytest.raises(UserInputError):
        validate_zip_members(buf.getvalue())


def test_zip_too_many_entries() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(10):
            zf.writestr(f"f{i}.shp", b"x")
    with pytest.raises(UserInputError):
        validate_zip_members(buf.getvalue(), max_entries=3)


def test_zip_duplicate_path_case() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("A.shp", b"x")
        zf.writestr("a.shp", b"y")
    with pytest.raises(UserInputError):
        validate_zip_members(buf.getvalue())
