# AGENTS.md

## 프로젝트 개요

`txt2sql`은 부산 GIS 데이터를 대상으로 자연어 질문을 PostgreSQL/PostGIS
SQL로 변환하고, 실행 결과를 한국어로 제공하는 Python 3.13 프로젝트다.

- 패키지 관리: `uv`
- 백엔드: FastAPI, psycopg, SQLAlchemy
- LLM: Ollama
- 공간 데이터: PostgreSQL + PostGIS + 선택적 GeoServer
- 테스트: pytest
- 프론트엔드: 정적 HTML/CSS/JavaScript, OpenLayers

## 우선 참고 문서

작업 범위에 맞는 문서만 확인한다.

- `README.md`: 설치, 실행, 환경 변수, 전체 구조
- `docs/작동방식_및_알고리즘.md`: 질의 처리 파이프라인
- `docs/Semantic_Query_Plan_구현.md`: SQP 구조와 계약
- `docs/implementation/`: SQP 배포, 마이그레이션, 롤백 작업
- `docs/20260908_txt2sql_v0.3.3.md`: 0.3.3 MAIN485 P0–P2·릴리스 요약

일반 동작은 현재 코드와 테스트를 우선한다. 다만 보안 정책이나 데이터 계약
문서와 충돌하면 임의로 해석하거나 완화하지 말고 불일치를 보고한다. 동작을
변경하면 관련 문서도 갱신한다.

## 주요 코드 영역

- `txt2sql/engine.py`: 공개 엔진 API와 외부 자원 수명주기
- `txt2sql/pipeline.py`: 전체 질의 처리 오케스트레이션
- `txt2sql/query_understanding/`: 질문 계약 추출과 카탈로그 바인딩
- `txt2sql/query_ir/`: 논리적 질의 중간 표현
- `txt2sql/semantic_catalog/`: 데이터셋, 필드, 지명, 관계 정의
- `txt2sql/planner/`: 논리·물리 계획과 실행 어댑터
- `txt2sql/compiler/`: 결정적 SQL 생성과 안전 정책
- `txt2sql/semantic_plan/`: SQP 생성, 검증, 보정, 컴파일
- `txt2sql/interaction/`: 세션, 후속 질문, 시각화 상태 변경
- `txt2sql/map/`: GeoServer 발행과 지도 레이어 처리
- `txt2sql/data/`: 공간 데이터 업로드와 메타데이터 관리
- `txt2sql/evaluation/`: 평가, 비교, 승격 게이트
- `txt2sql/webapp/`: FastAPI 및 정적 웹 UI
- `tests/`: 단위·회귀 테스트
- `scripts/`: 스모크 테스트, 벤치마크, 진단 도구

## 핵심 아키텍처 규칙

질문 처리는 다음 계약을 유지한다.

1. 질문에서 Query Contract와 의미 정보를 추출한다.
2. 규칙 라우터, Semantic Architecture v2, SQP 중 적격 경로를 선택한다.
3. 계획과 SQL을 검증하고 필요한 경우 결정적으로 보정한다.
4. 안전 검사를 통과한 SQL만 실행한다.
5. 실행할 수 없으면 정의된 clarify 또는 RAG+LLM fallback을 사용한다.

- 단순 질의를 고치면서 복합질의 조건을 누락시키지 않는다.
- LLM 결과가 검증기, 계약 게이트 또는 SQL 안전 검사를 우회하게 만들지 않는다.
- 골드 문항 ID나 특정 질문 문자열에 맞춘 하드코딩을 추가하지 않는다.
- 수정은 질문 유형, 연산자, 데이터 grain 또는 계약 수준으로 일반화한다.
- 동일한 물리 컬럼·테이블 매핑을 여러 모듈에 중복 정의하지 않는다.
- 기존 `AskResult`, route, session, chart, map payload 호환성을 유지한다.

## 데이터 및 SQL 안전 규칙

- 채팅 질의는 `SELECT` 또는 `WITH` 한 문장만 허용한다.
- 사용자 입력이나 LLM 출력으로 DDL/DML을 실행하지 않는다.
- 비집계 조회에는 기존 `LIMIT` 정책을 유지한다.
- geometry와 바이너리 데이터는 일반 채팅 응답에 직접 노출하지 않는다.
- 지도 발행을 위한 `temp_*` 변경은 전용 지도 코드 경로에서만 수행한다.
- 채팅 질의와 지도 발행 경로에서는 원본 공간 테이블을 삭제하거나 덮어쓰지 않는다.
- 데이터 관리 경로의 업로드·이름 변경은 사용자의 명시적 요청이 있을 때만
  수행하고, 기존 식별자·보호 테이블 검사를 유지한다. 시스템·보호 테이블은
  변경하지 않으며 `temp_*` 테이블은 데이터 관리 대상으로 취급하지 않는다.
- SQL 안전 검사를 테스트 통과 목적으로 약화하지 않는다.
- D010/D198 선택은 `dataset_grain`과 semantic catalog 정책을 따른다.
- 공간 거리 연산은 SRID와 `geometry`/`geography` 단위를 명시적으로 검토한다.

## 구현 규칙

- 기존 Python 스타일과 타입 힌트를 유지한다.
- 새 Python 모듈에는 `from __future__ import annotations`를 사용한다.
- 설정은 `Settings`와 환경 변수 경로를 통해 주입한다.
- DB, Ollama, GeoServer 접근은 테스트에서 mock 또는 fixture로 격리한다.
- 예외를 무조건 삼키지 말고, fallback이 의도된 경우에만 제한적으로 처리한다.
- 사용자 메시지와 답변은 기존 한국어 표현을 유지한다.
- 변경한 동작에는 최소 하나의 회귀 테스트를 추가한다.
- 관련 없는 대규모 리팩터링이나 포맷 변경은 함께 수행하지 않는다.

## 보안 및 저장소 관리

- `.env` 내용을 출력하거나 커밋하지 않는다.
- 비밀번호, DB URL, Ollama/GeoServer 인증 정보를 소스에 넣지 않는다.
- `.env.example`에는 실제 자격 증명 대신 설명용 값만 기록한다.
- 사용자가 만든 미추적 파일과 평가 산출물을 삭제하거나 덮어쓰지 않는다.
- `artifacts/`와 `tests/map_ui_gold500/results/` 결과는 요청 없이 재생성하지 않는다.
- 골드 데이터나 baseline을 변경할 때는 기대값 변경 근거를 문서화한다.

## 검증 명령

의존성은 다음 명령으로 설치한다.

```bash
uv sync
```

먼저 변경 범위의 테스트를 실행한다.

```bash
uv run pytest tests/<관련 경로> -q
```

기본 회귀 게이트는 다음과 같다.

```bash
uv run pytest tests/ -x -q --ignore=tests/map_ui_gold500
```

SQL 안전 로직을 변경하면 반드시 다음 테스트를 실행한다.

```bash
uv run pytest tests/compiler/test_sql_safety.py -q
```

지도 프론트엔드 변경 시 다음을 실행한다.

```bash
node scripts/test_map_stack.mjs
```

지도 Python 코드 변경 시 관련 스모크 테스트도 실행한다.

```bash
uv run python scripts/test_map_sql.py
uv run python scripts/test_map_layers.py
```

DB, Ollama, GeoServer 또는 500문항 평가가 필요한 테스트는 외부 서비스와 환경
변수를 확인한 후 실행한다. 전체 골드 평가는 명시적으로 요청되거나 변경 위험이
큰 경우에만 수행한다.

## 완료 조건

- 관련 테스트가 통과했다.
- SQL 읽기 전용 정책과 계약 검증이 유지된다.
- 기존 공개 API와 응답 구조가 불필요하게 변경되지 않았다.
- 새 동작이 특정 평가 문항에만 종속되지 않는다.
- 설정 또는 사용자 동작이 바뀌었다면 README나 관련 문서도 갱신했다.
- 생성된 로그, 임시 파일, 자격 증명이 변경 목록에 포함되지 않았다.
