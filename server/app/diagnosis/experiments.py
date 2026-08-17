"""Contracts and reproducibility metadata for diagnosis experiments."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel


class ExperimentSpec(StrictModel):
    schema_version: str = "experiment-spec.v1"
    experiment_id: str = Field(min_length=1, max_length=128)
    dataset: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(min_length=1, max_length=64)
    profile: str = Field(min_length=1, max_length=64)
    reasoner_id: str = Field(min_length=1, max_length=64)
    reasoner_version: str = Field(min_length=1, max_length=64)
    repetitions: int = Field(default=1, ge=1, le=100)
    seed: int = 0
    model_provider: Optional[str] = Field(default=None, max_length=64)
    model: Optional[str] = Field(default=None, max_length=128)
    prompt_version: Optional[str] = Field(default=None, max_length=64)
    rule_version: str = Field(min_length=1, max_length=64)
    feature_version: str = Field(min_length=1, max_length=64)
    planner_version: str = Field(min_length=1, max_length=64)
    toolset_version: str = Field(min_length=1, max_length=64)
    strategy: dict[str, Any] = Field(default_factory=dict)
    runtime_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_options: dict[str, Any] = Field(default_factory=dict)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_run_manifest(
    spec: ExperimentSpec,
    *,
    files: list[Path],
    repository_root: Path,
) -> dict[str, Any]:
    """Resolve an experiment into a machine-readable, secret-free run manifest."""

    return {
        "schema_version": "experiment-run.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment": spec.model_dump(mode="json"),
        "code": _git_state(repository_root),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "inputs": {
            str(path.relative_to(repository_root)): sha256_file(path)
            for path in sorted(files)
        },
    }


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    stable = dict(manifest)
    stable.pop("created_at", None)
    return hashlib.sha256(json.dumps(
        stable,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _git_state(repository_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"

    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }
