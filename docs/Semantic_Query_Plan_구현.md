# txt2sql Semantic Query Plan (0.3.3)

> 대상: `JeongwooPark/txt2sql` (구 `JeongwooPark/llm2sql`)  
> 도입 버전: **0.2.2** · 현재 제품: **0.3.3**  
> Plan 스키마: **v1.1** · 기본값: `SEMANTIC_PLAN_MODE=hybrid` (`shadow`는 생성만, `off`이면 0.2.1과 동일)  
> 0.3.3: MAIN485 P0–P2 — `docs/20260908_txt2sql_v0.3.3.md`  ·  0.3.2: Query Contract 게이트 + contract-driven `plan_repair` — `docs/20260904_txt2sql_v0.3.2.md`

원안은 Router 미적중 이후 LLM이 물리 SQL을 직접 쓰던 구간에 **Semantic Query Plan(SQP)** 을 끼워, LLM은 canonical JSON만 만들고 SQL은 Python compiler가 확정적으로 생성하게 하는 것이다. 이 문서는 그 원안을 **0.2.2에서 실제로 넣는 MVP**로 줄인 명세다.

---

## 1. 왜 줄였는가

원안(Phase 0~8, follow-up Plan merge, 산업단지, generic join graph, planner_first, 대규모 벤치)은 한 버전에 넣기엔 범위가 크다. 한 번에 넣으면 Router·RAG·지도 회귀를 가르기 어렵다.

0.2.2는 다음만 넣는다.

- 기존 Router / guide / meta / clarify / profile / followup / RAG는 **그대로 둔다**
- Router 미적중 뒤에만 SQP를 삽입한다. **복합조건**은 라우터가 일부만 먹지 않고 미적중으로 둔다
- 기본 모드는 `hybrid` (v1.1 승격). `shadow`는 생성만, `off`는 0.2.1과 동일
- building + 구/동 범위 + count/list/rank/aggregate/distribution
- 행정 경계 `ST_Intersects`, 동 버퍼 `ST_DWithin`, Plan follow-up delta
- 실패하면 기존 `run_rag_sql()` 로 내려간다

산업단지 전용 SQP, D198 건축년수 SQP, generic join graph, `planner_first`는 **다음 단계**로 남긴다.

---

## 2. 처리 흐름

```text
사용자 질문
  → Session / Guide / Intent / Clarify / Profile
  → Rule Router
       ├─ 적중 → 기존 SQL
       └─ 미적중
            ├─ SEMANTIC_PLAN_MODE=off     → RAG+LLM SQL (0.2.1과 동일)
            ├─ shadow                     → SQP 생성·검증·컴파일만, 결과는 RAG
            └─ hybrid                     → SQP 실행 (현재 기본).
                 계약·검증 실패(부분 계획) → RAG+LLM SQL (SQL 안전 위반·clarify는 제외)
```

삽입 위치는 `pipeline._ask_inner()` 의 `라우트 미매칭 → RAG+LLM` 직전이다. `run_rag_sql()` 은 삭제하지 않는다.

---

## 3. 역할 분리

| 계층 | 하는 일 | 하지 않는 일 |
|------|---------|--------------|
| LLM / heuristic | 자연어 → `SemanticQueryPlan` JSON | 물리 테이블·컬럼·PostGIS 함수·raw SQL |
| validator | 필드·연산·장소·복잡도 검사 | SQL 문자열 조립 |
| compiler | catalog allowlist로 SELECT 생성 | LLM 호출 |
| 기존 검증 | `assert_readonly_sql` → `validate_sql_preexec` → `execute_query` | Plan 재해석 |

Plan 필드에는 `where_sql`, `raw_sql`, 물리명 `A16`, `ST_DWithin(...)` 를 받지 않는다. 예:

```json
{"field": "height_m", "operator": "gte", "value": 100, "unit": "m"}
```

---

## 4. 패키지

```text
llm2sql/semantic_plan/
  models.py       SemanticQueryPlan (Pydantic)
  catalog.py      building.height_m → AL_D010.A16 등 allowlist
  prompts.py      SQL이 아닌 자연어→Plan few-shot
  generator.py    heuristic 우선, 실패 시 Ollama JSON
  normalizer.py   아파트→공동주택, 평→㎡, 장소 kind
  validator.py    ready | clarify | fallback
  compiler.py     deterministic SELECT (psycopg 식별자/리터럴 이스케이프)
  runner.py       generate → validate → compile → 검증 → 실행
  answer.py       count/list/rank/distribution 템플릿 답변
  followup.py     add_filter / change_sort / change_limit / add_select
```

직전 결과가 `semantic_plan_*` 이거나 직전 SQL이 D010이면, 짧은 후속은 Plan delta로 먼저 병합한다. 그 다음이 기존 `try_subset_followup` 이다. `heuristic_plan` / `plan_followup_delta` assumption과 **허가↔승인 day-gap** (`permit_day_gap_*`, `order_by_day_gap:*` 등, `is_permit_lag_assumption`)은 품질 점수에서 깎지 않는다.

**구조 필터:** 질문에 `철근콘크리트구조`처럼 공식 `…구조`가 있으면 heuristic/compiler가 `structure` **eq**(A11 equality)를 쓴다. 짧은 별칭·`일반목구조`는 contains/ILIKE.

**허가↔승인 시차:** `permit_day_gap_{gte,lte}:days` assumption이 있으면 D198 A33/A34로 컴파일하고, 건축연령(`rel_years`) 필터와 섞지 않는다. 비율 질의는 FILTER에 day 차를 둔다.

---

## 5. 0.2.2 지원 범위

**Entity:** `building` (실행). catalog에는 `admin_area`, `basic_zone` 매핑만 준비.

**Scope:** 구·법정동은 D010 `A4 LIKE`. `spatial_mode=boundary` 또는 행정전용 동은 `BND_ADM_DONG_PG` + `ST_Intersects`.
`PlaceSpec`은 선택적으로 `sido`/`sigungu`/`code`를 담아 동명 해소와 A3 접두 필터에 쓴다.

**거리:** `within_distance` / `outside_distance` → 행정 경계 `ST_Union` + `ST_DWithin(...::geography)`. 같은 장소를 A4로 중복 필터하지 않는다. 역·POI는 clarify.

**Query kind:** `count`, `list`, `rank`, `aggregate`, `distribution`. 휴리스틱이 「평균」「용도별」을 입구에서 고른다.

**P1 표현력 (optional):**
- `bins`: 임의 `edges` → 상호 배타 `CASE`, 또는 `width` → FLOOR 버킷. 비어 있으면 기존 `width_bucket:` assumption.
- `stages`: 비어 있으면 단일 SELECT. `rank`→`aggregate`면 `WITH stage_0 AS (…) SELECT AVG… FROM stage_0` (읽기 전용 WITH+SELECT, `SELECT *` 금지).
- 조건부 비율은 기존 FILTER; stages[0]이 모집단이면 공통 WHERE는 CTE에만 둔다.

**P2 결과 형상:** `infer_plan_result_kind`가 stages→scalar, bins/group_by→group, ratios→ratio로 분류한다.
답변·`table`·chart는 기존 `AskResult` 필드를 유지한 채 SQP 경로에 붙인다.

**복합질의 위임 (`should_defer_compound_to_plan`)**

라우터가 첫 적중만 쓰면 높이+연면적 질문이 연면적 건수만 답하는 식의 손실이 난다. 아래면 `try_route`는 `None`을 주고 SQP가 받는다.

- 수치 가족(면적·높이·층수)이 **둘 이상**
- 「안에/내에」 + (수치 또는 순위)
- 동 버퍼 + 추가 수치 (높이 40m 이상 등). 버퍼만 있는 건수는 기존 `place_buffer_*`
- 구조 + (층수·순위·면적/높이)
- 「평균/합계」 + 높이·면적·층
- 「용도별 개수/분포」(「용도별건물」데이터셋 질의는 제외)

고빈도 단일 패턴(구+용도 건수, 단일 임계 건수, 단일 지표 순위)은 **Router가 먼저** 가져간다.

**Filter:** `usage`, `structure`, `height_m`, `building_area_m2`, `gross_floor_area_m2`, `site_area_m2`, `ground_floors`, `basement_floors`

**출력:** `name`, `legal_dong`, `lot_address`, `usage`, `height_m`, 면적·층수 canonical alias

**별칭:** 아파트→공동주택, 창고→창고시설, 학교→교육연구시설. 단위는 기존 `units.py`.

**명시적 미지원 → RAG fallback**

- 건축년수 / 사용승인 (D198 coverage)
- 시가총액 등 catalog 밖 필드
- 역·POI 좌표 (동·구 경계 거리는 지원)
- `entity != building`
- 필터 6개·공간관계 2개 초과

**clarify**

- 맨몸 「면적」(건축면적/연면적/대지면적 구분 불가)
- Plan `requires_clarification=true`
- 동명 충돌은 기존 `check_ambiguity()` 가 SQP보다 먼저 처리

MVP로 안정 동작을 보는 질문 예:

```text
해운대구 아파트 중 높이 70m 이상인 건물 이름과 높이
금정구에서 연면적 5000㎡ 이상이고 15층 이상인 건물
동래구 철근콘크리트 건물 중 높이가 높은 10개
해운대구 공동주택 중 건축면적이 1000㎡ 이상인 건물 수
```

고빈도 패턴은 기존 Router가 먼저 가져가므로, SQP는 Router가 놓친 long-tail 에 쓰인다.

---

## 6. Generator 정책 (원안 수정)

원안은 모든 Plan을 LLM structured JSON으로 만들라고 했다. 작은 로컬 모델은 JSON 스키마를 자주 깨고, 단순 질의에 LLM을 쓰면 지연만 늘어난다.

0.2.2는 **deterministic heuristic을 먼저** 쓴다.

1. `extract_place` / `extract_usage` / `units` 로 hint
2. 장소·용도·수치 필터가 충분하면 heuristic Plan
3. 부족할 때만 Ollama `format=json`, temperature 0, 실패 시 1회 repair
4. 그래도 실패하면 RAG

Hint는 validator가 다시 검사한다. LLM 출력에 `SELECT`, `A16`, `AL_D010` 이 있으면 파싱 단계에서 버린다.

---

## 7. Feature flag

| 변수 | 기본 | 의미 |
|------|------|------|
| `SEMANTIC_PLAN_MODE` | `hybrid` | `off` / `shadow` / `hybrid` |
| `SEMANTIC_PLAN_MAX_RETRIES` | `1` | JSON repair 횟수 |
| `SEMANTIC_PLAN_MIN_QUALITY` | `0.85` | 미만이면 fallback |
| `SEMANTIC_PLAN_DEBUG` | `false` | `plan_quality` 등 디버그 필드 |

`planner_first`(Router보다 SQP 우선)는 구현하지 않는다.

권장 운영: 기본 `hybrid`. 관측만 하면 `shadow`, 장애 시 `off`.

---

## 8. 안전

- 사용자 문자열은 identifier가 되지 않는다. catalog lookup 후 `"A16"` 같은 고정 컬럼만 쓴다.
- 리터럴은 `'` 이스케이프. `해운대구' OR 1=1 --` 는 문자열 안에만 남는다.
- `SELECT *` / DML 금지. 기존 readonly·EXPLAIN 체인 재사용.
- SQP SQL 오류를 LLM에게 다시 쓰라고 넘기지 않는다. Plan 실패면 RAG로 넘긴다.
- 검증을 통과한 0건은 실제 결과로 본다. 조건 임의 완화 금지.
- GeoServer 실패는 채팅 답변을 깨지 않는다 (기존 정책).
- `model_confidence` 단독으로 실행하지 않는다.

---

## 9. 세션·지도·차트

- `SessionContext.last_semantic_plan` 저장. 후속 `그중 100m 이상` / `높이 순` / `10개만` / `지번도 같이` 는 Plan delta로 병합. 직전 라우터 D010 결과도 heuristic Plan을 재구성해 같은 delta를 탄다
- 「N m 이상만」은 차트 시리즈 필터(`chart_help`)가 가로채지 않는다
- route `semantic_plan_*` 는 지도 skip 목록에 넣지 않는다. D010 SELECT면 기존 geometry 주입이 동작한다.
- route `semantic_plan_*` 는 지도 skip 목록에 넣지 않는다. D010 SELECT면 기존 geometry 주입이 동작한다.
- distribution 은 `usage` + `n`/`count` alias 로 차트 제안을 탈 수 있다.
- 단순 결과는 SQP 템플릿 답변(LLM 2회 호출 회피).

---

## 10. 테스트

```text
uv run python scripts/test_semantic_plan.py
```

- catalog 매핑 (name→A24, height_m→A16, 면적 컬럼 분리)
- 용도·평·km 정규화
- unknown field / contains(height) / 맨몸 면적 / 건축년수
- 컴파일 SQL에 D010·A4·A9·A16, DML 없음
- place 리터럴 이스케이프
- heuristic MVP 질문, 물리 컬럼·SQL leak 파싱 거부
- 행정 경계 `ST_Intersects`, 동 버퍼 `ST_DWithin`
- Plan follow-up delta (filter/sort/limit/select). 직전 D010 SQL에서도 동작
- Router 동등 패턴 parity: 용도 건수, 높이 임계, 연면적 순위 (Router는 계속 우선)
- 복합질의 위임: 높이+연면적·공간+수치 등은 `try_route is None` 후 heuristic SQL에 모든 조건이 남는지
- 스모크: `scripts/smoke_compound30.py` (`SEMANTIC_PLAN_MODE=hybrid`)

`planner_first`로 Router를 끄지 않는다. parity가 확인된 패턴도 **Router 적중이 우선**이고, SQP는 미적중 long-tail·복합조건·후속 delta용이다.

---

## 11. 하지 않는 것 (원안 대비)

1. LLM에게 SQL을 만들게 하기
2. Plan에 raw SQL / 물리 테이블명
3. 검증 실패 Plan 억지 실행
4. 기존 RAG 삭제
5. 기존 Router를 SQP로 교체 (`planner_first` 없음. parity 패턴도 Router 우선)
6. generic join graph, 산업단지 SQP, D198 coverage 확장
7. 버전을 0.3.0으로 올리는 일 — 제품 버전은 **0.2.3**. Plan 스키마는 v1.1이다

---

## 12. 다음 단계 (이후)

1. ~~`hybrid` 기본값 검토~~ — FIX-4에서 승격 완료 (`sqp-v11-ready`)
2. 기초구역·산업단지 spatial relation을 SQP catalog로
3. Plan delta에 `remove_filter` / `change_scope` (event log의 remove/change_scope는 구현됨. catalog 확장과 연동 검토)
4. parity가 장기간 유지되는 패턴만 Router와 compiler 공유 검토
5. 그때 0.3.0 검토
6. QLoRA는 verified pair 5,000 미만이라 `NOT_ELIGIBLE`

목표 4계층은 유지한다.

```text
Level 1  Guide / Meta / Clarify / Session
Level 2  Rule Router
Level 3  Semantic Query Plan → deterministic SQL
Level 4  RAG + LLM free-form SQL
```
