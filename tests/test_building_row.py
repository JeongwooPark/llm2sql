from __future__ import annotations

import re

from txt2sql.building_row import (
    infer_building_schema_from_columns,
    infer_row_dataset,
    row_full_address,
    row_lot_address,
)
from txt2sql.followup_qa import answer_followup, is_followup_question
from txt2sql.session import SessionContext


def test_d198_row_lot_not_special_land_code() -> None:
    row = {
        "A4": "부산광역시 금정구 구서동",
        "A5": "1",
        "A6": "일반",
        "A7": "183-2",
        "A13": "구서 협성 엠파이어",
        "A19": 52643.8973,
        "A24": "02000",
        "A30": 90.9,
    }
    table = "AL_D198_26410_20260715"
    assert infer_row_dataset(row, table=table) == "d198"
    assert row_lot_address(row, table=table) == "183-2"
    assert row_full_address(row, table=table) == "부산광역시 금정구 구서동 183-2"


def test_infer_schema_from_columns() -> None:
    assert infer_building_schema_from_columns(["A4", "A7", "A13", "A30"]) == "d198"
    assert infer_building_schema_from_columns(["A4", "A5", "A24", "A16"]) == "d010"


def test_followup_address_and_lot(monkeypatch) -> None:
    row = {
        "A4": "부산광역시 금정구 구서동",
        "A5": "1",
        "A6": "일반",
        "A7": "183-2",
        "A13": "구서 협성 엠파이어",
        "A19": 52643.8973,
    }
    session = SessionContext(
        focus_row=dict(row),
        table="AL_D198_26410_20260715",
        last_route="d198_attr_rank",
    )

    class _Conn:
        def cursor(self, *args, **kwargs):
            raise AssertionError("DB should not be called when row is complete")

    assert is_followup_question("주소는?", session)
    addr = answer_followup(_Conn(), "주소는?", session)
    assert "구서동 183-2" in addr.answer
    assert not addr.answer.strip().startswith("주소는 1")

    lot = answer_followup(_Conn(), "지번은?", session)
    assert "183-2" in lot.answer
    # Avoid false positive: "지번은 183-2" contains substring "지번은 1".
    assert not re.search(r"지번은\s+1(?:\D|$)", lot.answer)
