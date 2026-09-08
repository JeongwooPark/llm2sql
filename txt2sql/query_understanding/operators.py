"""한국어 논리·집계·정렬·범위 연산자 사전."""

from __future__ import annotations

AND_PATTERNS = ("그리고", "이면서", "동시에", r"\s중\s", r"중$")
OR_PATTERNS = ("또는", "혹은", "이거나", r"(?<![가-힣])나 ", "이나 ", "든지", "둘 중 하나")
NOT_PATTERNS = ("제외", "아닌", "빼고", "뺀", "이외", "말고", "없이")
_RANGE_UNIT = r"제곱미터|평방미터|㎡|m²|m2|평(?!수|형|방)|미터|m|층|%"
RANGE_PATTERNS = (
    rf"(?P<lo>\d+(?:\.\d+)?)\s*(?P<u1>{_RANGE_UNIT})?\s*(?P<lo_rel>이상|초과|부터)\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<u2>{_RANGE_UNIT})?\s*(?P<hi_rel>이하|미만|까지|사이)",
    rf"(?P<lo>\d+(?:\.\d+)?)\s*(?P<u1>{_RANGE_UNIT})?\s*부터\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<u2>{_RANGE_UNIT})?\s*까지",
    rf"(?P<lo>\d+(?:\.\d+)?)\s*(?P<u1>{_RANGE_UNIT})?\s*사이\s*(?P<hi>\d+(?:\.\d+)?)",
    rf"(?P<lo>\d+(?:\.\d+)?)\s*(?P<u1>{_RANGE_UNIT})?\s*[~～\-]\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<u2>{_RANGE_UNIT})",
)
SORT_ASC = ("낮은", "작은", "오래된", "낮은 순", "작은 순", "오래된 순")
SORT_DESC = ("높은", "큰", "최신", "높은 순", "큰 순", "최신 순", "가장 높", "가장 큰")
AGG_MAP = {
    "평균": "avg",
    "합계": "sum",
    "총합": "sum",
    "최대": "max",
    "최소": "min",
    "최솟값": "min",
    "최댓값": "max",
    "중앙값": "median",
    "표준편차": "stddev",
    "분산": "variance",
}
METRIC_MAP = {
    "높이": "height_m",
    "고도": "height_m",
    "연면적": "gross_floor_area_m2",
    "건축물면적": "building_area_m2",
    "건축면적": "building_area_m2",
    "건물면적": "building_area_m2",
    "대지면적": "site_area_m2",
    "면적": "gross_floor_area_m2",
    "지상층": "ground_floors",
    "지하층": "basement_floors",
    "층수": "ground_floors",
    "용도": "usage",
    "건폐율": "building_coverage_ratio",
    "용적율": "floor_area_ratio",
    "용적률": "floor_area_ratio",
    "위반": "violation_status",
    "건폐율": "building_coverage_ratio",
    "산지": "special_land",
    "특수지": "special_land",
    "일반지번": "special_land",
    "가지번": "special_land",
    "블럭지번": "special_land",
    "블록지번": "special_land",
    "동명": "building_dong_name",
    "건물동명": "building_dong_name",
    "건축연령": "building_age_years",
    "건축 연령": "building_age_years",
    "사용승인": "approval_date",
    "허가일": "permit_date",
    "허가일자": "permit_date",
}
OUTPUT_FIELD_MAP = {
    "이름": "name",
    "건물명": "name",
    "지번": "lot_address",
    "법정동": "legal_dong",
    "용도": "usage",
    "높이": "height_m",
    "연면적": "gross_floor_area_m2",
    "건축물면적": "building_area_m2",
    "건축면적": "building_area_m2",
    "층수": "ground_floors",
    "건폐율": "building_coverage_ratio",
    "용적율": "floor_area_ratio",
}
OUTPUT_HINTS = (
    "이름",
    "건물명",
    "지번",
    "법정동",
    "용도",
    "높이",
    "연면적",
    "건축물면적",
    "건축면적",
    "층수",
    "건폐율",
    "용적율",
)
COMPARE_PATTERNS = (
    r"(?P<left>건축물면적|건축면적|건물면적|연면적|대지면적|높이).{0,6}(?P<rel>보다 큰|보다 작은|보다 높은|보다 낮은).{0,6}(?P<right>건축물면적|건축면적|건물면적|연면적|대지면적|높이)",
    r"(?P<left>건축물면적|건축면적|건물면적|연면적|대지면적|높이).{0,4}(?P<right>건축물면적|건축면적|건물면적|연면적|대지면적|높이).{0,4}(?P<rel>보다 큰|보다 작은|보다 높은|보다 낮은)",
    r"(?P<left>건축물면적|건축면적|건물면적).{0,8}(?P<right>연면적|대지면적).{0,4}(보다 큰|보다 작)",
    r"(?P<left>높이).{0,20}(?P<right>지상층수|지상층|층수)(?:의\s*)?(?P<scale>[1-9]\d*)\s*배.{0,8}(?P<rel>보다 큰|보다 높)",
)
GROUP_HINTS = (
    "특수지구분명별",
    "위반건축물 여부별",
    "위반건축물여부별",
    "법정동코드별",
    "구·군별",
    "세부용도별",
    "용도별",
    "층수별",
    "층별",
    "구별",
    "군별",
    "구조별",
    "법정동별",
    "기초구역별",
)
GROUP_FIELD_MAP = {
    "특수지구분명별": "special_land",
    "위반건축물 여부별": "violation_status",
    "위반건축물여부별": "violation_status",
    "법정동코드별": "bjd_cd",
    "구·군별": "sigungu_name",
    "세부용도별": "detail_usage",
    "용도별": "usage",
    "층수별": "ground_floors",
    "층별": "ground_floors",
    "구별": "sigungu_name",
    "군별": "sigungu_name",
    "구조별": "structure",
    "법정동별": "legal_dong",
    "기초구역별": "basic_zone",
}
RATIO_HINTS = ("비율", "퍼센트", "몇%", "몇 %", "%씩", "몇 프로", "백분율")
RANK_HINTS = ("상위", "순위", "가장", "제일", "랭킹", "큰 순", "높은 순")
PERCENTILE_HINTS = ("백분위", "분위")
BIN_HINTS = ("구간별", "구간 별", "크기별", "나눠", "나누어", "나눠서")
HAVING_HINTS = ("평균이", "합계가", "건수가")
LIMIT_PATTERN = r"(?P<n>\d+)\s*(개|곳|채|동)"
PLACE_PATTERN = r"[가-힣A-Za-z0-9]+(?:구|군|시|동|읍|면|리)"
NUMBER_UNIT_PATTERN = r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>m|미터|km|㎡|m2|평|층)?"
