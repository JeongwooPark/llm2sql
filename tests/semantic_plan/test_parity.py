"""Router가 맡는 고빈도 패턴을 SQP compiler가 같은 물리 단서로 표현하는지.

Router를 대체하지 않는다. 동등 컴파일이 확인된 패턴만 문서/테스트로 고정한다.
"""

from txt2sql.config import Settings
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import generate_semantic_plan
from txt2sql.semantic_plan.normalizer import normalize_semantic_plan
from txt2sql.semantic_plan.validator import validate_semantic_plan

_SETTINGS = Settings(database_url="postgresql://x")


def _sql(question: str) -> str:
    plan = generate_semantic_plan(question, _SETTINGS, allow_llm=False)
    plan = normalize_semantic_plan(plan, question)
    checked = validate_semantic_plan(plan, question)
    assert checked.status == "ready", checked.errors
    return compile_semantic_plan(checked.plan).sql


def test_parity_usage_count() -> None:
    sql = _sql("해운대구 아파트가 몇 채야?")
    assert "COUNT(*)" in sql.upper()
    assert '"A3"' in sql or '"A4"' in sql
    # D198 커버 구에서는 세부용도(아파트), 비커버면 D010 공동주택.
    assert "AL_D010" in sql or "AL_D198" in sql
    assert "공동주택" in sql or "아파트" in sql


def test_parity_height_threshold() -> None:
    sql = _sql("해운대구 아파트 중 높이 70m 이상인 건물 이름과 높이")
    # D010 height=A16, D198 height=A30
    assert '"A16"' in sql or '"A30"' in sql
    assert ">= 70" in sql.replace(".0", "")
    assert "공동주택" in sql or "아파트" in sql


def test_parity_rank_floor_area() -> None:
    sql = _sql("금정구에서 연면적이 가장 큰 건물 5개")
    assert '"A14"' in sql
    assert "ORDER BY" in sql.upper()
    assert "LIMIT 5" in sql.upper()
