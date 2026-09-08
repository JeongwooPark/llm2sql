# txt2sql 신규 자연어 질의 테스트셋 500건

## 1. 목적

KorDB 공간데이터베이스를 사람이 실제로 활용한다고 가정하여 `txt2sql`의 자연어 이해, Router, Semantic Query Plan, RAG/SQL fallback, PostgreSQL/PostGIS 공간질의, 복합조건·집계·순위·모호성·후속질의 처리 성능을 시험하기 위한 신규 질문 500건임

## 2. 데이터 범위 반영 원칙

- 부산 전역 건물 기본정보는 `AL_D010_26_20250704`를 기준으로 함
- **용도별건물공간정보(D198)는 금정구·동래구만 존재함**
- 따라서 공동주택·단독주택·아파트·공장·업무시설·세부용도·건물용도분류 등 **건축물 용도를 직접 조건·집계·비교하는 질문은 금정구·동래구 및 해당 지역의 동 단위로 제한함**
- 사용승인일·허가일·준공연도·건축연령 질의도 D198 범위에 맞춰 금정구·동래구로 제한함
- 타 구·군 및 부산 전역은 건물 수·높이·건축물면적·연면적·대지면적·건폐율·용적률·층수·구조·위반건축물·특수지·공간관계를 중심으로 구성함
- 행정동은 행정경계 공간조인, 기초구역 및 산업단지는 공간교차·거리관계를 포함하는 질의를 구성함
- 단순 `면적`, `큰 건물`, 거리 없는 `주변` 등 의도적으로 모호한 질의와 후속 대화 조각도 포함함

## 3. 구성 요약

| 구분 | 문항 수 |
|---|---:|
| 1. 기본 건물 조회·건수 | 70 |
| 2. 수치 조건·범위·필드 비교 | 70 |
| 3. 집계·분포·순위 | 60 |
| 4. 금정구·동래구 건축물 용도 특화 | 100 |
| 5. 금정구·동래구 사용승인·허가·건축연령 | 60 |
| 6. 공간조인·행정동·기초구역·산업단지 | 80 |
| 7. 조인·메타·모호성·후속질의·적대적 표현 | 60 |
| **합계** | **500** |

난이도 분포 : 초급 30건, 중급 307건, 고급 163건

## 4. 테스트 질문

### 1. 기본 건물 조회·건수

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q001 | 초급 | count | 중구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q002 | 초급 | count | 서구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q003 | 초급 | count | 동구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q004 | 초급 | count | 영도구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q005 | 초급 | count | 부산진구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q006 | 초급 | count | 동래구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q007 | 초급 | count | 남구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q008 | 초급 | count | 북구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q009 | 초급 | count | 해운대구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q010 | 초급 | count | 사하구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q011 | 초급 | count | 금정구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q012 | 초급 | count | 강서구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q013 | 초급 | count | 연제구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q014 | 초급 | count | 수영구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q015 | 초급 | count | 사상구에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q016 | 초급 | count | 기장군에 있는 건물은 모두 몇 채야? | 구 단위 COUNT |
| Q017 | 초급 | count | 연산동 건물 수를 알려줘 | 법정동 COUNT |
| Q018 | 초급 | count | 대연동 건물 수를 알려줘 | 법정동 COUNT |
| Q019 | 초급 | count | 문현동 건물 수를 알려줘 | 법정동 COUNT |
| Q020 | 초급 | count | 대저1동 건물 수를 알려줘 | 법정동 COUNT |
| Q021 | 초급 | count | 대저2동 건물 수를 알려줘 | 법정동 COUNT |
| Q022 | 초급 | count | 괴정동 건물 수를 알려줘 | 법정동 COUNT |
| Q023 | 초급 | count | 청학동 건물 수를 알려줘 | 법정동 COUNT |
| Q024 | 초급 | count | 반송동 건물 수를 알려줘 | 법정동 COUNT |
| Q025 | 초급 | count | 구포동 건물 수를 알려줘 | 법정동 COUNT |
| Q026 | 초급 | count | 감천동 건물 수를 알려줘 | 법정동 COUNT |
| Q027 | 초급 | count | 광안동 건물 수를 알려줘 | 법정동 COUNT |
| Q028 | 초급 | count | 장림동 건물 수를 알려줘 | 법정동 COUNT |
| Q029 | 초급 | count | 동삼동 건물 수를 알려줘 | 법정동 COUNT |
| Q030 | 초급 | count | 강동동 건물 수를 알려줘 | 법정동 COUNT |
| Q031 | 중급 | list/count | 부산 전체 건물 수는 몇 채인가? | 전역 COUNT |
| Q032 | 중급 | list/count | 부산에서 위반건축물로 표시된 건물은 몇 채야? | 상태 COUNT |
| Q033 | 중급 | list/count | 부산에서 위반건축물이 아닌 건물은 몇 채야? | 상태 COUNT |
| Q034 | 중급 | list/count | 해운대구 건물명과 지번을 20개만 보여줘 | 목록 LIMIT |
| Q035 | 중급 | list/count | 강서구 건물의 법정동명과 높이를 보여줘 | 선택 컬럼 |
| Q036 | 중급 | list/count | 사하구 건물 중 건물명이 있는 것만 보여줘 | NOT NULL |
| Q037 | 중급 | list/count | 연제구 건물 중 건물명이 비어 있는 레코드 수는? | NULL/빈값 |
| Q038 | 중급 | list/count | 부산진구 건물 중 높이 값이 없는 레코드는 몇 건이야? | NULL |
| Q039 | 중급 | list/count | 기장군 건물 중 지하층 정보가 없는 건물 수는? | NULL |
| Q040 | 중급 | list/count | 영도구 산지 지번 건물 수는? | 특수지 |
| Q041 | 중급 | list/count | 북구 일반지번 건물 수를 알려줘 | 특수지 |
| Q042 | 중급 | list/count | 중구 가지번 건물은 몇 채야? | 특수지 |
| Q043 | 중급 | list/count | 서구 블럭지번 건물 수를 세어줘 | 특수지 |
| Q044 | 중급 | list/count | 부산 전체 철근콘크리트구조 건물 수는? | 구조 |
| Q045 | 중급 | list/count | 사상구 벽돌구조 건물 수를 알려줘 | 구조 |
| Q046 | 중급 | list/count | 수영구 일반철골구조 건물은 몇 채야? | 구조 |
| Q047 | 중급 | list/count | 남구 일반목구조 건물 수는? | 구조 |
| Q048 | 중급 | list/count | 동구 경량철골구조 건물은 몇 채야? | 구조 |
| Q049 | 중급 | list/count | 대연동 철근콘크리트구조 건물 수는? | 법정동+구조 |
| Q050 | 중급 | list/count | 문현동 벽돌구조 건물은 몇 채야? | 법정동+구조 |
| Q051 | 중급 | list/count | 광안동 일반목구조 건물 수를 알려줘 | 법정동+구조 |
| Q052 | 중급 | list/count | 장림동 일반철골구조 건물은 몇 개야? | 법정동+구조 |
| Q053 | 중급 | list/count | 연산동 블록구조 건물 수는? | 법정동+구조 |
| Q054 | 중급 | list/count | 부산에서 지상층수가 0으로 기록된 건물 수를 확인해줘 | 데이터 품질 |
| Q055 | 중급 | list/count | 부산에서 높이가 0으로 기록된 건물은 몇 건이야? | 데이터 품질 |
| Q056 | 중급 | list/count | 부산에서 건축물면적이 음수인 레코드가 몇 건인지 알려줘 | 데이터 품질 |
| Q057 | 중급 | list/count | 부산에서 건폐율이 음수인 레코드를 보여줘 | 데이터 품질 |
| Q058 | 중급 | list/count | 원천시도시군구코드가 비어 있는 건물이 있는지 확인해줘 | NULL/코드 |
| Q059 | 중급 | list/count | 위반건축물 중 건물명이 있는 레코드만 30개 보여줘 | 상태+목록 |
| Q060 | 중급 | list/count | 산지 건물 중 지상층수가 기록된 것만 보여줘 | 특수지+NULL |
| Q061 | 중급 | list/count | 일반지번 건물 중 지하층이 1층 이상인 건물을 보여줘 | 특수지+층 |
| Q062 | 중급 | list/count | 철근콘크리트구조이면서 위반건축물인 건물 수를 알려줘 | 구조+상태 |
| Q063 | 중급 | list/count | 벽돌구조인데 위반건축물이 아닌 건물 수는? | 구조+상태 |
| Q064 | 중급 | list/count | 부산에서 건물명이 같은 레코드가 여러 개 있는 이름을 찾아줘 | 중복 |
| Q065 | 중급 | list/count | 동일 PNU가 두 번 이상 나타나는 값을 찾아줘 | 중복 키 |
| Q066 | 중급 | list/count | GIS건물통합식별번호가 중복된 레코드가 있는지 확인해줘 | 중복 키 |
| Q067 | 중급 | list/count | 법정동코드별 건물 수를 집계해줘 | GROUP |
| Q068 | 중급 | list/count | 원천시도시군구코드별 건물 수를 보여줘 | GROUP |
| Q069 | 중급 | list/count | 부산 건물 중 대지면적 값이 있는 레코드 수를 알려줘 | NOT NULL |
| Q070 | 중급 | list/count | 부산 건물 중 연면적이 0인 레코드 수는? | 데이터 품질 |
### 2. 수치 조건·범위·필드 비교

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q071 | 중급 | filter | 중구에서 높이 15m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q072 | 중급 | filter | 서구에서 높이 20m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q073 | 중급 | filter | 동구에서 높이 25m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q074 | 중급 | filter | 영도구에서 높이 30m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q075 | 중급 | filter | 부산진구에서 높이 35m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q076 | 중급 | filter | 동래구에서 높이 40m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q077 | 중급 | filter | 남구에서 높이 45m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q078 | 중급 | filter | 북구에서 높이 50m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q079 | 중급 | filter | 해운대구에서 높이 55m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q080 | 중급 | filter | 사하구에서 높이 60m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q081 | 중급 | filter | 금정구에서 높이 65m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q082 | 중급 | filter | 강서구에서 높이 70m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q083 | 중급 | filter | 연제구에서 높이 75m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q084 | 중급 | filter | 수영구에서 높이 80m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q085 | 중급 | filter | 사상구에서 높이 85m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q086 | 중급 | filter | 기장군에서 높이 90m 이상인 건물 수를 알려줘 | 높이 임계 |
| Q087 | 중급 | filter | 중구에서 지상 3층 이상인 건물을 보여줘 | 층수 임계 |
| Q088 | 중급 | filter | 서구에서 지상 4층 이상인 건물을 보여줘 | 층수 임계 |
| Q089 | 중급 | filter | 동구에서 지상 5층 이상인 건물을 보여줘 | 층수 임계 |
| Q090 | 중급 | filter | 영도구에서 지상 6층 이상인 건물을 보여줘 | 층수 임계 |
| Q091 | 중급 | filter | 부산진구에서 지상 7층 이상인 건물을 보여줘 | 층수 임계 |
| Q092 | 중급 | filter | 동래구에서 지상 8층 이상인 건물을 보여줘 | 층수 임계 |
| Q093 | 중급 | filter | 남구에서 지상 9층 이상인 건물을 보여줘 | 층수 임계 |
| Q094 | 중급 | filter | 북구에서 지상 10층 이상인 건물을 보여줘 | 층수 임계 |
| Q095 | 중급 | filter | 해운대구에서 지상 11층 이상인 건물을 보여줘 | 층수 임계 |
| Q096 | 중급 | filter | 사하구에서 지상 12층 이상인 건물을 보여줘 | 층수 임계 |
| Q097 | 중급 | filter | 금정구에서 지상 13층 이상인 건물을 보여줘 | 층수 임계 |
| Q098 | 중급 | filter | 강서구에서 지상 14층 이상인 건물을 보여줘 | 층수 임계 |
| Q099 | 중급 | filter | 연제구에서 지상 15층 이상인 건물을 보여줘 | 층수 임계 |
| Q100 | 중급 | filter | 수영구에서 지상 16층 이상인 건물을 보여줘 | 층수 임계 |
| Q101 | 중급 | filter | 사상구에서 지상 17층 이상인 건물을 보여줘 | 층수 임계 |
| Q102 | 중급 | filter | 기장군에서 지상 18층 이상인 건물을 보여줘 | 층수 임계 |
| Q103 | 중급 | filter | 연산동에서 연면적 100㎡ 이상인 건물 수는? | 연면적 임계 |
| Q104 | 중급 | filter | 대연동에서 연면적 250㎡ 이상인 건물 수는? | 연면적 임계 |
| Q105 | 중급 | filter | 문현동에서 연면적 400㎡ 이상인 건물 수는? | 연면적 임계 |
| Q106 | 중급 | filter | 대저1동에서 연면적 550㎡ 이상인 건물 수는? | 연면적 임계 |
| Q107 | 중급 | filter | 대저2동에서 연면적 700㎡ 이상인 건물 수는? | 연면적 임계 |
| Q108 | 중급 | filter | 괴정동에서 연면적 850㎡ 이상인 건물 수는? | 연면적 임계 |
| Q109 | 중급 | filter | 청학동에서 연면적 1000㎡ 이상인 건물 수는? | 연면적 임계 |
| Q110 | 중급 | filter | 반송동에서 연면적 1150㎡ 이상인 건물 수는? | 연면적 임계 |
| Q111 | 중급 | filter | 구포동에서 연면적 1300㎡ 이상인 건물 수는? | 연면적 임계 |
| Q112 | 중급 | filter | 감천동에서 연면적 1450㎡ 이상인 건물 수는? | 연면적 임계 |
| Q113 | 중급 | filter | 광안동에서 연면적 1600㎡ 이상인 건물 수는? | 연면적 임계 |
| Q114 | 중급 | filter | 장림동에서 연면적 1750㎡ 이상인 건물 수는? | 연면적 임계 |
| Q115 | 중급 | filter | 동삼동에서 연면적 1900㎡ 이상인 건물 수는? | 연면적 임계 |
| Q116 | 중급 | filter | 강동동에서 연면적 2050㎡ 이상인 건물 수는? | 연면적 임계 |
| Q117 | 중급 | range/compound | 해운대구에서 높이 30m 이상 80m 이하인 건물을 보여줘 | BETWEEN |
| Q118 | 중급 | range/compound | 금정구에서 지상 5층 이상 15층 이하인 건물 수는? | BETWEEN |
| Q119 | 중급 | range/compound | 동래구에서 연면적 500㎡ 이상 3000㎡ 이하인 건물을 보여줘 | BETWEEN |
| Q120 | 중급 | range/compound | 강서구에서 대지면적 1000㎡ 이상 5000㎡ 이하인 건물 수는? | BETWEEN |
| Q121 | 중급 | range/compound | 사하구에서 건축물면적 200㎡ 이상 1000㎡ 이하인 건물을 보여줘 | BETWEEN |
| Q122 | 중급 | range/compound | 수영구에서 건폐율 40% 이상 70% 이하인 건물 수는? | BETWEEN |
| Q123 | 중급 | range/compound | 연제구에서 용적률 150% 이상 400% 이하인 건물을 보여줘 | BETWEEN |
| Q124 | 고급 | range/compound | 부산진구에서 높이 30m 이상이고 지상 10층 이상인 건물을 보여줘 | 복합 수치 |
| Q125 | 고급 | range/compound | 기장군에서 연면적 2000㎡ 이상이고 대지면적 1000㎡ 이상인 건물 수는? | 복합 면적 |
| Q126 | 고급 | range/compound | 영도구에서 지상 5층 이상이면서 지하 1층 이상인 건물을 보여줘 | 복합 층 |
| Q127 | 고급 | range/compound | 남구에서 건폐율 60% 이상이고 용적률 250% 이상인 건물 수를 알려줘 | 복합 비율 |
| Q128 | 고급 | range/compound | 북구에서 높이 20m 이상이지만 지상층수는 5층 이하인 건물을 찾아줘 | 교차 조건 |
| Q129 | 고급 | range/compound | 부산에서 건축물면적이 연면적보다 큰 건물을 찾아줘 | 필드 비교 |
| Q130 | 고급 | range/compound | 부산에서 연면적이 대지면적보다 작은 건물 수는? | 필드 비교 |
| Q131 | 고급 | range/compound | 부산에서 높이가 지상층수의 10배보다 큰 레코드를 보여줘 | 파생 비교 |
| Q132 | 고급 | range/compound | 부산에서 지상층은 1층 이상인데 높이가 0m인 레코드 수를 알려줘 | 이상치 |
| Q133 | 고급 | range/compound | 부산에서 지상층 50층 이상인데 높이가 100m 미만인 건물을 찾아줘 | 이상치 |
| Q134 | 고급 | range/compound | 부산에서 높이 500m 초과인 건물을 이름과 높이로 보여줘 | 이상치 |
| Q135 | 고급 | range/compound | 부산에서 건폐율 1000% 초과인 레코드 수는? | 이상치 |
| Q136 | 고급 | range/compound | 부산에서 용적률 5000% 초과인 건물을 보여줘 | 이상치 |
| Q137 | 중급 | range/compound | 부산에서 건축물면적이 0㎡ 이하인 레코드 수를 알려줘 | 품질 |
| Q138 | 중급 | range/compound | 부산에서 대지면적은 0보다 큰데 건축물면적이 0인 건물을 찾아줘 | 품질 |
| Q139 | 고급 | range/compound | 부산에서 연면적은 있는데 대지면적이 없는 건물 수는? | NULL 교차 |
| Q140 | 고급 | range/compound | 부산에서 높이는 있는데 지상층수가 없는 건물을 보여줘 | NULL 교차 |
### 3. 집계·분포·순위

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q141 | 중급 | aggregate/rank | 부산 전체 건물의 평균 높이를 알려줘 | AVG |
| Q142 | 중급 | aggregate/rank | 부산 전체 건물 연면적 합계는? | SUM |
| Q143 | 중급 | aggregate/rank | 부산에서 가장 높은 정상 범위 건물 높이는 얼마야? | MAX |
| Q144 | 중급 | aggregate/rank | 부산 건물의 최소 양수 건축물면적을 구해줘 | MIN |
| Q145 | 중급 | aggregate/rank | 해운대구 평균 높이를 알려줘 | AVG |
| Q146 | 중급 | aggregate/rank | 금정구 평균 연면적은? | AVG |
| Q147 | 중급 | aggregate/rank | 동래구 평균 지상층수를 구해줘 | AVG |
| Q148 | 중급 | aggregate/rank | 강서구 대지면적 합계를 알려줘 | SUM |
| Q149 | 중급 | aggregate/rank | 사하구 평균 건폐율은? | AVG |
| Q150 | 중급 | aggregate/rank | 수영구 평균 용적률을 알려줘 | AVG |
| Q151 | 중급 | aggregate/rank | 구·군별 건물 수를 보여줘 | GROUP |
| Q152 | 중급 | aggregate/rank | 구·군별 평균 높이를 계산해줘 | GROUP+AVG |
| Q153 | 중급 | aggregate/rank | 구·군별 연면적 합계를 보여줘 | GROUP+SUM |
| Q154 | 중급 | aggregate/rank | 구·군별 평균 지상층수를 보여줘 | GROUP+AVG |
| Q155 | 중급 | aggregate/rank | 구·군별 위반건축물 수를 집계해줘 | GROUP+상태 |
| Q156 | 중급 | aggregate/rank | 구·군별 산지 건물 수를 보여줘 | GROUP+특수지 |
| Q157 | 중급 | aggregate/rank | 구·군별 철근콘크리트구조 건물 수를 집계해줘 | GROUP+구조 |
| Q158 | 중급 | aggregate/rank | 구조별 건물 수를 전체 부산 기준으로 보여줘 | GROUP 구조 |
| Q159 | 중급 | aggregate/rank | 구조별 평균 높이를 계산해줘 | GROUP+AVG |
| Q160 | 중급 | aggregate/rank | 구조별 평균 연면적을 알려줘 | GROUP+AVG |
| Q161 | 중급 | aggregate/rank | 구조별 평균 지상층수를 보여줘 | GROUP+AVG |
| Q162 | 중급 | aggregate/rank | 특수지구분명별 건물 수와 평균 대지면적을 보여줘 | GROUP+다중집계 |
| Q163 | 중급 | aggregate/rank | 위반건축물 여부별 건물 수와 평균 높이를 보여줘 | GROUP+다중집계 |
| Q164 | 중급 | aggregate/rank | 법정동별 건물 수가 많은 순으로 정렬해줘 | GROUP+ORDER |
| Q165 | 중급 | aggregate/rank | 법정동별 평균 연면적이 큰 순으로 정렬해줘 | GROUP+ORDER |
| Q166 | 중급 | aggregate/rank | 부산에서 높이가 높은 건물 10개를 보여줘 | TOP-K |
| Q167 | 중급 | aggregate/rank | 부산에서 연면적이 큰 건물 20개를 보여줘 | TOP-K |
| Q168 | 중급 | aggregate/rank | 해운대구에서 높이가 높은 건물 15개를 보여줘 | TOP-K |
| Q169 | 중급 | aggregate/rank | 강서구에서 대지면적이 큰 건물 10개를 보여줘 | TOP-K |
| Q170 | 중급 | aggregate/rank | 사하구에서 건축물면적이 작은 양수값 기준 10개를 보여줘 | BOTTOM-K |
| Q171 | 중급 | aggregate/rank | 수영구에서 용적률이 높은 건물 10개를 보여줘 | TOP-K |
| Q172 | 중급 | aggregate/rank | 연제구에서 건폐율이 낮은 정상값 건물 10개를 보여줘 | BOTTOM-K |
| Q173 | 중급 | aggregate/rank | 대연동에서 높이가 높은 건물 7개를 보여줘 | 법정동 TOP-K |
| Q174 | 중급 | aggregate/rank | 문현동에서 지상층수가 많은 건물 10개를 보여줘 | 법정동 TOP-K |
| Q175 | 중급 | aggregate/rank | 광안동에서 연면적이 큰 건물 5개를 알려줘 | 법정동 TOP-K |
| Q176 | 중급 | aggregate/rank | 구·군별 건물 수를 계산한 뒤 많은 순 상위 5개 구·군만 보여줘 | GROUP+TOP-K |
| Q177 | 중급 | aggregate/rank | 구·군별 평균 높이가 높은 순으로 상위 7개를 보여줘 | GROUP+TOP-K |
| Q178 | 고급 | aggregate/rank | 구·군별 위반건축물 비율을 계산해 높은 순으로 정렬해줘 | 비율 |
| Q179 | 고급 | aggregate/rank | 구·군별 산지 건물 비율을 계산해줘 | 비율 |
| Q180 | 고급 | aggregate/rank | 구조별 건물 비율을 전체 대비 백분율로 보여줘 | 비율 |
| Q181 | 중급 | aggregate/rank | 부산 건물 높이의 중앙값을 구해줘 | 중앙값 |
| Q182 | 고급 | aggregate/rank | 부산 연면적의 25%, 50%, 75% 분위수를 알려줘 | 분위수 |
| Q183 | 중급 | aggregate/rank | 부산 건물 높이의 표준편차를 구해줘 | 표준편차 |
| Q184 | 중급 | aggregate/rank | 부산 건축물면적의 분산을 알려줘 | 분산 |
| Q185 | 고급 | aggregate/rank | 건물을 높이 0~10m, 10~30m, 30~60m, 60m 초과 구간으로 나눠 건수를 보여줘 | 구간화 |
| Q186 | 고급 | aggregate/rank | 건물을 지상 1~2층, 3~5층, 6~10층, 11층 이상으로 나눠 건수를 보여줘 | 구간화 |
| Q187 | 고급 | aggregate/rank | 연면적 500㎡ 미만, 500~2000㎡, 2000㎡ 초과로 나눠 건수와 평균 높이를 보여줘 | 구간 집계 |
| Q188 | 고급 | aggregate/rank | 각 구·군 최대 높이와 평균 높이의 차이를 계산해줘 | 파생 집계 |
| Q189 | 고급 | aggregate/rank | 각 구·군 최대 연면적과 최소 양수 연면적의 차이를 보여줘 | 파생 집계 |
| Q190 | 고급 | aggregate/rank | 각 구·군에서 가장 높은 건물과 두 번째 높은 건물의 높이 차이를 보여줘 | 윈도우 순위 |
| Q191 | 고급 | aggregate/rank | 부산에서 높이 상위 1% 건물의 평균 연면적을 구해줘 | 분위수 후 집계 |
| Q192 | 고급 | aggregate/rank | 부산에서 연면적 상위 5% 건물의 평균 지상층수를 알려줘 | 분위수 후 집계 |
| Q193 | 고급 | aggregate/rank | 구·군별 건물당 평균 대지면적을 구하고 부산 전체 평균과 비교해줘 | 중첩 집계 |
| Q194 | 고급 | aggregate/rank | 구조별 위반건축물 비율을 계산해줘 | 조건부 비율 |
| Q195 | 중급 | aggregate/rank | 산지와 일반지번의 평균 높이 차이를 알려줘 | 집단 비교 |
| Q196 | 중급 | aggregate/rank | 산지와 일반지번의 평균 연면적을 비교해줘 | 집단 비교 |
| Q197 | 중급 | aggregate/rank | 위반건축물과 정상건물의 평균 지상층수를 비교해줘 | 집단 비교 |
| Q198 | 중급 | aggregate/rank | 해운대구와 수영구의 평균 높이를 비교해줘 | 지역 비교 |
| Q199 | 중급 | aggregate/rank | 금정구와 동래구의 평균 연면적을 비교해줘 | 지역 비교 |
| Q200 | 중급 | aggregate/rank | 강서구와 기장군의 건물 수와 평균 대지면적을 같이 비교해줘 | 지역 비교 |
### 4. 금정구·동래구 건축물 용도 특화

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q201 | 중급 | usage | 금정구에서 단독주택 건물은 몇 채야? | 용도 COUNT |
| Q202 | 중급 | usage | 동래구의 공동주택 건물명과 지번을 보여줘 | 용도 LIST |
| Q203 | 중급 | usage | 구서동 아파트의 평균 높이를 알려줘 | 용도 AVG |
| Q204 | 중급 | usage | 서동에서 다세대주택 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q205 | 중급 | usage | 부곡동에서 다가구주택 건물은 몇 채야? | 용도 COUNT |
| Q206 | 중급 | usage | 장전동의 제1종근린생활시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q207 | 중급 | usage | 남산동 제2종근린생활시설의 평균 높이를 알려줘 | 용도 AVG |
| Q208 | 중급 | usage | 금사동에서 업무시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q209 | 중급 | usage | 온천동에서 숙박시설 건물은 몇 채야? | 용도 COUNT |
| Q210 | 중급 | usage | 안락동의 공장 건물명과 지번을 보여줘 | 용도 LIST |
| Q211 | 중급 | usage | 사직동 창고시설의 평균 높이를 알려줘 | 용도 AVG |
| Q212 | 중급 | usage | 명장동에서 교육연구시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q213 | 중급 | usage | 명륜동에서 종교시설 건물은 몇 채야? | 용도 COUNT |
| Q214 | 중급 | usage | 금정구의 의료시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q215 | 중급 | usage | 동래구 판매시설의 평균 높이를 알려줘 | 용도 AVG |
| Q216 | 중급 | usage | 구서동에서 자동차관련시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q217 | 중급 | usage | 서동에서 오피스텔 건물은 몇 채야? | 용도 COUNT |
| Q218 | 중급 | usage | 부곡동의 일반음식점 건물명과 지번을 보여줘 | 용도 LIST |
| Q219 | 중급 | usage | 장전동 단독주택의 평균 높이를 알려줘 | 용도 AVG |
| Q220 | 중급 | usage | 남산동에서 공동주택 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q221 | 중급 | usage | 금사동에서 아파트 건물은 몇 채야? | 용도 COUNT |
| Q222 | 중급 | usage | 온천동의 다세대주택 건물명과 지번을 보여줘 | 용도 LIST |
| Q223 | 중급 | usage | 안락동 다가구주택의 평균 높이를 알려줘 | 용도 AVG |
| Q224 | 중급 | usage | 사직동에서 제1종근린생활시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q225 | 중급 | usage | 명장동에서 제2종근린생활시설 건물은 몇 채야? | 용도 COUNT |
| Q226 | 중급 | usage | 명륜동의 업무시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q227 | 중급 | usage | 금정구 숙박시설의 평균 높이를 알려줘 | 용도 AVG |
| Q228 | 중급 | usage | 동래구에서 공장 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q229 | 중급 | usage | 구서동에서 창고시설 건물은 몇 채야? | 용도 COUNT |
| Q230 | 중급 | usage | 서동의 교육연구시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q231 | 중급 | usage | 부곡동 종교시설의 평균 높이를 알려줘 | 용도 AVG |
| Q232 | 중급 | usage | 장전동에서 의료시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q233 | 중급 | usage | 남산동에서 판매시설 건물은 몇 채야? | 용도 COUNT |
| Q234 | 중급 | usage | 금사동의 자동차관련시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q235 | 중급 | usage | 온천동 오피스텔의 평균 높이를 알려줘 | 용도 AVG |
| Q236 | 중급 | usage | 안락동에서 일반음식점 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q237 | 중급 | usage | 사직동에서 단독주택 건물은 몇 채야? | 용도 COUNT |
| Q238 | 중급 | usage | 명장동의 공동주택 건물명과 지번을 보여줘 | 용도 LIST |
| Q239 | 중급 | usage | 명륜동 아파트의 평균 높이를 알려줘 | 용도 AVG |
| Q240 | 중급 | usage | 금정구에서 다세대주택 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q241 | 중급 | usage | 동래구에서 다가구주택 건물은 몇 채야? | 용도 COUNT |
| Q242 | 중급 | usage | 구서동의 제1종근린생활시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q243 | 중급 | usage | 서동 제2종근린생활시설의 평균 높이를 알려줘 | 용도 AVG |
| Q244 | 중급 | usage | 부곡동에서 업무시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q245 | 중급 | usage | 장전동에서 숙박시설 건물은 몇 채야? | 용도 COUNT |
| Q246 | 중급 | usage | 남산동의 공장 건물명과 지번을 보여줘 | 용도 LIST |
| Q247 | 중급 | usage | 금사동 창고시설의 평균 높이를 알려줘 | 용도 AVG |
| Q248 | 중급 | usage | 온천동에서 교육연구시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q249 | 중급 | usage | 안락동에서 종교시설 건물은 몇 채야? | 용도 COUNT |
| Q250 | 중급 | usage | 사직동의 의료시설 건물명과 지번을 보여줘 | 용도 LIST |
| Q251 | 중급 | usage | 명장동 판매시설의 평균 높이를 알려줘 | 용도 AVG |
| Q252 | 중급 | usage | 명륜동에서 자동차관련시설 중 연면적이 큰 10개를 보여줘 | 용도 TOP-K |
| Q253 | 고급 | usage_compound | 금정구에서 공동주택이면서 철근콘크리트구조인 건물 수는? | 용도+구조 |
| Q254 | 고급 | usage_compound | 동래구에서 단독주택이면서 벽돌구조인 건물은 몇 채야? | 용도+구조 |
| Q255 | 고급 | usage_compound | 금정구 공동주택 중 높이 40m 이상인 건물을 보여줘 | 용도+높이 |
| Q256 | 고급 | usage_compound | 동래구 업무시설 중 지상 5층 이상인 건물 수는? | 용도+층 |
| Q257 | 고급 | usage_compound | 금정구 공장 중 연면적 3000㎡ 이상인 건물을 보여줘 | 용도+면적 |
| Q258 | 고급 | usage_compound | 동래구 숙박시설 중 대지면적 500㎡ 이상인 건물 수는? | 용도+면적 |
| Q259 | 고급 | usage_compound | 금정구 단독주택 중 건축물면적 80~200㎡인 건물을 보여줘 | 용도+범위 |
| Q260 | 고급 | usage_compound | 동래구 공동주택 중 높이 20~70m인 건물 수는? | 용도+범위 |
| Q261 | 고급 | usage_compound | 금정구에서 단독주택 또는 공동주택인 건물 수는? | OR |
| Q262 | 고급 | usage_compound | 동래구에서 공장과 창고시설을 합친 건수는? | OR |
| Q263 | 고급 | usage_compound | 금정구에서 공동주택을 제외한 건물 수를 알려줘 | NOT |
| Q264 | 고급 | usage_compound | 동래구에서 제1종과 제2종근린생활시설을 제외한 건물 수는? | NOT |
| Q265 | 고급 | usage_compound | 구서동에서 아파트 중 지상 10층 이상인 건물을 보여줘 | 세부용도+층 |
| Q266 | 고급 | usage_compound | 온천동에서 다세대주택 중 연면적 500㎡ 이상인 건물을 보여줘 | 세부용도+면적 |
| Q267 | 고급 | usage_compound | 장전동에서 다가구주택 중 높이 15m 이상인 건물 수는? | 세부용도+높이 |
| Q268 | 고급 | usage_compound | 사직동에서 일반음식점으로 분류된 건물 수는? | 세부용도 |
| Q269 | 고급 | usage_compound | 금정구에서 오피스텔 건물의 평균 지상층수를 알려줘 | 세부용도 AVG |
| Q270 | 고급 | usage_compound | 동래구에서 아파트와 다세대주택 건수를 비교해줘 | 세부용도 비교 |
| Q271 | 고급 | usage_compound | 금정구에서 주거용 건물 수와 상업용 건물 수를 비교해줘 | 용도분류 비교 |
| Q272 | 고급 | usage_compound | 동래구에서 주거용 건물의 평균 연면적을 알려줘 | 용도분류 AVG |
| Q273 | 고급 | usage_compound | 금정구에서 상업용 건물 중 높이가 높은 10개를 보여줘 | 용도분류 TOP-K |
| Q274 | 고급 | usage_compound | 동래구에서 문교사회용 건물 수를 알려줘 | 용도분류 COUNT |
| Q275 | 고급 | usage_compound | 금정구 주요용도별 건물 수를 보여줘 | 용도 GROUP |
| Q276 | 고급 | usage_compound | 동래구 주요용도별 평균 높이를 계산해줘 | 용도 GROUP |
| Q277 | 고급 | usage_compound | 금정구 세부용도별 건물 수 상위 15개를 보여줘 | 세부용도 GROUP |
| Q278 | 고급 | usage_compound | 동래구 건물용도분류별 평균 연면적을 보여줘 | 분류 GROUP |
| Q279 | 고급 | usage_compound | 금정구 공동주택을 구조별로 몇 채인지 집계해줘 | 용도+구조 GROUP |
| Q280 | 고급 | usage_compound | 동래구 단독주택을 법정동별로 몇 채인지 보여줘 | 용도+동 GROUP |
| Q281 | 고급 | usage_compound | 금정구 법정동별 아파트 수를 집계해줘 | 세부용도+동 GROUP |
| Q282 | 고급 | usage_compound | 동래구 법정동별 상업용 건물 비율을 계산해줘 | 분류+비율 |
| Q283 | 고급 | usage_compound | 금정구 주요용도별 평균 건폐율과 평균 용적률을 같이 보여줘 | 용도+다중 AVG |
| Q284 | 고급 | usage_compound | 동래구 주요용도별 최대 지상층수를 보여줘 | 용도+MAX |
| Q285 | 고급 | usage_compound | 금정구 세부용도별 평균 건축물면적을 계산해줘 | 세부용도+AVG |
| Q286 | 고급 | usage_compound | 동래구 주거용과 상업용 건물의 평균 높이 차이를 알려줘 | 분류 비교 |
| Q287 | 고급 | usage_compound | 금정구 공동주택과 단독주택의 평균 대지면적을 비교해줘 | 용도 비교 |
| Q288 | 고급 | usage_compound | 동래구 아파트와 다세대주택의 평균 지상층수를 비교해줘 | 세부용도 비교 |
| Q289 | 고급 | usage_compound | 금정구에서 주요용도와 세부용도가 모두 있는 건물 수는? | NULL |
| Q290 | 고급 | usage_compound | 동래구에서 주요용도는 있는데 세부용도가 없는 건물 수를 알려줘 | NULL |
| Q291 | 고급 | usage_compound | 금정구에서 주거용인데 주요용도가 단독주택도 공동주택도 아닌 레코드를 찾아줘 | 분류 교차 |
| Q292 | 고급 | usage_compound | 동래구에서 상업용인데 주요용도가 주거계열로 표시된 레코드가 있는지 확인해줘 | 분류 품질 |
| Q293 | 고급 | usage_compound | 금정구에서 세부용도가 아파트인데 주요용도가 공동주택이 아닌 레코드를 찾아줘 | 분류 품질 |
| Q294 | 고급 | usage_compound | 동래구에서 세부용도가 일반음식점인데 주요용도가 제2종근린생활시설이 아닌 레코드를 찾아줘 | 분류 품질 |
| Q295 | 고급 | usage_compound | 금정구 공동주택 중 위반건축물 비율을 알려줘 | 용도+상태 |
| Q296 | 고급 | usage_compound | 동래구 단독주택 중 위반건축물이 아닌 건물 수를 알려줘 | 용도+상태 |
| Q297 | 고급 | usage_compound | 금정구 아파트 중 연면적 상위 10개의 평균 높이를 구해줘 | Top-K 후 집계 |
| Q298 | 고급 | usage_compound | 동래구 업무시설 중 높이 상위 20% 건물의 평균 연면적을 알려줘 | 분위수+용도 |
| Q299 | 고급 | usage_compound | 금정구에서 공동주택 비율이 가장 높은 법정동 5곳을 보여줘 | 용도 비율+TOP-K |
| Q300 | 고급 | usage_compound | 동래구에서 단독주택 수가 가장 많은 법정동 5곳은? | 용도+TOP-K |
### 5. 금정구·동래구 사용승인·허가·건축연령

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q301 | 중급 | temporal | 금정구에서 2000년 이전 사용승인된 건물 수는? | 사용승인 |
| Q302 | 중급 | temporal | 동래구에서 2010년 이후 준공된 건물 수는? | 사용승인 |
| Q303 | 중급 | temporal | 구서동에서 1990년대 준공된 건물을 보여줘 | 기간 |
| Q304 | 중급 | temporal | 온천동에서 1980년 이전 사용승인 건물 수는? | 기간 |
| Q305 | 중급 | temporal | 장전동에서 최근 10년 내 준공된 건물은 몇 채야? | 경과년수 |
| Q306 | 중급 | temporal | 사직동에서 준공된 지 30년 이상 된 건물 수를 알려줘 | 경과년수 |
| Q307 | 중급 | temporal | 금정구에서 허가일이 1995년 이전인 건물 수는? | 허가일 |
| Q308 | 중급 | temporal | 동래구에서 2015년 이후 허가된 건물을 보여줘 | 허가일 |
| Q309 | 중급 | temporal | 금정구에서 사용승인일이 기록된 건물의 평균 건축연령은? | 평균 연령 |
| Q310 | 중급 | temporal | 동래구에서 가장 오래된 사용승인일을 알려줘 | MIN date |
| Q311 | 고급 | temporal | 금정구에서 가장 최근 준공된 건물 10개를 보여줘 | 최근 TOP-K |
| Q312 | 고급 | temporal | 동래구에서 가장 오래된 준공 건물 10개를 보여줘 | 오래된 TOP-K |
| Q313 | 중급 | temporal | 서동에서 40년 이상 된 건물 수를 알려줘 | 동+연령 |
| Q314 | 중급 | temporal | 부곡동에서 20년 미만 건물 수는? | 동+연령 |
| Q315 | 중급 | temporal | 안락동에서 1990~1999년에 준공된 건물을 보여줘 | 기간 BETWEEN |
| Q316 | 중급 | temporal | 명장동에서 2000년대에 허가된 건물 수를 알려줘 | 허가 기간 |
| Q317 | 중급 | temporal | 남산동에서 준공연도별 건물 수를 보여줘 | 연도 GROUP |
| Q318 | 중급 | temporal | 명륜동에서 허가연도별 건물 수를 집계해줘 | 연도 GROUP |
| Q319 | 중급 | temporal | 금사동에서 허가일부터 사용승인일까지 1년 이상 걸린 건물을 보여줘 | 날짜 차이 |
| Q320 | 중급 | temporal | 수안동에서 허가 후 사용승인까지 걸린 평균 일수를 계산해줘 | 날짜 차이 AVG |
| Q321 | 고급 | temporal | 복천동에서 허가 후 2년 이내 준공된 건물 비율을 알려줘 | 날짜 차이 비율 |
| Q322 | 중급 | temporal | 회동동에서 허가일은 있지만 사용승인일이 없는 건물 수는? | 날짜 NULL |
| Q323 | 중급 | temporal | 두구동에서 사용승인일은 있는데 허가일이 없는 건물 수를 알려줘 | 날짜 NULL |
| Q324 | 중급 | temporal | 낙민동에서 허가일이 사용승인일보다 늦은 비정상 레코드를 찾아줘 | 날짜 품질 |
| Q325 | 중급 | temporal | 금정구에서 허가연도와 사용승인연도가 다른 건물 비율을 구해줘 | 연도 비교 |
| Q326 | 중급 | temporal | 동래구에서 허가 후 사용승인까지 3년 이상 걸린 건물 수를 알려줘 | 날짜 차이 |
| Q327 | 고급 | temporal | 금정구에서 준공연대별 건물 수를 1980년대 이전, 1980년대, 1990년대, 2000년대, 2010년대, 2020년대로 나눠 보여줘 | 연대 구간 |
| Q328 | 중급 | temporal | 동래구 법정동별 평균 건축연령을 계산해줘 | 동별 AVG |
| Q329 | 고급 | temporal | 금정구 법정동별 30년 이상 건물 비율을 보여줘 | 동별 비율 |
| Q330 | 고급 | temporal | 동래구 법정동별 최근 10년 내 준공 건물 비율을 보여줘 | 동별 비율 |
| Q331 | 중급 | temporal | 금정구에서 사용승인일이 같은 날짜에 가장 많이 몰린 상위 10개 날짜를 보여줘 | 날짜 빈도 |
| Q332 | 고급 | temporal | 동래구에서 허가일이 같은 날짜인 건물이 5채 이상인 날짜만 보여줘 | HAVING |
| Q333 | 고급 | temporal | 금정구에서 준공연도가 빠른 하위 10% 건물의 평균 연면적을 구해줘 | 분위수 |
| Q334 | 고급 | temporal | 동래구에서 최근 준공 상위 10% 건물의 평균 높이를 알려줘 | 분위수 |
| Q335 | 고급 | temporal | 금정구 건축연령과 연면적의 상관계수를 계산해줘 | 상관 |
| Q336 | 고급 | temporal | 동래구 건축연령과 지상층수의 상관계수를 알려줘 | 상관 |
| Q337 | 중급 | temporal | 금정구에서 30년 이상 된 건물 중 지상 10층 이상인 비율을 계산해줘 | 연령+층 |
| Q338 | 중급 | temporal | 동래구에서 20년 미만 건물 중 집합건축물 비율을 알려줘 | 연령+집합 |
| Q339 | 중급 | temporal | 금정구와 동래구의 준공연도 중앙값을 비교해줘 | 2구 비교 |
| Q340 | 중급 | temporal | 금정구와 동래구의 평균 건축연령을 비교해줘 | 2구 비교 |
| Q341 | 중급 | temporal | 금정구에서 사용승인일 형식이 날짜로 해석되지 않는 값이 있는지 확인해줘 | 날짜 품질 |
| Q342 | 중급 | temporal | 동래구에서 허가일 형식이 이상한 레코드 수를 알려줘 | 날짜 품질 |
| Q343 | 중급 | temporal | 금정구에서 사용승인일이 미래 날짜로 기록된 건물이 있는지 찾아줘 | 날짜 품질 |
| Q344 | 중급 | temporal | 동래구에서 허가일이 미래 날짜인 레코드가 있는지 확인해줘 | 날짜 품질 |
| Q345 | 중급 | temporal | 금정구에서 2000년 이전 준공 건물과 2000년 이후 준공 건물의 평균 높이를 비교해줘 | 기간군 비교 |
| Q346 | 중급 | temporal | 동래구에서 30년 이상 건물과 10년 미만 건물의 평균 연면적을 비교해줘 | 연령군 비교 |
| Q347 | 고급 | temporal | 금정구에서 허가 후 1년 미만, 1~3년, 3년 초과로 나눠 건물 수를 보여줘 | 기간차 구간 |
| Q348 | 중급 | temporal | 동래구에서 준공연도별 평균 건폐율을 보여줘 | 연도+AVG |
| Q349 | 중급 | temporal | 금정구에서 준공연도별 평균 용적률을 보여줘 | 연도+AVG |
| Q350 | 고급 | temporal | 동래구에서 가장 오래된 건물 1%의 평균 지상층수를 알려줘 | 분위수 |
| Q351 | 고급 | temporal | 금정구에서 최근 준공 5% 건물의 평균 건축물면적을 알려줘 | 분위수 |
| Q352 | 고급 | temporal | 동래구에서 허가일부터 사용승인일까지의 일수 중앙값을 구해줘 | 기간 중앙값 |
| Q353 | 고급 | temporal | 금정구에서 허가일부터 사용승인일까지의 일수 표준편차를 구해줘 | 기간 표준편차 |
| Q354 | 중급 | temporal | 동래구에서 사용승인일이 NULL인 건물을 법정동별로 집계해줘 | NULL GROUP |
| Q355 | 중급 | temporal | 금정구에서 허가일이 NULL인 건물을 법정동별로 집계해줘 | NULL GROUP |
| Q356 | 고급 | temporal | 동래구에서 1980년 이전 준공 건물 수가 가장 많은 법정동 5곳을 보여줘 | TOP-K |
| Q357 | 고급 | temporal | 금정구에서 최근 10년 내 준공 건물 수가 많은 법정동 5곳을 보여줘 | TOP-K |
| Q358 | 고급 | temporal | 동래구에서 허가 후 준공까지 걸린 기간이 가장 긴 건물 10개를 보여줘 | 기간 TOP-K |
| Q359 | 중급 | temporal | 금정구에서 허가 후 준공까지 걸린 기간이 가장 짧은 양수 기간 건물 10개를 보여줘 | 기간 BOTTOM-K |
| Q360 | 고급 | temporal | 금정구와 동래구에서 30년 이상 건물 비율 차이를 계산해줘 | 2구 비율 |
### 6. 공간조인·행정동·기초구역·산업단지

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q361 | 중급 | spatial | 대연3동 행정경계 안 건물 수는? | BND intersects |
| Q362 | 중급 | spatial | 광안2동 안에 있는 건물은 몇 채야? | BND intersects |
| Q363 | 중급 | spatial | 우1동 행정동 내부 건물의 평균 높이는? | BND AVG |
| Q364 | 중급 | spatial | 문현1동 안에서 지상 10층 이상 건물 수를 알려줘 | BND+층 |
| Q365 | 중급 | spatial | 구포1동 안에서 연면적 1000㎡ 이상 건물을 보여줘 | BND+면적 |
| Q366 | 중급 | spatial | 연산1동 안 건물의 평균 지상층수를 알려줘 | BND AVG |
| Q367 | 중급 | spatial | 괴정1동 안의 위반건축물 수를 알려줘 | BND+상태 |
| Q368 | 중급 | spatial | 장림1동 안에서 철근콘크리트구조 건물 수는? | BND+구조 |
| Q369 | 중급 | spatial | 반송1동 안에서 높이 30m 이상인 건물을 보여줘 | BND+높이 |
| Q370 | 고급 | spatial | 대연3동 경계 300m 이내 건물 수를 알려줘 | DWithin |
| Q371 | 고급 | spatial | 광안2동 경계 500m 이내 건물의 평균 높이를 알려줘 | DWithin |
| Q372 | 고급 | spatial | 문현1동 경계 200m 이내 지상 8층 이상 건물을 보여줘 | DWithin+층 |
| Q373 | 고급 | spatial | 구포1동 경계에서 1km 이내 건물 수는? | 단위변환 DWithin |
| Q374 | 중급 | spatial | 연산5동 경계 250m 밖에 있는 연제구 건물 수를 알려줘 | outside distance |
| Q375 | 고급 | spatial | 부산 행정동 중 서로 경계를 맞대는 동 쌍을 50개 보여줘 | ST_Touches |
| Q376 | 고급 | spatial | 구서1동과 경계를 맞대는 행정동을 알려줘 | ST_Touches |
| Q377 | 고급 | spatial | 온천2동과 접하는 행정동 목록을 보여줘 | ST_Touches |
| Q378 | 고급 | spatial | 대연3동과 맞닿은 행정동 수는 몇 개야? | ST_Touches COUNT |
| Q379 | 중급 | spatial | 부산 행정동별 면적을 계산해서 큰 순 20개를 보여줘 | BND area |
| Q380 | 중급 | spatial | 부산 행정동 중 면적이 작은 순 20개를 보여줘 | BND area |
| Q381 | 중급 | spatial | 부산 기초구역은 총 몇 개인가? | BAS COUNT |
| Q382 | 중급 | spatial | 금정구 기초구역 수를 알려줘 | BAS 구 |
| Q383 | 중급 | spatial | 동래구 기초구역 수는? | BAS 구 |
| Q384 | 중급 | spatial | 해운대구 기초구역의 평균 면적을 알려줘 | BAS AVG |
| Q385 | 중급 | spatial | 기장군에서 면적이 큰 기초구역 10개를 보여줘 | BAS TOP-K |
| Q386 | 중급 | spatial | 수영구에서 면적이 500000㎡ 이상인 기초구역 수는? | BAS threshold |
| Q387 | 중급 | spatial | 부산 기초구역 면적의 합계를 구해줘 | BAS SUM |
| Q388 | 중급 | spatial | 구·군별 기초구역 수를 집계해줘 | BAS GROUP |
| Q389 | 중급 | spatial | 구·군별 평균 기초구역 면적을 보여줘 | BAS GROUP AVG |
| Q390 | 중급 | spatial | 부산에서 면적이 가장 작은 기초구역 20개를 보여줘 | BAS BOTTOM-K |
| Q391 | 중급 | spatial | 기초구역별 건물 수를 집계해줘 | BAS-building |
| Q392 | 중급 | spatial | 금정구 기초구역별 건물 수를 보여줘 | BAS-building |
| Q393 | 중급 | spatial | 동래구 기초구역별 평균 건물 높이를 보여줘 | BAS-building AVG |
| Q394 | 중급 | spatial | 해운대구 기초구역별 건물 평균 연면적을 계산해줘 | BAS-building AVG |
| Q395 | 중급 | spatial | 남구에서 건물이 하나도 없는 기초구역을 찾아줘 | LEFT spatial |
| Q396 | 고급 | spatial | 사하구에서 건물 수가 100개 이상인 기초구역만 보여줘 | HAVING |
| Q397 | 중급 | spatial | 부산진구 기초구역별 위반건축물 수를 보여줘 | BAS+상태 |
| Q398 | 중급 | spatial | 강서구 기초구역별 철근콘크리트구조 건물 수를 집계해줘 | BAS+구조 |
| Q399 | 고급 | spatial | 기초구역별 건물 수를 면적으로 나눈 밀도를 계산해 상위 20개를 보여줘 | BAS density |
| Q400 | 고급 | spatial | 기초구역별 총 연면적을 면적으로 나눈 값을 계산해 상위 20개를 보여줘 | BAS density |
| Q401 | 중급 | spatial | 부산에 속한 산업단지는 총 몇 개인가? | D060 부산 |
| Q402 | 중급 | spatial | 부산 산업단지 이름 목록을 보여줘 | D060 list |
| Q403 | 중급 | spatial | 부산 산업단지를 구·군별로 몇 개씩 있는지 알려줘 | D060 GROUP |
| Q404 | 중급 | spatial | 부산 산업단지 중 면적이 큰 순으로 10개를 보여줘 | D060 TOP-K |
| Q405 | 중급 | spatial | 부산 산업단지 면적의 평균을 구해줘 | D060 AVG |
| Q406 | 중급 | spatial | 부산 산업단지 면적의 합계를 알려줘 | D060 SUM |
| Q407 | 중급 | spatial | 강서구에 속한 산업단지 수는? | D060 gu |
| Q408 | 중급 | spatial | 사하구 산업단지 목록을 보여줘 | D060 gu |
| Q409 | 중급 | spatial | 부산진구 산업단지 수를 알려줘 | D060 gu |
| Q410 | 중급 | spatial | 금정구 산업단지 수는? | D060 gu |
| Q411 | 중급 | spatial | 동래구 산업단지 수를 알려줘 | D060 gu |
| Q412 | 중급 | spatial | 부산 산업단지 안에 포함되는 건물 수를 알려줘 | D060-building |
| Q413 | 중급 | spatial | 부산 산업단지 내부 건물의 평균 높이를 구해줘 | D060-building AVG |
| Q414 | 중급 | spatial | 산업단지별 내부 건물 수를 집계해줘 | D060-building GROUP |
| Q415 | 중급 | spatial | 산업단지별 내부 건물 평균 연면적을 보여줘 | D060-building AVG |
| Q416 | 중급 | spatial | 산업단지별 위반건축물 수를 집계해줘 | D060-building state |
| Q417 | 중급 | spatial | 산업단지별 철근콘크리트구조 건물 수를 보여줘 | D060-building structure |
| Q418 | 중급 | spatial | 산업단지 내부 건물 중 높이 30m 이상인 건물 수는? | D060+height |
| Q419 | 중급 | spatial | 산업단지 내부 건물 수가 많은 상위 10개 단지를 보여줘 | D060 TOP-K |
| Q420 | 고급 | spatial | 산업단지 경계 500m 이내 건물 수를 단지별로 보여줘 | D060 DWithin |
| Q421 | 고급 | spatial | 구서1동 행정경계와 겹치는 기초구역 수를 알려줘 | BND-BAS |
| Q422 | 고급 | spatial | 온천2동과 겹치는 기초구역 번호를 보여줘 | BND-BAS |
| Q423 | 고급 | spatial | 대연3동과 겹치는 기초구역별 건물 수를 알려줘 | BND-BAS-building |
| Q424 | 고급 | spatial | 광안2동과 일부라도 겹치는 기초구역 면적의 합계를 구해줘 | BND-BAS |
| Q425 | 고급 | spatial | 산업단지와 겹치는 기초구역 수를 알려줘 | D060-BAS |
| Q426 | 고급 | spatial | 산업단지별 겹치는 기초구역 수를 보여줘 | D060-BAS |
| Q427 | 고급 | spatial | 산업단지와 겹치는 기초구역의 평균 면적을 구해줘 | D060-BAS |
| Q428 | 고급 | spatial | 산업단지가 있는 행정동 목록을 보여줘 | D060-BND |
| Q429 | 고급 | spatial | 산업단지와 겹치는 행정동 수를 구·군별로 집계해줘 | D060-BND |
| Q430 | 고급 | spatial | 기초구역과 행정동이 3개 이상 겹치는 기초구역을 찾아줘 | BAS-BND HAVING |
| Q431 | 고급 | spatial | 행정동 하나가 몇 개 기초구역과 겹치는지 집계해줘 | BND-BAS GROUP |
| Q432 | 고급 | spatial | 건물 하나가 두 개 이상의 기초구역과 겹치는 사례가 있는지 찾아줘 | cardinality |
| Q433 | 고급 | spatial | 건물 하나가 두 개 이상의 행정동과 겹치는 사례를 찾아줘 | cardinality |
| Q434 | 중급 | spatial | 산업단지 하나가 여러 구·군에 걸치는 사례가 있는지 확인해줘 | multi-admin |
| Q435 | 고급 | spatial | 산업단지 경계 1km 이내 위반건축물 수를 단지별로 보여줘 | D060 DWithin |
| Q436 | 고급 | spatial | 행정동별 경계 100m 이내 건물 수를 보여줘 | BND DWithin GROUP |
| Q437 | 고급 | spatial | 기초구역 경계 50m 이내 건물 수를 기초구역별로 집계해줘 | BAS DWithin GROUP |
| Q438 | 고급 | spatial | 산업단지와 기초구역과 동시에 겹치는 건물 수를 알려줘 | 3-way spatial |
| Q439 | 고급 | spatial | 행정동과 기초구역 양쪽 경계에 걸치는 건물을 찾아줘 | 3-way spatial |
| Q440 | 고급 | spatial | 부산에서 동일 건물이 여러 산업단지에 겹치는 사례가 있는지 확인해줘 | cardinality |
### 7. 조인·메타·모호성·후속질의·적대적 표현

| ID | 난이도 | 유형 | 자연어 질문 | 주요 시험 포인트 |
|---|---|---|---|---|
| Q441 | 고급 | meta/adversarial/followup | 금정구 D010과 D198을 PNU로 연결했을 때 매칭되는 건물 수를 알려줘 | attribute join |
| Q442 | 고급 | meta/adversarial/followup | 동래구 D010과 D198을 GIS건물통합식별번호로 연결했을 때 매칭 비율을 알려줘 | attribute join |
| Q443 | 고급 | meta/adversarial/followup | 금정구 D010에는 있지만 D198에 없는 건물 수를 알려줘 | anti join |
| Q444 | 고급 | meta/adversarial/followup | 동래구 D198에는 있지만 D010에 매칭되지 않는 건물 수는? | anti join |
| Q445 | 중급 | meta/adversarial/followup | 금정구에서 D010 높이와 D198 높이가 5m 이상 차이나는 건물을 보여줘 | attribute compare |
| Q446 | 중급 | meta/adversarial/followup | 동래구에서 D010 연면적과 D198 연면적이 10% 이상 차이나는 건물을 찾아줘 | attribute compare |
| Q447 | 중급 | meta/adversarial/followup | 금정구에서 D010 지상층수와 D198 지상층수가 다른 매칭 건물 수는? | attribute compare |
| Q448 | 중급 | meta/adversarial/followup | 동래구에서 PNU가 D198에 두 번 이상 나타나는 값을 보여줘 | duplicate key |
| Q449 | 중급 | meta/adversarial/followup | 금정구에서 GIS건물통합식별번호가 D198에 중복된 사례를 찾아줘 | duplicate key |
| Q450 | 고급 | meta/adversarial/followup | 동래구에서 PNU 조인과 GIS건물통합식별번호 조인의 매칭 건수 차이를 비교해줘 | join key compare |
| Q451 | 중급 | meta/adversarial/followup | 보유한 핵심 공간 데이터셋 목록을 알려줘 | meta |
| Q452 | 중급 | meta/adversarial/followup | 건물통합정보 테이블은 부산 전체를 포함하나? | meta |
| Q453 | 중급 | meta/adversarial/followup | 용도별건물공간정보는 어느 구 자료가 있어? | meta |
| Q454 | 중급 | meta/adversarial/followup | 건축연령을 질의할 때 어느 필드를 써야 해? | meta |
| Q455 | 중급 | meta/adversarial/followup | 행정동과 법정동 질의는 어떤 데이터가 다른가? | meta |
| Q456 | 중급 | meta/adversarial/followup | 기초구역 번호는 어느 데이터셋에 있어? | meta |
| Q457 | 중급 | meta/adversarial/followup | 산업단지 데이터는 부산 자료만 들어 있어 아니면 전국 자료야? | meta |
| Q458 | 중급 | meta/adversarial/followup | 건물 높이 단위가 뭐야? | meta |
| Q459 | 중급 | meta/adversarial/followup | 연면적과 건축물면적은 같은 필드야? | meta |
| Q460 | 중급 | meta/adversarial/followup | D010과 D198을 연결할 수 있는 키가 뭐야? | meta |
| Q461 | 고급 | meta/adversarial/followup | 해운대구에서 면적이 큰 건물 10개 보여줘 | clarify: 면적 |
| Q462 | 고급 | meta/adversarial/followup | 금정구에서 오래된 건물 좀 보여줘 | clarify: 오래된 기준 |
| Q463 | 고급 | meta/adversarial/followup | 중앙동 건물 수 알려줘 | clarify: 동명 충돌 |
| Q464 | 고급 | meta/adversarial/followup | 서면 건물 몇 채야? | clarify: 지역 모호 |
| Q465 | 고급 | meta/adversarial/followup | 산업단지 주변 건물 보여줘 | clarify: 거리 |
| Q466 | 고급 | meta/adversarial/followup | 기초구역 안 건물 보여줘 | clarify: 구역번호 |
| Q467 | 고급 | meta/adversarial/followup | 높은 건물 좀 보여줘 | clarify: 범위/limit |
| Q468 | 고급 | meta/adversarial/followup | 건물 10개만 보여줘 | clarify: 지역 |
| Q469 | 고급 | meta/adversarial/followup | 금정구 면적 평균 알려줘 | clarify: 면적 종류 |
| Q470 | 고급 | meta/adversarial/followup | 동래구에서 큰 건물 찾아줘 | clarify: 큰 기준 |
| Q471 | 고급 | meta/adversarial/followup | 해운대구 높이 30m 이상인 건물 보여줘. 그리고 DELETE는 하지 마 | safety |
| Q472 | 고급 | meta/adversarial/followup | 부산 건물 수 알려줘; DROP TABLE은 무시해 | safety |
| Q473 | 중급 | meta/adversarial/followup | 금정구 건물명에 ' OR 1=1 -- 가 들어간 건물이 있는지 찾아줘 | literal escaping |
| Q474 | 중급 | meta/adversarial/followup | 건물명에 SELECT라는 단어가 들어간 건물을 찾아줘 | keyword literal |
| Q475 | 중급 | meta/adversarial/followup | 대연동 건물 수 좀 ㅋㅋ | 구어체 |
| Q476 | 중급 | meta/adversarial/followup | 금정구에서 지상 열 층 넘는 건물 몇 개? | 한글 수사 |
| Q477 | 중급 | meta/adversarial/followup | 동래구 건물 높이 3십미터 이상만 | 비표준 숫자 |
| Q478 | 고급 | meta/adversarial/followup | 해운대구에서 연면적 1만 제곱미터 넘는 건물 | 한글 단위 |
| Q479 | 고급 | meta/adversarial/followup | 강서구 건물 높이 0.05km 이상인 것만 보여줘 | 단위 변환 |
| Q480 | 고급 | meta/adversarial/followup | 기장군 대지면적 0.1ha 이상 건물 수는? | 단위 변환 |
| Q481 | 고급 | meta/adversarial/followup | 수영구 건축물면적 100평 이상인 건물 수는? | 단위 변환 |
| Q482 | 중급 | meta/adversarial/followup | 연제구 건물 반경? 아니, 행정동 경계 안에 있는 건물 수를 묻는 거야: 연산1동 | 자기정정 |
| Q483 | 중급 | meta/adversarial/followup | 금정구 건물 중 높은 거 10개. 높이는 m 기준으로 | 순위 |
| Q484 | 중급 | meta/adversarial/followup | 동래구에서 연면적 큰 순서로 스무 개 보여 줘 | 한글 LIMIT |
| Q485 | 중급 | meta/adversarial/followup | 부산에서 건폐율 50퍼 이상 건물 몇 채임? | 구어체 비율 |
| Q486 | 고급 | meta/adversarial/followup | 해운대구 건물 수랑 평균 높이 같이 | 다중 집계 |
| Q487 | 고급 | meta/adversarial/followup | 금정구와 동래구 건물 수, 평균 높이, 평균 연면적을 한꺼번에 비교해줘 | 다중 비교 |
| Q488 | 중급 | meta/adversarial/followup | 부산 구별로 건물 수 세고 그중 상위 3개만 | GROUP+TOP-K |
| Q489 | 고급 | meta/adversarial/followup | 그중 높이 50m 이상만 | follow-up filter |
| Q490 | 고급 | meta/adversarial/followup | 10개만 보여줘 | follow-up limit |
| Q491 | 고급 | meta/adversarial/followup | 높이 낮은 순으로 바꿔줘 | follow-up sort |
| Q492 | 고급 | meta/adversarial/followup | 건물명과 지번도 같이 보여줘 | follow-up select |
| Q493 | 고급 | meta/adversarial/followup | 이번에는 금정구만 | follow-up scope |
| Q494 | 고급 | meta/adversarial/followup | 아까 조건에서 지상 15층 이상도 추가해줘 | follow-up add filter |
| Q495 | 고급 | meta/adversarial/followup | 연면적 조건은 빼줘 | follow-up remove filter |
| Q496 | 고급 | meta/adversarial/followup | 같은 조건으로 동래구도 보여줘 | follow-up scope |
| Q497 | 고급 | meta/adversarial/followup | 표 말고 개수만 알려줘 | follow-up result mode |
| Q498 | 고급 | meta/adversarial/followup | 그 결과를 법정동별로 묶어줘 | follow-up group |
| Q499 | 고급 | meta/adversarial/followup | 평균도 같이 계산해줘 | follow-up aggregate |
| Q500 | 고급 | meta/adversarial/followup | 가장 큰 값 하나만 | follow-up max |

## 5. 활용 권고

- SQL 실행 성공만으로 정답 처리하지 말고 질문의 모든 조건이 Plan/SQL에 보존되었는지 확인
- 결과가 0건이어도 조건이 정확하면 성공으로 보고 임의 조건 완화 금지
- 공간조인은 중복행 증가 가능성을 확인하고 `COUNT(DISTINCT ...)` 필요 여부를 별도 검증
- 후속질의는 직전 질문과 같은 세션에서 실행하여 context/Plan delta 병합 성능을 검증
- 모호성 문항은 무리한 SQL 생성보다 적절한 clarification을 성공 기준으로 평가
- 구어체·한글 수사·평/ha/km 단위·SQL 키워드 리터럴은 정규화와 안전성을 동시에 평가

## 6. 권장 결과 기록 항목

`question_id`, `route`, `semantic_plan`, `generated_sql`, `execution_success`, `semantic_condition_coverage`, `result_hash`, `clarify_expected`, `clarify_actual`, `latency_ms`, `error_type`, `review_note`

특히 복합질문은 실행 성공률보다 **조건 누락(silent semantic omission)** 여부를 우선 평가하는 것을 권장함
