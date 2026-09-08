# llm2_geodb → llm2sql 고도화 비교

| 항목 | 내용 |
|------|------|
| 기준 시스템 | `llm2_geodb` (LM2GISDB **v0.1.0**) — 대화형 GIS 해석·오케스트레이션 |
| 고도화 시스템 | **`txt2sql` v0.3.3** — 자연어→SQL→한국어 답변 + 지도·데이터 관리 + SQP(hybrid) + Query Contract·plan repair |
| 비교 범위 | 자연어·SQL·의도·메타데이터·안전·세션·답변·지도·데이터 관리·제품 표면 |
| 작성 기준일 | 2026-08-25 |
| 작성 목적 | `llm2_geodb`에서 `txt2sql`로 이어진 **고도화 내용** 문서화 |

> **현재 버전: txt2sql 0.3.3** (`pyproject.toml`, FastAPI `version`, 웹 Head/Bottom 패널 표기와 동일). SQP Plan 스키마는 v1.1이고 `SEMANTIC_PLAN_MODE` 기본값은 `hybrid`다. 0.3.3 상세는 `docs/20260908_txt2sql_v0.3.3.md`, 0.3.2는 `docs/20260904_txt2sql_v0.3.2.md`, 0.3.0은 `docs/20260825_txt2sql_v0.3.0.md`.

---

## 목차

1. [요약](#1-요약)
2. [문제 정의와 포지셔닝](#2-문제-정의와-포지셔닝)
3. [계승한 설계](#3-계승한-설계)
4. [고도화 핵심: NL→SQL 완결](#4-고도화-핵심-nlsql-완결)
5. [의도·라우팅 고도화](#5-의도라우팅-고도화)
6. [스키마·메타데이터 고도화](#6-스키마메타데이터-고도화)
7. [SQL 검증·교정·실행](#7-sql-검증교정실행)
8. [세션·모호성·후속 질의](#8-세션모호성후속-질의)
9. [답변 생성·품질](#9-답변-생성품질)
10. [지도 시각화 고도화](#10-지도-시각화-고도화)
11. [데이터 관리 고도화](#11-데이터-관리-고도화)
12. [제품 표면·운영](#12-제품-표면운영)
13. [처리 흐름 대비](#13-처리-흐름-대비)
14. [기능 대조표](#14-기능-대조표)
15. [연구 한계(L)와 llm2sql 대응](#15-연구-한계l와-llm2sql-대응)
16. [모듈 매핑](#16-모듈-매핑)
17. [버전별 고도화](#17-버전별-고도화)
18. [결론](#18-결론)

---

## 1. 요약

`llm2_geodb`(LM2GISDB v0.1.0)는 행정 GIS DB의 **스키마·질의·결과 이해 장벽**을 완화하기 위해, 사용자가 제공한 Spatial SQL을 **안전하게 실행**하고 결과를 **메타데이터 주입 한국어로 해석**한 뒤 **GeoServer·OpenLayers 지도**에 올리는 대화 계층을 정립했다. 다만 **자연어→SQL 생성(NL→SQL)** 은 의도적으로 비어 있었고, 연구보고서에서도 **L5: 스키마 RAG + Text-to-SQL + 검증 실행** 을 후속 과제로 명시했다. UI의 자연어→SQL 버튼은 stub였다.

**`txt2sql` v0.2.1**은 이 기반(읽기 전용 거버넌스, 메타데이터 정렬 답변, 의도 분기, 다턴 맥락, 로컬 Ollama, GeoServer 지도, Shapefile·메타데이터 관리)을 계승하면서, L5를 중심으로 **자연어 질의만으로 SELECT를 생성·검증·실행하고 한국어·차트·지도로 답하는 파이프라인**을 구현했다. **현재 배포는 v0.2.3**이다. 라우터 미적중·복합조건 구간에 Semantic Query Plan을 끼우고, 기본 `SEMANTIC_PLAN_MODE=hybrid`이면 검증된 Plan SQL을 실행한다. `off`이면 0.2.1과 같다.

> 한 줄: **해석·안전 실행·사용자 SQL 지도화(llm2_geodb) → 자연어 조회 완결 + 자동 지도·제품화(llm2sql) → 복합조건은 canonical Plan→확정 SQL(0.2.3, 기본 `hybrid`)**.

---

## 2. 문제 정의와 포지셔닝

### 2.1 공통으로 다루는 세 가지 장벽

| 장벽 | 현상 | llm2_geodb 대응 | llm2sql (현재 0.2.3) 대응 |
|------|------|-----------------|---------------------------|
| 스키마 장벽 | `AL_D010_…`, `A9` 등 코드·영문 식별자 | 실행 SQL의 테이블 메타 조회 후 해석에 주입 | 스키마 RAG + display/fix + 답변·Identify에서 표시명 우선. SQP는 물리명을 Plan에 넣지 않음 |
| 질의 장벽 | PostGIS·JOIN 숙련 요구 | SQL 작성 **상담 힌트** (자동 생성·실행 루프 없음) | **NL→SQL 생성** + 규칙 라우터 + SQP(기본 hybrid) + 검증·재시도 |
| 결과 이해 장벽 | 실행 성공 ≠ 의미 이해 | 샘플 결과 + 메타 → 한국어 해석 + 지도 | 템플릿/LLM 서술 + 프로필·비교·차트 + **자동 지도 발행** |

### 2.2 시스템 포지션

| 구분 | llm2_geodb v0.1.0 | llm2sql (현재 0.2.3) |
|------|-------------------|----------------------|
| 핵심 I/O | SQL 입력 → 실행 → 한국어 해석 → 지도 | 자연어 입력 → SQL → 실행 → 한국어 답변 → (선택) 차트·지도 |
| NL→SQL | **미구현** (UI stub, L5) | **구현** (규칙 + SQP 기본 hybrid + RAG+LLM) |
| 의도 모델 | `EXECUTE` / `SQL` / `CHAT` | `guide`·`meta`·`clarify`·`profile`·`rank_compare`·`sql` 등 9라벨 |
| SQL 출처 | 사용자 작성·붙여넣기·히스토리 회수 | 시스템 생성(또는 규칙 템플릿) |
| 주 스택 | Flask · Ollama · PostGIS · GeoServer | FastAPI/CLI 엔진 · Ollama · PostGIS · sqlglot · GeoServer(선택) |
| 웹 구성 | 단일 지도+채팅 화면 | Head/Bottom 공통 프레임 · 지도 `/map` · 채팅 `/`·`/chat` · 데이터 관리 `/data` |
| 도메인 | 범용 GIS 대화 프로토타입 | 부산 GIS(건물·행정구역·기초구역·산업단지)에 특화 |

### 2.3 설계 철학의 연속성

양 시스템 모두 다음 제약을 공유한다.

1. 원본 DB에 대한 **쓰기성 연산 금지** (업로드·메타데이터·임시 `temp_*` 레이어만 예외)
2. 사용자 대면 설명은 **업무 표시명·설명** 우선
3. 가능하면 **온프레미스 LLM(Ollama)** 으로 처리
4. 규칙과 LLM을 **계층화**하여 비용·안정성 균형

`txt2sql`은 여기에 **“질문을 SQL로 바꿀 책임”** 을 시스템이 지고, 그 책임을 **검증 루프**로 뒷받침한다. 지도는 사용자가 SQL을 넣을 때가 아니라 **채팅 성공 결과에서 자동 발행**된다.

---

## 3. 계승한 설계

### 3.1 읽기 전용 거버넌스

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 핵심 API | `dialogue/safety.py` — `assert_readonly_sql`, `guard_and_execute` | `llm2sql/db.py` — `assert_readonly_sql`, `ensure_limit`, `execute_query` |
| 정책 | SELECT/WITH 계열 조회만 허용, 쓰기·DDL 거부 | 동일 + 다중 문장(`;`) 차단, LIMIT 강제, geometry 직렬화 생략 |
| 계층 | 의도 분류 · 생성 프롬프트 · 실행 API 키워드 차단 | 의도/가이드 거절 · SQL 생성 프롬프트 · 실행 전 가드 · 사전 검증 |
| 지도 DDL | 질의 결과용 임시 레이어 | 채팅은 읽기 전용 유지. 지도용 `temp_*` UNLOGGED만 서버가 생성·삭제 |

### 3.2 메타데이터 정렬 해석·답변

`llm2_geodb`는 실행 SQL에서 주 테이블을 추출한 뒤 `table_metadata` / `column_metadata`(및 col_def·pnu_def 계열)로 **표시명·설명·단위**를 프롬프트에 넣어 해석한다. SQL 재출력을 억제하는 **해석 모드**와 일반 **상담 모드**를 분리한다.

`txt2sql`은 동일 메타 자산을 다음으로 확장한다.

- 답변·프로필 서술에서 컬럼코드(`A9` 등) **비노출**
- 생성 SQL의 한글 display → 물리명 **rewrite** (`sql_fix.py`)
- 스키마 RAG 카탈로그에 동의어·샘플값 반영 (`semantic_meta.py`, `schema_retriever.py`)
- 메타 전용 QA 경로 (`meta_qa.py`) — “어떤 데이터가 있어?”, “A4 의미가 뭐야?”
- KorDB 레이어·Identify·속성 테이블에도 **한글 표시명** 사용

### 3.3 의도 분기와 이중 게이트

`llm2_geodb`의 ORCH는 **규칙 게이트**(단문 실행, 발화 내 SQL 추출) 후 **LLM 의도 분류**(EXECUTE/SQL/CHAT)로 분기한다. 실행과 상담을 API·프롬프트 수준에서 분리한다.

`txt2sql`은 “실행할지 말지”를 넘어 **어떤 스킬로 답할지** 까지 라우팅한다. 고빈도 조회는 규칙 라우터로 LLM을 우회하고, 애매한 경우만 생성 경로로 보낸다.

### 3.4 다턴 대화 상태

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 상태 위치 | 브라우저 `chatHistory`, `lastExecutedSQL` | 서버/CLI 공용 `SessionContext` |
| 주 용도 | SQL 재실행, 상담 맥락 | clarify 선택, focus 건물 후속, 차트 pending, 직전 질문 확장, **분석 레이어 재사용** |
| 한계(당시) | 히스토리 무제한·요약 메모리 없음 | 슬라이딩 요약은 제한적이나, 구조화 슬롯으로 후속 정확도 향상 |

### 3.5 로컬 LLM·후처리

- Ollama 기반 호출, 모델 교체 가능 사상
- think 태그·역할 접두어 등 출력 정규화 아이디어 → `txt2sql` 답변 경로에서도 정제·템플릿 폴백으로 품질 관리

### 3.6 지도·데이터 인프라

`llm2_geodb`가 정립한 GeoServer 워크스페이스(`korDB`)·PostGIS 데이터스토어(`KoreaDB`)·Shapefile ZIP 적재·메타데이터 화면을 `txt2sql` v0.2~0.2.1이 **엔진 파이프라인에 결합**하여 재구현했다. 입력은 자연어 채팅만 쓰고, Spatial SQL 텍스트 영역은 이식하지 않았다.

---

## 4. 고도화 핵심: NL→SQL 완결

### 4.1 llm2_geodb의 공백 (L5)

연구보고서 한계표:

| ID | 한계 | 후속 방향 |
|----|------|-----------|
| L5 | NL→SQL 미완 | 스키마 RAG + Text-to-SQL + 검증 실행 |

UI에도 자연어→SQL 버튼이 stub로 남아 “아직 구현되지 않았습니다” 메시지를 넣는 수준이었다. 채팅의 SQL 도움은 **자유 텍스트 제안**이며, 생성 SQL의 자동 검증·실행 루프는 없다.

### 4.2 llm2sql의 생성 파이프라인

미매칭(규칙 라우터 비적중) 질의에 대해, 0.2.2는 먼저 SQP(`SEMANTIC_PLAN_MODE`)를 시도하고 실패·`off`이면 아래로 간다.

```text
질문
 → (0.2.2) Semantic Query Plan hybrid — 실패 시 계속
 → retrieve_schema (llm_schema_catalog 벡터 검색 + 도메인 강제 포함/제외)
 → build_few_shot_for_question (정적 FEW_SHOT + example_store 동적 검색)
 → generate_sql (Ollama)
 → rewrite_display_names / fix_common_sql_mistakes
 → validate_sql_preexec (도메인 진단 → sqlglot → EXPLAIN)
 → execute_query (+ 오류·빈결과 시 재생성, 최대 sql_max_retries)
 → format_success (한국어 답변)
 → attach_map (적격 시 GeoServer 임시 레이어)
```

관련 모듈:

| 모듈 | 역할 |
|------|------|
| `schema_retriever.py` | 임베딩 검색, 컴팩트 스키마 조립 |
| `sql_generator.py` | SYSTEM_PROMPT, `generate_sql` |
| `prompt_examples.py` | 정적 few-shot, DOMAIN_HINTS |
| `example_store.py` | 임베딩+Jaccard 예제 검색 |
| `sql_fix.py` | 한글명 → 물리 테이블/컬럼명 |
| `sql_validator.py` | 사전 검증 체인 |
| `rag_sql.py` | 라우터·SQP 우회한 RAG-only 벤치 경로 |
| `semantic_plan/` | canonical Plan → compiler SQL (0.2.2) |

### 4.3 규칙 SQL 경로 (성능·안정성)

고빈도 패턴(건수, 높이/층수 필터, top-N 순위, 버퍼, 동 경계 교차, 산업단지, 건축년수 등)은 `intent_router.py`가 **템플릿 SQL**을 직접 만들어 LLM 생성을 우회한다.  
이는 `llm2_geodb`의 “규칙 우선 게이트” 철학을 **조회 생성 단계**까지 확장한 것이다.

`route_dispatch.py`(v0.1.2)는 `try_route` 1회 호출 + early allowlist + deferred 재사용으로 디스패치 비용을 줄인다.

한 질문에 수치·공간·구조·순위가 **둘 이상** 겹치면 라우터가 조건 하나만 적용하지 않는다. `should_defer_compound_to_plan`이 `None`을 반환하고, `SEMANTIC_PLAN_MODE=hybrid`이면 SQP가 확정 SQL을 만든다.

### 4.3.1 Semantic Query Plan (v0.2.2)

라우터 미적중 뒤 LLM이 물리 SQL을 직접 쓰던 구간에 canonical JSON Plan을 끼운다. compiler가 카탈로그 allowlist로 SELECT를 만든다. v1.1 승격 후 기본 모드는 `hybrid`다. Plan SQL을 실행하고 실패·clarify·품질 미달이면 기존 RAG로 내려간다. `off`는 0.2.1과 같고, `shadow`는 생성만 한다. SQL 오류 시 `execute_query`가 `rollback`하여 세션의 다음 질의를 지킨다. heuristic gold verified는 30/30, 복합 30문항 스모크(`scripts/smoke_compound30.py`)는 hybrid 기준이다.

### 4.4 지명 사전 (v0.1.4)

`gazetteer.py`는 부산 법정동·행정동·구군을 **트라이 최장일치**로 찾아, `구서역`·`공동주택` 같은 오탐을 줄인다. 행정전용 동은 경계 교차(`BND_ADM_DONG_PG`), 법정동은 속성 LIKE로 분기한다. `llm2_geodb`에는 등록 명칭 사전이 없었다.

---

## 5. 의도·라우팅 고도화

### 5.1 의도 라벨 비교

| llm2_geodb | llm2sql (`INTENT_LABELS`) | 설명 |
|------------|---------------------------|------|
| EXECUTE | (세션 후속·재질의로 대체) | 직전 SQL 재실행 중심 → focus/clarify 선택으로 재구성 |
| SQL | `sql` | DB 조회 필요 일반 질의 |
| CHAT | `guide`, `meta`, `out_of_scope` 등으로 세분 | 상담을 스킬별로 분리 |
| — | `clarify` | 동명 모호·주관 표현 확인 |
| — | `profile` | 지역·용도 특징 요약·비교 |
| — | `usage_overview` | 용도 구성·분포 |
| — | `rank_compare` | 복수 지역 최고 건물 비교 |
| — | `coverage` | 자료 범위(부산만인지 등) |

### 5.2 분류 방식

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 기본 | LLM 초단답 + 부분문자열 라벨 파싱 | `INTENT_MODE=hybrid` (기본): LLM JSON + 규칙 폴백/보정 |
| 규칙 | 단문 실행·SQL 포함 여부 | `predict_intent_rules`, 알려진 오분류 패치(메타/프로필/단일지역 순위 등) |
| 설정 | — | `rules` / `hybrid` / `llm` 전환 가능 |

### 5.3 파이프라인 상 스킬 순서 (개념)

`txt2sql` `_ask_inner` / `run_ask` 기준 개념 순서:

1. 세션 전처리 (clarify 번호 선택, 차트 수락/거절/종류, 짧은 기준 보정 등)
2. 의도 분류 (hybrid/llm)
3. `guide_qa` — 기능·제한·인사·범위 외
4. SQP / 목록 후속, `followup_qa` — Plan delta 또는 focus 건물 속성
5. early `match_route` — 건물명·산업단지·순위 등. 복합조건은 미적중
6. preferred intent 디스패치 — rank_compare / profile / meta / clarify …
7. `rank_compare_qa` → `profile_qa` / usage_overview → `meta_qa` → `clarify_qa`
8. `intent_router` (규칙 SQL). `should_defer_compound_to_plan`이면 None
9. Semantic Query Plan (기본 `hybrid`. `shadow`는 생성만, `off`는 생략) — 실패 시 아래로
10. RAG + LLM 생성·검증·실행
11. `answer` + (선택) 차트 제안, `attach_map`, 세션 갱신

`llm2_geodb`에는 위와 같은 **도메인 스킬 분해**가 없고, 상담·해석·재실행의 오케스트레이션에 가깝다.

---

## 6. 스키마·메타데이터 고도화

### 6.1 스키마 선택

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 방법 | SQL 문자열에서 첫 `FROM` 테이블 정규식 | `llm_schema_catalog` 벡터 유사도 검색 |
| 약점/보완 | JOIN·서브쿼리 시 메타 누락 (L1) | top-k + 건물/행정/산업단지 테이블 **강제 포함·제외** 휴리스틱 |
| 임베딩 | 없음 | Ollama embed (`OLLAMA_EMBED_MODEL`, 예: `mxbai-embed-large`) |
| 갱신 | 메타 CSV 적재 도구 | `scripts/refresh_schema_catalog.py` |

### 6.2 의미 메타·동의어

`txt2sql`은 `semantic_meta.py`로 테이블/컬럼 동의어를 보강하고, 컴팩트 스키마 텍스트에 샘플값·한글 설명을 실어 생성 프롬프트의 적중률을 높인다.  
`llm2_geodb`의 “메타 주입 해석”이 **실행 후 설명**에 쓰였다면, `txt2sql`에서는 **생성 전 스키마 선택·프롬프트**와 **실행 후 답변·Identify** 양쪽에 쓰인다.

### 6.3 메타 QA 전용 경로

| 질문 예 | llm2_geodb | llm2sql |
|---------|------------|---------|
| 어떤 데이터가 있어? | 일반 CHAT 상담 | `meta_catalog` |
| A4 컬럼 의미가 뭐야? | 상담(정확도 편차) | `meta_column` / display명 매칭 |
| 특정 데이터셋 요약 | 없음 | 건수·상위 용도 등 **요약** (스키마 나열만 하지 않음) |

---

## 7. SQL 검증·교정·실행

### 7.1 검증 깊이

| 단계 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 읽기 전용 키워드 | 있음 (선두/키워드 가드) | 있음 (강화: 금지 키워드 목록, 단일 문장) |
| 구문 검증 | 없음 (TODO 수준) | **sqlglot** |
| 계획 검증 | 없음 | **EXPLAIN** |
| 도메인 진단 | 없음 | `diagnose_sql` (잘못된 테이블/패턴 등) |
| 실행 실패 복구 | 사용자 재작성 의존 | LLM 재생성 + `error_feedback`, 재시도 상한 |
| 빈 결과 | 해석만 | 진단 후 재생성 / 공간 의도 시 PostGIS 강제·템플릿 폴백 |
| 공통 실수 교정 | 없음 | `fix_common_sql_mistakes` (예: A3→A4, D198↔D010, 순위 강제) |
| LIMIT | 실행 정책에 따름 | `ensure_limit`로 기본 LIMIT 부여 |

### 7.2 안전 속성 강화 포인트

`llm2_geodb`도 3층 안전 정책을 명시했으나, 가드가 **선두 키워드**에 치우친 단순 패턴이었다.  
`txt2sql`은 생성 SQL이 시스템에 의해 만들어지므로 **사전 검증 체인**이 더 중요하다. 여전히 범용 SQL 인젝션·CTE 우회에 대한 완전 형식 검증은 아니며, **읽기 전용 + 단일 SELECT + LIMIT** 을 운영 전제로 한다.

지도 DDL은 `temp_[0-9a-f]{8,32}` 이름에만 허용하고, 원본 KorDB 테이블 삭제는 API가 거부한다. `execute_query`는 SQL 오류 시 `conn.rollback()` 하여 aborted transaction이 다음 질의를 오염하지 않게 한다.

---

## 8. 세션·모호성·후속 질의

### 8.1 모호성 (Clarification)

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 구조화 슬롯 | 없음 | `clarify_qa.check_ambiguity` |
| 동명 복수 구 | 상담에 의존 | 후보 제시 → `1` / `1번` / 구 이름 선택 |
| 주관 표현(좋은/추천) | 상담 안내 | `clarify_vague` — 수치 기준으로 유도 |
| 미지 지명 | 상담 | `clarify_unknown_place` 등 |

확인이 끝나기 전에는 지도를 발행하지 않는다.

### 8.2 후속 질의

| 항목 | llm2_geodb | llm2sql |
|------|------------|---------|
| 중심 객체 | 직전 **SQL** | 직전 **focus 건물**(및 last question/route/Plan) |
| 예 | “실행해줘”, “다시” | “그 아파트의 이름은?”, “지번은?”, “그중 높이 80m 이상만” |
| 구현 | RecoverSQL / lastExecutedSQL | `followup_qa` + Plan delta (`semantic_plan/followup.py`) + `SessionContext` |
| 짧은 보정 | 자유 상담 | 건축년수 기준 등 `_expand_followup_question` |

### 8.3 세션 객체 (`SessionContext`)

대표 상태:

- clarify 후보·선택 대기
- focus 건물(속성 후속용)
- pending chart (제안·종류·시리즈 필터)
- last question / route / full question
- **last_semantic_plan / last_semantic_plan_route** — 복합질의 후속 delta (0.2.2)
- **last_map_scope / last_map_payload** — 같은 FROM/WHERE면 분석 레이어 재사용

웹은 `session_id`로, CLI는 `--chat`으로 동일 모델을 유지한다.

---

## 9. 답변 생성·품질

### 9.1 해석 vs 답변

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 주 모드 | 실행 결과 **해석** (`interpret_sql_result`) | 질의 목적에 맞는 **답변** (`format_success` 등) |
| 입력 | SQL + 샘플 rows + 메타 컨텍스트 | route·rows·도메인 템플릿·(선택) LLM 서술 |
| SQL 노출 | 해석 본문에서 SQL 재출력 금지 설계 | 사용자 답변은 의미 중심, verbose/API로 SQL 확인 가능 |
| 샘플 | 상위 N건 중심 | 집계·순위·프로필은 **의도된 집계 SQL** 결과를 서술 |

### 9.2 템플릿 우선과 할루시네이션 완화

순위·건물명·일부 산업단지 경로는 **템플릿 답변**을 우선해 컬럼코드 노출·과장 서술을 막는다.  
특징 요약(`profile_qa`)은 집계 후 LLM 문단 서술, 실패 시 문장 폴백이다.

### 9.3 도메인 품질

- `domain.py`: 구·동·용도 별칭, 부산시 전역, 건축년수, 건물명 후보, 한국어 조사
- 이상값 필터: 비정상 높이·건축면적·연면적 제외 (`sane_height_sql` 등)
- 공간 템플릿: `spatial_templates.py` (동 내 건물 건수 등)
- 단위: `units.py` — 평→㎡, km→m, 준공연도→사용승인일 등

### 9.4 차트 (지도와 별개)

`chart_qa.py`는 비교·프로필 등 답변 후 **Chart.js 스펙 제안**, 차트 종류 변경, 시리즈 필터(예: 높이만)를 제공한다.  
`llm2_geodb`에는 결과 이해 보조가 지도·해석 텍스트에 한정되었고, 채팅 내 차트는 없다.

---

## 10. 지도 시각화 고도화

`llm2_geodb`는 **사용자가 넣은 Spatial SQL**을 실행한 뒤 GeoServer 레이어를 만들고 OpenLayers로 그렸다. `txt2sql` v0.2부터는 **시스템이 만든 SELECT**에 geometry를 주입(또는 집계를 행정 경계로 대체)해 자동 발행한다. v0.2.1에서 UI·수명·Identify를 제품 수준으로 다듬었다.

### 10.1 발행 모델

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 트리거 | 사용자가 SQL을 넣고 “맵 쿼리” | 채팅 성공 + `include_map=true` + 적격 route |
| SQL 변환 | 사용자 SQL 그대로(또는 근접) | `plan_map_sql` — geometry 주입, COUNT→행정동 경계, UNION ALL 비교 |
| 테이블 | 세션 임시 데이터 | `CREATE UNLOGGED TABLE temp_[hex]` + GIST |
| 실패 정책 | 실행/지도가 한 흐름 | **지도 실패해도 채팅 답변 유지** |
| 재사용 | 질의마다 새 레이어 경향 | 같은 FROM/WHERE 스코프면 **기존 분석 레이어 재사용** |
| GeoServer 미설정 | 지도 중심이라 기능 공백 | 채팅만 동작, 발행 건너뜀 |

### 10.2 웹 레이아웃

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 화면 | Flask 단일 지도+채팅 | FastAPI **3분할** (레이어 · 지도 · 채팅) + **채팅 전용** `/chat` |
| 사이트 프레임 | 메뉴가 지도 앱에 종속 | 공통 **Head**(지도·채팅·데이터 관리) / **Bottom**(버전 표기, 현재 0.2.3) |
| 채팅 폭 | 고정에 가깝 | 기본 600px, 드래그 조절 |
| SQL 입력란 | Spatial SQL 텍스트 영역 | **이식하지 않음** — 자연어만 |

### 10.3 레이어 체계

`txt2sql`은 z-index의 유일한 기준을 **출력 레이어**로 둔다.

| 구분 | 동작 |
|------|------|
| 출력 레이어 | KorDB·분석결과를 체크하면 추가. ▲▼·드래그가 곧 겹침 순서. 우클릭 삭제·속성 테이블 |
| KorDB | 카탈로그 체크 시 bbox fit, 한글 표시명. 해제는 출력에서만 제거 |
| 분석결과 | 질의마다 적재. 세션당 최대 `MAP_MAX_ANALYSIS_LAYERS`(기본 8). 「모두 지우기」는 섹션 하단 |
| 배경 | OSM / Carto Dark / ESRI Imagery **최대 1개** (전부 끄기 가능). 항상 z-index 0 |

### 10.4 렌더링·Identify

| 항목 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 렌더 선택 | 특성 1000개 기준 WMS vs GeoJSON | WMS 기본. `MAP_WFS_MAX_FEATURES` 이하면 WFS. ImageWMS(분석)/TileWMS(KorDB) |
| Identify | WMS GetFeatureInfo, 채팅/팝업 혼재 가능 | 맨 위 출력 레이어부터 조회. LLM 설명은 **팝업만** (`POST /api/map/explain`) |
| 스타일 | 투명도·순서 | 테마(기본/컬러풀/지구본/모던) + 분석 레이어 SLD(채우기·선·두께·투명도) |
| 수명 | 24시간 임시 데이터, 세션 정리 | `MAP_RETENTION_HOURS`(24h) TTL + 새 대화 cleanup + 세션 상한 |

---

## 11. 데이터 관리 고도화

v0.2.1에서 `llm2_geodb`의 공간데이터 업로드·메타데이터 화면을 Head 메뉴 **데이터 관리**로 이식·정돈했다. 채팅 SQL 경로는 읽기 전용을 유지하고, 쓰기는 이 메뉴의 API만 수행한다.

| 화면 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 업로드 | `templates/spatial_data_upload.html` · Flask `/api/upload-shapefile` | `/data/upload` · `POST /api/data/upload` (Shapefile ZIP → PostGIS → GeoServer `korDB`) |
| 메타데이터 | `templates/metadata_update.html` | `/data/metadata` — 표시명·설명·단위 저장, `new_table_name`이면 테이블명 변경 |
| 코드 해석 | `parse_table_code` (`col_def`·`pnu_def`) | 동일 사상 — `GET /api/data/tables/{name}/parse` |
| 개요 | 지도 앱 서브메뉴 | `/data` 개요 + Head 드롭다운 |
| 모듈 | `shapefile_uploader.py`, `database_manager.py` | `llm2sql/data/` (`upload.py`, `catalog.py`, `names.py`, `router.py`) |

보호 테이블은 업로드로 덮어쓰지 않도록 `is_protected_table`로 가드한다.

---

## 12. 제품 표면·운영

### 12.1 진입점

| 구분 | llm2_geodb | llm2sql v0.2.1 |
|------|------------|----------------|
| 웹 | Flask `:5000` 단일 앱 | FastAPI `txt2sql-web` (`127.0.0.1:8000`) |
| 지도 | 앱의 본체 | `GET /map` |
| 채팅 | 지도 화면 부속 | `GET /` · `/chat` (지도 없이 대화·차트) |
| 데이터 | 서브 템플릿 | `/data`, `/data/upload`, `/data/metadata` |
| CLI | (대화는 웹 중심) | `txt2sql.cli` 일회·`--chat`, progress/verbose/json |
| 라이브러리 | 모듈 직접 호출 | `Txt2SqlEngine.ask`, `AskResult`, `SessionContext` |

### 12.2 HTTP API (고도화 쪽)

| 메서드 | 경로 | 역할 |
|--------|------|------|
| `POST` | `/api/chat` | SSE 질의. `include_map`이 true일 때만 지도 발행 |
| `POST` | `/api/session` | 새 `session_id` |
| `GET` | `/api/health` | 웹 프로세스 상태 |
| `GET` | `/api/map/status` | GeoServer 연결·WMS/WFS URL |
| `GET` | `/api/map/layers` | KorDB 카탈로그 (`temp_*` 제외) |
| `POST` | `/api/map/attributes` | 속성 테이블 |
| `POST` | `/api/map/explain` | Identify·속성용 LLM 설명 (채팅에 넣지 않음) |
| `DELETE` | `/api/map/layer/{name}` | 임시 분석 레이어만 삭제 |
| `GET/POST` | `/api/data/...` | 테이블 목록·구조·메타·업로드 |

### 12.3 스트리밍·관측

- `on_progress` / `on_token` 콜백, 웹 SSE로 단계·토큰 스트리밍
- `ProgressTracker`로 파이프라인 단계 가시화

### 12.4 설정 (대표)

자격 증명은 `.env`만 사용한다. `llm2_geodb`는 `database_manager.py` / `geoserver_manager.py`에 접속 정보를 두는 방식이었다.

| 변수 | 용도 |
|------|------|
| `DATABASE_URL`, `OLLAMA_*` | DB·로컬 LLM |
| `INTENT_MODE`, `ROUTE_DISPATCH_MODE` | 의도·라우터 |
| `GEOSERVER_*`, `MAP_*` | 지도 발행·TTL·레이어 상한 |

### 12.5 평가·회귀

`llm2_geodb`는 연구용 검증 축(의도 accuracy, 해석 인간평가, 안전 차단율)을 **제시**했다.  
`txt2sql`은 실행 가능한 스크립트로 일부를 상시화한다.

예시:

- `scripts/smoke_nl_queries.py`, `smoke_clarify.py`, `smoke_profile_qa.py`, `smoke_engine.py`
- `scripts/benchmark_intent_hybrid.py`, `benchmark_route_opt.py`, `benchmark_new10.py`
- `scripts/eval_spatial_queries.py`
- 지도: `scripts/test_map_sql.py`, `test_map_layers.py`, `test_map_explain.py`, `smoke_map.py`
- 데이터: `scripts/test_data_admin.py`

---

## 13. 처리 흐름 대비

### 13.1 llm2_geodb

```text
사용자 발화
  ├─ 단문 실행? → 직전/히스토리 SQL 회수 → 읽기전용 실행 → 메타 주입 해석 → (선택) 지도
  ├─ 발화에 SELECT/WITH? → ExtractSQL → 실행 → 해석 → 지도
  ├─ intent=EXECUTE? → 재실행 → 해석
  └─ 그 외 → classify → CHAT 상담 (SQL 생성·검증 루프 없음)
```

### 13.2 llm2sql (현재 0.2.3)

```text
자연어 질문
  → 세션 전처리 (clarify 선택, 차트, SQP/목록 후속, 짧은 후속 보정)
  → 의도 분류 (hybrid)
  → guide / followup / rank_compare / profile / meta / clarify
  → 규칙 라우터 SQL
       ├─ 단건 패턴 적중 → 템플릿 SQL
       └─ 복합조건·미적중 → SQP (기본 hybrid) → 실패 시 RAG+LLM SQL
  → 검증·교정·실행 (재시도). SQL 오류 시 rollback
  → 한국어 답변 (+ 선택적 차트 제안)
  → attach_map (적격 시 temp_* + GeoServer, 같은 스코프면 재사용)
  → SessionContext 갱신 (last_semantic_plan 포함)
```

---

## 14. 기능 대조표

| 기능 | llm2_geodb v0.1.0 | llm2sql (현재 0.2.3) |
|------|:-----------------:|:--------------:|
| 사용자 SQL 실행 | ✅ | △ (시스템 생성 SQL 실행이 주) |
| NL→SQL 생성 | ❌ | ✅ |
| 스키마 벡터 RAG | ❌ | ✅ |
| 동적/정적 few-shot | ❌ | ✅ |
| sqlglot / EXPLAIN 사전검증 | ❌ | ✅ |
| 실행 실패·빈결과 재생성 | ❌ | ✅ |
| 규칙 템플릿 SQL (고빈도) | △ (오케스트레이션 규칙만) | ✅ |
| Semantic Query Plan (복합조건) | ❌ | ✅ (0.2.3, 기본 `hybrid`) |
| 하이브리드 다중 의도 | △ (3라벨) | ✅ (9라벨 스킬) |
| 지명 사전 최장일치 | ❌ | ✅ |
| 메타데이터 주입 설명 | ✅ (해석) | ✅ (해석+생성+meta QA+Identify) |
| 읽기 전용 가드 | ✅ | ✅ (강화) |
| 구조화 clarify | ❌ | ✅ |
| focus 기반 속성 후속 | ❌ | ✅ |
| 특징 요약·지역 비교 | ❌ | ✅ |
| 순위·최고건물 비교 | ❌ | ✅ |
| 이상값 필터 | ❌ | ✅ |
| 차트 스펙 제안 | ❌ | ✅ |
| GeoServer 지도 | ✅ (사용자 SQL) | ✅ (채팅 결과 자동 발행) |
| 3분할 지도 UI · 출력 레이어 | △ (단일 앱) | ✅ |
| 분석 레이어 재사용·상한·TTL | △ (세션 임시) | ✅ |
| Identify LLM 팝업 분리 | △ | ✅ |
| Shapefile 업로드 | ✅ | ✅ (`/data/upload`) |
| 메타데이터 편집 | ✅ | ✅ (`/data/metadata`) |
| 엔진 API + SSE 챗봇 | △ (Flask REST) | ✅ |
| CLI · 라이브러리 | ❌ | ✅ |
| 벤치/스모크 스크립트 | △ (문서 축) | ✅ |

범례: ✅ 지원 / △ 부분·간접 / ❌ 없음

---

## 15. 연구 한계(L)와 llm2sql 대응

`llm2_geodb` 보고서 §18 한계와 대응 현황이다.

| ID | llm2_geodb 한계 | llm2sql (현재 0.2.3)에서의 대응 |
|----|-----------------|---------------------------|
| L1 | FROM 단일 테이블 휴리스틱 | 벡터 RAG + 다중 테이블 스키마 조립·강제 포함 (완전 AST 파서는 아님) |
| L2 | 세미콜론 의존 SQL 추출 | 사용자 SQL 추출 의존도 감소 — **시스템 생성 SQL**이 주경로 |
| L3 | 의도 라벨 부분문자열 파싱 | JSON 의도 분류 + 라벨 enum + hybrid 보정 |
| L4 | 샘플 10건 해석의 대표성 | 질의 유형별 **집계·순위 SQL**로 요약; 프로필은 통계 기반 |
| L5 | NL→SQL 미완 | **해소** — 규칙 + SQP(기본 hybrid) + RAG + Text-to-SQL + 검증 실행 |
| L6 | 히스토리 무제한 | 구조화 세션 슬롯으로 핵심 맥락 유지 (장기 요약 메모리는 향후 과제) |
| L7 | `has_last_sql` 가정 | focus/last_question/last_map_scope 등 명시 상태 필드 |
| L8 | 해석·상담 라우팅 UI 부재 | 의도·스킬 자동 라우터 + 웹 보기 버튼(clarify) + 화면 분리(지도/채팅/데이터) |

---

## 16. 모듈 매핑

### 16.1 llm2_geodb (v0.1.0)

| 영역 | 주요 위치 |
|------|-----------|
| 의도 | `llm_interpreter.classify_intent` |
| 상담 | `chat`, `chat_conversation` |
| 해석 | `interpret_sql_result`, `prompts/sql_interpretation_prompt.txt` |
| 메타 | `dialogue/metadata_context.py` |
| 안전 | `dialogue/safety.py` |
| 정규화 | `dialogue/normalize.py` |
| API | `webapp.py` `/api/chat`, `/api/process-llm`, `/api/execute-query` |
| 지도 | `geoserver_manager.py`, `static/js/map.js`, `layer-manager.js` |
| 데이터 | `shapefile_uploader.py`, `database_manager.py` |
| 임시 | `temp_data_manager.py` |
| 프론트 ORCH | `static/js/map.js`, `dialogue-algorithms.js` |

### 16.2 llm2sql (현재 0.2.3)

| 영역 | 주요 위치 |
|------|-----------|
| 엔진·파이프라인 | `engine.py`, `pipeline.py`, `types.py` |
| 의도 | `intent_classifier.py`, `route_dispatch.py` |
| 규칙 SQL | `intent_router.py` (`should_defer_compound_to_plan`), `spatial_templates.py`, `spatial_router.py` |
| Semantic Query Plan | `semantic_plan/` — models, catalog, generator, validator, compiler, runner, followup |
| RAG·생성 | `schema_retriever.py`, `sql_generator.py`, `example_store.py` |
| 검증·실행 | `sql_validator.py`, `sql_fix.py`, `db.py` |
| 스킬 QA | `guide_qa.py`, `meta_qa.py`, `clarify_qa.py`, `profile_qa.py`, `rank_compare_qa.py`, `followup_qa.py` |
| 답변·차트 | `answer.py`, `chart_qa.py` |
| 세션·도메인 | `session.py`, `domain.py`, `semantic_meta.py`, `gazetteer.py` |
| 지도 | `map/` — `attach.py`, `sql.py`, `publish.py`, `geoserver.py`, `layers.py`, `explain.py` |
| 데이터 | `data/` — `upload.py`, `catalog.py`, `router.py` |
| 웹·CLI | `webapp/`, `cli.py` |

---

## 17. 버전별 고도화

`txt2sql`이 `llm2_geodb` 이후 쌓아 온 단계이다. **현재 배포 버전은 0.3.3**이고, SQP는 v1.1·기본 `hybrid`다.

| 버전 | 요지 |
|------|------|
| **0.1.0** | `Txt2SqlEngine`, 웹 SSE, clarify 번호 선택, 부산시 전역 순위, 이상값 필터, 특징·최고건물 비교, 컬럼코드 비노출 |
| **0.1.1** | 하이브리드 의도, 차트, 데이터셋 요약, 메타/프로필 오분류 수정, RAG·example store·벤치 |
| **0.1.2** | route_dispatch 최적화, 건물명·산업단지·차트 시리즈 필터, `benchmark_route_opt` |
| **0.1.3** | 파이프라인·Ollama·RAG 경로 단순화, 용적율/산업단지 프로필 비교, 고도화 문서 초안 |
| **0.1.4** | 지명 사전(법정/행정), 공간·임계·행정동 목록, 의도분류/답변 템플릿 최적화 |
| **0.2** | 지도 3분할, GeoServer WMS/WFS, KorDB·분석 레이어, Identify. 채팅 전용과 지도 화면 이원화 |
| **0.2.1** | Head/Bottom 프레임, 데이터 관리(업로드·메타데이터), Identify 설명을 팝업으로 분리, 분석 레이어 재사용·상한·TTL, WMS 기본+WFS 선택, 채팅 폭 조절 |
| **0.2.2** | Semantic Query Plan MVP (`off`/`shadow`/`hybrid`, 도입 시 기본 `off`). 복합조건 라우터 위임. 행정 경계·동 버퍼·Plan follow-up delta. SQL 오류 `rollback` |
| **0.2.3** | SQP v1.1 승격. 기본 `hybrid`. verified gold 30/30, linking holdout, live spatial 6/6. 태그 `sqp-v11-ready` |
| **0.3.0** | 업로드 공간테이블 RAG 검색, 지명 사전 자동 갱신, 단계구분도, 열 많은 레이어 속성 설명, 메타데이터 CSV. GitHub 저장소명 `txt2sql` |
| **0.3.2** | PlaceScope·Contract·repair. `docs/20260904_txt2sql_v0.3.2.md` |
| **0.3.3** (현재) | MAIN485 P0–P2 회복 459/485. `docs/20260908_txt2sql_v0.3.3.md` |

`llm2_geodb`는 **v0.1.0**에서 해석·안전 실행·사용자 SQL 지도화·Shapefile ETL을 프로토타입으로 닫고, NL→SQL(L5)을 후속으로 남겼다.

---

## 18. 결론

`llm2_geodb`는 GIS DB에 대한 **질문–(사용자 SQL)–실행–메타데이터 해석–지도** 순환과 **다층 안전·대화 오케스트레이션**을 연구·프로토타입으로 정립했다.

**`txt2sql`**은 그 계층을 전제로 다음을 고도화했다. 0.2.1까지가 L5·제품화의 골격이고, 0.2.3이 Plan SQL을 기본 실행하며, **현재 배포는 0.3.3**이다.

1. **L5 완결**: 스키마 RAG + Text-to-SQL + 검증·재시도 실행  
2. **규칙/하이브리드 라우팅**: 고빈도 LLM 우회 + 9라벨 다스킬 의도. 복합조건은 라우터 미적중으로 두고 SQP에 넘김  
3. **Semantic Query Plan (0.2.2 → 0.2.3)**: canonical JSON → deterministic SQL. 기본 `hybrid`, 실패 시 RAG  
4. **구조화 세션**: clarify 번호 선택, focus 후속, Plan delta, 차트 pending, 지도 스코프 재사용  
5. **도메인 품질**: 부산 GIS 바인딩, 지명 사전, 이상값 필터, 템플릿 답변, 메타/프로필/순위 전용 경로  
6. **지도 재결합**: 사용자 SQL이 아니라 채팅 결과에서 자동 발행. 실패해도 답변 유지  
7. **데이터 관리 이식**: Shapefile 업로드·한글 메타데이터를 Head 메뉴로 제품화  
8. **제품화**: 재사용 엔진, SSE 챗봇, CLI, 벤치·스모크(`smoke_compound30.py`), 공통 사이트 프레임

따라서 `txt2sql` v0.3.3는 `llm2_geodb`의 **대화형 이해·안전 실행·지도·데이터 관리** 성과를 계승하면서, 실무 사용자가 SQL을 직접 쓰지 않아도 되는 **자연어 조회 완결형**으로 발전한 결과물이다. SQP v1.1 기본값은 `hybrid`라 라우터 미적중 복합 질의는 Plan SQL로 닫고, Contract 검증·repair를 거친 뒤 실패 시 RAG로 내려간다. 장애 롤백은 `SEMANTIC_PLAN_MODE=off`다.

---

## 부록. 참고 경로

- 기준: `D:\py_workspace\llm2_geodb\docs\RND_대화형GIS해석_알고리즘_연구결과보고서.md`
- 고도화 알고리즘: `D:\py_workspace\llm2sql\docs\RND_자연어GIS질의_알고리즘_연구결과보고서.md`
- 제품 README: `README.md` (버전 **0.3.3**, SQP v1.1 기본 `hybrid`)
- 0.3.3 변경: `docs/20260908_txt2sql_v0.3.3.md`
- 0.3.0 변경: `docs/20260825_txt2sql_v0.3.0.md`
- Semantic Query Plan: `D:\py_workspace\llm2sql\docs\Semantic_Query_Plan_구현.md`
- 본 문서: `D:\py_workspace\llm2sql\docs\고도화_llm2_geodb_to_txt2sql.md`
