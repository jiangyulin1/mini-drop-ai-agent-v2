"""Server-side analyzer fallback for raw perf artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from server.app.logging_utils import log_event

ANALYZER_TIMEOUT_SEC = 180


def analyze_raw_perf_artifacts(task_id: str, artifacts: list[dict]) -> list[dict]:
    raw_path = _find_local_raw_perf_path(artifacts)
    if raw_path is None:
        return []

    output_root = _artifact_root()
    cmd = [
        sys.executable,
        "-m",
        "analyzer.mini_drop_analyzer.hotmethod_analyzer",
        "--task-id",
        task_id,
        "--perf-data",
        str(raw_path),
        "--output-dir",
        str(output_root),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=ANALYZER_TIMEOUT_SEC)
    except (subprocess.SubprocessError, OSError) as exc:
        log_event("warning", "analyzer_runner_failed", error=str(exc)[:200])
        return []
    if proc.returncode != 0:
        return []

    output_dir = output_root / task_id
    return _collect_analyzer_outputs(output_dir)


def _find_local_raw_perf_path(artifacts: list[dict]) -> Path | None:
    for artifact in artifacts:
        if artifact.get("artifact_type") != "raw":
            continue
        local_path = artifact.get("local_path")
        filename = artifact.get("filename") or ""
        if filename and filename != "perf.data":
            continue
        path = _resolve_under_root(local_path)
        if path and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _collect_analyzer_outputs(output_dir: Path) -> list[dict]:
    outputs = {
        "flamegraph_json": ("flamegraph.json", "application/json"),
        "flamegraph_svg": ("flamegraph.svg", "image/svg+xml"),
        "top_json": ("top.json", "application/json"),
        "suggestions_md": ("suggestions.md", "text/markdown"),
    }
    artifacts: list[dict] = []
    for artifact_type, (filename, content_type) in outputs.items():
        path = output_dir / filename
        if not path.is_file():
            continue
        artifacts.append({
            "artifact_type": artifact_type,
            "filename": filename,
            "local_path": str(path),
            "content_type": content_type,
            "size_bytes": path.stat().st_size,
        })
    return artifacts


def _artifact_root() -> Path:
    return Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def _resolve_under_root(local_path: str | None) -> Path | None:
    if not local_path:
        return None
    root = _artifact_root()
    candidate = Path(local_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved
