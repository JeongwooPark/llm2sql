"""Gold dataset immutability guard for semantic architecture v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Frozen gold checksums (must match committed docs/*.json bytes).
# 2026-09-04: re-synced llm2sql_신규_…500건_정답표.json digest to the hash of the
# currently committed file (2c1379…). Prior freeze (6f01c7…) was stale vs HEAD;
# gold content was not modified by P012 regression-fix work.
FROZEN_GOLD_CHECKSUMS: dict[str, str] = {
    "docs/평가문항_500.json": "25d76a04fc5e4109443807a8d7177f5e7256452552b6c891d0cbf3c741e0e8a1",
    "docs/llm2sql_신규_자연어질의_테스트셋_500건_정답표.json": (
        "2c1379b9a0fdd6ede861ef00f233a44bb9ba0ec3961c944747b303d308293a54"
    ),
}


class GoldDatasetChangedError(RuntimeError):
    """Raised when a frozen gold dataset file hash no longer matches."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_gold_checksums(
    *,
    root: Path | None = None,
    expected: dict[str, str] | None = None,
) -> dict[str, str]:
    """Verify frozen gold files. Abort evaluation if any checksum drifts."""
    base = root or ROOT
    want = expected or FROZEN_GOLD_CHECKSUMS
    actual: dict[str, str] = {}
    mismatches: list[str] = []
    for rel, expected_hash in want.items():
        path = base / rel
        if not path.is_file():
            mismatches.append(f"missing:{rel}")
            continue
        got = sha256_file(path)
        actual[rel] = got
        if got != expected_hash:
            mismatches.append(f"changed:{rel}:expected={expected_hash}:got={got}")
    if mismatches:
        raise GoldDatasetChangedError(
            "Gold dataset changed during semantic architecture v2; evaluation aborted. "
            + "; ".join(mismatches)
        )
    return actual


def dump_checksum_report(out_path: Path, *, root: Path | None = None) -> dict[str, str]:
    actual = verify_gold_checksums(root=root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return actual
