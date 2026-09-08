from __future__ import annotations

from unittest.mock import patch

import pytest

from txt2sql.config import Settings
from txt2sql.map.publish import delete_published_layer, layer_owner_session


def test_delete_enforces_owner() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    with patch(
        "txt2sql.map.publish.layer_owner_session", return_value="session_aaa"
    ):
        with pytest.raises(ValueError, match="소유"):
            delete_published_layer(
                settings,
                "temp_deadbeef",
                session_id="session_bbb",
                enforce_owner=True,
            )


def test_layer_owner_session_invalid_name() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    assert layer_owner_session(settings, "not_temp") is None
