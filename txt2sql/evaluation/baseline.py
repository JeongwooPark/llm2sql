"""Baseline manifest generation and verification for MAIN benchmark."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from txt2sql.config import load_settings
from txt2sql.evaluation.gold_checksum import FROZEN_GOLD_CHECKSUMS, sha256_file

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BASELINE_DIR = ROOT / "artifacts" / "baselines" / "main_62_9"
DEFAULT_RESULT_FILE = (
    ROOT / "tests" / "map_ui_gold500" / "results" / "mapui_place_scope_20260827.json"
)

FIXED_IDS = ["Q303", "Q315", "Q344", "Q354", "Q374", "Q482"]


def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool | None:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return bool(out.strip())
    except Exception:
        return None


def _config_hash() -> str:
    settings = load_settings()
    payload = {
        "ollama_model": settings.ollama_model,
        "ollama_embed_model": settings.ollama_embed_model,
        "semantic_plan_mode": settings.semantic_plan_mode,
        "intent_mode": settings.intent_mode,
        "route_dispatch_mode": settings.route_dispatch_mode,
        "reference_date": settings.reference_date,
        "default_sido": settings.default_sido,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_baseline_manifest(
    *,
    result_file: Path | None = None,
    branch: str = "semantic-architecture-v2",
    product_version: str = "0.3.3",
) -> dict[str, Any]:
    """Build benchmark_manifest.json from frozen baseline result."""
    result_path = result_file or DEFAULT_RESULT_FILE
    if not result_path.is_file():
        raise FileNotFoundError(f"baseline result not found: {result_path}")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    gold_rel = "docs/llm2sql_신규_자연어질의_테스트셋_500건_정답표.json"
    testset_rel = "docs/평가문항_500.json"
    settings = load_settings()

    manifest: dict[str, Any] = {
        "name": "main_62_9_place_scope",
        "branch": branch,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "product_version": product_version,
        "result_file": str(result_path.relative_to(ROOT)) if result_path.is_relative_to(ROOT) else str(result_path),
        "main_definition": "exclude data_quality",
        "main_pass": result.get("passed", 305),
        "main_total": result.get("total", 485),
        "main_accuracy": result.get("accuracy_pct", 62.9),
        "full_total": 500,
        "place_scope_policy_version": "1.0",
        "regressed": 0,
        "fixed_ids": FIXED_IDS,
        "gold_file": gold_rel,
        "testset_hash": FROZEN_GOLD_CHECKSUMS.get(testset_rel),
        "gold_hash": FROZEN_GOLD_CHECKSUMS.get(gold_rel),
        "config_hash": _config_hash(),
        "model": settings.ollama_model,
        "model_digest": settings.ollama_plan_digest or None,
        "embedding_model": settings.ollama_embed_model,
        "embedding_digest": settings.ollama_embed_digest or None,
        "database_snapshot": None,
        "evaluator_version": "map_ui_gold500_v1",
        "reference_date": settings.reference_date,
        "default_sido": settings.default_sido,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "todos": {
            "database_snapshot": "Record DB snapshot/version when available",
            "model_digest": "Populate from Ollama model digest when available",
        },
    }
    return manifest


def write_baseline_manifest(
    out_dir: Path | None = None,
    *,
    result_file: Path | None = None,
    copy_result: bool = True,
) -> Path:
    """Write benchmark_manifest.json and optionally copy result file."""
    base_dir = out_dir or DEFAULT_BASELINE_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    manifest = build_baseline_manifest(result_file=result_file)
    manifest_path = base_dir / "benchmark_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if copy_result:
        src = result_file or DEFAULT_RESULT_FILE
        if src.is_file():
            dest = base_dir / src.name
            if not dest.exists() or sha256_file(dest) != sha256_file(src):
                dest.write_bytes(src.read_bytes())

    return manifest_path


def load_baseline_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or (DEFAULT_BASELINE_DIR / "benchmark_manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))
