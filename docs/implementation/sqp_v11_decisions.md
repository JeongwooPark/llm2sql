# SQP v1.1 결정 기록

## ADR-0001 작업트리를 원 저장소와 분리한다

- 상태: accepted
- 맥락: `D:\py_workspace\llm2sql`에 사용자 untracked 스크립트 5개가 있음
- 결정: `git worktree add D:\py_workspace\llm2sql-sqp-v11 -b feat/sqp-v11-roadmap`
- 결과: 사용자 파일을 커밋하지 않음

## ADR-0002 정적 분석 도구를 새로 도입하지 않는다

- 상태: accepted
- 맥락: 저장소에 ruff/mypy/CI가 없음
- 결정: `uv run pytest`와 `git diff --check`만 사용

## ADR-0003 카탈로그 테이블 식별은 대소문자 보존 이름으로 기록한다

- 상태: accepted
- 맥락: `to_regclass('public.AL_D010_26_20250704')`는 소문자 fold 때문에 false를 반환할 수 있음
- 결정: `pg_class.relname` 실측 목록을 snapshot 식별자로 사용
- 식별자: `public.AL_D010_26_20250704`, `public.BND_ADM_DONG_PG`, `public.TL_KODIS_BAS_26_202507`, `public.AL_D060_00_20250804`

## ADR-0004 평가 로직은 `txt2sql.evaluation`에 두고 스크립트는 CLI만 담당한다

- 상태: accepted
- 맥락: 당시 지시 문서는 `scripts/eval_*.py`를 요구했지만 단위 테스트가 필요함
- 결정: 비교·taxonomy·jsonl IO는 패키지에 두고 `scripts/eval_plan.py`, `scripts/eval_nl2sql.py`, `scripts/compare_runs.py`는 CLI
- SQL 토큰 존재는 정답 조건이 아님. smoke 30/100은 `status=draft` candidate만 import

## ADR-0005 Plan-SQL 동등성은 sqlglot 연산자 트리로 검사한다

- 상태: accepted
- 맥락: SQL 문자열 토큰 존재는 정답이 아님. AND/OR 반전·NOT 누락·집계 변경을 실행 전에 막아야 함
- 결정: 기존 `sql_validator.py` domain diagnose는 유지하고, `sqlglot` WHERE boolean tree와 Plan predicate ops를 비교한다
- 결과: 완전 논리 정규화(CNF)는 하지 않고 OR/NOT/집계/ORDER/LIMIT 누락을 탐지한다

## ADR-0006 within은 covered_by 정책이며 ST_Intersects가 아니다

- 상태: accepted
- 맥락: v1 compiler가 within과 intersects를 모두 ST_Intersects로 번역함
- 결정: Plan에는 PostGIS 함수 문자열을 넣지 않고 `spatial_policy.py`가 관계명→함수를 고른다. 장소 scope의 기존 boundary JOIN은 건물 footprint 겹침 관례로 ST_Intersects를 유지한다
- join은 generic SQL이 아니라 `building_in_admin` 등 edge_id만 허용한다

## ADR-0007 공식 벤치는 :latest를 쓰지 않는다. hybrid 기본값은 gate 통과 후에만 올린다

- 상태: accepted (FIX-4에서 기본값 갱신)
- 결정: planner/embed 역할 pin과 digest를 설정으로 둔다. 트레이스는 URL·비밀번호를 마스킹한다
- STEP-24 A–E에서는 Phase 1 gold·Phase 2 holdout 미달로 `shadow`를 유지했다
- FIX-4에서 Phase 1 30/30, holdout Recall 1.0, live spatial 6/6, A `ENV_BLOCKED` 아님을 확인한 뒤 기본값을 `hybrid`로 올렸다
