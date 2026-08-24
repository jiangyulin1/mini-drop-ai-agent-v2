#!/usr/bin/env python3
"""Prepare and (when possible) run the GitHub PR attribution evaluation.

The Mini-Drop runtime currently evaluates VM fault fixtures directly.  The
cases in this runner are GitHub pull requests, so this script keeps the
reproducible part independent from the runtime:

* fetches each PR and its public discussion serially, with response caching;
* builds public ``pr_core``, ``external_evidence`` and synthetic runtime packs;
* writes deduplicated projections for a one-time low-bandwidth import;
* keeps the expected mechanisms in a separate local oracle directory;
* records host/disk/network metrics and a per-case preflight; and
* emits explicit ``blocked`` records when the control plane, sidecar or model
  provider is not configured.  It never turns text matches into an AI score.

No GitHub token is read by this script.  Anonymous REST requests are enough for
the small default suite, and cached responses can be reused with the same
``--output-dir``.  Use ``--offline`` to validate an existing cache without any
network access.

Example::

    python scripts/run_github_pr_attribution_eval.py
    python scripts/run_github_pr_attribution_eval.py --offline \
        --output-dir reports/eval/github-pr-attribution-20260821
    python scripts/run_github_pr_attribution_eval.py --offline --low-bandwidth \
        --rounds 3 --output-dir reports/eval/github-pr-attribution-9x3

The report is intentionally useful even when live execution is blocked.  In
that situation ``real_ai_score`` remains null and ``synthetic_evidence_check``
is the only check reported as a pass/fail signal.

With ``--import-evidence`` the script sends each compact projection once to
the explicitly enabled evaluation-import endpoint.  It never uploads a raw
pack and reuses successful local receipts on reruns.  Provider turns remain a
separate operation: a successful import is not an AI score.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = ROOT / "reports" / "eval"
SCRIPT_VERSION = "1.0"
PROJECTION_SCHEMA = "mini-drop.github-pr.evaluation-projection.v1"
PROJECTION_MANIFEST_SCHEMA = "mini-drop.github-pr.projection-manifest.v1"
IMPORT_RESULT_SCHEMA = "mini-drop.github-pr.projection-import.v1"
GITHUB_API = "https://api.github.com"
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 10
DEFAULT_DELAY_SECONDS = 0.25


def _default_output_dir(timestamp: str) -> Path:
    return DEFAULT_REPORT_ROOT / f"github-pr-attribution-{timestamp}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_value(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def server_projection_hash(value: Any) -> str:
    """Hash format used by ``stable_projection_hash`` at the import route."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    write_bytes_atomic(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def write_text(path: Path, value: str) -> None:
    write_bytes_atomic(path, value.encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return normalized or "case"


def tree_size(path: Path) -> int:
    """Return the regular-file bytes below *path*, tolerating partial output."""
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if not path.exists():
        return 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def command_output(*args: str, timeout: float = 3.0) -> str:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def command_bytes(*args: str, timeout: float = 30.0) -> tuple[bytes, str]:
    """Run a bounded helper command without exposing its environment or stderr."""
    try:
        completed = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return b"", type(exc).__name__
    if completed.returncode != 0:
        return b"", f"exit_{completed.returncode}"
    return completed.stdout, ""


def _parse_first_int(text: str, pattern: str) -> Optional[int]:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def memory_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {}
    if sys.platform == "darwin":
        result["physical_bytes"] = _parse_first_int(command_output("sysctl", "-n", "hw.memsize"), r"(\d+)")
        vm_stat = command_output("vm_stat")
        page_size = _parse_first_int(command_output("sysctl", "-n", "hw.pagesize"), r"(\d+)") or 4096
        pages: dict[str, int] = {}
        for line in vm_stat.splitlines():
            match = re.match(r"([^:]+):\s+(\d+)\.", line)
            if match:
                pages[match.group(1).strip().lower()] = int(match.group(2))
        if pages:
            result["page_size_bytes"] = page_size
            result["vm_stat_pages"] = pages
            result["free_bytes_estimate"] = sum(
                pages.get(key, 0) * page_size for key in ("pages free", "pages inactive", "pages speculative")
            )
        swap = command_output("sysctl", "vm.swapusage")
        if swap:
            result["swapusage"] = swap
    elif Path("/proc/meminfo").exists():
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                key, _, raw = line.partition(":")
                match = re.search(r"(\d+)", raw)
                if match:
                    values[key] = int(match.group(1)) * 1024
        except OSError:
            values = {}
        result["bytes"] = values
    return result


def swap_snapshot() -> dict[str, Any]:
    if sys.platform == "darwin":
        return {"text": command_output("sysctl", "vm.swapusage")}
    swaps = Path("/proc/swaps")
    if swaps.exists():
        try:
            lines = swaps.read_text(encoding="utf-8").splitlines()
            rows = [line.split() for line in lines[1:] if line.split()]
            return {
                "entries": [
                    {"path": row[0], "size_bytes": int(row[2]) * 1024, "used_bytes": int(row[3]) * 1024}
                    for row in rows
                    if len(row) >= 4
                ]
            }
        except (OSError, ValueError):
            pass
    return {}


def collect_metrics(output_dir: Path, workspace: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(output_dir)
        disk = {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}
    except OSError as exc:
        disk = {"error": str(exc)}
    try:
        load_average = list(os.getloadavg())
    except (AttributeError, OSError):
        load_average = []
    try:
        max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux reports KiB.
        max_rss_bytes = int(max_rss if sys.platform == "darwin" else max_rss * 1024)
    except (AttributeError, OSError):
        max_rss_bytes = None
    return {
        "captured_at": utc_now(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "load_average": load_average,
        "memory": memory_snapshot(),
        "swap": swap_snapshot(),
        "max_rss_bytes": max_rss_bytes,
        "disk": disk,
        "output_dir_bytes": tree_size(output_dir),
        "workspace_bytes": tree_size(workspace),
    }


@dataclass
class FetchResult:
    path: str
    url: str
    ok: bool
    status: Optional[int]
    bytes: int
    sha256: Optional[str]
    from_cache: bool
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "url": self.url,
            "ok": self.ok,
            "status": self.status,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "from_cache": self.from_cache,
            "error": self.error,
        }


@dataclass
class ImportResult:
    """Bounded result for one optional projection import.

    ``request_bytes`` deliberately counts only the JSON body.  It is the
    useful lower-bound estimate for server upload accounting and never
    includes credentials or response headers.
    """

    case_id: str
    source_case_id: str
    pack_kind: str
    evidence_id: str
    projection_hash: str
    request_bytes: int
    response_bytes: int
    ok: bool
    status: Optional[int]
    from_cache: bool = False
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": IMPORT_RESULT_SCHEMA,
            "case_id": self.case_id,
            "source_case_id": self.source_case_id,
            "pack_kind": self.pack_kind,
            "evidence_id": self.evidence_id,
            "projection_hash": self.projection_hash,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "ok": self.ok,
            "status": self.status,
            "from_cache": self.from_cache,
            "error": self.error,
        }


class GitHubFetcher:
    """Serial, bounded, cache-first GitHub REST fetcher."""

    def __init__(
        self,
        raw_root: Path,
        *,
        offline: bool = False,
        refresh: bool = False,
        timeout: float = 30.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
    ) -> None:
        self.raw_root = raw_root
        self.offline = offline
        self.refresh = refresh
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.delay_seconds = max(0.0, delay_seconds)
        self.results: list[FetchResult] = []

    def _gh_api(self, url: str, accept: str) -> tuple[Optional[bytes], Optional[str]]:
        """Use an existing ``gh`` login as a credential-safe API fallback.

        The token remains inside the GitHub CLI credential store.  Only the
        response body is captured; neither ``gh auth token`` nor environment
        values are read.  This fallback is deliberately used only after an
        anonymous REST request receives a common rate-limit/auth response.
        """
        if shutil.which("gh") is None:
            return None, "gh_unavailable"
        parsed = urllib.parse.urlparse(url)
        endpoint = parsed.path.lstrip("/")
        if endpoint.endswith(".diff"):
            endpoint = endpoint[:-5]
            accept = "application/vnd.github.v3.diff"
        if parsed.query:
            endpoint += f"?{parsed.query}"
        data, error = command_bytes(
            "gh",
            "api",
            endpoint,
            "--header",
            f"Accept: {accept}",
            timeout=self.timeout,
        )
        if error or not data:
            return None, error or "gh_empty_response"
        if len(data) > self.max_response_bytes:
            return None, f"response_too_large>{self.max_response_bytes}"
        return data, None

    def _cache_path(self, relative: str) -> Path:
        return self.raw_root / relative

    def _meta_path(self, relative: str) -> Path:
        return self.raw_root / f"{relative}.meta.json"

    def _read_cache(self, relative: str, url: str) -> Optional[tuple[bytes, FetchResult]]:
        path = self._cache_path(relative)
        if self.refresh or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        meta: dict[str, Any] = {}
        try:
            meta = read_json(self._meta_path(relative))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        result = FetchResult(
            path=relative,
            url=url,
            ok=bool(meta.get("ok", True)),
            status=meta.get("status"),
            bytes=len(data),
            sha256=sha256_bytes(data),
            from_cache=True,
            error=meta.get("error"),
        )
        return data, result

    def get(self, relative: str, url: str, *, accept: str = "application/vnd.github+json") -> tuple[Optional[bytes], FetchResult]:
        cached = self._read_cache(relative, url)
        if cached is not None:
            data, result = cached
            self.results.append(result)
            return data, result
        if self.offline:
            result = FetchResult(relative, url, False, None, 0, None, False, "offline_cache_miss")
            self.results.append(result)
            return None, result
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        request = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": "mini-drop-github-pr-eval/1.0"},
            method="GET",
        )
        response: Optional[bytes] = None
        status: Optional[int] = None
        error: Optional[str] = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as handle:
                status = int(getattr(handle, "status", 200))
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = handle.read(min(256 * 1024, self.max_response_bytes - total + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > self.max_response_bytes:
                        error = f"response_too_large>{self.max_response_bytes}"
                        break
                if error is None:
                    response = b"".join(chunks)
                else:
                    response = None
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            error = f"http_{exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = f"network_error:{type(exc).__name__}"
        # The developer machine may have a logged-in GitHub CLI while
        # anonymous REST requests are rate-limited.  Retry through that local
        # credential store without reading or serializing its token.
        if response is None and status in (401, 403):
            gh_response, gh_error = self._gh_api(url, accept)
            if gh_response is not None:
                response = gh_response
                status = 200
                error = None
            elif gh_error and error is None:
                error = gh_error
        ok = response is not None and status is not None and 200 <= status < 300
        path = self._cache_path(relative)
        if ok and response is not None:
            write_bytes_atomic(path, response)
        result = FetchResult(
            path=relative,
            url=url,
            ok=ok,
            status=status,
            bytes=len(response or b""),
            sha256=sha256_bytes(response) if response is not None else None,
            from_cache=False,
            error=error if error else (None if ok else "empty_response"),
        )
        write_json(
            self._meta_path(relative),
            {"fetched_at": utc_now(), "url": url, **result.as_dict()},
        )
        self.results.append(result)
        return response, result

    def json(self, relative: str, url: str) -> tuple[Optional[Any], FetchResult]:
        data, result = self.get(relative, url)
        if data is None:
            return None, result
        try:
            return json.loads(data.decode("utf-8")), result
        except (UnicodeDecodeError, json.JSONDecodeError):
            result.ok = False
            result.error = "invalid_json"
            return None, result


# The expected refs are only used for reproducibility checks.  They are not
# written to public packs or sent to a provider.
CASE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "case_id": "prometheus-19393",
        "repo": "prometheus/prometheus",
        "number": 19393,
        "related_issues": [],
        "expected_state": "open",
        "expected_refs": {
            "base": "2b2c984026ab86d22d9396aafe35ad77a37b4b14",
            "head": "2ca670a74baf9c672b1ce2d9d94f926704b0290a",
            "merge": None
        },
        "oracle": {
            "expected_verdict": "candidate_text_lexer_hotspot",
            "expected_location": "self",
            "expected_domain": "cpu",
            "expected_mechanism": "the text format lexer spends avoidable work on tokenization and can be optimized at the parser boundary",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "The PR is a WIP performance proposal; without a reproducible benchmark the runtime impact must remain qualified.",
            "abstention_required": False
        },
        "runtime": {
            "signals": [
                {"name": "lexer_parse_p95_ms", "unit": "ms", "samples": [2.8, 2.1, 1.9]},
                {"name": "tokens_per_second", "unit": "tokens_per_second", "samples": [41000, 56000, 61000]},
                {"name": "benchmark_reproduction", "unit": "qualitative", "samples": ["pending", "pending", "pending"]}
            ],
            "evaluation_focus": "Identify the lexer boundary and keep the performance claim qualified because the PR is WIP and benchmark confirmation is pending."
        }
    },
    {
        "case_id": "grafana-123359",
        "repo": "grafana/grafana",
        "number": 123359,
        "related_issues": [1088, 1087],
        "expected_state": "merged",
        "expected_refs": {
            "base": "b556615d742cd32f464c6741637c2b75a5e40e15",
            "head": "bad44be6a870ebd0efe84b2c113f26ef877a7f71",
            "merge": "0934565212377ad3863a24fd8aac4359604aa691",
        },
        "oracle": {
            "expected_verdict": "confirmed_root_cause",
            "expected_location": "self",
            "expected_domain": "memory",
            "expected_mechanism": "workqueue item identity uses pointers, so deduplication fails and each item retains a full Repository reference",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "A generic memory-leak claim is insufficient without queue depth or retention evidence.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "workqueue_depth", "unit": "items", "samples": [120, 520, 1700]},
                {"name": "rss_bytes", "unit": "bytes", "samples": [294912000, 463470592, 943718400]},
                {"name": "cpu_percent", "unit": "percent", "samples": [44, 49, 48]},
            ],
            "evaluation_focus": "Connect queue growth and object retention to the mechanism; do not stop at OOM.",
        },
    },
    {
        "case_id": "prometheus-19412",
        "repo": "prometheus/prometheus",
        "number": 19412,
        "related_issues": [19136],
        "expected_state": "open",
        "expected_refs": {
            "base": "44d6a0e0b1dbdcba4e68853d50e0bafc87d90507",
            "head": "da183769b5c762c68a4315185da7ae6674672b25",
            "merge": None,
        },
        "oracle": {
            "expected_verdict": "confirmed_retained_capacity",
            "expected_location": "self",
            "expected_domain": "memory",
            "expected_mechanism": "an early return when a series drops to zero or one head chunk retains an oversized backing array",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "Fresh confirmation did not reproduce the initial benchmark regression; this is retained capacity, not a classic leak.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "retained_head_chunk_capacity_bytes", "unit": "bytes", "samples": [268435456, 268435456, 268435456]},
                {"name": "rss_bytes", "unit": "bytes", "samples": [1182793728, 1191182336, 1191182336]},
                {"name": "benchmark_p95_ms", "unit": "ms", "samples": [3.10, 3.02, 3.04]},
            ],
            "evaluation_focus": "Distinguish retained backing capacity from a leak and cite the benchmark non-reproduction as a boundary.",
        },
    },
    {
        "case_id": "redis-15427",
        "repo": "redis/redis",
        "number": 15427,
        "related_issues": [15411, 15410, 15412],
        "expected_state": "open",
        "expected_refs": {
            "base": "5037e19c6110ec18ff0acd9950cce94be881ebb5",
            "head": "07d87c11ee0c7e9f9deac27f1b82e48a7292a2fc",
            "merge": None,
        },
        "oracle": {
            "expected_verdict": "confirmed_background_reclaim_starvation",
            "expected_location": "self",
            "expected_domain": "cpu",
            "expected_mechanism": "SCAN and the expires cursor advance in the same local window, making the activeExpireCycle estimate look clean while expired backlog grows",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "CPU can remain idle while the expired backlog increases; CPU shortage is not the root cause.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "expired_key_backlog", "unit": "keys", "samples": [200, 700, 1800]},
                {"name": "cpu_percent", "unit": "percent", "samples": [38, 35, 36]},
                {"name": "scan_cursor_overlap_ratio", "unit": "ratio", "samples": [0.92, 0.90, 0.91]},
            ],
            "evaluation_focus": "Explain the local-window estimation bias and reject the tempting CPU-underprovisioning explanation.",
        },
    },
    {
        "case_id": "kubernetes-138571",
        "repo": "kubernetes/kubernetes",
        "number": 138571,
        "related_issues": [137085],
        "expected_state": "merged",
        "expected_refs": {
            "base": "4c7a3becca7f24769c446935527c67b5bb7d00f8",
            "head": "9daabbd6c73aac543ef8dfd5326098eb4accfa23",
            "merge": "7d3b347d200b5d4cab7622ad11c7b02b0e1a3210",
        },
        "oracle": {
            "expected_verdict": "confirmed_reconcile_cost",
            "expected_location": "self",
            "expected_domain": "latency",
            "expected_mechanism": "periodic full syncs in large-cluster mode consume dataplane reconcile budget and perturb workloads",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "An nft backend delay mentioned in issue context is not by itself the cause fixed by this PR.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "full_sync_duration_ms", "unit": "ms", "samples": [8000, 12000, 25000]},
                {"name": "workload_p99_ms", "unit": "ms", "samples": [120, 300, 900]},
                {"name": "cluster_size_nodes", "unit": "nodes", "samples": [500, 1000, 2000]},
            ],
            "evaluation_focus": "Tie the small diff to full-sync cost at scale without generalizing to a network outage.",
        },
    },
    {
        "case_id": "kubernetes-140886",
        "repo": "kubernetes/kubernetes",
        "number": 140886,
        "related_issues": [140877],
        "expected_state": "merged",
        "expected_refs": {
            "base": "aa94f417962c7c4a3798e042dd0ce24cc9374a1e",
            "head": "cd1f19ca7e4303b9cc53d89699fdc39208795e30",
            "merge": "32385309d3adfddd8d79f7a8e2e873db88a782b1",
        },
        "oracle": {
            "expected_verdict": "insufficient_evidence_abstain",
            "expected_location": "unknown",
            "expected_domain": "unknown",
            "expected_mechanism": "the revert responds to a possible informer-cache event performance regression, but the PR explicitly says the impact is not entirely certain",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "Issue data does not close the causal loop; a confident performance-regression root cause would overstate evidence.",
            "abstention_required": True,
        },
        "runtime": {
            "signals": [
                {"name": "reconcile_p99_ms", "unit": "ms", "samples": [84, 86, 92]},
                {"name": "informer_event_rate", "unit": "events_per_second", "samples": [210, 214, 218]},
                {"name": "benchmark_confidence", "unit": "qualitative", "samples": ["low", "low", "low"]},
            ],
            "evaluation_focus": "Abstain or qualify the conclusion; do not invent a closed causal chain from a revert.",
        },
    },
    {
        "case_id": "opentelemetry-python-4224",
        "repo": "open-telemetry/opentelemetry-python",
        "number": 4224,
        "related_issues": [4220],
        "expected_state": "merged",
        "expected_refs": {
            "base": "679297f5ebd37510b6c9e086fc27837935d57e81",
            "head": "84c6b0a419226328b6884b43a61cfd7a8fa3b3bb",
            "merge": "5de1ccbfe296abeb79a46d3a895eaf34a758c62d",
        },
        "oracle": {
            "expected_verdict": "confirmed_reference_retention",
            "expected_location": "self",
            "expected_domain": "memory",
            "expected_mechanism": "exporter and reader keep strong references; WeakMethod and WeakSet break the retention chain",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "GC alone is not proof of a leak; weakref/referrer evidence is required.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "retained_exporter_objects", "unit": "objects", "samples": [1000, 5000, 12000]},
                {"name": "rss_bytes", "unit": "bytes", "samples": [262144000, 608174080, 1258291200]},
                {"name": "gc_collected_objects", "unit": "objects", "samples": [0, 1, 2]},
            ],
            "evaluation_focus": "Use weakref/referrer evidence to establish retention instead of treating RSS growth alone as proof.",
        },
    },
    {
        "case_id": "grafana-124542",
        "repo": "grafana/grafana",
        "number": 124542,
        "related_issues": [105808],
        "expected_state": "closed",
        "expected_refs": {
            "base": "d86b2d0996e8ad7ed4a339fcb5ded4fa547db279",
            "head": "cab23c236423b798659d37c438f58a7f17757919",
            "merge": None,
        },
        "oracle": {
            "expected_verdict": "negative_control_unverified_fix",
            "expected_location": "unknown",
            "expected_domain": "unknown",
            "expected_mechanism": "the proposed byName Map cleanup is not validated because review notes detached nodes remain retained",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "The closed draft PR and review discussion do not establish that the candidate fix removes retained detached nodes.",
            "abstention_required": True,
        },
        "runtime": {
            "signals": [
                {"name": "detached_dom_nodes_retained", "unit": "nodes", "samples": [10000, 20000, 20000]},
                {"name": "byname_map_entries", "unit": "entries", "samples": [1200, 1200, 1200]},
                {"name": "cleanup_verification", "unit": "qualitative", "samples": ["not_run", "not_run", "not_run"]},
            ],
            "evaluation_focus": "Treat this as a negative control and refuse to certify an unverified fix.",
        },
    },
    {
        "case_id": "envoy-42752",
        "repo": "envoyproxy/envoy",
        "number": 42752,
        "related_issues": [],
        "expected_state": "merged",
        "expected_refs": {
            "base": "69ab88c911e2d2bbe59ce7e1c8e69d272f809166",
            "head": "fef2badc3ac6676128a8fa96acccec20f4cce72f",
            "merge": "5ddb5464228041b2bf23adf327e84af6f971294d",
        },
        "oracle": {
            "expected_verdict": "confirmed_micro_hotspot",
            "expected_location": "self",
            "expected_domain": "cpu",
            "expected_mechanism": "debug log expression evaluation still runs for every data chunk when debug logging is disabled",
            "required_evidence_kinds": ["pr_core", "external_evidence", "simulated_runtime"],
            "counterevidence": "This is a bounded micro-hotspot; stable end-to-end latency should prevent system-level overclaiming.",
            "abstention_required": False,
        },
        "runtime": {
            "signals": [
                {"name": "debug_expression_eval_us_per_chunk", "unit": "microseconds", "samples": [0.62, 0.59, 0.61]},
                {"name": "chunks_per_second", "unit": "chunks_per_second", "samples": [100000, 100500, 100200]},
                {"name": "request_p99_ms", "unit": "ms", "samples": [4.2, 4.1, 4.2]},
            ],
            "evaluation_focus": "Identify the per-chunk cost while keeping the impact proportional to a micro-optimization.",
        },
    },
)


def selected_specs(selection: Optional[str]) -> list[dict[str, Any]]:
    if not selection:
        return [dict(item) for item in CASE_SPECS]
    wanted = {part.strip() for part in selection.split(",") if part.strip()}
    result = [dict(item) for item in CASE_SPECS if item["case_id"] in wanted or str(item["number"]) in wanted]
    unknown = wanted - {item["case_id"] for item in result} - {str(item["number"]) for item in result}
    if unknown:
        raise SystemExit(f"unknown case id(s): {', '.join(sorted(unknown))}")
    return result


def github_url(repo: str, path: str) -> str:
    return f"{GITHUB_API}/repos/{repo}/{path.lstrip('/')}"


def nested(mapping: Any, *keys: str) -> Any:
    value = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def text_from_record(record: Any) -> str:
    if not isinstance(record, Mapping):
        return ""
    body = record.get("body")
    return body if isinstance(body, str) else ""


def compact_user(record: Any) -> Optional[dict[str, Any]]:
    if not isinstance(record, Mapping):
        return None
    login = record.get("login")
    if not isinstance(login, str):
        return None
    return {"login": login, "type": record.get("type")}


def compact_pr(pr: Mapping[str, Any]) -> dict[str, Any]:
    base = pr.get("base") if isinstance(pr.get("base"), Mapping) else {}
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "body": pr.get("body"),
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "merged_at": pr.get("merged_at"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "html_url": pr.get("html_url"),
        "user": compact_user(pr.get("user")),
        "base": {"ref": base.get("ref"), "sha": base.get("sha"), "repo": nested(base, "repo", "full_name")},
        "head": {"ref": head.get("ref"), "sha": head.get("sha"), "repo": nested(head, "repo", "full_name")},
        "merge_commit_sha": pr.get("merge_commit_sha"),
        "labels": [label.get("name") for label in pr.get("labels", []) if isinstance(label, Mapping)],
        "changed_files": pr.get("changed_files"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "commits": pr.get("commits"),
    }


def compact_issue(issue: Any) -> dict[str, Any]:
    if not isinstance(issue, Mapping):
        return {"available": False}
    return {
        "available": True,
        "number": issue.get("number"),
        "title": issue.get("title"),
        "body": issue.get("body"),
        "state": issue.get("state"),
        "comments": issue.get("comments"),
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "html_url": issue.get("html_url"),
        "user": compact_user(issue.get("user")),
    }


def compact_comment(comment: Any) -> dict[str, Any]:
    if not isinstance(comment, Mapping):
        return {"body": ""}
    return {
        "id": comment.get("id"),
        "body": comment.get("body"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
        "user": compact_user(comment.get("user")),
        "path": comment.get("path"),
        "line": comment.get("line"),
        "commit_id": comment.get("commit_id"),
        "html_url": comment.get("html_url"),
    }


def compact_review(review: Any) -> dict[str, Any]:
    if not isinstance(review, Mapping):
        return {"body": ""}
    return {
        "id": review.get("id"),
        "body": review.get("body"),
        "state": review.get("state"),
        "submitted_at": review.get("submitted_at"),
        "user": compact_user(review.get("user")),
        "html_url": review.get("html_url"),
    }


def flatten_pages(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return list(value["items"])
    return []


def fetch_paginated(
    fetcher: GitHubFetcher,
    raw_case_dir: Path,
    endpoint_name: str,
    url_path: str,
    *,
    max_pages: int,
) -> tuple[list[Any], list[dict[str, Any]]]:
    rows: list[Any] = []
    fetches: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        relative = f"{raw_case_dir.name}/{endpoint_name}.page-{page}.json"
        # url_path is already an API-relative path; keeping this construction
        # explicit avoids accidentally carrying query parameters into cache names.
        url = f"{GITHUB_API}/{url_path.lstrip('/')}" + f"?per_page={DEFAULT_PAGE_SIZE}&page={page}"
        payload, result = fetcher.json(relative, url)
        fetches.append(result.as_dict())
        if payload is None:
            break
        page_rows = flatten_pages(payload)
        rows.extend(page_rows)
        if len(page_rows) < DEFAULT_PAGE_SIZE:
            break
    return rows, fetches


def benchmark_mentions(records: Iterable[Any], limit: int = 30) -> list[dict[str, Any]]:
    keywords = re.compile(r"benchmark|benchstat|perf|latency|p99|rss|memory|cpu|throughput|regression", re.IGNORECASE)
    result: list[dict[str, Any]] = []
    for record in records:
        if len(result) >= limit:
            break
        if not isinstance(record, Mapping):
            continue
        body = text_from_record(record)
        if not body:
            continue
        lines = [line.strip() for line in body.splitlines() if line.strip() and keywords.search(line)]
        if lines:
            result.append({"source_id": record.get("id"), "lines": lines[:20]})
    return result


def make_evidence_record(
    case_id: str,
    kind: str,
    field_path: str,
    value: Any,
    source_ref: str,
    *,
    synthetic: bool = False,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    projection = {
        "schema": "mini-drop.github-pr.evidence-projection.v1",
        "case_id": case_id,
        "kind": kind,
        "field_path": field_path,
        "source_ref": source_ref,
        "value": value,
        "synthetic": synthetic,
    }
    record: dict[str, Any] = {
        "evidence_id": f"ghpr:{slug(case_id)}:{slug(kind)}:{sha256_value(field_path)[:12]}",
        "projection_hash": sha256_value(projection),
        "field_path": field_path,
        "source_ref": source_ref,
        "value": value,
        "synthetic": synthetic,
    }
    if extra:
        record.update(dict(extra))
    return record


def build_runtime_pack(spec: Mapping[str, Any]) -> dict[str, Any]:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), Mapping) else {}
    signals = runtime.get("signals") if isinstance(runtime.get("signals"), list) else []
    evidence = [
        make_evidence_record(
            str(spec["case_id"]),
            "simulated_runtime",
            f"signals[{index}]",
            signal,
            f"synthetic://{spec['case_id']}/runtime/{index}",
            synthetic=True,
        )
        for index, signal in enumerate(signals)
    ]
    return {
        "schema": "mini-drop.github-pr.public-pack.v1",
        "pack_kind": "simulated_runtime",
        "case_id": spec["case_id"],
        "synthetic": True,
        "notice": "Synthetic runtime evidence for evaluator wiring only; not collected by Mini-Drop and not a real production run.",
        "generated_at": utc_now(),
        "run_metadata": {
            "generator": "run_github_pr_attribution_eval.py",
            "sample_count_per_signal": 3,
            "low_scale_only": True,
        },
        "signals": signals,
        "evidence": evidence,
    }


def _public_projection_record(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the auditable fields needed by the Evidence tool gateway.

    Public packs intentionally contain convenient top-level duplicates.  The
    importer does not need those duplicates: each record already carries its
    stable ID, field path, source and value.  Keeping this projection builder
    separate also makes it harder to accidentally include the private oracle.
    """
    allowed = (
        "evidence_id", "projection_hash", "field_path", "source_ref",
        "value", "synthetic", "derived",
    )
    record = {key: item[key] for key in allowed if key in item}
    # These fields are required for a useful citation.  A malformed public
    # pack is retained in the local report and rejected by the verifier rather
    # than being silently repaired here.
    return record


def build_evaluation_projection(
    spec: Mapping[str, Any],
    pack_kind: str,
    pack: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one compact, deduplicated projection for a public pack.

    The full PR diff/comments stay on the evaluator disk.  Only this bounded
    projection is eligible for the one-time ``/evidence/import`` request; all
    later turns reference its canonical Evidence ID.  No oracle fields are
    copied, even if a caller hands this function an unexpected mapping.
    """
    case_id = str(spec.get("case_id") or pack.get("case_id") or "")
    records = [
        _public_projection_record(item)
        for item in (pack.get("evidence") or [])
        if isinstance(item, Mapping)
    ]
    projection: dict[str, Any] = {
        "schema": PROJECTION_SCHEMA,
        "case_id": case_id,
        "pack_kind": str(pack_kind),
        "record_count": len(records),
        "records": records,
    }
    # Retain small, pack-specific indexes that help a model navigate records;
    # do not repeat the large diff/body values already present in ``records``.
    if pack_kind == "pr_core":
        github = pack.get("github") if isinstance(pack.get("github"), Mapping) else {}
        projection["github"] = {
            key: github.get(key)
            for key in ("repo", "number", "state", "draft", "merged", "base_sha", "head_sha", "merge_sha", "analysis_ref")
            if github.get(key) is not None
        }
        projection["stats"] = pack.get("stats") or {}
    elif pack_kind == "external_evidence":
        projection["benchmark_mentions"] = pack.get("benchmark_mentions") or []
        projection["related_issue_count"] = len(pack.get("related_issues") or [])
    elif pack_kind == "simulated_runtime":
        projection["synthetic"] = True
        projection["signals"] = pack.get("signals") or []
    return projection


def projection_payload(
    spec: Mapping[str, Any],
    pack_kind: str,
    pack: Mapping[str, Any],
    *,
    target_case_id: Optional[str] = None,
) -> tuple[dict[str, Any], bytes, str]:
    """Return the HTTP payload, canonical bytes and aggregate projection hash."""
    source_case_id = str(spec.get("case_id") or pack.get("case_id") or "")
    case_id = str(target_case_id or source_case_id)
    projection = build_evaluation_projection(spec, pack_kind, pack)
    # Keep the source identity in the projection when a server-side case map
    # is used; this is useful during manual scoring and does not expose oracle
    # data.
    if case_id != source_case_id:
        projection = {**projection, "source_case_id": source_case_id}
    # The server route uses its stable_projection_hash serializer (which keeps
    # the default JSON separators). Match it exactly so an import is accepted
    # without a retry or a second upload.
    projection_hash = server_projection_hash(projection)
    source_ref = (
        f"synthetic://{source_case_id}/runtime"
        if pack_kind == "simulated_runtime"
        else f"github://{spec.get('repo')}/pull/{spec.get('number')}"
    )
    source_id = (
        f"synthetic:{source_case_id}:runtime"
        if pack_kind == "simulated_runtime"
        else f"github:{spec.get('repo')}#{spec.get('number')}:{pack_kind}"
    )
    payload = {
        "evidence_id": f"eval:{case_id}:{source_case_id}:{pack_kind}",
        "pack_kind": pack_kind,
        "source_id": source_id,
        "source_ref": source_ref,
        "projection": projection,
        "projection_hash": projection_hash,
        "content_hash": sha256_value(pack),
        "source_bytes": len(canonical_json(pack)),
        "synthetic": pack_kind == "simulated_runtime",
    }
    body = canonical_json(payload)
    return payload, body, projection_hash


def write_projection_packs(
    output_dir: Path,
    case_results: Sequence[Mapping[str, Any]],
    *,
    case_map: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Write compact projection files and a deterministic byte manifest."""
    case_map = case_map or {}
    entries: list[dict[str, Any]] = []
    for result in case_results:
        source_case_id = str(result.get("case_id") or "")
        pack_dir = Path(result.get("pack_dir") or "")
        if not source_case_id or not pack_dir.is_dir():
            continue
        spec = result.get("spec") if isinstance(result.get("spec"), Mapping) else {"case_id": source_case_id}
        target_case_id = str(case_map.get(source_case_id) or source_case_id)
        projection_dir = output_dir / "projections" / slug(source_case_id)
        projection_dir.mkdir(parents=True, exist_ok=True)
        for pack_kind in ("pr_core", "external_evidence", "simulated_runtime"):
            pack_path = pack_dir / f"{pack_kind}.json"
            if not pack_path.is_file():
                continue
            try:
                pack = read_json(pack_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(pack, Mapping):
                continue
            payload, body, projection_hash = projection_payload(
                spec, pack_kind, pack, target_case_id=target_case_id,
            )
            projection_path = projection_dir / f"{pack_kind}.json"
            write_json(projection_path, payload)
            entries.append({
                "source_case_id": source_case_id,
                "case_id": target_case_id,
                "pack_kind": pack_kind,
                "path": str(projection_path.relative_to(output_dir)),
                "evidence_id": payload["evidence_id"],
                "projection_hash": projection_hash,
                "source_pack_bytes": pack_path.stat().st_size,
                "request_bytes": len(body),
                "gzip_request_bytes_estimate": len(gzip.compress(body, compresslevel=6)),
                "content_hash": payload["content_hash"],
                "synthetic": bool(payload["synthetic"]),
            })
    request_bytes = sum(int(item["request_bytes"]) for item in entries)
    compressed_bytes = sum(int(item["gzip_request_bytes_estimate"]) for item in entries)
    manifest = {
        "schema": PROJECTION_MANIFEST_SCHEMA,
        "generated_at": utc_now(),
        "projection_schema": PROJECTION_SCHEMA,
        "raw_packs_uploaded": False,
        "entry_count": len(entries),
        "entries": entries,
        "estimated_one_time_import_request_bytes": request_bytes,
        "estimated_one_time_import_gzip_bytes": compressed_bytes,
        "estimated_repeated_turn_upload_bytes": 0,
        "notes": [
            "Only projection payloads are eligible for import; raw packs remain local.",
            "gzip size is an estimate only; the import endpoint currently accepts JSON, not Content-Encoding gzip.",
            "Any repeated evaluation rounds should reuse these Evidence IDs and send references/user instructions only.",
        ],
    }
    write_json(output_dir / "projection-manifest.json", manifest)
    return manifest


def write_low_bandwidth_round_plan(
    output_dir: Path,
    manifest: Mapping[str, Any],
    *,
    rounds: int = 1,
) -> dict[str, Any]:
    """Describe repeated rounds without duplicating Evidence payloads."""
    entries = [item for item in (manifest.get("entries") or []) if isinstance(item, Mapping)]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in entries:
        by_case.setdefault(str(item.get("source_case_id") or ""), []).append({
            "case_id": item.get("case_id"),
            "evidence_id": item.get("evidence_id"),
            "pack_kind": item.get("pack_kind"),
            "projection_hash": item.get("projection_hash"),
        })
    runs: list[dict[str, Any]] = []
    for round_no in range(1, max(1, int(rounds)) + 1):
        for source_case_id, evidence in by_case.items():
            target_case_id = str(evidence[0].get("case_id") or source_case_id) if evidence else source_case_id
            runs.append({
                "run_id": f"{source_case_id}:round-{round_no}",
                "round": round_no,
                "source_case_id": source_case_id,
                "case_id": target_case_id,
                "status": "PENDING_RUNTIME",
                "evidence_ids": [item.get("evidence_id") for item in evidence],
                "projection_hashes": [item.get("projection_hash") for item in evidence],
                "upload_policy": "reuse_imported_evidence_and_send_references_only",
                "manual_scoring_required": True,
            })
    plan = {
        "schema": "mini-drop.github-pr.low-bandwidth-round-plan.v1",
        "generated_at": utc_now(),
        "round_count": max(1, int(rounds)),
        "case_count": len(by_case),
        "run_count": len(runs),
        "import_once": True,
        "raw_packs_uploaded": False,
        "runs": runs,
        "scoring_note": "每个 run 需要人工记录模型回答、证据引用、反证处理和 abstention；本文件不生成分数。",
    }
    write_json(output_dir / "low-bandwidth-round-plan.json", plan)
    return plan


def synthetic_check(spec: Mapping[str, Any], runtime_pack: Mapping[str, Any]) -> dict[str, Any]:
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), Mapping) else {}
    expected = runtime.get("signals") if isinstance(runtime.get("signals"), list) else []
    observed = runtime_pack.get("signals") if isinstance(runtime_pack.get("signals"), list) else []
    expected_names = [item.get("name") for item in expected if isinstance(item, Mapping)]
    observed_names = [item.get("name") for item in observed if isinstance(item, Mapping)]
    missing = [name for name in expected_names if name not in observed_names]
    all_synthetic = bool(runtime_pack.get("synthetic")) and all(
        bool(item.get("synthetic")) for item in runtime_pack.get("evidence", []) if isinstance(item, Mapping)
    )
    return {
        "status": "PASS" if not missing and all_synthetic else "FAIL",
        "kind": "synthetic_evidence_check",
        "not_real_ai_score": True,
        "expected_signal_names": expected_names,
        "observed_signal_names": observed_names,
        "missing_signal_names": missing,
        "all_records_marked_synthetic": all_synthetic,
    }


def make_public_metadata(spec: Mapping[str, Any], pr: Mapping[str, Any], fetched_at: str) -> dict[str, Any]:
    base = nested(pr, "base", "sha")
    head = nested(pr, "head", "sha")
    merged = bool(pr.get("merged")) or bool(pr.get("merged_at"))
    merge_sha = pr.get("merge_commit_sha") if merged else None
    return {
        "schema": "mini-drop.github-pr.public-pack.v1",
        "case_id": spec["case_id"],
        "pack_kind": "pr_core",
        "retrieved_at": fetched_at,
        "github": {
            "repo": spec["repo"],
            "number": spec["number"],
            "html_url": pr.get("html_url") or f"https://github.com/{spec['repo']}/pull/{spec['number']}",
            "state": pr.get("state"),
            "draft": pr.get("draft"),
            "merged": merged,
            "base_sha": base,
            "head_sha": head,
            # Open/closed unmerged PRs deliberately have no provisional merge SHA.
            "merge_sha": merge_sha,
            "analysis_ref": merge_sha or head,
        },
        "title": pr.get("title"),
        "body": pr.get("body"),
        "stats": {
            key: pr.get(key) for key in ("changed_files", "additions", "deletions", "commits")
        },
    }


def build_core_pack(spec: Mapping[str, Any], pr: Mapping[str, Any], files: list[Any], diff: str) -> dict[str, Any]:
    metadata = make_public_metadata(spec, pr, utc_now())
    evidence = [
        make_evidence_record(spec["case_id"], "pr_core", "github.title", pr.get("title"), f"github://{spec['repo']}/pull/{spec['number']}"),
        make_evidence_record(spec["case_id"], "pr_core", "github.body", pr.get("body"), f"github://{spec['repo']}/pull/{spec['number']}"),
        make_evidence_record(spec["case_id"], "pr_core", "github.diff", diff, f"github://{spec['repo']}/pull/{spec['number']}.diff"),
        make_evidence_record(spec["case_id"], "pr_core", "github.changed_files", files, f"github://{spec['repo']}/pull/{spec['number']}/files"),
    ]
    return {
        **metadata,
        "files": files,
        "diff": diff,
        "evidence": evidence,
    }


def build_external_pack(
    spec: Mapping[str, Any],
    issue: Mapping[str, Any],
    issue_comments: list[Any],
    pr_comments: list[Any],
    reviews: list[Any],
    *,
    pr_body: Optional[str] = None,
    related_issues: Optional[list[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    case_id = str(spec["case_id"])
    compact_issue_value = compact_issue(issue)
    compact_issue_comments = [compact_comment(item) for item in issue_comments]
    compact_pr_comments = [compact_comment(item) for item in pr_comments]
    compact_reviews = [compact_review(item) for item in reviews]
    compact_related = [dict(item) for item in (related_issues or [])]
    all_records: list[Any] = [
        {"id": "pr-body", "body": pr_body or ""},
        compact_issue_value,
        *compact_issue_comments,
        *compact_pr_comments,
        *compact_reviews,
    ]
    for item in compact_related:
        all_records.append(item.get("issue") or {})
        all_records.extend(item.get("comments") or [])
    evidence: list[dict[str, Any]] = []
    evidence.append(make_evidence_record(case_id, "external_evidence", "issue.body", compact_issue_value.get("body"), f"github://{spec['repo']}/issues/{spec['number']}"))
    for index, item in enumerate(compact_issue_comments):
        evidence.append(make_evidence_record(case_id, "external_evidence", f"issue.comments[{index}].body", item.get("body"), f"github://{spec['repo']}/issues/{spec['number']}/comments/{item.get('id') or index}"))
    for index, item in enumerate(compact_pr_comments):
        evidence.append(make_evidence_record(case_id, "external_evidence", f"pr.comments[{index}].body", item.get("body"), f"github://{spec['repo']}/pulls/{spec['number']}/comments/{item.get('id') or index}"))
    for index, item in enumerate(compact_reviews):
        evidence.append(make_evidence_record(case_id, "external_evidence", f"reviews[{index}].body", item.get("body"), f"github://{spec['repo']}/pulls/{spec['number']}/reviews/{item.get('id') or index}"))
    for index, item in enumerate(compact_related):
        related_issue = item.get("issue") or {}
        related_number = item.get("number") or (related_issue.get("number") if isinstance(related_issue, Mapping) else None)
        related_body = related_issue.get("body") if isinstance(related_issue, Mapping) else None
        evidence.append(make_evidence_record(case_id, "external_evidence", f"related_issues[{index}].body", related_body, f"github://{spec['repo']}/issues/{related_number or index}"))
        for comment_index, comment in enumerate(item.get("comments") or []):
            evidence.append(make_evidence_record(case_id, "external_evidence", f"related_issues[{index}].comments[{comment_index}].body", comment.get("body"), f"github://{spec['repo']}/issues/{related_number or index}/comments/{comment.get('id') or comment_index}"))
    mentions = benchmark_mentions(all_records)
    if mentions:
        evidence.append(make_evidence_record(case_id, "external_evidence", "derived.benchmark_mentions", mentions, f"github://{spec['repo']}/pull/{spec['number']}/discussion", extra={"derived": True}))
    return {
        "schema": "mini-drop.github-pr.public-pack.v1",
        "pack_kind": "external_evidence",
        "case_id": case_id,
        "synthetic": False,
        "notice": "Public GitHub issue, comments, reviews and derived benchmark mentions. No private oracle fields are included.",
        "issue": compact_issue_value,
        "issue_comments": compact_issue_comments,
        "pr_comments": compact_pr_comments,
        "reviews": compact_reviews,
        "related_issues": compact_related,
        "benchmark_mentions": mentions,
        "evidence": evidence,
    }


def fetch_related_issues(
    spec: Mapping[str, Any],
    fetcher: GitHubFetcher,
    raw_case_dir: Path,
    *,
    max_pages: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch curated issue references as optional discussion evidence.

    Some references are private, deleted, or unavailable.  Those failures are
    retained in the manifest but do not invalidate the PR's own source bundle.
    """
    related: list[dict[str, Any]] = []
    fetches: list[dict[str, Any]] = []
    repo = str(spec["repo"])
    for issue_number in spec.get("related_issues", []) or []:
        issue_payload, issue_result = fetcher.json(
            f"{raw_case_dir.name}/related-{issue_number}.json",
            github_url(repo, f"issues/{issue_number}"),
        )
        issue_fetch = issue_result.as_dict()
        issue_fetch["optional"] = True
        fetches.append(issue_fetch)
        item: dict[str, Any] = {
            "number": issue_number,
            "issue": compact_issue(issue_payload) if isinstance(issue_payload, Mapping) else {"available": False},
            "comments": [],
        }
        comments, comment_fetches = fetch_paginated(
            fetcher,
            raw_case_dir,
            f"related-{issue_number}-comments",
            f"repos/{repo}/issues/{issue_number}/comments",
            max_pages=max_pages,
        )
        for comment_fetch in comment_fetches:
            comment_fetch["optional"] = True
        fetches.extend(comment_fetches)
        item["comments"] = [compact_comment(comment) for comment in comments]
        if not issue_result.ok:
            item["error"] = issue_result.error or "unavailable"
        related.append(item)
    return related, fetches


def fetch_case(spec: Mapping[str, Any], fetcher: GitHubFetcher, max_pages: int) -> dict[str, Any]:
    case_id = str(spec["case_id"])
    raw_case_dir = fetcher.raw_root / slug(case_id)
    raw_case_dir.mkdir(parents=True, exist_ok=True)
    repo = str(spec["repo"])
    number = int(spec["number"])
    pr_payload, pr_result = fetcher.json(f"{slug(case_id)}/pr.json", github_url(repo, f"pulls/{number}"))
    pr = pr_payload if isinstance(pr_payload, Mapping) else {}
    diff_bytes, diff_result = fetcher.get(f"{slug(case_id)}/pr.diff", github_url(repo, f"pulls/{number}.diff"), accept="application/vnd.github.v3.diff")
    diff = (diff_bytes or b"").decode("utf-8", errors="replace")
    files_payload, files_result = fetcher.json(f"{slug(case_id)}/files.json", github_url(repo, f"pulls/{number}/files?per_page={DEFAULT_PAGE_SIZE}&page=1"))
    files = flatten_pages(files_payload)
    file_fetches = [files_result.as_dict()]
    # The first response is kept under the stable ``files.json`` name for
    # auditability; large PRs continue with page-specific cache files.
    if len(files) >= DEFAULT_PAGE_SIZE:
        for page in range(2, max_pages + 1):
            page_payload, page_result = fetcher.json(
                f"{slug(case_id)}/files.page-{page}.json",
                github_url(repo, f"pulls/{number}/files?per_page={DEFAULT_PAGE_SIZE}&page={page}"),
            )
            file_fetches.append(page_result.as_dict())
            page_files = flatten_pages(page_payload)
            files.extend(page_files)
            if len(page_files) < DEFAULT_PAGE_SIZE:
                break
    issue_payload, issue_result = fetcher.json(f"{slug(case_id)}/issue.json", github_url(repo, f"issues/{number}"))
    issue = issue_payload if isinstance(issue_payload, Mapping) else {}
    issue_comments, issue_comment_fetches = fetch_paginated(fetcher, raw_case_dir, "issue_comments", f"repos/{repo}/issues/{number}/comments", max_pages=max_pages)
    pr_comments, pr_comment_fetches = fetch_paginated(fetcher, raw_case_dir, "pr_comments", f"repos/{repo}/pulls/{number}/comments", max_pages=max_pages)
    reviews, review_fetches = fetch_paginated(fetcher, raw_case_dir, "reviews", f"repos/{repo}/pulls/{number}/reviews", max_pages=max_pages)
    related_issues, related_issue_fetches = fetch_related_issues(spec, fetcher, raw_case_dir, max_pages=max_pages)

    pack_dir = fetcher.raw_root.parent / "packs" / slug(case_id)
    pack_dir.mkdir(parents=True, exist_ok=True)
    core = build_core_pack(spec, pr, files, diff)
    external = build_external_pack(
        spec,
        issue,
        issue_comments,
        pr_comments,
        reviews,
        pr_body=pr.get("body"),
        related_issues=related_issues,
    )
    runtime = build_runtime_pack(spec)
    write_json(pack_dir / "pr_core.json", core)
    write_json(pack_dir / "external_evidence.json", external)
    write_json(pack_dir / "simulated_runtime.json", runtime)
    manifest = {
        "schema": "mini-drop.github-pr.public-pack-manifest.v1",
        "case_id": case_id,
        "generated_at": utc_now(),
        "public_only": True,
        "packs": {
            name: {
                "path": f"packs/{slug(case_id)}/{name}.json",
                "bytes": (pack_dir / f"{name}.json").stat().st_size,
                "sha256": sha256_bytes((pack_dir / f"{name}.json").read_bytes()),
            }
            for name in ("pr_core", "external_evidence", "simulated_runtime")
        },
    }
    write_json(pack_dir / "manifest.json", manifest)
    return {
        "case_id": case_id,
        "spec": spec,
        "pr": pr,
        "issue": issue,
        "files": files,
        "diff": diff,
        "pack_dir": pack_dir,
        "runtime_pack": runtime,
        "fetches": [
            pr_result.as_dict(),
            diff_result.as_dict(),
            *file_fetches,
            issue_result.as_dict(),
            *issue_comment_fetches,
            *pr_comment_fetches,
            *review_fetches,
            *related_issue_fetches,
        ],
    }


def verify_public_pack(pack_dir: Path, case_id: str) -> dict[str, Any]:
    failures: list[str] = []
    pack_paths = [pack_dir / "pr_core.json", pack_dir / "external_evidence.json", pack_dir / "simulated_runtime.json"]
    loaded: dict[str, Any] = {}
    for path in pack_paths:
        if not path.is_file():
            failures.append(f"missing:{path.name}")
            continue
        try:
            value = read_json(path)
            loaded[path.stem] = value
            if value.get("case_id") != case_id:
                failures.append(f"case_id:{path.name}")
            if "oracle" in value or "expected_mechanism" in value:
                failures.append(f"private_field:{path.name}")
            for evidence in value.get("evidence", []):
                if not evidence.get("evidence_id") or not evidence.get("projection_hash") or not evidence.get("field_path"):
                    failures.append(f"invalid_evidence:{path.name}")
                    break
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures.append(f"invalid_json:{path.name}:{type(exc).__name__}")
    return {"status": "PASS" if not failures else "FAIL", "failures": failures, "pack_count": len(loaded)}


def verify_sha_pinning(spec: Mapping[str, Any], pr: Mapping[str, Any]) -> dict[str, Any]:
    expected = spec.get("expected_refs") if isinstance(spec.get("expected_refs"), Mapping) else {}
    observed = {
        "base": nested(pr, "base", "sha"),
        "head": nested(pr, "head", "sha"),
        "merge": pr.get("merge_commit_sha") if (pr.get("merged") or pr.get("merged_at")) else None,
    }
    mismatches = [key for key in ("base", "head", "merge") if expected.get(key) != observed.get(key)]
    # A closed, unmerged draft may legitimately have no merge SHA.  The spec
    # explicitly says when one is expected, so an expected SHA mismatch remains
    # a reproducibility failure.
    return {"status": "PASS" if not mismatches else "FAIL", "observed": observed, "mismatches": mismatches}


def control_probe(url: str, timeout: float = 5.0) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    result: dict[str, Any] = {"url": url, "reachable": False, "http_status": None, "error": None}
    if not host:
        result["error"] = "invalid_url"
        return result
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["reachable"] = True
    except OSError as exc:
        result["error"] = type(exc).__name__
        return result
    try:
        request = urllib.request.Request(url.rstrip("/") + "/health", headers={"User-Agent": "mini-drop-github-pr-eval/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result["http_status"] = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        result["http_status"] = int(exc.code)
        result["error"] = f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        result["error"] = type(exc).__name__
    return result


def _read_projection_import_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Load successful imports without exposing response bodies or secrets."""
    cached: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return cached
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return cached
    for line in lines:
        try:
            value = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping) or not value.get("ok"):
            continue
        key = f"{value.get('case_id')}|{value.get('evidence_id')}|{value.get('projection_hash')}"
        cached[key] = dict(value)
    return cached


def _post_projection(
    url: str,
    body: bytes,
    *,
    headers: Mapping[str, str],
    timeout: float,
    max_response_bytes: int = 1024 * 1024,
) -> tuple[bool, Optional[int], int, Optional[str]]:
    """POST one JSON projection and return status/response size only.

    Response bodies are intentionally discarded.  This keeps both the local
    report and the upload accounting free of API keys or proxy diagnostics
    accidentally echoed by an upstream service.
    """
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mini-drop-github-pr-eval/1.0",
            **dict(headers),
        },
    )
    response_bytes = 0
    status: Optional[int] = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            while response_bytes <= max_response_bytes:
                chunk = response.read(min(64 * 1024, max_response_bytes - response_bytes + 1))
                if not chunk:
                    break
                response_bytes += len(chunk)
            if response_bytes > max_response_bytes:
                return False, status, response_bytes, "response_too_large"
            return 200 <= status < 300, status, response_bytes, None if 200 <= status < 300 else f"http_{status}"
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        # Do not persist the response body; only count a bounded diagnostic.
        try:
            while response_bytes <= max_response_bytes:
                chunk = exc.read(min(64 * 1024, max_response_bytes - response_bytes + 1))
                if not chunk:
                    break
                response_bytes += len(chunk)
        except OSError:
            pass
        return False, status, response_bytes, f"http_{status}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, status, response_bytes, f"network_error:{type(exc).__name__}"


def import_projection_packs(
    output_dir: Path,
    manifest: Mapping[str, Any],
    *,
    control_url: str,
    import_token: str,
    api_key: str = "",
    timeout: float = 10.0,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Import each projection once, reusing successful local receipts.

    The function is opt-in and intentionally does not create Cases.  Cases
    must already exist on the server; ``case_map`` in the manifest lets an
    operator map local GitHub case IDs to those durable Case IDs.  This avoids
    a second, potentially much larger API workflow during bandwidth-limited
    evaluations.
    """
    results_path = output_dir / "projection-import-results.jsonl"
    cache = {} if force else _read_projection_import_cache(results_path)
    results: list[dict[str, Any]] = []
    entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    headers: dict[str, str] = {"X-Evaluation-Import-Token": import_token}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        case_id = str(entry.get("case_id") or "")
        evidence_id = str(entry.get("evidence_id") or "")
        projection_hash = str(entry.get("projection_hash") or "")
        key = f"{case_id}|{evidence_id}|{projection_hash}"
        cached = cache.get(key)
        if cached is not None:
            receipt = dict(cached)
            receipt["from_cache"] = True
            receipt["request_bytes"] = 0
            results.append(receipt)
            continue
        relative = str(entry.get("path") or "")
        projection_path = output_dir / relative
        try:
            payload = read_json(projection_path)
            body = canonical_json(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            result = ImportResult(
                case_id=case_id,
                source_case_id=str(entry.get("source_case_id") or ""),
                pack_kind=str(entry.get("pack_kind") or ""),
                evidence_id=evidence_id,
                projection_hash=projection_hash,
                request_bytes=0,
                response_bytes=0,
                ok=False,
                status=None,
                error=f"projection_read_error:{type(exc).__name__}",
            ).as_dict()
            results.append(result)
            continue
        endpoint = f"{control_url.rstrip('/')}/api/v1/cases/{urllib.parse.quote(case_id, safe='')}/evidence/import"
        ok, status, response_bytes, error = _post_projection(
            endpoint, body, headers=headers, timeout=max(1.0, timeout),
        )
        result = ImportResult(
            case_id=case_id,
            source_case_id=str(entry.get("source_case_id") or ""),
            pack_kind=str(entry.get("pack_kind") or ""),
            evidence_id=evidence_id,
            projection_hash=projection_hash,
            request_bytes=len(body),
            response_bytes=response_bytes,
            ok=ok,
            status=status,
            error=error,
        ).as_dict()
        results.append(result)
        # Append each receipt immediately so an interrupted run can resume
        # without re-uploading already accepted projections.
        try:
            with results_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass
    return results


def provider_key_presence() -> dict[str, Any]:
    # Only inspect whether a conventional variable exists.  Values are never
    # read, logged, serialized, or passed to another process.
    names = (
        "MINI_DROP_AI_API_KEY",
        "MINI_DROP_PROVIDER_API_KEY",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_V4_FLASH_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    return {
        "checked_names": list(names),
        # Deliberately test only key presence.  Reading the value here would
        # expand the runner's secret-handling surface; provider health/auth
        # checks are responsible for rejecting an empty or invalid key.
        "present": [name for name in names if name in os.environ],
    }


def api_key_presence() -> bool:
    """Return whether the runner can authenticate to a protected Control API."""
    return bool(os.getenv("MINI_DROP_API_KEY", "").strip())


def build_preflight(
    cases: Sequence[Mapping[str, Any]],
    case_results: Sequence[Mapping[str, Any]],
    *,
    control_url: str,
    pi_runtime_url: Optional[str],
    provider_url: Optional[str],
    offline: bool,
    import_requested: bool = False,
    import_token_present: bool = False,
    api_auth_enabled: bool = False,
) -> dict[str, Any]:
    if offline:
        control = {"url": control_url, "reachable": False, "http_status": None, "error": "offline_mode"}
        runtime = {"url": pi_runtime_url, "reachable": False, "http_status": None, "error": "offline_mode"}
    else:
        control = control_probe(control_url)
        runtime = control_probe(pi_runtime_url, timeout=3.0) if pi_runtime_url else {"reachable": False, "error": "not_configured", "url": None}
    provider = provider_key_presence()
    reasons: list[str] = []
    if not offline and not control.get("reachable"):
        reasons.append("control_plane_unavailable")
    if not offline and not runtime.get("reachable"):
        reasons.append("pi_runtime_unavailable")
    if not provider.get("present"):
        reasons.append("provider_key_missing")
    if api_auth_enabled and not api_key_presence():
        reasons.append("control_api_key_missing")
    if offline:
        reasons.append("offline_mode")
    if import_requested and offline:
        reasons.append("projection_import_disabled_in_offline_mode")
    elif import_requested and not import_token_present:
        reasons.append("evaluation_import_token_missing")
    per_case: list[dict[str, Any]] = []
    for result in case_results:
        spec = result["spec"]
        fetches = result.get("fetches", [])
        fetch_complete = bool(fetches) and all(item.get("ok") or item.get("optional") for item in fetches)
        pack_check = verify_public_pack(Path(result["pack_dir"]), str(result["case_id"]))
        sha_check = verify_sha_pinning(spec, result.get("pr", {}))
        runtime_check = synthetic_check(spec, result.get("runtime_pack", {}))
        per_case.append({
            "case_id": result["case_id"],
            "fetch_complete": fetch_complete,
            "fetch_failures": [item for item in fetches if not item.get("ok") and not item.get("optional")],
            "optional_fetch_failures": [item for item in fetches if not item.get("ok") and item.get("optional")],
            "public_pack_integrity": pack_check,
            "sha_pinning": sha_check,
            "synthetic_evidence_check": runtime_check,
            "preflight_pass": fetch_complete and pack_check["status"] == "PASS" and sha_check["status"] == "PASS" and runtime_check["status"] == "PASS",
        })
    all_case_preflight = all(item["preflight_pass"] for item in per_case) if per_case else False
    return {
        "schema": "mini-drop.github-pr.preflight.v1",
        "generated_at": utc_now(),
        "offline": offline,
        "control_plane": control,
        "pi_runtime": runtime,
        "provider": {"url_configured": bool(provider_url), "key_presence": provider},
        "control_api_auth": {
            "enabled": bool(api_auth_enabled),
            "key_present": api_key_presence(),
        },
        "projection_import": {
            "requested": bool(import_requested),
            "token_present": bool(import_token_present),
            "raw_packs_uploaded": False,
        },
        "blocked_reasons": reasons,
        "case_count": len(cases),
        "all_case_preflight_pass": all_case_preflight,
        "live_preflight_pass": all_case_preflight and not reasons,
        "cases": per_case,
        "notes": [
            "No provider key value is read or written by this runner.",
            "Public packs and private oracles are written to separate directories.",
            "Synthetic runtime evidence is explicitly marked and is not a collector result.",
        ],
    }


def write_oracles(output_dir: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    oracle_dir = output_dir / "oracle"
    oracle_dir.mkdir(parents=True, exist_ok=True)
    for spec in cases:
        oracle = {
            "schema": "mini-drop.github-pr.private-oracle.v1",
            "case_id": spec["case_id"],
            "github": {"repo": spec["repo"], "number": spec["number"], "expected_refs": spec.get("expected_refs")},
            "oracle": spec.get("oracle"),
            "synthetic_runtime_fixture": spec.get("runtime"),
        }
        write_json(oracle_dir / f"{slug(str(spec['case_id']))}.json", oracle)


def write_live_results(output_dir: Path, cases: Sequence[Mapping[str, Any]], preflight: Mapping[str, Any]) -> list[dict[str, Any]]:
    reasons = list(preflight.get("blocked_reasons", []))
    case_preflight = {item["case_id"]: item for item in preflight.get("cases", [])}
    rows: list[dict[str, Any]] = []
    for spec in cases:
        case_id = str(spec["case_id"])
        checks = case_preflight.get(case_id, {})
        for input_kind in ("pr_core", "external_evidence", "simulated_runtime"):
            row = {
                "schema": "mini-drop.github-pr.live-result.v1",
                "run_id": f"{slug(case_id)}:{input_kind}",
                "case_id": case_id,
                "input_kind": input_kind,
                "status": "blocked",
                "blocked_reasons": reasons or ["live_runtime_not_enabled"],
                "preflight_pass": checks.get("preflight_pass", False),
                "real_ai_score": None,
                "model_attempts": None,
                "elapsed_seconds": None,
                "synthetic_evidence_check": checks.get("synthetic_evidence_check"),
                "note": "No model score is produced while runtime/provider prerequisites are unavailable.",
            }
            rows.append(row)
    with (output_dir / "live-results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return rows


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def write_analysis(
    output_dir: Path,
    cases: Sequence[Mapping[str, Any]],
    case_results: Sequence[Mapping[str, Any]],
    preflight: Mapping[str, Any],
) -> None:
    lines = [
        "# GitHub PR Attribution Evaluation Analysis",
        "",
        f"Generated: `{utc_now()}`",
        "",
        "This report separates public evidence preparation from model execution. The runtime signals below are synthetic fixtures for checking evidence wiring; they are not real collector output and must not be treated as measured production behavior.",
        "",
        "## Case feedback",
        "",
        "| Case | Evaluation focus | Fetch | Synthetic check | Live status |",
        "| --- | --- | --- | --- | --- |",
    ]
    preflight_cases = {item["case_id"]: item for item in preflight.get("cases", [])}
    for spec in cases:
        case_id = str(spec["case_id"])
        runtime = spec.get("runtime") if isinstance(spec.get("runtime"), Mapping) else {}
        check = preflight_cases.get(case_id, {})
        lines.append(
            f"| `{case_id}` | {runtime.get('evaluation_focus', '')} | "
            f"{'pass' if check.get('fetch_complete') else 'blocked'} | "
            f"{nested(check, 'synthetic_evidence_check', 'status') or 'missing'} | blocked |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundaries",
            "",
            "- A public PR description or keyword match is not a root-cause score.",
            "- Open PRs are pinned to their observed head SHA; no provisional merge SHA is used.",
            "- Revert/negative-control cases require qualification or abstention when the evidence does not close the causal loop.",
            "- Redis is represented only by a low-scale synthetic signal fixture; this runner does not enable AOF or a large keyspace.",
            "- The runner does not clone any repository. Only bounded PR API responses and diffs are cached.",
            "",
            "## Live blockers",
            "",
        ]
    )
    reasons = preflight.get("blocked_reasons") or ["none"]
    lines.extend(f"- `{reason}`" for reason in reasons)
    if preflight.get("live_preflight_pass"):
        lines.append("- All prerequisites appear reachable; a future runtime adapter can execute the packs without changing the public/oracle split.")
    else:
        lines.append("- No real AI score was produced in this run.")
    write_text(output_dir / "analysis.md", "\n".join(lines) + "\n")


def write_summary(
    output_dir: Path,
    cases: Sequence[Mapping[str, Any]],
    case_results: Sequence[Mapping[str, Any]],
    preflight: Mapping[str, Any],
    live_results: Sequence[Mapping[str, Any]],
    metrics_before: Mapping[str, Any],
    metrics_after: Mapping[str, Any],
    elapsed_seconds: float,
    round_count: int = 1,
) -> None:
    result_by_id = {str(item["case_id"]): item for item in case_results}
    pf_by_id = {str(item["case_id"]): item for item in preflight.get("cases", [])}
    effective_round_count = max(1, int(round_count))
    round_label = "one smoke round" if effective_round_count == 1 else f"{effective_round_count} rounds"
    lines = [
        "# Mini-Drop GitHub PR Attribution Evaluation",
        "",
        f"Generated: `{utc_now()}`  ",
        f"Runner: `{SCRIPT_VERSION}`  ",
        f"Cases: **{len(cases)}**  |  Input packs: **{len(cases) * 3}**  |  Preflight pack records: **{len(live_results)}**  |  Requested model rounds: **{len(cases) * max(1, int(round_count))}**",
        "",
        "## Outcome",
        "",
        f"- Public data preparation: **{'PASS' if preflight.get('all_case_preflight_pass') else 'PARTIAL/BLOCKED'}**",
        f"- Evaluation rounds represented: **{effective_round_count}** ({round_label})",
        f"- Live AI execution: **{'READY' if preflight.get('live_preflight_pass') else 'BLOCKED'}**",
        "- Real AI score: **not produced** (all `real_ai_score` fields remain `null`)",
        f"- Wall time: **{elapsed_seconds:.1f}s**",
        "",
        "The three pack types are `pr_core`, `external_evidence`, and `simulated_runtime`. Synthetic runtime evidence is explicitly labelled and is not a real collector result.",
        "",
        "## Cases",
        "",
        "| Case | PR | State | Raw bytes | Pack bytes | Fetch | SHA pin | Synthetic check |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for spec in cases:
        case_id = str(spec["case_id"])
        result = result_by_id.get(case_id, {})
        pf = pf_by_id.get(case_id, {})
        raw_bytes = sum(int(item.get("bytes") or 0) for item in result.get("fetches", []))
        pack_bytes = sum(
            int((Path(result.get("pack_dir", "")) / f"{name}.json").stat().st_size)
            for name in ("pr_core", "external_evidence", "simulated_runtime")
            if result.get("pack_dir") and (Path(result["pack_dir"]) / f"{name}.json").is_file()
        )
        pr = result.get("pr", {})
        lines.append(
            f"| `{case_id}` | [{spec['repo']}#{spec['number']}](https://github.com/{spec['repo']}/pull/{spec['number']}) | "
            f"{pr.get('state') or 'unknown'} | {format_bytes(raw_bytes)} | {format_bytes(pack_bytes)} | "
            f"{'pass' if pf.get('fetch_complete') else 'blocked'} | "
            f"{nested(pf, 'sha_pinning', 'status') or 'missing'} | "
            f"{nested(pf, 'synthetic_evidence_check', 'status') or 'missing'} |"
        )
    disk_before = nested(metrics_before, "disk", "free_bytes")
    disk_after = nested(metrics_after, "disk", "free_bytes")
    output_bytes = tree_size(output_dir)
    projection_manifest: Mapping[str, Any] = {}
    try:
        loaded_projection_manifest = read_json(output_dir / "projection-manifest.json")
        if isinstance(loaded_projection_manifest, Mapping):
            projection_manifest = loaded_projection_manifest
    except (OSError, ValueError, json.JSONDecodeError):
        projection_manifest = {}
    lines.extend(
        [
            "",
            "## Resource and disk notes",
            "",
            f"- Report directory: `{output_dir}`",
            f"- Report bytes: **{format_bytes(output_bytes)}**",
            f"- Disk free before/after: **{format_bytes(disk_before) if isinstance(disk_before, int) else 'unknown'}** / **{format_bytes(disk_after) if isinstance(disk_after, int) else 'unknown'}**",
            f"- Estimated report write rate: **{format_bytes(output_bytes / elapsed_seconds)}/s**" if elapsed_seconds > 0 else "- Estimated report write rate: unknown",
            "- Fetching is serial, response bodies are bounded, and no repository clone or Docker workload is started.",
            f"- Low-bandwidth projection import estimate: **{format_bytes(int(projection_manifest.get('estimated_one_time_import_request_bytes') or 0))}** JSON once; gzip estimate **{format_bytes(int(projection_manifest.get('estimated_one_time_import_gzip_bytes') or 0))}**.",
            f"- {round_label.capitalize()} reuses canonical Evidence IDs; raw packs are not uploaded and repeated-turn upload is not counted as an import.",
            "",
            "## Blockers",
            "",
        ]
    )
    reasons = preflight.get("blocked_reasons") or ["none"]
    lines.extend(f"- `{reason}`" for reason in reasons)
    lines.extend(
        [
            "",
            f"See `preflight.json` for per-case integrity checks, `live-results.jsonl` for {len(live_results)} pack-level preflight records (not model answers), `packs/` for public inputs, and `oracle/` for private expected conclusions.",
        ]
    )
    write_text(output_dir / "summary.md", "\n".join(lines) + "\n")


def write_feedback_cn(
    output_dir: Path,
    cases: Sequence[Mapping[str, Any]],
    preflight: Mapping[str, Any],
    metrics_before: Mapping[str, Any],
    metrics_after: Mapping[str, Any],
    elapsed_seconds: float,
    round_count: int = 1,
) -> None:
    """Write a concise Chinese handoff alongside the machine-readable report."""
    disk_before = nested(metrics_before, "disk", "free_bytes")
    disk_after = nested(metrics_after, "disk", "free_bytes")
    reasons = "、".join(preflight.get("blocked_reasons") or ["无"])
    projection_manifest: Mapping[str, Any] = {}
    try:
        loaded_projection_manifest = read_json(output_dir / "projection-manifest.json")
        if isinstance(loaded_projection_manifest, Mapping):
            projection_manifest = loaded_projection_manifest
    except (OSError, ValueError, json.JSONDecodeError):
        projection_manifest = {}
    focus = {
        "prometheus-19393": "识别 text format lexer 的 CPU 优化边界；这是 WIP，必须保留 benchmark 未确认的不确定性。",
        "grafana-123359": "重点验证 workqueue 指针键导致去重失效和 Repository retention，不接受只写“内存泄漏”。",
        "prometheus-19412": "区分 oversized backing array 保留与传统泄漏，并引用 benchmark 未复现回归的边界。",
        "redis-15427": "验证 CPU 可以空闲但 expired backlog 仍增长，根因应是 activeExpireCycle 局部窗口偏差。",
        "kubernetes-138571": "从周期性 full sync 成本解释大集群扰动，不把关联 nft 延迟泛化为网络故障。",
        "kubernetes-140886": "这是 revert/不确定性样本，应保留 abstention，不能宣称因果链闭合。",
        "opentelemetry-python-4224": "沿 strong reference、weakref、GC/referrer 证据链定位对象 retention。",
        "grafana-124542": "负向 control；detached node 仍 retained 时不能认证候选修复有效。",
        "envoy-42752": "识别每个 data chunk 的 debug expression 微热点，不夸大为系统级故障。",
    }
    effective_round_count = max(1, int(round_count))
    round_label = "单轮 smoke" if effective_round_count == 1 else f"{effective_round_count} 轮"
    case_count = len(cases)
    preflight_cases = [item for item in (preflight.get("cases") or []) if isinstance(item, Mapping)]
    fetched_case_count = sum(1 for item in preflight_cases if item.get("fetch_complete"))
    expected_pack_count = case_count * 3
    verified_pack_count = sum(
        int((item.get("public_pack_integrity") or {}).get("pack_count") or 0)
        for item in preflight_cases
    )
    live_record_count = case_count * 3
    lines = [
        "# Mini-Drop GitHub PR 归因评测中文反馈",
        "",
        f"生成时间：`{utc_now()}`",
        f"输出目录：`{output_dir}`",
        "",
        "## 结论",
        "",
        f"- {fetched_case_count}/{case_count} PR 的 metadata、diff、changed files、PR issue/评论/review 和关联 issue 资料抓取成功。",
        f"- {verified_pack_count}/{expected_pack_count} 公开 pack 的 Evidence ID、projection hash、field path、SHA pin 和 synthetic 标记检查通过。",
        f"- 真实 AI 运行没有伪造结果：{live_record_count} 条记录均为 `blocked`，`real_ai_score` 全部为 `null`。",
        f"- 当前阻塞：`{reasons}`。",
        "- 合成 runtime 信号只用于检查证据接线，不能当作真实 collector 或生产测量。",
        f"- 低流量导入估算：首次只上传 projection JSON 约 `{format_bytes(int(projection_manifest.get('estimated_one_time_import_request_bytes') or 0))}`；原始 pack 不上传。",
        f"- {round_label} 评测复用同一组 Evidence ID；导入成功回执会写入 `projection-import-results.jsonl`，重复运行自动跳过已成功项。",
        "",
        "## 资源影响",
        "",
        f"- 串行抓取耗时：约 `{elapsed_seconds:.1f}` 秒；不 clone 仓库、不启动 Docker、不进行大规模 Redis 复现。",
        f"- 磁盘可用空间：`{format_bytes(disk_before) if isinstance(disk_before, int) else 'unknown'}` -> `{format_bytes(disk_after) if isinstance(disk_after, int) else 'unknown'}`。",
        "- 请求有间隔、响应有大小上限；报告同时记录原始字节、缓存 hash、磁盘快照和估算写入速率。",
        "",
        "## 样本反馈",
        "",
    ]
    for spec in cases:
        case_id = str(spec["case_id"])
        lines.append(f"- `{case_id}`：{focus.get(case_id, '按 oracle 和公开证据进行机制级归因。')}")
    lines.extend([
        "",
        "## 继续真实运行所需条件",
        "",
        "1. 启动 `127.0.0.1:8191` control API 和可达的 Pi sidecar/runtime。",
        "2. 在 sidecar 配置模型 provider；本次只检查配置是否存在，没有读取或写入 key。",
        "3. 将 `packs/` 物化为 canonical Evidence 后，使用 READ_ONLY/PROPOSE_ONLY agent turn。",
        "4. 记录模型尝试、引用的 quote/span/field_path/projection hash，并按机制、反证、abstention 分别评分。",
    ])
    write_text(output_dir / "feedback.zh-CN.md", "\n".join(lines) + "\n")


def load_case_map(path: Optional[str]) -> dict[str, str]:
    """Read a local-case -> server-case mapping without accepting surprises."""
    if not path:
        return {}
    source = Path(path).expanduser()
    try:
        value = read_json(source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read --case-map: {type(exc).__name__}") from exc
    if not isinstance(value, Mapping):
        raise SystemExit("--case-map must contain a JSON object")
    result: dict[str, str] = {}
    for key, mapped in value.items():
        source_id = str(key).strip()
        target_id = str(mapped).strip()
        if not source_id or not target_id:
            raise SystemExit("--case-map keys and values must be non-empty strings")
        result[source_id] = target_id
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        help="Report directory; defaults to reports/eval/github-pr-attribution-<timestamp>.",
    )
    parser.add_argument("--cases", help="Comma-separated case IDs or PR numbers (default: all nine).")
    parser.add_argument("--offline", action="store_true", help="Use only cached responses; never access GitHub or control URLs.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached response bodies and refetch GitHub data.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request GitHub timeout in seconds.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="Maximum comment/review pages per endpoint.")
    parser.add_argument("--max-response-mb", type=int, default=20, help="Maximum response size to cache per endpoint.")
    parser.add_argument("--request-delay", type=float, default=DEFAULT_DELAY_SECONDS, help="Delay between uncached GitHub requests.")
    parser.add_argument(
        "--low-bandwidth",
        action="store_true",
        help="Write compact projection packs and a reusable round plan; never upload raw packs.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="Manual/runtime evaluation rounds represented in the low-bandwidth plan (default: 1 smoke; formal 9x3 uses 3).",
    )
    parser.add_argument(
        "--case-map",
        help="JSON object mapping local GitHub case IDs to existing server Case IDs for projection import.",
    )
    parser.add_argument(
        "--import-evidence",
        action="store_true",
        help="Import compact projections once through the fail-closed evidence import endpoint.",
    )
    parser.add_argument(
        "--import-force",
        action="store_true",
        help="Ignore local successful-import receipts and upload projections again.",
    )
    parser.add_argument(
        "--control-url",
        default=os.getenv("MINI_DROP_CONTROL_URL", "http://127.0.0.1:8191"),
        help="Mini-Drop control URL to probe (default: MINI_DROP_CONTROL_URL or localhost).",
    )
    parser.add_argument(
        "--pi-runtime-url",
        default=os.getenv("MINI_DROP_PI_RUNTIME_URL") or None,
        help="Optional Pi sidecar/runtime URL to probe (default: MINI_DROP_PI_RUNTIME_URL).",
    )
    parser.add_argument(
        "--provider-url",
        default=os.getenv("MINI_DROP_AI_BASE_URL") or os.getenv("DEEPSEEK_API_BASE") or None,
        help="Optional provider URL marker (default: configured provider base URL); no credential value is read.",
    )
    parser.add_argument("--strict", action="store_true", help="Exit 2 when preflight or live prerequisites fail.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.max_pages < 1:
        parser.error("--max-pages must be >= 1")
    if args.max_response_mb < 1:
        parser.error("--max-response-mb must be >= 1")
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    if args.import_evidence:
        # Importing a projection is the low-bandwidth path by definition.
        args.low_bandwidth = True
    cases = selected_specs(args.cases)
    case_map = load_case_map(args.case_map)
    workspace = ROOT
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = _default_output_dir(stamp)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)
    (output_dir / "packs").mkdir(parents=True, exist_ok=True)
    metrics_before = collect_metrics(output_dir, workspace)
    write_json(output_dir / "metrics-before.json", metrics_before)
    environment = {
        "schema": "mini-drop.github-pr.environment.v1",
        "captured_at": utc_now(),
        "workspace": str(workspace),
        "output_dir": str(output_dir),
        "case_count": len(cases),
        "github_client": "anonymous_rest_with_gh_cli_fallback",
        "repository_cloned": False,
        "docker_started": False,
        "secret_values_recorded": False,
        "resource_policy": {
            "serial_fetch": True,
            "request_delay_seconds": max(0.0, float(args.request_delay)),
            "max_response_bytes": max(1, int(args.max_response_mb)) * 1024 * 1024,
            "max_pages": max(1, int(args.max_pages)),
            "redis_large_scale_reproduction": False,
            "low_bandwidth_mode": bool(args.low_bandwidth),
            "manual_evaluation_rounds": int(args.rounds),
            "raw_pack_upload": False,
            "projection_import_once": bool(args.import_evidence),
        },
        "case_map": case_map,
        "metrics_before": "metrics-before.json",
    }
    write_json(output_dir / "environment.json", environment)
    started = time.monotonic()
    fetcher = GitHubFetcher(
        output_dir / "raw",
        offline=bool(args.offline),
        refresh=bool(args.refresh),
        timeout=max(1.0, float(args.timeout)),
        max_response_bytes=max(1, int(args.max_response_mb)) * 1024 * 1024,
        delay_seconds=max(0.0, float(args.request_delay)),
    )
    case_results: list[dict[str, Any]] = []
    for spec in cases:
        try:
            case_results.append(fetch_case(spec, fetcher, max_pages=max(1, int(args.max_pages))))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Preserve a report for the remaining cases; do not turn a single
            # malformed upstream response into a fabricated evaluation result.
            case_results.append({
                "case_id": spec["case_id"],
                "spec": spec,
                "pr": {},
                "pack_dir": output_dir / "packs" / slug(str(spec["case_id"])),
                "runtime_pack": build_runtime_pack(spec),
                "fetches": [{"ok": False, "error": f"case_error:{type(exc).__name__}", "bytes": 0}],
                "error": f"case_error:{type(exc).__name__}",
            })
            pack_dir = output_dir / "packs" / slug(str(spec["case_id"]))
            pack_dir.mkdir(parents=True, exist_ok=True)
            write_json(pack_dir / "simulated_runtime.json", build_runtime_pack(spec))
    write_oracles(output_dir, cases)
    projection_manifest = write_projection_packs(
        output_dir, case_results, case_map=case_map,
    )
    write_low_bandwidth_round_plan(
        output_dir, projection_manifest, rounds=int(args.rounds),
    )
    import_token = os.getenv("MINI_DROP_EVAL_IMPORT_TOKEN", "").strip() if args.import_evidence else ""
    import_api_key = os.getenv("MINI_DROP_API_KEY", "").strip() if args.import_evidence else ""
    preflight = build_preflight(
        cases,
        case_results,
        control_url=args.control_url,
        pi_runtime_url=args.pi_runtime_url,
        provider_url=args.provider_url,
        offline=bool(args.offline),
        import_requested=bool(args.import_evidence),
        import_token_present=bool(import_token),
        api_auth_enabled=os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"},
    )
    import_results: list[dict[str, Any]] = []
    if args.import_evidence and import_token and not args.offline:
        import_results = import_projection_packs(
            output_dir,
            projection_manifest,
            control_url=args.control_url,
            import_token=import_token,
            api_key=import_api_key,
            timeout=max(1.0, float(args.timeout)),
            force=bool(args.import_force),
        )
    elif args.import_evidence:
        # Keep an explicit receipt file even when the operator intentionally
        # used --offline or omitted the token; this is easier to audit than a
        # silent no-op and contains no credential value.
        write_text(output_dir / "projection-import-results.jsonl", "")
    if args.import_evidence:
        successful = [item for item in import_results if item.get("ok")]
        projection_manifest = {
            **projection_manifest,
            "import_requested": True,
            "import_attempted": bool(import_results),
            "import_success_count": len(successful),
            "import_failure_count": len(import_results) - len(successful),
            "actual_import_request_bytes": sum(int(item.get("request_bytes") or 0) for item in import_results),
            "actual_import_response_bytes": sum(int(item.get("response_bytes") or 0) for item in import_results),
        }
        write_json(output_dir / "projection-manifest.json", projection_manifest)
        preflight["projection_import"].update({
            "attempted": bool(import_results),
            "success_count": len(successful),
            "failure_count": len(import_results) - len(successful),
            "request_bytes": projection_manifest["actual_import_request_bytes"],
            "response_bytes": projection_manifest["actual_import_response_bytes"],
        })
    write_json(output_dir / "preflight.json", preflight)
    live_results = write_live_results(output_dir, cases, preflight)
    write_analysis(output_dir, cases, case_results, preflight)
    metrics_after = collect_metrics(output_dir, workspace)
    write_json(output_dir / "metrics-after.json", metrics_after)
    elapsed = time.monotonic() - started
    environment.update({
        "completed_at": utc_now(),
        "elapsed_seconds": round(elapsed, 3),
        "metrics_after": "metrics-after.json",
        "runtime_preflight": "preflight.json",
        "projection_manifest": "projection-manifest.json",
        "low_bandwidth_round_plan": "low-bandwidth-round-plan.json",
        "projection_import_results": "projection-import-results.jsonl" if args.import_evidence else None,
    })
    write_json(output_dir / "environment.json", environment)
    write_summary(
        output_dir, cases, case_results, preflight, live_results,
        metrics_before, metrics_after, elapsed, round_count=int(args.rounds),
    )
    write_feedback_cn(
        output_dir, cases, preflight, metrics_before, metrics_after, elapsed,
        round_count=int(args.rounds),
    )
    # The fetch manifest helps audit exactly what was transferred without
    # persisting any authorization header or credential.
    write_json(
        output_dir / "fetch-manifest.json",
        {"schema": "mini-drop.github-pr.fetch-manifest.v1", "generated_at": utc_now(), "requests": fetcher.results and [item.as_dict() for item in fetcher.results] or []},
    )
    if args.strict and not preflight.get("live_preflight_pass"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
