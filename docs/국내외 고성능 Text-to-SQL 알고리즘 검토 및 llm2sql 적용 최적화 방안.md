# 국내외 고성능 Text-to-SQL 알고리즘 검토 및 llm2sql 적용 최적화 방안

**작성일 : 2026-08-25**  
**검토 대상 : llm2sql의 `Contract → Capability → Router/SQP → Compiler` 전환 구조**  
**목적 : 국내외 Text-to-SQL 연구 중 성능평가가 우수하고 현 llm2sql에 실제 이식 가능성이 높은 알고리즘 요소를 선별하여 적용 우선순위와 최적 통합구조를 제안**

---

## 1. 요약

최근 Text-to-SQL 연구의 성능 향상은 단일 대형 LLM의 SQL 생성능력 자체보다 다음 요소를 결합하는 방향에서 두드러짐

1. **정확한 Schema Linking / Schema Retrieval**
2. **복합질문의 단계적 분해**
3. **질문 구조와 유사한 예제 검색**
4. **다중 후보 생성 및 선택**
5. **실행 결과를 이용한 검증·수정**
6. **불확실한 경우에만 추가 추론 또는 재생성**
7. **SQL 문법·스키마 제약에 기반한 생성 제약**

llm2sql에는 모든 최신 알고리즘을 그대로 복제하는 것보다 다음 조합이 가장 적합함

> **Contract-first + Hybrid Schema Linking + Capability-gated Fast Path + Deterministic SQP + Unresolved-only LLM + Contract-aware Execution Verification + Adaptive Candidate Resampling**

즉, 현재 논의 중인

```text
Contract
→ Capability
→ Router / SQP
```

구조를 유지하면서, 국내외 고성능 연구에서 검증된 **Schema Linking, Decomposition, Example Retrieval, Execution Verification, Adaptive Candidate Selection**을 선택적으로 결합하는 것이 가장 합리적임

가장 우선적으로 도입할 알고리즘은 다음과 같음

| 우선순위 | 적용 요소 | 참고 연구 |
|---|---|---|
| S | Vector + graph 기반 Schema Linking | LitE-SQL, DCG-SQL, RAT-SQL, SchemaGraphSQL |
| S | 복합질문 분해 및 의미 슬롯 고정 | DIN-SQL, CHASE-SQL |
| S | Contract-aware execution verification | LitE-SQL, VET, ReEx-SQL |
| A | 구조 기반 Few-shot 검색 | DAIL-SQL, DCG-SQL |
| A | 저신뢰 질문에만 후보 생성·재선택 | R³-SQL, CHASE-SQL, XiYan-SQL |
| B | constrained decoding | PICARD |
| B | 실행 보상 기반 강화학습 | LitE-SQL, ReEx-SQL, ReSQL |

---

# 2. 검토 기준

연구 성능을 단순 benchmark 점수만으로 비교하지 않고 다음 기준으로 평가함

## 2.1 정확도 개선 근거

- Spider Execution Accuracy
- BIRD Execution Accuracy
- Spider 2.0 계열 평가
- robustness benchmark
- 연구 내 ablation 결과

## 2.2 llm2sql 구조 적합성

현재 추진 중인 다음 구조와 결합 가능한지 평가

```text
Natural Language
↓
Query Contract
↓
Route Capability
↓
Router / Semantic Query Plan
↓
Compiler
↓
SQL
```

## 2.3 구현 난이도

- 기존 코드의 부분 확장으로 가능한지
- 별도 모델 학습이 필요한지
- 대규모 agent framework 재구축이 필요한지

## 2.4 추론 비용

- LLM 호출 수
- 다중 candidate 필요 여부
- vector DB 필요 여부
- 모델 크기
- test-time compute 증가량

## 2.5 로컬·프라이버시 환경 적합성

- 소형·오픈소스 모델 적용 가능성
- 데이터 외부 전송 없이 구현 가능한지
- deterministic 모듈과 결합 가능한지

---

# 3. Benchmark 수치 해석 시 유의사항

Spider, BIRD, Spider 2.0의 성능 수치는 서로 직접 비교하면 안 됨

Spider는 200개 데이터베이스와 10,181개 질문을 포함하는 cross-domain benchmark이며, BIRD는 95개 대규모 DB, 12,751개 Text-to-SQL 쌍과 약 33.4GB 규모 데이터를 사용하여 DB value와 외부지식을 더 강하게 요구함

Spider 2.0은 632개의 enterprise workflow 문제를 포함하며 실제 환경에서 1,000개가 넘는 컬럼, 여러 SQL dialect, 장문 SQL, metadata 및 프로젝트 코드 검색까지 요구함

따라서 본 보고서의 성능 수치는 **각 연구가 보고한 동일 benchmark 내부의 성능 수준을 확인하기 위한 근거**이며 서로 다른 benchmark의 숫자를 직접 우열 비교하는 용도로 사용하지 않음

---

# 4. 해외 주요 연구 검토

## 4.1 RAT-SQL — Relation-aware Schema Encoding and Linking

**Wang et al., ACL 2020**

핵심

```text
Natural Language
↔ Schema
↔ Table/Column Relations
```

를 relation-aware self-attention으로 동시에 모델링

Spider에서 BERT 결합 시 당시 65.6% exact match를 기록

### 핵심 시사점

Text-to-SQL 성능의 중요한 병목이 단순 SQL decoder가 아니라

```text
질문의 표현
→ 정확한 table/column
```

으로 연결하는 schema linking이라는 점을 보여줌

### txt2sql 적용성

**높음 — 단, 모델 자체를 재구현할 필요는 낮음**

적용할 핵심 아이디어는 neural encoder가 아니라

```text
Schema Relation Graph
```

임

예

```text
table
column
PK/FK
dataset relation
semantic alias
administrative hierarchy
```

를 graph로 구성하여 Query Contract 생성과 SQP schema 선택에 활용하는 방식이 적절함

---

# 5. PICARD — Constrained Decoding

**Scholak et al., EMNLP 2021**

LLM이 SQL token을 생성할 때 incremental parser로 잘못된 token을 즉시 거부

```text
LLM token
↓
SQL parser
↓
허용 가능한 token?
├─ YES → continue
└─ NO  → reject
```

### 장점

- invalid SQL 감소
- schema와 문법에 맞지 않는 generation 차단
- 기존 language model 위에 적용 가능

### txt2sql 적용성

**중간**

현재 llm2sql은 장기적으로

```text
Semantic Plan
→ deterministic Compiler
```

를 지향하므로 SQL token-level PICARD를 핵심으로 가져올 필요는 낮음

대신 개념을 다음과 같이 전환 적용하는 것이 적합

```text
LLM-generated SQP
↓
JSON Schema / Pydantic constraint
↓
invalid Plan node 즉시 거부
```

즉 **PICARD for SQL**이 아니라 **PICARD-like constrained Semantic Plan decoding**으로 적용 권장

---

# 6. RESDSQL — Schema Linking과 Skeleton Parsing 분리

**Li et al., AAAI 2023**

RESDSQL은 schema linking과 SQL skeleton parsing을 분리하여 생성 난이도를 낮춤

Spider test execution accuracy 79.9%, Spider dev 84.1%를 보고

### 핵심 구조

```text
Question
↓
Schema Item Ranking
↓
Relevant Schema
↓
SQL Skeleton
↓
Schema Filling
```

### txt2sql 적용성

**높음**

현재 Contract-first 구조와 매우 잘 맞음

다음과 같이 대응 가능

```text
RESDSQL Schema Ranking
→ llm2sql Schema Retriever

RESDSQL Skeleton
→ Query Contract / SQP

Schema Filling
→ Semantic Catalog Binding
```

특히 SQL부터 생성하지 않고 구조를 먼저 확정한다는 철학이 llm2sql의 SQP 방향과 일치함

---

# 7. DIN-SQL — Decomposed In-Context Learning

**Pourreza & Rafiei, 2023**

Spider test execution accuracy 85.3%, BIRD 55.9%를 보고

핵심 4단계

```text
1. Schema Linking
2. Query Classification & Decomposition
3. SQL Generation
4. Self-Correction
```

단순 few-shot 대비 약 10% 수준의 개선을 보고함

### txt2sql 적용성

**매우 높음**

다만 DIN-SQL의 LLM 기반 classification을 그대로 도입하기보다 현재 구조에 맞게 deterministic하게 변환하는 것이 좋음

```text
DIN-SQL
Query Classification
↓
llm2sql
Query Contract complexity / required capabilities
```

그리고 decomposition 결과는 바로 SQP가 됨

예

```text
질문
15층 이상 건물 중 공동주택 비율

↓ Contract

denominator
ground_floors >= 15

numerator
ground_floors >= 15
AND usage = 공동주택
```

즉 llm2sql의 Contract 모델은 DIN-SQL의 decomposition을 더욱 명시적인 typed structure로 구현하는 역할을 할 수 있음

---

# 8. DAIL-SQL — SQL Skeleton 기반 Few-shot Retrieval

**Gao et al., PVLDB 2024**

Spider test에서 GPT-4와 self-consistency를 사용하여 86.6% execution accuracy를 보고

핵심은 무작위 few-shot이 아니라

```text
Question structure
↓
SQL skeleton similarity
↓
유사 demonstration 검색
```

을 사용하는 것임

### txt2sql 적용성

**매우 높음**

특히 SQP가 LLM을 호출해야 하는 경우 모든 학습 예제를 넣지 않고

```text
Query Contract signature
```

가 비슷한 예제만 검색하는 방식으로 적용 가능

예

```text
signature =
entity:building
operation:ratio
denominator:predicate
numerator:predicate
```

이 signature와 유사한 기존 성공 질문을 검색

이는 SQL 문자열 기반 skeleton보다 현재 llm2sql 구조에 더 적합함

---

# 9. CHESS — Schema Pruning + Candidate Generation + Unit Test

**Talaei et al., 2024/2025**

CHESS는 다음 4개 agent를 사용

```text
Information Retriever
Schema Selector
Candidate Generator
Unit Tester
```

대규모 schema에서 Schema Selector를 사용하여 token을 약 5배 줄이고 정확도를 약 2% 향상했다고 보고

BIRD test에서 71.10% 수준의 성능을 보고하며, 고비용 proprietary 방식보다 적은 LLM 호출로 높은 성능을 달성

### txt2sql 적용성

**핵심 아이디어는 매우 높음, 전체 multi-agent 구조 복제는 중간**

llm2sql에 필요한 부분

```text
Information Retriever
→ Catalog / Value Retriever

Schema Selector
→ Contract-based Schema Linking

Candidate Generator
→ SQP Builder

Unit Tester
→ Contract-aware SQL Validator
```

하지만 항상 4개 agent를 호출하는 방식은 현재 구조보다 복잡하고 비용이 큼

따라서 **agent를 그대로 도입하지 말고 기능을 pipeline 단계로 흡수**하는 것을 권장

---

# 10. CHASE-SQL — Multi-path Reasoning + Candidate Selection

**Pourreza et al., ICLR 2025**

BIRD test 73.0% execution accuracy를 보고

핵심

1. divide-and-conquer
2. query execution plan 기반 reasoning
3. instance-aware synthetic example
4. multiple candidate generation
5. pairwise selection model

### txt2sql 적용성

**중상**

복잡한 Q3 계열 질문에는 매우 유용할 수 있으나 모든 질문에서 candidate를 여러 개 만드는 것은 비용이 큼

권장 적용

```text
Contract confidence 높음
→ candidate 1개

Contract confidence 중간
→ SQP 1개 + validation

Contract confidence 낮음 / verifier fail
→ K candidates
→ selection
```

즉 CHASE의 test-time scaling을 **adaptive hard-query path**로 제한하는 것이 적절함

---

# 11. XiYan-SQL — Multi-generator Ensemble

**Liu et al., IEEE TKDE 2026**

보고 성능

- BIRD : 75.63%
- Spider test : 89.65%

핵심

```text
Schema Filter
↓
Multiple Generators
↓
Refiner
↓
Selection Model
```

고성능이지만 구조가 크고 후보 생성 비용이 높음

### txt2sql 적용성

**중간**

전면 도입보다는 다음 두 요소만 채택 권장

1. schema filter
2. low-confidence query에 한정한 candidate selection

항상 multi-generator를 수행하는 구조는 llm2sql의 deterministic Router 장점을 훼손할 가능성이 있음

---

# 12. SchemaGraphSQL / 최근 Schema Linking 연구

**EACL 2026**

최근 연구는 schema linking을 단순 column embedding 검색이 아니라 graph pathfinding 문제로 보는 방향으로 발전

SchemaGraphSQL은 실제 DB에서 foreign key가 불완전한 경우도 고려하여 joinability를 추론한 뒤 graph를 구성

### txt2sql 적용성

**높음**

특히 여러 행정·건축·산업·공간 테이블을 연계하는 경우 다음 구조에 적합

```text
Query Contract
↓
candidate columns
↓
Schema Graph
↓
최단 / 최소 연결 subgraph
↓
SQP joins
```

이는 향후 SQP의 multi-table join 정확도 향상에 직접 기여 가능

---

# 13. VET — Verifiable Execution Tracing

**Wang et al., ACL Findings 2026**

BIRD 70.93%, Spider 2.0-lite 37.04%를 보고

기존 CoT처럼 텍스트로 reasoning만 하는 것이 아니라 중간 단계를 실제 데이터베이스에서 실행하여 검증

```text
reasoning step
↓
execute
↓
observable result
↓
next step
```

### txt2sql 적용성

**높음 — 복합 질문에 제한 적용**

다만 실제 SQL을 계속 수정하는 agent 방식 대신

```text
Contract predicate
↓
Plan fragment
↓
bounded execution probe
```

정도로 제한하는 것이 적합

예

```text
분모 집합 count
분자 집합 count
```

를 각각 검증하여 conditional ratio의 semantic error를 탐지 가능

---

# 14. 국내 연구 검토

## 14.1 LitE-SQL — 연세대학교 DELAB

**Piao, Lee, Park, Findings of EACL 2026**

구성

```text
Vector-based Schema Retriever
+
SQL Generator
+
Execution-guided Reinforcement
```

보고 성능

- BIRD : 72.10%
- Spider 1.0 : 88.45%

특히 연구는 훨씬 작은 모델로 LLM 기반 접근과 경쟁 가능한 성능을 달성했다고 보고함

### txt2sql 적용성

**최상**

현재 llm2sql에서 가장 우선적으로 참고할 국내 알고리즘

적용

```text
Semantic Catalog
↓
precomputed embeddings
↓
vector retrieval
↓
hard negative filtering
↓
Contract schema candidates
```

장점

- local model 친화적
- schema token 감소
- large proprietary LLM 의존도 감소
- 현재 catalog 구조와 결합 용이

Execution-guided reinforcement는 즉시 적용하기보다 Q500 성공/실패 로그가 충분히 축적된 이후 학습 단계에 도입하는 것이 적절함

---

# 15. DCG-SQL — 성균관대학교

**Lee et al., ACL 2025**

Deep Contextual Schema Link Graph를 구성하고 이를 이용하여 few-shot example을 검색

Llama-3.1-8B-Instruct에서 Spider execution accuracy가 random retrieval 73.0%에서 DCG-SQL 82.1%로 개선된 결과를 보고

### 핵심

단순 question embedding 대신

```text
Question
↔ Schema Item
↔ Semantic Relation
```

graph를 retrieval representation으로 사용

### txt2sql 적용성

**최상**

특히 경량 모델에서 효과가 크다는 점이 중요

현재 llm2sql에 적용 시

```text
Query Contract Graph
+
Schema Link Graph
```

를 이용하여 기존 성공 예제 또는 plan template을 검색하는 방식으로 구현 가능

---

# 16. MCS-SQL — 두나무

**Lee et al., COLING 2025**

보고 성능

- BIRD : 65.5%
- Spider : 89.6%

구조

```text
Multiple Schema Linking Prompts
↓
Multiple Candidate SQL
↓
Confidence Filtering
↓
LLM Multiple-choice Selection
```

### txt2sql 적용성

**중간~높음**

다중 prompt 방식 전체를 기본 경로에 넣는 것은 비효율적이지만

```text
검증 실패
또는
낮은 Contract confidence
```

일 때만 후보를 여러 개 생성하는 fallback 전략으로 유용

---

# 17. R³-SQL — 서울대학교 연구진 참여

**Han et al., Findings of ACL 2026**

BIRD-dev 75.03 execution accuracy를 보고

기존 candidate ranking의 두 문제를 지적

1. 같은 execution result를 내는 SQL이 다른 점수를 받음
2. candidate pool 안에 정답 자체가 없으면 ranking으로 해결 불가

해결

```text
Candidates
↓
Execution Result Grouping
↓
Group Ranking
↓
Correct candidate가 없다고 판단
↓
Selective Resampling
```

### txt2sql 적용성

**매우 높음 — 단 hard path 전용**

llm2sql에 가장 적합한 형태

```text
SQP candidate
↓
Contract verifier
↓
execution/result-shape verifier
↓
낮은 confidence
↓
추가 SQP candidate 생성
```

항상 N개를 생성하는 것이 아니라 **필요할 때만 resample**하는 것이 핵심

---

# 18. ReEx-SQL — 7B 기반 Execution-aware RL

**ACL 2026**

보고 성능

- Spider : 89.1%
- BIRD : 65.3%
- 7B scale
- tree-based decoding이 linear sampling 대비 51.9% 빠른 inference를 보고

### txt2sql 적용성

**중기적으로 높음**

현재 immediate architecture 수정 단계보다는

```text
Q500 / 신규 질의
↓
Contract
↓
Plan
↓
SQL
↓
실행 성공 / 실패
```

로그가 충분히 축적된 이후 execution-aware reward를 학습에 사용하는 방식으로 적합

---

# 19. 알고리즘 종합 비교

점수 기준

```text
5 = 매우 우수
4 = 우수
3 = 보통
2 = 낮음
1 = 부적합
```

| 연구 | 성능 근거 | 구조 적합성 | 구현 용이성 | 비용 효율 | 로컬 적합성 | llm2sql 권고 |
|---|---:|---:|---:|---:|---:|---|
| LitE-SQL | 5 | 5 | 4 | 5 | 5 | **즉시 적용** |
| DCG-SQL | 4 | 5 | 4 | 5 | 5 | **즉시 적용** |
| DIN-SQL | 4 | 5 | 5 | 4 | 4 | **즉시 적용** |
| DAIL-SQL | 4 | 5 | 4 | 5 | 4 | **즉시 적용** |
| RESDSQL | 4 | 5 | 4 | 4 | 4 | 핵심 개념 적용 |
| R³-SQL | 5 | 5 | 3 | 4 | 4 | hard path 적용 |
| CHESS | 5 | 4 | 3 | 4 | 4 | 일부 기능 적용 |
| CHASE-SQL | 5 | 4 | 2 | 2 | 2 | hard path 선택 적용 |
| XiYan-SQL | 5 | 3 | 2 | 2 | 2 | 전면 도입 비권장 |
| PICARD | 4 | 3 | 3 | 4 | 4 | Plan 제약에 응용 |
| VET | 5 | 4 | 2 | 2 | 3 | 고난도 검증용 |
| ReEx-SQL | 5 | 4 | 2 | 3 | 4 | 학습 2단계 |

---

# 20. llm2sql에 가장 적합한 통합 알고리즘

## 20.1 권장 구조

```text
Natural Language
        ↓
① Normalization
        ↓
② Hybrid Schema Retrieval
   ├─ lexical / catalog
   ├─ vector retrieval        ← LitE-SQL
   └─ schema graph            ← RAT-SQL / DCG / SchemaGraphSQL
        ↓
③ Query Contract
        ↓
④ Contract Completeness / Confidence
        ↓
⑤ Route Capability
        ↓
┌──────────────────────┬────────────────────────────┐
│ 100% supported       │ unsupported / complex      │
│                      │                            │
│ Router Fast Path     │ Deterministic SQP Builder  │
└──────────────────────┴─────────────┬──────────────┘
                                     ↓
                          unresolved node 존재?
                             ┌───────┴───────┐
                            NO              YES
                             │               │
                             │       ⑥ Structure-aware
                             │          example retrieval
                             │       ← DAIL / DCG
                             │               ↓
                             │         LLM resolution
                             └───────┬───────┘
                                     ↓
                          ⑦ Contract ↔ Plan Verify
                                     ↓
                              ⑧ Pure Compiler
                                     ↓
                              ⑨ SQL Validation
                                     ↓
                       ⑩ Safe Execution Verification
                         ← LitE-SQL / VET
                                     ↓
                           confidence sufficient?
                             ┌───────┴────────┐
                            YES               NO
                             │                │
                           Result      ⑪ Adaptive Resample
                                      ← R³-SQL / CHASE
                                             ↓
                                      Candidate Selection
```

---

# 21. 핵심 최적화 1 — Hybrid Schema Linking

가장 먼저 적용할 것을 권장

현재 schema catalog의 keyword match만으로 끝내지 말고 다음 세 계층 결합

```text
Layer 1
Exact / Alias / Regex

Layer 2
Vector similarity

Layer 3
Schema relation graph
```

최종 점수 예

```text
schema_score =
    lexical_score
  + vector_score
  + graph_connectivity_score
  + value_evidence_score
```

Contract에는 최종 선택 필드뿐 아니라 confidence도 저장

```python
SchemaBinding(
    mention="지하층",
    field="basement_floors",
    confidence=0.98,
)
```

---

# 22. 핵심 최적화 2 — DIN-SQL식 분해를 Contract로 흡수

별도 decomposition agent를 두기보다 Query Contract가 decomposition 결과 자체가 되도록 설계

예

```text
15층 이상 건물 중 공동주택 비율
```

Contract

```text
scope
building

denominator
ground_floors >= 15

numerator
ground_floors >= 15
AND usage = 공동주택

operation
ratio
```

따라서 SQL generator가 다시 문장을 해석할 필요가 없음

---

# 23. 핵심 최적화 3 — 구조 기반 Example Retrieval

DAIL-SQL과 DCG-SQL의 핵심을 결합

질문 문장 embedding만 비교하지 말고 Contract feature를 사용

예

```text
entity
operation
aggregation
ratio type
group dimension
predicate structure
order
limit
```

이를 graph 또는 vector로 변환하여 과거 성공 예제 검색

장점

- 표현이 다른 한국어 문장에도 구조가 같으면 검색 가능
- 소형 LLM 성능 향상 가능
- prompt token 낭비 감소

---

# 24. 핵심 최적화 4 — Execution Verification

SQL이 실행됐다고 정답인 것은 아님

따라서 execution validation을 두 단계로 분리

## 24.1 SQL Validity

- syntax
- referenced table
- referenced column
- type
- read-only
- timeout

## 24.2 Semantic Execution Probe

Contract 기준으로 최소 검증

예

```text
ratio
→ numerator count
→ denominator count
→ numerator <= denominator
```

```text
top N
→ result rows <= N
```

```text
group_by structure
→ output에 structure column 존재
```

```text
count + avg
→ output measure 2개 존재
```

Compiler가 결과를 보고 질문 의미를 수정하지는 않으며, 검증 실패 시에만 재계획하도록 함

---

# 25. 핵심 최적화 5 — Adaptive Candidate Generation

XiYan-SQL / CHASE-SQL처럼 항상 여러 후보를 생성하지 말 것

Contract confidence에 따라 동적 결정

```text
confidence >= 0.95
→ 1 candidate

0.80 ~ 0.95
→ 1 candidate + verifier

< 0.80
→ 2~3 candidates

verification fail
→ additional resampling
```

R³-SQL의 selective resampling 개념을 채택

이 방식이 성능과 비용 간 균형이 가장 좋음

---

# 26. 도입 우선순위

## Phase A — 즉시 적용

### A1. Hybrid Schema Retrieval

참고

- LitE-SQL
- DCG-SQL
- RAT-SQL

### A2. Contract-first decomposition

참고

- DIN-SQL
- RESDSQL

### A3. Capability-gated Router

이는 llm2sql 고유 architecture로 유지

### A4. Deterministic SQP

가능한 의미는 LLM 없이 Plan 생성

---

## Phase B — 단기 적용

### B1. Structure-aware demonstration retrieval

참고

- DAIL-SQL
- DCG-SQL

### B2. Contract ↔ Plan Verifier

### B3. Execution Result Shape Validator

### B4. Safe execution probe

참고

- LitE-SQL
- VET

---

## Phase C — 중기 적용

### C1. Adaptive multi-candidate generation

참고

- R³-SQL
- CHASE-SQL
- MCS-SQL

### C2. Candidate execution-result grouping

### C3. selective resampling

---

## Phase D — 학습 고도화

### D1. execution-guided fine-tuning

참고

- LitE-SQL

### D2. execution-aware reinforcement learning

참고

- ReEx-SQL

### D3. self-improving reasoning data

참고

- ReSQL

---

# 27. 우선 도입을 권하지 않는 방식

## 27.1 모든 질문에 Multi-Agent 적용

CHESS, CHASE 계열 전체 구조를 그대로 복제하는 것은 비권장

이유

- latency 증가
- debugging 어려움
- deterministic Router 자산 활용 저하
- 동일 작업을 여러 agent가 중복 수행할 위험

기능을 pipeline 단계로 흡수하는 방식 권장

---

## 27.2 모든 질문에 Multi-Candidate 생성

XiYan-SQL 방식은 최고 수준의 성능을 보이지만 비용이 큼

현재 llm2sql에서는 low-confidence query만 적용 권장

---

## 27.3 LLM이 직접 SQL을 생성하는 구조로 회귀

현재 SQP를 구축하는 상황에서

```text
Question
→ LLM
→ SQL
```

을 주 경로로 만드는 것은 비권장

장기적으로

```text
Question
→ Contract
→ Plan
→ Compiler
```

구조가 유지되어야 디버깅과 학술적 설명력이 높음

---

# 28. 추천 최종 알고리즘

본 보고서에서 최종 권장하는 알고리즘을 요약하면 다음과 같음

## Contract-Aware Adaptive Hybrid Text-to-SQL

```python
def solve(question):

    normalized = normalize(question)

    schema_candidates = hybrid_schema_retrieve(
        normalized,
        lexical=True,
        vector=True,
        graph=True,
    )

    contract = build_contract(
        normalized,
        schema_candidates,
    )

    contract = validate_contract(contract)

    routes = candidate_routes(contract)

    eligible_routes = [
        r for r in routes
        if capability(r).covers(contract)
    ]

    if eligible_routes:
        route = choose_lowest_cost_route(
            eligible_routes
        )
        return execute_route(route, contract)

    plan = deterministic_plan_builder(contract)

    if plan.has_unresolved:
        examples = retrieve_examples_by_contract(
            contract
        )

        plan = resolve_unresolved_nodes(
            contract,
            plan,
            examples,
        )

    verify_contract_plan(contract, plan)

    sql = compile(plan)

    validate_sql(sql)

    result = execute_readonly(sql)

    verification = verify_result(
        contract,
        plan,
        result,
    )

    if verification.ok:
        return result

    candidates = adaptive_resample(
        contract,
        plan,
        verification,
    )

    return select_verified_candidate(
        candidates,
        contract,
    )
```

---

# 29. 예상 효과

## 정확도

- schema-column 오연결 감소
- 복합 aggregation 의미 손실 감소
- ratio denominator 오류 감소
- 잘못된 route 조기 선점 감소
- low-confidence query에 대한 복구율 증가

## 성능

- simple query는 deterministic fast path 유지
- 대부분 질문은 candidate 1개만 생성
- LLM은 unresolved 부분에만 호출
- schema retrieval로 prompt 크기 감소

## 유지보수성

오류 위치를 다음과 같이 명확히 구분 가능

```text
Schema Linking Error
Contract Error
Capability Error
Plan Error
Compiler Error
Execution Verification Error
```

## 연구 측면

단순 LLM fine-tuning 연구가 아니라

> **Contract-aware, capability-gated, adaptive hybrid Text-to-SQL**

architecture로 차별화 가능

특히 기존 연구의 주요 요소가 대체로

```text
Schema → LLM → SQL
```

또는

```text
Schema → Multi-Agent → SQL
```

인 반면

llm2sql은

```text
Schema Retrieval
→ Explicit Semantic Contract
→ Capability-based Algorithm Selection
→ Deterministic Route / Semantic Plan
→ Pure Compiler
→ Adaptive Verification
```

이라는 명시적 semantic control layer를 중심으로 설계할 수 있음

---

# 30. 결론

국내외 고성능 연구를 검토한 결과, llm2sql에 가장 적합한 방향은 특정 한 논문의 알고리즘을 그대로 복제하는 것이 아님

가장 적용성이 높은 조합은 다음과 같음

```text
LitE-SQL
→ 경량 vector schema retrieval

DCG-SQL
→ schema/contract graph 기반 retrieval

DIN-SQL
→ 복합 질문 decomposition

DAIL-SQL
→ 구조 기반 demonstration retrieval

R³-SQL
→ low-confidence selective resampling

VET / LitE-SQL
→ execution-aware verification
```

이를 현재 설계와 결합하면

```text
Hybrid Schema Linking
        ↓
Query Contract
        ↓
Contract Confidence
        ↓
Capability Gate
        ↓
Router Fast Path / Deterministic SQP
        ↓
필요한 경우에만 LLM
        ↓
Contract ↔ Plan Verify
        ↓
Pure Compiler
        ↓
Execution Verification
        ↓
필요한 경우에만 Resampling
```

구조가 가장 적합함

**따라서 다음 개발 우선순위는 `Contract-first 전환 → Hybrid Schema Linking → Contract 기반 SQP → Contract/Plan Verifier → Execution Verification → Adaptive Resampling` 순으로 권고함**

---

# 참고문헌 및 주요 출처

1. Wang, B. et al. (2020), *RAT-SQL: Relation-Aware Schema Encoding and Linking for Text-to-SQL Parsers*, ACL 2020  
   https://aclanthology.org/2020.acl-main.677/

2. Scholak, T. et al. (2021), *PICARD: Parsing Incrementally for Constrained Auto-Regressive Decoding from Language Models*, EMNLP 2021  
   https://aclanthology.org/2021.emnlp-main.779/

3. Li et al. (2023), *RESDSQL*  
   https://github.com/RUCKBReasoning/RESDSQL

4. Pourreza, M. & Rafiei, D. (2023), *DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction*  
   https://arxiv.org/abs/2304.11015

5. Gao, D. et al. (2024), *Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation / DAIL-SQL*, PVLDB  
   https://doi.org/10.14778/3641204.3641221

6. Talaei, S. et al. (2024), *CHESS: Contextual Harnessing for Efficient SQL Synthesis*  
   https://arxiv.org/abs/2405.16755

7. Pourreza, M. et al. (2025), *CHASE-SQL: Multi-Path Reasoning and Preference Optimized Candidate Selection in Text-to-SQL*, ICLR 2025  
   https://proceedings.iclr.cc/paper_files/paper/2025/hash/974ff7b5bf08dbf9400b5d599a39c77f-Abstract-Conference.html

8. Liu, Y. et al. (2026), *XiYan-SQL: A Novel Multi-Generator Framework for Text-to-SQL*, IEEE TKDE  
   https://doi.org/10.1109/TKDE.2026.3657851

9. Piao, S., Lee, J., Park, S. (2026), *LitE-SQL: A Lightweight and Efficient Text-to-SQL Framework with Vector-based Schema Linking and Execution-Guided Self-Correction*, Findings of EACL 2026  
   https://aclanthology.org/2026.findings-eacl.186/

10. Lee, J. et al. (2025), *DCG-SQL: Enhancing In-Context Learning for Text-to-SQL with Deep Contextual Schema Link Graph*, ACL 2025  
    https://aclanthology.org/2025.acl-long.748/

11. Lee, D. et al. (2025), *MCS-SQL: Leveraging Multiple Prompts and Multiple-Choice Selection For Text-to-SQL Generation*, COLING 2025  
    https://aclanthology.org/2025.coling-main.24/

12. Han, H. et al. (2026), *R³-SQL: Ranking Reward and Resampling for Text-to-SQL*, Findings of ACL 2026  
    https://aclanthology.org/2026.findings-acl.2146/

13. Dai, Y. et al. (2026), *ReEx-SQL: Reasoning with Execution-Aware Reinforcement Learning for Text-to-SQL*, ACL 2026  
    https://aclanthology.org/2026.acl-long.35/

14. Wang, D. et al. (2026), *VET: Verifiable Execution Tracing for Reliable Text-to-SQL Generation*, Findings of ACL 2026  
    https://aclanthology.org/2026.findings-acl.1544/

15. Yu, T. et al. (2018), *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL Task*, EMNLP 2018  
    https://aclanthology.org/D18-1425/

16. Li, J. et al. (2023), *BIRD: Can LLM Already Serve as A Database Interface?*  
    https://arxiv.org/abs/2305.03111

17. Lei, F. et al. (2024), *Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows*  
    https://arxiv.org/abs/2411.07763
