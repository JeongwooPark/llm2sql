"""평가문항 500: 엔진 답 vs KorDB 골드 수치 채점.

SQL 토큰이 같아도 숫자가 다르면 오답이다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

from txt2sql import Txt2SqlEngine, SessionContext
from txt2sql.evaluation.evaluation_policy import (
    infer_gold_context,
    infer_pred_context,
    match_numbers_in_haystack,
)
from txt2sql.evaluation.taxonomy import diagnose_eval_failure

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "docs" / "평가문항_500.json"
OUT = ROOT / "artifacts" / "evaluation" / "q500_gold_eval.json"
TIMEOUT_S = 40
FAIL_REPORT = ROOT / "artifacts" / "systematic_fix" / "14_failure_diagnosis.json"
FAIL_MD = ROOT / "artifacts" / "systematic_fix" / "14_failure_diagnosis.md"
FULL_COPY: Path | None = ROOT / "artifacts" / "systematic_fix" / "13_post_fix500.json"
PATTERN_IDS = ROOT / "artifacts" / "systematic_fix" / "15_pattern_ids.json"

NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+")
NAME_RE = re.compile(r"(?:A24|name)\s*=\s*([^,;/|]+)")


def _clip(text: str, n: int = 240) -> str:
    t = (text or "").replace("\n", " / ")
    return t if len(t) <= n else t[: n - 3] + "..."


def parse_nums(text: str) -> list[float]:
    text = text or ""
    # Metric-style kv only (p25=, resi_h=). Group ``gu=/n=`` keeps full NUM_RE.
    metric_kv = re.findall(
        r"(?:p\d+|pctl\w*|percentile\w*|var_\w*|resi_\w*|com_\w*|ind_\w*|"
        r"avg_\w*|sum_\w*|min_\w*|max_\w*|median_\w*|std\w*|diff|ratio\w*|pct\w*)"
        r"\s*=\s*(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+)",
        text,
        flags=re.IGNORECASE,
    )
    raw = metric_kv if metric_kv else NUM_RE.findall(text)
    out: list[float] = []
    for m in raw:
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def as_int(x: float) -> int | None:
    if abs(x - round(x)) < 1e-6 and abs(x) < 1e15:
        return int(round(x))
    return None


def num_close(a: float, b: float) -> bool:
    if as_int(a) is not None and as_int(b) is not None:
        return as_int(a) == as_int(b)
    scale = max(abs(a), abs(b), 1.0)
    return abs(a - b) <= max(0.05, 0.005 * scale)


def has_num(hay: list[float], target: float) -> bool:
    return any(num_close(x, target) for x in hay)


def row_nums(rows: list[dict[str, Any]] | None) -> list[float]:
    out: list[float] = []
    for row in rows or []:
        for v in row.values():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                out.append(float(v))
            elif isinstance(v, str):
                out.extend(parse_nums(v))
    return out


def row_text(rows: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for row in rows or []:
        for v in row.values():
            if v is not None:
                parts.append(str(v))
    return " ".join(parts)


def gold_names(gold: str) -> list[str]:
    names: list[str] = []
    for m in NAME_RE.findall(gold or ""):
        n = m.strip()
        if n and n not in {"없음", "null", "NULL", "-"}:
            names.append(n)
    return names


def primary_count(gold: str) -> float | None:
    nums = parse_nums(gold)
    if not nums:
        return None
    # "n=530; avg_h=..." 는 첫 정수, "114채"도 첫 수
    for x in nums:
        iv = as_int(x)
        if iv is not None:
            return float(iv)
    return nums[0]


def meta_ok(gold: str, answer: str) -> bool:
    g = gold or ""
    a = (answer or "").lower()
    if any(k in g for k in ("범위 외", "보유 데이터에 없다", "해당하지")):
        return any(
            k in a
            for k in (
                "범위",
                "없",
                "지원하지",
                "해당",
                "제공하지",
                "조회할 수 없",
                "날씨",
                "환율",
                "항공",
                "점심",
            )
        )
    if any(k in g for k in ("확인 필요", "모호", "기준이 없다", "어느")):
        return any(k in a for k in ("확인", "어느", "모호", "구체", "기준", "어디", "동을"))
    if "주관" in g or "객관" in g:
        return any(k in a for k in ("주관", "객관", "데이터", "높이", "연면적", "조회"))
    return any(
        k in a
        for k in ("gis", "건물", "조회", "데이터", "컬럼", "부산", "공간", "질문")
    )


def score(
    kind: str,
    gold: str,
    answer: str,
    rows: list[dict[str, Any]] | None,
    *,
    question: str = "",
    sql: str | None = None,
) -> tuple[bool, str]:
    g = gold or ""
    a = answer or ""
    blob = a + " " + row_text(rows)
    hay = parse_nums(a) + row_nums(rows)

    if kind == "meta":
        ok = meta_ok(g, a)
        return ok, "meta-intent" if ok else "meta-mismatch"

    if kind == "count":
        target = primary_count(g)
        if target is None:
            return False, "gold-no-number"
        gold_ctx = infer_gold_context(kind="count", gold=g, question=question)
        pred_ctx = infer_pred_context(
            kind="count", answer=a, rows=rows, sql=sql, question=question
        )
        ok, _, reason = match_numbers_in_haystack(
            hay, [target], gold_ctx=gold_ctx, pred_ctx=pred_ctx
        )
        return ok, reason if ok else f"count-mismatch gold={target:g} pred={hay[:6]} ({reason})"

    if kind == "scalar":
        names = gold_names(g)
        nums = parse_nums(g)
        if names:
            if not any(n in blob for n in names[:3]):
                return False, f"name-missing {names[0]}"
        if not nums:
            return (bool(names) and any(n in blob for n in names[:3]), "scalar-name")
        gold_ctx = infer_gold_context(kind="scalar", gold=g, question=question)
        pred_ctx = infer_pred_context(
            kind="scalar", answer=a, rows=rows, sql=sql, question=question
        )
        ok, hits, reason = match_numbers_in_haystack(
            hay, nums, gold_ctx=gold_ctx, pred_ctx=pred_ctx
        )
        if ok:
            return True, reason
        return False, f"scalar-mismatch hits={hits} gold={nums[:4]} ({reason})"

    if kind == "list":
        names = gold_names(g)
        named = [n for n in names if n != "없음"]
        if not named:
            target = primary_count(g)
            if target is not None and has_num(hay, target):
                return True, "list-count"
            return bool(a.strip()), "list-nonempty"
        top = named[0]
        if top in blob:
            extra = sum(1 for n in named[1:4] if n in blob)
            return True, f"list-top1 extra={extra}"
        return False, f"list-top-missing {top}"

    if kind == "compare":
        names = gold_names(g)
        nums = parse_nums(g)
        if names:
            if names[0] not in blob and not any(n in blob for n in names[:2]):
                return False, f"compare-name-missing {names[0]}"
        if nums:
            hits = sum(1 for x in nums[:4] if has_num(hay, x))
            if hits == 0:
                return False, "compare-num-missing"
        return True, "compare-ok"

    if kind == "group":
        names = gold_names(g)
        labels = re.findall(r"(?:table_name|display_name|A9|A4)\s*=\s*([^,;/]+)", g)
        keys = [x.strip() for x in (names + labels) if x.strip()]
        if not keys:
            nums = parse_nums(g)
            hits = sum(1 for x in nums[:6] if has_num(hay, x))
            ok = hits >= min(2, max(1, len(nums[:6]) // 2)) if nums else bool(a.strip())
            return ok, "group-nums" if ok else "group-mismatch"
        hit = sum(1 for k in keys[:6] if k in blob)
        ok = hit >= 1
        return ok, f"group-labels {hit}" if ok else "group-label-missing"

    return bool(a.strip()), "fallback"


def _ask(engine: Txt2SqlEngine, q: str, session: SessionContext | None):
    return engine.ask(q, session=session, include_map=False)


def summarize(rows: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for r in rows if r["pass"])
    failed = total - passed
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "ok": 0})
    by_block = {"nl100": {"n": 0, "ok": 0}, "new400": {"n": 0, "ok": 0}, "followup": {"n": 0, "ok": 0}}
    latencies = [r["ms"] for r in rows]
    latencies_ok = [r["ms"] for r in rows if r["pass"]]
    latencies_no_llm = [r["ms"] for r in rows if not r.get("llm_used")]
    latencies_llm = [r["ms"] for r in rows if r.get("llm_used")]
    llm_used_n = sum(1 for r in rows if r.get("llm_used"))
    llm_call_counts = [int(r.get("llm_call_count") or 0) for r in rows]
    llm_purposes: Counter[str] = Counter()
    llm_by_route: dict[str, dict[str, int]] = defaultdict(
        lambda: {"n": 0, "llm": 0}
    )
    buckets = Counter()
    routes = Counter()
    fail_reasons = Counter()
    for r in rows:
        by_kind[r["kind"]]["n"] += 1
        by_cat[r["cat"]]["n"] += 1
        by_source[r["source"]]["n"] += 1
        if r["pass"]:
            by_kind[r["kind"]]["ok"] += 1
            by_cat[r["cat"]]["ok"] += 1
            by_source[r["source"]]["ok"] += 1
        else:
            fail_reasons[r["reason"].split()[0]] += 1
        routes[r.get("route") or "(none)"] += 1
        route_key = str(r.get("route") or "(none)")
        llm_by_route[route_key]["n"] += 1
        if r.get("llm_used"):
            llm_by_route[route_key]["llm"] += 1
        for purpose in r.get("llm_calls") or []:
            llm_purposes[str(purpose)] += 1
        ms = r["ms"]
        if ms < 100:
            buckets["<100ms"] += 1
        elif ms < 500:
            buckets["100–500ms"] += 1
        elif ms < 2000:
            buckets["0.5–2s"] += 1
        elif ms < 10000:
            buckets["2–10s"] += 1
        elif ms < 30000:
            buckets["10–30s"] += 1
        else:
            buckets[">30s"] += 1
        src_id = str(r["id"])
        if src_id.startswith("N"):
            by_block["nl100"]["n"] += 1
            if r["pass"]:
                by_block["nl100"]["ok"] += 1
        else:
            by_block["new400"]["n"] += 1
            if r["pass"]:
                by_block["new400"]["ok"] += 1
        if r.get("parent") or r.get("cat") in {"후속", "후속앵커"}:
            by_block["followup"]["n"] += 1
            if r["pass"]:
                by_block["followup"]["ok"] += 1

    def pct(ok: int, n: int) -> float:
        return round(100.0 * ok / n, 1) if n else 0.0

    def pctile(vals: list[int], p: float) -> int:
        if not vals:
            return 0
        s = sorted(vals)
        k = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
        return s[k]

    return {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gold_file": str(GOLD),
        "md_file": str(ROOT / "docs" / "평가문항_500.md"),
        "scoring": "gold_value_match_not_sql_tokens",
        "timeout_s": TIMEOUT_S,
        "elapsed_s": round(elapsed, 1),
        "total": total,
        "passed": passed,
        "failed": failed,
        "accuracy_pct": pct(passed, total),
        "latency": {
            "avg_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
            "p50_ms": pctile(latencies, 50),
            "p95_ms": pctile(latencies, 95),
            "max_ms": max(latencies) if latencies else 0,
            "avg_ms_passed": int(sum(latencies_ok) / len(latencies_ok)) if latencies_ok else 0,
            "avg_ms_no_llm": int(sum(latencies_no_llm) / len(latencies_no_llm))
            if latencies_no_llm
            else 0,
            "avg_ms_llm": int(sum(latencies_llm) / len(latencies_llm))
            if latencies_llm
            else 0,
            "p50_ms_no_llm": pctile(latencies_no_llm, 50),
            "p50_ms_llm": pctile(latencies_llm, 50),
            "buckets": dict(buckets),
        },
        "llm": {
            "used_n": llm_used_n,
            "used_pct": pct(llm_used_n, total),
            "avg_calls_per_question": round(
                sum(llm_call_counts) / len(llm_call_counts), 2
            )
            if llm_call_counts
            else 0.0,
            "total_calls": sum(llm_call_counts),
            "purposes": dict(llm_purposes.most_common(20)),
            "by_route": {
                k: {
                    **v,
                    "llm_pct": pct(v["llm"], v["n"]),
                }
                for k, v in sorted(llm_by_route.items(), key=lambda x: -x[1]["n"])
            },
        },
        "by_kind": {
            k: {**v, "acc_pct": pct(v["ok"], v["n"])}
            for k, v in sorted(by_kind.items(), key=lambda x: -x[1]["n"])
        },
        "by_cat": {
            k: {**v, "acc_pct": pct(v["ok"], v["n"])}
            for k, v in sorted(by_cat.items(), key=lambda x: -x[1]["n"])
        },
        "by_source": {
            k: {**v, "acc_pct": pct(v["ok"], v["n"])}
            for k, v in sorted(by_source.items())
        },
        "by_block": {
            k: {**v, "acc_pct": pct(v["ok"], v["n"])} for k, v in by_block.items()
        },
        "routes": dict(routes.most_common()),
        "fail_reasons": dict(fail_reasons.most_common(20)),
        "failed_items": [
            {
                "id": r["id"],
                "cat": r["cat"],
                "kind": r["kind"],
                "q": r["q"],
                "gold": r["gold"],
                "answer": r["answer"],
                "reason": r["reason"],
                "route": r["route"],
                "ms": r["ms"],
                "llm_used": r.get("llm_used"),
                "llm_call_count": r.get("llm_call_count"),
                "sql": r.get("sql"),
                "root_causes": r.get("root_causes") or [],
            }
            for r in rows
            if not r["pass"]
        ],
        "rows": rows,
    }


def dump(payload: dict[str, Any]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_failure_report(payload: dict[str, Any]) -> None:
    from collections import Counter

    FAIL_REPORT.parent.mkdir(parents=True, exist_ok=True)
    if FULL_COPY is not None:
        FULL_COPY.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    failed = [r for r in payload.get("rows") or [] if not r.get("pass")]
    cause_counts: Counter[str] = Counter()
    by_cat: dict[str, Counter[str]] = defaultdict(Counter)
    by_kind: dict[str, Counter[str]] = defaultdict(Counter)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in failed:
        causes = list(row.get("root_causes") or ["UNCLASSIFIED"])
        for cause in causes:
            cause_counts[cause] += 1
            by_cat[str(row.get("cat") or "?")][cause] += 1
            by_kind[str(row.get("kind") or "?")][cause] += 1
            if len(samples[cause]) < 8:
                samples[cause].append(
                    {
                        "id": row.get("id"),
                        "q": row.get("q"),
                        "gold": row.get("gold"),
                        "answer": row.get("answer"),
                        "sql": row.get("sql"),
                        "route": row.get("route"),
                        "reason": row.get("reason"),
                        "ms": row.get("ms"),
                    }
                )
    report = {
        "when": payload.get("when"),
        "baseline": {
            "passed": 198,
            "failed": 302,
            "accuracy_pct": 39.6,
            "source": "artifacts/evaluation/q500_gold_eval.json (2026-08-24 12:53)",
        },
        "current": {
            "passed": payload.get("passed"),
            "failed": payload.get("failed"),
            "accuracy_pct": payload.get("accuracy_pct"),
            "elapsed_s": payload.get("elapsed_s"),
            "timeout_s": payload.get("timeout_s"),
        },
        "cause_counts": dict(cause_counts.most_common()),
        "by_cat": {k: dict(v) for k, v in sorted(by_cat.items())},
        "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
        "samples": {k: samples[k] for k in cause_counts},
        "failed_items": payload.get("failed_items") or [],
    }
    FAIL_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 500문항 실패 원인 진단",
        "",
        f"- 시각: {payload.get('when')}",
        f"- 현재: {payload.get('passed')}/{payload.get('total')} "
        f"({payload.get('accuracy_pct')}%)",
        "- 기준선: 198/500 (39.6%)",
        "",
        "## 구조 원인 빈도",
        "",
    ]
    for cause, n in cause_counts.most_common():
        lines.append(f"- `{cause}`: {n}")
    lines.extend(["", "## 대표 실패 사례 (원인별 최대 8건)", ""])
    for cause, items in samples.items():
        lines.append(f"### {cause}")
        lines.append("")
        for item in items:
            lines.append(
                f"- `{item.get('id')}` {item.get('q')}  \n"
                f"  gold=`{item.get('gold')}` reason=`{item.get('reason')}` "
                f"route=`{item.get('route')}`"
            )
        lines.append("")
    FAIL_MD.write_text("\n".join(lines), encoding="utf-8")


def _load_wanted_ids(argv: list[str]) -> tuple[set[str] | None, list[str]]:
    global OUT, FAIL_REPORT, FAIL_MD, FULL_COPY, PATTERN_IDS, GOLD, TIMEOUT_S
    wanted: set[str] | None = None
    rest: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--pattern-ids" and i + 1 < len(argv):
            PATTERN_IDS = Path(argv[i + 1])
            i += 2
            continue
        i += 1
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--pattern-ids":
            i += 2
            continue
        if arg == "--ids" and i + 1 < len(argv):
            wanted = {x.strip() for x in argv[i + 1].split(",") if x.strip()}
            i += 2
            continue
        if arg == "--ids-file" and i + 1 < len(argv):
            raw = json.loads(Path(argv[i + 1]).read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "ids" in raw:
                wanted = {str(x) for x in raw["ids"]}
            elif isinstance(raw, list):
                wanted = {str(x) for x in raw}
            i += 2
            continue
        if arg == "--pattern" and i + 1 < len(argv):
            blob = json.loads(PATTERN_IDS.read_text(encoding="utf-8"))
            key = argv[i + 1]
            if key not in blob:
                raise SystemExit(f"unknown pattern {key}; have {sorted(blob)}")
            wanted = {str(x) for x in blob[key]}
            i += 2
            continue
        if arg == "--gold" and i + 1 < len(argv):
            GOLD = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--timeout" and i + 1 < len(argv):
            TIMEOUT_S = int(argv[i + 1])
            i += 2
            continue
        if arg == "--out" and i + 1 < len(argv):
            OUT = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--fail-report" and i + 1 < len(argv):
            FAIL_REPORT = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--fail-md" and i + 1 < len(argv):
            FAIL_MD = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--full-copy" and i + 1 < len(argv):
            FULL_COPY = Path(argv[i + 1])
            i += 2
            continue
        if arg == "--no-full-copy":
            FULL_COPY = None  # type: ignore[misc]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return wanted, rest


def _select_questions(
    questions: list[dict[str, Any]], wanted: set[str]
) -> list[dict[str, Any]]:
    sessions = {c["id"]: c.get("session") for c in questions}
    wanted_sessions = {sessions[i] for i in wanted if sessions.get(i)}
    selected = [
        case
        for case in questions
        if case["id"] in wanted
        or (case.get("session") and case["session"] in wanted_sessions)
    ]
    return selected or [case for case in questions if case["id"] in wanted]


def main() -> int:
    global FULL_COPY
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    wanted, rest = _load_wanted_ids(sys.argv)
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    questions = gold["questions"]
    if wanted:
        questions = _select_questions(questions, wanted)
        if FULL_COPY is not None and str(FULL_COPY).endswith("13_post_fix500.json"):
            FULL_COPY = None
    elif rest and rest[0].isdigit():
        questions = questions[: int(rest[0])]
    sessions: dict[str, SessionContext] = {}
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    engine = Txt2SqlEngine.from_env()
    try:
        print(f"=== 평가문항 500 골드채점 timeout={TIMEOUT_S}s ===\n", flush=True)
        for i, case in enumerate(questions, 1):
            qid = case["id"]
            q = case["q"]
            sid = case.get("session")
            session = sessions.setdefault(sid, SessionContext()) if sid else None
            print(f"[{i:03d}/{len(questions)}] ... {qid}", flush=True)
            t1 = time.perf_counter()
            timed_out = False
            error = None
            r = None
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                fut = pool.submit(_ask, engine, q, session)
                r = fut.result(timeout=TIMEOUT_S)
            except FuturesTimeout:
                timed_out = True
                error = f"timeout>{TIMEOUT_S}s"
                try:
                    engine.close()
                except Exception:
                    pass
                engine = Txt2SqlEngine.from_env()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
                try:
                    engine.close()
                except Exception:
                    pass
                engine = Txt2SqlEngine.from_env()
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            ms = int((time.perf_counter() - t1) * 1000)
            answer = "" if r is None else (r.answer or "")
            pred_rows = [] if r is None else list(r.rows or [])
            ok, reason = False, error or "no-result"
            if timed_out:
                reason = error or "timeout"
            elif r is None:
                reason = error or "no-result"
            else:
                ok, reason = score(
                    case["kind"],
                    case.get("gold") or "",
                    answer,
                    pred_rows,
                    question=case.get("q") or "",
                    sql=r.sql if r else None,
                )
                if not r.ok and not ok:
                    reason = f"engine-fail:{r.error or r.route}"
            rec = {
                "id": qid,
                "cat": case.get("cat"),
                "kind": case.get("kind"),
                "source": case.get("source"),
                "session": sid,
                "parent": case.get("parent"),
                "q": q,
                "gold": case.get("gold"),
                "pass": ok,
                "reason": reason,
                "route": None if r is None else r.route,
                "error": error or (None if r is None else r.error),
                "ms": ms,
                "answer": _clip(answer),
                "sql": _clip("" if r is None else str(r.sql or ""), 800 if not ok else 220),
                "row_count": 0 if r is None else (r.row_count or 0),
                "timed_out": timed_out,
                "root_causes": []
                if ok
                else diagnose_eval_failure(
                    question=q,
                    sql="" if r is None else str(r.sql or ""),
                    answer=answer,
                    reason=reason,
                    timed_out=timed_out,
                    route=None if r is None else r.route,
                ),
                "stage_latency_ms": {}
                if r is None
                else dict(getattr(r, "stage_latency_ms", None) or {}),
                "llm_used": False if r is None else bool(getattr(r, "llm_used", False)),
                "llm_calls": []
                if r is None
                else list(getattr(r, "llm_calls", None) or []),
                "llm_call_count": 0
                if r is None
                else len(getattr(r, "llm_calls", None) or []),
                "latency_ms": ms,
            }
            rows.append(rec)
            mark = "OK" if ok else "FAIL"
            print(
                f"[{i:03d}/{len(questions)}] {mark} {qid} {ms}ms {case.get('kind')} {reason}",
                flush=True,
            )
            if i % 10 == 0 or i == len(questions):
                payload = summarize(rows, time.perf_counter() - t0)
                payload["partial"] = i < len(questions)
                dump(payload)
                print(
                    f"  .. saved {payload['passed']}/{payload['total']} "
                    f"acc={payload['accuracy_pct']}% elapsed={payload['elapsed_s']}s",
                    flush=True,
                )
    finally:
        try:
            engine.close()
        except Exception:
            pass

    payload = summarize(rows, time.perf_counter() - t0)
    payload["partial"] = False
    if wanted:
        pattern_rows = [r for r in rows if r["id"] in wanted]
        payload["pattern_total"] = len(pattern_rows)
        payload["pattern_passed"] = sum(1 for r in pattern_rows if r["pass"])
        payload["pattern_failed"] = [r["id"] for r in pattern_rows if not r["pass"]]
    dump(payload)
    write_failure_report(payload)
    if wanted:
        print(
            f"\n=== 패턴 {payload.get('pattern_passed')}/{payload.get('pattern_total')} "
            f"전체선택 {payload['passed']}/{payload['total']} "
            f"({payload['accuracy_pct']}%) {payload['elapsed_s']}s ===",
            flush=True,
        )
    else:
        print(
            f"\n=== 결과 {payload['passed']}/{payload['total']} "
            f"({payload['accuracy_pct']}%) {payload['elapsed_s']}s ===",
            flush=True,
        )
    print("wrote", OUT, flush=True)
    print("wrote", FAIL_REPORT, flush=True)
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
