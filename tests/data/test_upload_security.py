from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from txt2sql.config import Settings
from txt2sql.data.names import is_protected_table
from txt2sql.data.upload import TableExistsConflict, process_zip_upload
from txt2sql.security.errors import UserInputError
from txt2sql.security.upload_io import find_single_shapefile, safe_extract_zip, validate_zip_members


def test_protected_and_temp_names() -> None:
    assert is_protected_table("temp_abc")
    assert is_protected_table("table_metadata")
    assert not is_protected_table("AL_D010_26_20250704")


def test_multi_shp_rejected(tmp_path: Path) -> None:
    root = tmp_path / "ex"
    root.mkdir()
    (root / "a.shp").write_bytes(b"x")
    (root / "b.shp").write_bytes(b"y")
    with pytest.raises(UserInputError):
        find_single_shapefile(root)


def test_safe_extract_keeps_inside(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("data/sample.shp", b"shp")
        zf.writestr("data/sample.dbf", b"dbf")
    data = buf.getvalue()
    members = validate_zip_members(data)
    dest = tmp_path / "out"
    safe_extract_zip(data, dest, members=members)
    assert (dest / "data" / "sample.shp").is_file()


def test_existing_table_requires_confirm() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo.shp", b"x")
        zf.writestr("demo.dbf", b"y")
        zf.writestr("demo.shx", b"z")
    with (
        patch("txt2sql.data.upload._table_exists", return_value=True),
        patch("txt2sql.data.upload.find_single_shapefile") as find,
    ):
        find.return_value = Path("demo.shp")
        with pytest.raises(TableExistsConflict):
            process_zip_upload(
                settings,
                filename="demo.zip",
                content=buf.getvalue(),
                replace_existing=False,
            )
