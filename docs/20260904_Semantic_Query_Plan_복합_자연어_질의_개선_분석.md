# Semantic Query Plan 복합 자연어 질의 개선 분석

- 상태: P0·P1·P2 구현 + −15 원천 회귀 수정 이후, MAIN485 **P0–P2 클러스터 회복**까지 반영.
  Full: `main485_p012_full.json` **459/485 (94.6%)** (vs r3b 413/485, +46, lost=0).
  요약: `docs/20260904_txt2sql_v0.3.2.md` §7.
- 작성일: 2026-09-04 (진행 갱신 2026-09-08)
- 대상: `txt2sql` Semantic Query Plan(SQP) 및 연관 질의 처리 경로
- 목적: 복합 자연어 질의의 의미 보존율과 실행 정확도를 높이기 위한 구현 전 분석

## 1. 관련 아키텍처 분석

현재 질의 처리는 하나의 단일 계획기가 아니라 여러 의미 해석·계획 경로가 결합된 구조다.

```text
자연어 질문
  ├─ Query Contract 추출
  │    └─ QueryIR → 논리 계획 → 물리 계획
  ├─ 규칙 라우터
  ├─ Semantic Architecture v2
  └─ Semantic Query Plan
       ├─ 휴리스틱 계획 생성
       ├─ 필요 시 LLM 계획 생성
       ├─ 계약 검증 및 계획 보정
       └─ 결정적 SQL 컴파일
            └─ SQL 안전 검사 → DB 실행 → 결과 형상화

계획을 안전하게 실행할 수 없는 경우
  └─ clarify 또는 RAG+LLM fallback
```

핵심 구성 요소는 다음과 같다.

| 영역 | 주요 파일 | 역할 |
|---|---|---|
| 오케스트레이션 | `txt2sql/pipeline.py` | 라우팅, SQP 시도, fallback, 실행 결과 반환 |
| 질문 계약 | `txt2sql/query_understanding/contract.py` | 장소·필드·수치·집계·불리언 요구사항 추출 |
| 연산자 추출 | `txt2sql/query_understanding/operators.py` | 비교, 범위, 장소 등 표면 표현 인식 |
| 중간 표현 | `txt2sql/query_ir/adapters.py` | Query Contract를 QueryIR로 변환 |
| 의미 카탈로그 | `txt2sql/semantic_catalog/` | 데이터셋·필드·지명·관계 바인딩 |
| 데이터 grain | `txt2sql/dataset_grain.py` | D010/D198 선택 정책 |
| SQP 생성 | `txt2sql/semantic_plan/generator.py` | 휴리스틱 및 LLM 기반 계획 생성 |
| SQP 모델 | `txt2sql/semantic_plan/models.py` | 필터·집계·비율·공간 관계 등 계획 스키마 |
| 계약 검증 | `txt2sql/semantic_plan/contract_verifier.py` | 질문 계약과 계획의 의미 일치 검증 |
| 계획 보정 | `txt2sql/semantic_plan/plan_repair.py` | 누락된 계획 요소의 결정적 보정 |
| SQL 컴파일 | `txt2sql/semantic_plan/compiler.py` | 검증된 계획을 읽기 전용 SQL로 변환 |
| 실행 | `txt2sql/planner/semantic_executor.py` | 계획 실행 및 결과 전달 |

현행 구조의 핵심 특징은 Query Contract/QueryIR 경로와 SQP 휴리스틱 경로가 자연어를 각각 해석한다는 점이다. 이 이중 해석은 단순 질의에서는 빠른 처리를 가능하게 하지만, 복합 질의에서는 조건·범위·부정·집계 단계가 서로 다르게 해석될 가능성을 높인다.

최근 정적 평가 산출물 기준으로 전체 MAIN485 기준선은 약 `355/485`, `73.2%`다. 복합 질의 관련 실패를 넓게 분류한 표본에서는 143건 중 49건이 실패했으며, 주요 오류 표지는 `ENTITY_SELECTION_ERROR` 32건, `PREDICATE_DROPPED` 15건, `BOOLEAN_NOT_DROPPED` 4건이었다. 오류 표지는 한 문항에 중복될 수 있다.

세부 취약 영역은 다음과 같다.

| 유형 | 성공/전체 | 성공률 |
|---|---:|---:|
| 복합 AND | 37/50 | 74.0% |
| 공간 복합 | 21/35 | 60.0% |
| BETWEEN | 8/13 | 61.5% |
| OR | 5/12 | 41.7% |
| NOT | 3/10 | 30.0% |
| 집계·비율 | 16/40 | 40.0% |
| 비교연도 | 7/40 | 17.5% |
| 후속 질문 | 34/93 | 36.6% |

이 수치는 복합 질의 개선이 단순히 SQL 템플릿을 늘리는 문제가 아니라 의미 추출, 계획 표현력, 데이터셋 선택, 계약 검증 및 fallback을 함께 다뤄야 함을 보여준다.

## 2. 문제 원인

### 2.1 의미 해석 경로의 이중화

`pipeline.py`는 초기에 Query Contract와 QueryIR 기반 실행 계획을 만들지만, 이후 `semantic_plan/generator.py`가 원문을 다시 분석하여 SQP 힌트와 휴리스틱 계획을 생성한다. 이 때문에 같은 질문에서 다음 항목이 경로별로 달라질 수 있다.

- 질의 종류와 결과 형상
- 데이터셋 및 grain
- 장소 범위
- 필터 대상 필드와 연산자
- 집계 대상과 그룹 기준
- OR/NOT의 결합 범위

개선 방향은 Query Contract와 QueryIR을 정규 의미의 단일 출처로 삼고, SQP 휴리스틱은 그 의미를 SQL 실행 가능한 형태로 구체화하는 역할에 집중하는 것이다.

### 2.2 장소 표현의 과대 인식

`operators.py`의 장소 패턴은 `구`, `시`, `동`, `읍`, `면`, `리`로 끝나는 넓은 문자열을 장소로 인식한다. 후처리인 `_drop_false_places`는 예외 목록 방식이어서 복합 조사나 건축 용어를 충분히 제거하지 못한다.

확인된 오인식 예시는 다음과 같다.

- `철근콘크리트구조이면` → 장소로 오인
- `제2종근린생활시` → 장소로 오인
- `이상이면` → 장소로 오인
- `업무시` → 장소로 오인

오인식된 장소는 잘못된 행정구역 코드, 불필요한 명확화 요청, 공간 범위 오류로 이어진다. 단순 정규식 확장보다 형태 경계, 조사 제거, gazetteer 검증, 상위 행정구역 문맥을 함께 적용해야 한다.

### 2.3 수치의 의미 역할 미분리

`_extract_numbers`, `_bind_numbers_greedily`, `_nearest_unused_metric`은 숫자를 주로 가까운 필드와 결합한다. 그러나 복합 질의의 숫자는 다음과 같이 서로 다른 역할을 가진다.

- 임계값
- 범위의 하한·상한
- 상위 N 또는 출력 제한
- 공간 거리
- 연도·날짜
- 백분위·비율
- 행정동이나 용도명에 포함된 식별자

예를 들어 `상위 10개`의 10이 연면적 임계값으로 결합되거나, `500m`가 높이 500으로 해석될 수 있다. `광안2동`, `제1종`, `제2종`의 숫자도 일반 수치 조건에서 제외되어야 한다.

### 2.4 집계 대상 및 다단계 연산 표현 부족

`_finalize_requests`가 여러 집계 요청에 첫 번째 metric을 재사용하는 경향이 있어, 필터 필드와 집계 필드가 섞일 수 있다. 또한 다음과 같은 질의는 단일 SELECT 수준의 집계만으로 정확히 표현하기 어렵다.

- `아파트 중 연면적 상위 10개의 평균 높이`
- 그룹별 상위 N 이후 재집계
- 조건부 비율의 분자·분모 분리
- 임의 구간 목록별 집계

이 경우 SQP가 단계형 연산을 표현하고 컴파일러가 CTE 또는 서브쿼리로 변환할 수 있어야 한다.

### 2.5 불리언 구조와 범위 보존 실패

현재 계약은 불리언 토큰 구간을 보유하지만 완전한 predicate AST를 구성하지 않는다. `contract_to_query_ir`의 OR 결합도 전역적이고 제한적이어서 실제 우선순위와 부정 범위를 보존하기 어렵다.

필요한 표현 예시는 다음과 같다.

```text
(A OR B) AND C
A AND NOT (B OR C)
(A BETWEEN x AND y) OR (A BETWEEN z AND w)
```

예를 들어 `공장·창고를 제외`가 `창고 AND NOT 공장`으로 바뀌면 결과 의미가 완전히 달라진다. 복합 질의에서는 필드 단위 predicate 목록이 아니라 타입이 있는 predicate tree가 필요하다.

### 2.6 D010/D198 grain 선택 과잉

`dataset_grain.py`의 D198 요구 필드에는 높이, 면적, 층수, 구조, 비율 등 D010에서도 지원되는 공통 필드가 포함되어 있다. 이로 인해 건축물 단위로 충분한 질의도 D198로 선택될 수 있다.

D010과 D198의 미세한 레코드 범위 차이 때문에 단순한 높이·용도·면적 조건에서도 집계값이 달라질 수 있다. 정책은 다음 원칙으로 정리할 필요가 있다.

- 필요한 모든 필드가 D010에 있고 건축물 grain이 목적이면 D010 우선
- 세부 용도, 용도 분류, 대장 종류, 허가일 등 D198 전용 의미가 필요한 경우에만 D198 선택
- 공간 질의는 특별한 상세 속성 요구가 없다면 D010 우선

### 2.7 검증기와 보정기의 의미 보존 검사 부족

`verify_contract`는 metric 필드가 필터·선택·그룹·집계 중 어딘가에 등장하면 충족된 것으로 볼 수 있다. 하지만 복합 질의에서 중요한 것은 단순 필드 등장 여부가 아니라 다음 원자 조건의 동일성이다.

```text
(필드, 연산자, 정규화된 값, 부정 여부, 불리언 그룹, 적용 단계)
```

`inject_missing_predicates`도 주로 수치 임계값과 범위를 복구하며, 다음 항목은 충분히 보정하지 못한다.

- 범주형 OR/NOT
- 비율의 분자와 분모 조건
- 임의 구간 목록
- 상위 N 이후 집계
- 장소의 상위 행정구역 문맥

또한 일부 조건 누락이 soft issue로 취급되거나 휴리스틱 수용 보조 조건으로 우회될 수 있어, 부분 의미만 가진 계획이 실행될 위험이 있다. 복합 질의의 `PREDICATE_DROPPED`, `RANGE_BOUND_DROPPED`, `BOOLEAN_NOT_DROPPED`는 실행 전에 hard failure로 취급하는 것이 안전하다.

### 2.8 SQP 모델 표현력 한계

현재 계획 모델은 일반 필터, 그룹, 집계, 비율, 공간 관계를 표현하지만 다음 의미를 직접 표현하기 어렵다.

- 명시적인 predicate tree
- 임의 구간 배열
- 상위 N 입력 집합에 대한 후속 집계
- 비율의 공통 모집단과 분자 조건 분리
- 동일 필드의 복수 장소 비교
- 장소의 `sido`·`sigungu` 문맥 보존

이 한계 때문에 컴파일러 단계에서 원문의 의도를 재추론하거나, 표현할 수 없는 의미가 조용히 소실될 수 있다.

### 2.9 공간 범위 문맥 손실

상위 단계의 `PlaceScopeBinding`에는 `sido`, `sigungu`가 있지만 SQP의 `PlaceSpec`으로 전달되면서 문맥이 줄어든다. 동명이 존재하는 행정동은 단일 이름만으로 잘못된 코드 prefix를 선택할 수 있다.

장소 계획에 상위 행정구역과 정규화된 행정 코드 후보를 보존하고, SQL 컴파일 시 해당 문맥을 우선 사용해야 한다.

### 2.10 문서상 fallback과 실제 제어 흐름 불일치

문서와 `_try_semantic_result`의 의도는 SQP 실패 후 RAG+LLM fallback으로 진행하는 것이다. 그러나 hybrid 경로에서 semantic fallback 표지가 있으면 `pipeline.py`가 오류 응답을 즉시 반환하여 이후 RAG 경로에 도달하지 못할 수 있다.

계약이나 안전 검사를 통과하지 못한 SQP를 실행해서는 안 되지만, 실패가 안전하게 분류된 경우에는 정의된 RAG fallback으로 제어를 넘겨야 한다.

## 3. 수정 대상 파일

### P0: 의미 보존과 실행 경로 정합성

| 파일 | 예상 변경 |
|---|---|
| `txt2sql/query_understanding/contract.py` | 수치 역할, 집계 대상, predicate 구조 및 장소 정제 강화 |
| `txt2sql/query_understanding/operators.py` | 장소 경계 및 복합 불리언 연산자 인식 개선 |
| `txt2sql/query_ir/adapters.py` | 계약의 불리언 구조·역할 정보를 QueryIR에 손실 없이 전달 |
| `txt2sql/semantic_plan/generator.py` | QueryIR 우선 생성, 휴리스틱 수용 조건 강화 |
| `txt2sql/semantic_plan/contract_verifier.py` | 원자 predicate 및 적용 단계 단위 의미 검증 |
| `txt2sql/semantic_plan/plan_repair.py` | 범주형·불리언·비율·구간 누락의 결정적 보정 |
| `txt2sql/dataset_grain.py` | D010 우선 원칙과 D198 전용 요구 재정의 |
| `txt2sql/semantic_catalog/binding.py` | 공통 필드 조합에서 불필요한 D198 선택 방지 |
| `txt2sql/planner/physical.py` | 논리 요구와 물리 데이터셋 선택 정합성 강화 |
| `txt2sql/pipeline.py` | SQP 실패 이후 RAG fallback 제어 흐름 복원 |

### P1: 계획 표현력 및 컴파일 확장

| 파일 | 예상 변경 |
|---|---|
| `txt2sql/semantic_plan/models.py` | predicate tree, 수치 역할, 단계형 집계, 구간, 장소 문맥 모델 추가 |
| `txt2sql/semantic_plan/compiler.py` | CTE/서브쿼리, 조건부 비율, 임의 구간, 장소 문맥 SQL 지원 |
| `txt2sql/planner/semantic_executor.py` | 확장 계획의 실행 및 오류 분류 유지 |
| `txt2sql/semantic_catalog/place_scope.py` | 동명 장소의 상위 행정구역 해소 강화 |
| `txt2sql/semantic_plan/prompts.py` | LLM 계획 출력에 확장 계약과 의미 보존 규칙 반영 |

### P2: 결과 형상 정합성

| 파일 | 예상 변경 |
|---|---|
| `txt2sql/semantic_plan/result_shape.py` | 다단계 집계·비율·구간 결과의 기존 `AskResult` 호환성 유지 |

수정은 P0와 P1을 분리하여 진행해야 한다. 먼저 의미 누락과 잘못된 데이터셋 선택을 막고, 이후 기존 모델로 표현할 수 없는 복합 연산을 확장하는 편이 회귀 원인을 격리하기 쉽다.

## 4. 변경 예상 함수

실제 구현 시 우선 검토할 함수는 다음과 같다. 함수명은 현재 코드 기준이며 리팩터링 과정에서 세부 이름이 달라질 수 있다.

### 질문 계약 및 QueryIR

- `extract_contract`
- `_drop_false_places`
- `_extract_numbers`
- `_bind_numbers_greedily`
- `_nearest_unused_metric`
- `_finalize_requests`
- `_infer_query_kind`
- `contract_to_query_ir`
- `_coalesce_or_predicates`

예상 변경은 수치를 먼저 역할별로 분류한 뒤 metric과 결합하고, 집계 대상을 문장 내 지역 구문에 따라 결정하며, 불리언 predicate tree를 QueryIR까지 보존하는 것이다.

### SQP 생성·검증·보정

- `try_heuristic_plan`
- `generate_semantic_plan`
- `_defer_uses_heuristic`
- `verify_contract`
- `inject_missing_predicates`
- `repair_plan_from_contract`

예상 변경은 휴리스틱 계획이 모든 필수 원자 조건을 포함하는 경우에만 수용하고, 일부 조건이 빠진 계획은 보정하거나 안전한 fallback으로 보내는 것이다.

### 데이터셋 및 물리 계획

- `query_ir_needs_d198`
- `bind_concepts`
- `select_physical_plan`
- `build_sqp`
- `should_try_semantic_v2`

예상 변경은 데이터셋 선택을 단일 필드 존재 여부가 아니라 요구 필드의 완전한 지원 여부와 목표 grain에 따라 결정하는 것이다.

### 컴파일·실행·fallback

- `_ratio_sql`
- `_bin_expr_for`
- `_adm_cd_sql`
- `_run_semantic`
- `_try_semantic_result`
- `run_ask`

예상 변경은 조건부 비율, 명시적 구간, 단계형 집계 및 장소 문맥을 SQL로 보존하고, SQP 실패가 검증·안전 정책을 우회하지 않은 상태에서 RAG fallback으로 이어지도록 제어 흐름을 정리하는 것이다.

## 5. Regression risk

| 위험 | 영향 | 완화 방법 |
|---|---|---|
| D010/D198 선택 변경 | 기존 집계값과 행 수가 달라질 수 있음 | 대표 질의의 데이터셋·결과값을 함께 고정한 회귀 테스트 추가 |
| OR/NOT AST 도입 | 기존 단순 필터의 SQL 괄호와 결과 변경 | 단순 AND, 단일 OR, 중첩 OR/NOT을 계층별로 테스트 |
| 장소 인식 강화 | 실제 장소가 제거되는 false negative 가능 | gazetteer 기반 양성·음성 corpus를 분리하여 평가 |
| 수치 역할 분류 | 기존 임계값 바인딩이 limit/date로 오분류될 수 있음 | 단위·문맥별 파라미터화 테스트와 미사용 숫자 검사 |
| SQP 모델 확장 | 세션 저장, 역직렬화, 프롬프트 응답 호환성 저하 | 새 필드는 선택적·후방 호환으로 추가하고 버전 테스트 수행 |
| CTE/서브쿼리 도입 | 지도 결과, count, column mapping 손상 가능 | 결과 형상 및 지도 경로와의 계약 테스트 추가 |
| verifier 엄격화 | SQP 성공률이 일시 하락하고 fallback 비율 증가 | 정확도와 fallback 비율을 함께 관찰하고 누락 의미 실행은 금지 |
| RAG fallback 복원 | LLM 호출량, 지연시간, 비결정성 증가 | fallback 사유 제한, 호출량·p95 지연 측정, SQL 안전 검사 유지 |
| 공간 범위 교정 | 기존 모호한 동명 장소 결과 변화 | 상위 행정구역이 있는 경우와 없는 경우를 분리하여 검증 |
| 평가셋 과적합 | 특정 문항만 통과하는 예외 코드 증가 | 질문 ID/문자열 하드코딩 금지, unseen 조합 평가 수행 |

가장 중요한 안전 원칙은 정확하지 않은 부분 계획을 실행하지 않는 것이다. verifier 강화로 단기 성공률이 낮아지더라도, 조건이 누락된 SQL을 정상 결과처럼 반환하는 회귀보다 안전한 fallback이 우선이다.

## 6. 테스트 계획

### 6.1 계약 추출 단위 테스트

다음 범주를 독립적으로 검증한다.

- 실제 장소와 `구조이면`, `이상이면`, `업무시` 같은 비장소 표현 구분
- `상위 10개`, `500m`, `2025년`, `제2종`, `광안2동`의 수치 역할 구분
- 필터 metric과 집계 metric이 다른 문장
- `(A OR B) AND C`, `A AND NOT(B OR C)`의 predicate tree
- 여러 BETWEEN 구간의 OR 관계
- 조건부 비율의 모집단과 분자 조건 분리

### 6.2 QueryIR 변환 테스트

- Contract에서 QueryIR로 변환한 뒤 필드·연산자·값·부정·그룹 정보가 보존되는지 확인
- 변환 전후 필수 predicate 원자 수가 동일한지 확인
- 수치 역할이 필터, limit, distance, date로 올바르게 분배되는지 확인

### 6.3 SQP 및 컴파일러 테스트

- 휴리스틱 수용 전 계약 완전성 검사
- 범주형 predicate, OR/NOT, 범위의 보정 가능 여부
- `top-N → aggregate`가 CTE 또는 서브쿼리로 컴파일되는지 확인
- 조건부 비율에서 분모와 분자 WHERE 조건이 분리되는지 확인
- 임의 구간이 상호 배타적인 CASE 또는 UNION 구조로 생성되는지 확인
- 컴파일된 SQL과 계획 간 sqlglot 기반 동등성 및 읽기 전용 정책 확인

### 6.4 데이터셋 및 공간 테스트

- D010 공통 필드만 요구하는 질의가 D010을 선택하는지 확인
- D198 전용 필드가 있을 때만 D198을 선택하는지 확인
- 복합 공간 질의에서 거리 단위, SRID, geometry/geography 처리가 유지되는지 확인
- 동일 동명에 상위 `sido`·`sigungu` 문맥이 반영되는지 확인

### 6.5 파이프라인 테스트

외부 DB와 Ollama는 mock 또는 fixture로 격리한다.

- SQP 성공 시 RAG를 호출하지 않음
- SQP 계약 검증 실패 시 부분 계획을 실행하지 않음
- hybrid SQP fallback 시 허용된 경우 RAG 경로로 진행
- RAG 결과도 SQL 안전 검사와 응답 계약을 우회하지 않음
- 기존 `AskResult`, route, session, chart, map payload가 유지됨

### 6.6 회귀 테스트 실행 순서

```bash
uv run pytest tests/query_understanding/ -q
uv run pytest tests/query_ir/ -q
uv run pytest tests/semantic_plan/ -q
uv run pytest tests/planner/ -q
uv run pytest tests/compiler/test_sql_safety.py -q
uv run pytest tests/ -x -q --ignore=tests/map_ui_gold500
```

실제 테스트 디렉터리 구성과 파일명은 구현 직전에 다시 확인한다. SQL 안전 로직이 변경되면 `tests/compiler/test_sql_safety.py`는 필수 게이트다.

## 7. Evaluation 계획

평가는 빠른 의미 검증부터 전체 회귀까지 단계적으로 수행한다.

### 단계 1: Plan-only 평가

DB 실행 없이 다음을 측정한다.

- 필수 predicate 보존율
- OR/NOT 괄호와 부정 범위 정확도
- 수치 역할 분류 정확도
- 집계 대상 및 결과 형상 정확도
- D010/D198 선택 정확도
- 장소 범위 해소 정확도

### 단계 2: 복합 질의 집중 평가

기존 평가셋에서 다음 유형을 별도 subset으로 고정한다.

- 복합 AND
- OR
- NOT/제외
- BETWEEN 및 복수 구간
- 집계·비율
- 상위 N 이후 집계
- 비교연도
- 공간 복합
- 후속 질문

각 유형별로 `fixed`, `regressed`, `unchanged-pass`, `unchanged-fail`을 기록한다.

### 단계 3: Live DB 대표 평가

외부 서비스와 환경 변수를 확인한 후 대표 질의를 실제 실행한다.

- D010/D198 결과 차이가 있는 질의
- 조건부 비율
- 다단계 집계
- 공간 거리와 행정구역 문맥
- 결과 형상과 지도 연동이 있는 질의

### 단계 4: 전체 평가

GT1, GT2, MAIN485를 동일 설정에서 실행하고 기존 baseline과 비교한다. 같은 입력을 최소 두 번 실행하여 LLM fallback에 따른 결과 변동도 확인한다. 기존 평가 산출물은 덮어쓰지 않고 새 실행 ID나 새 파일명으로 저장한다.

### 핵심 측정 지표

- 전체 및 유형별 exact-result 정확도
- predicate·불리언 의미 보존율
- 데이터셋 선택 정확도
- semantic engine 성공·실패율
- clarify 및 RAG fallback 비율
- LLM 호출 수
- 평균 및 p95 응답 지연
- 실행 오류율과 SQL 안전 위반 건수
- fixed 대 regressed 비율

### 제안 승격 게이트

- 계약이 검출한 필수 predicate의 계획 보존율 100%
- SQL 안전 회귀 0건
- 단순 규칙 라우터 회귀 0건
- 복합 질의 subset 정확도 최소 10%p 향상
- OR/NOT subset 정확도 최소 20%p 향상
- 전체 gold 정확도 하락 없음
- `fixed` 건수가 `regressed` 건수의 2배 이상
- engine failure 및 불필요한 fallback 비율 감소
- p95 지연 증가율 20% 이내

정확도 목표는 현재 baseline을 기준으로 다시 확정해야 하며, 평가 데이터나 gold 기대값을 변경할 경우에는 별도 근거와 리뷰가 필요하다.

## 권장 구현 순서

1. 장소 오인식, 수치 역할, 집계 대상, 불리언 구조에 대한 실패 재현 테스트를 먼저 추가한다.
2. Query Contract와 QueryIR을 정규 의미의 단일 출처로 만들고 변환 손실을 차단한다.
3. SQP verifier와 휴리스틱 수용 기준을 강화하여 부분 의미 계획의 실행을 막는다.
4. D010/D198 grain 선택 정책을 정리한다.
5. SQP 실패 후 RAG fallback 제어 흐름을 문서 계약과 일치시킨다.
6. 조건부 비율, 임의 구간, 상위 N 후 집계 등 SQP 모델 표현력을 확장한다.
7. 집중 subset을 통과한 뒤 전체 회귀와 live DB 평가를 수행한다.

## 분석 제약

이 문서는 코드, 설정, 아키텍처 문서 및 기존 평가 산출물의 정적 대조를 기반으로 작성했다.
P0 구현(2026-09-04)에서 다음을 반영했다.

- 장소 오인식·수치 역할·집계 대상·NOT/OR 범위의 Contract/QueryIR 보존
- verifier hard fail·범주형 OR/NOT 결정적 repair
- D010 우선 grain (D198 전용 필드·세부용도만 ledger)
- hybrid SQP 계약 실패 시 RAG fallback (readonly/clarify 제외)

P1 구현(2026-09-04)에서 다음을 반영했다.

- `PlaceSpec.sido`/`sigungu`/`code`와 Binding→enrich→compiler 연결
- `BinSpec`(edges/width) 및 `StageSpec`(top-N→outer aggregate CTE)
- prompts few-shot(OR predicate·ratio·bins·place+parent)과 heuristic stages/bins 채움

P2 구현(2026-09-04)에서 다음을 반영했다.

- `infer_plan_result_kind`(scalar/group/ratio)와 stages·bins·ratios shape 검증
- 한국어 답변·`AskResult.table`·chart_offer 호환 (스키마 변경 없음)
- stale count 계약이어도 실행 Plan 형상이 맞으면 결과 유지

MAIN485 live 평가 r1 (2026-09-04, 기능 추가 직후):

- 산출물: `artifacts/evaluation/main485_p012_r1.json` (기존 baseline 미덮어씀)
- 결과: **339/485 (69.9%)**, Phase6 final 354/485 (73.0%) 대비 **−15 (−3.1%p)**
- gained 24 / regressed 39 · group·list 상승, count·scalar·meta 하락
- 승격 게이트「전체 gold 정확도 하락 없음」미통과 → count/scalar 회귀 격리 후 재평가

MAIN485 회귀 수정 live (2026-09-07, p012_fix_r4):

- 산출물: `artifacts/evaluation/main485_p012_fix_r4.json`
- 결과: **391/485 (80.6%)**, Phase6 대비 **+37 (+7.6%p)**, gain 37 / **reg 0**
- `gate_no_drop=true`. 원 −15의 핵심은 PhysicalPlan D010이 coverage+usage→D198을
  덮어쓰던 grain 붕괴였고, `resolve_dataset_grain` 단일 권한으로 수정

MAIN485 P0–P2 클러스터 회복 (2026-09-08):

- Full: `artifacts/evaluation/main485_p012_full.json` **459/485 (94.6%)**
- vs r3b(413/485): **+46**, **lost=0**. 상세 `docs/20260904_txt2sql_v0.3.2.md` §7
