#!/usr/bin/env python3
"""Run the human-scored GitHub PR attribution evaluation.

The preparation runner deliberately stops before the live runtime boundary.
This companion runner crosses that boundary only after an operator has
explicitly enabled the bounded evaluation-import endpoint.  It is designed
for a bandwidth-constrained server:

* each pack is converted to a projection and imported once;
* the default smoke run uses one round per PR; the formal suite uses nine PRs
  and three independent rounds per PR; optional later rounds reuse the
  same canonical Evidence IDs and hashes;
* rounds are serial and use one Case at a time;
* no raw pack, repository clone, collector task, artifact, or MCP query is
  sent during a round; and
* no automatic score is calculated.  The output contains the model's visible
  text, citations/events and a blank manual scoring ledger.

The script never writes API keys to disk or to the traffic log.  Use the
preparation report's ``packs/`` directory as input, preferably on the same
host as Control so the one-time import is not a public upload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    from run_github_pr_attribution_eval import CASE_SPECS, read_json, slug
except ImportError:  # pragma: no cover - supports importing from the repository root
    from scripts.run_github_pr_attribution_eval import CASE_SPECS, read_json, slug


PACK_KINDS = ("pr_core", "external_evidence", "simulated_runtime")
READ_ONLY_TOOLS = (
    "get_case_snapshot",
    "list_case_evidence",
    "get_evidence_projection",
    "compare_evidence",
    "get_causal_graph",
    "get_evidence_gaps",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")


def hash_value(value: Any) -> str:
    # Keep this serialization identical to server.app.diagnosis.case_evidence
    # stable_projection_hash(), including its default JSON separators.
    return hashlib.sha256(json_bytes(value)).hexdigest()


def redact(value: Any) -> Any:
    """Remove accidental secret echoes before a response is persisted."""
    secret_values = [
        os.getenv(name, "")
        for name in (
            "MINI_DROP_API_KEY",
            "MINI_DROP_EVAL_IMPORT_TOKEN",
            "MINI_DROP_PI_INTERNAL_TOKEN",
            "MINI_DROP_AI_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        )
        if os.getenv(name, "")
    ]
    if isinstance(value, str):
        result = value
        for secret in secret_values:
            result = result.replace(secret, "[REDACTED]")
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact(item) for key, item in value.items()}
    return value


def redact_runtime_event(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep runtime audit metadata without persisting model-visible context."""
    result = redact(dict(value))
    payload = result.get("payload")
    if not isinstance(payload, dict) or "message" not in payload:
        return result
    raw_message = payload.get("message")
    encoded = json_bytes(raw_message)
    payload["message"] = "[REDACTED_RUNTIME_MESSAGE]"
    payload["message_bytes"] = len(encoded)
    payload["message_sha256"] = hashlib.sha256(encoded).hexdigest()
    return result


@dataclass
class Transfer:
    phase: str
    method: str
    path: str
    request_bytes: int
    response_bytes: int
    status: Optional[int]
    elapsed_ms: float
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "captured_at": utc_now(),
            "phase": self.phase,
            "method": self.method,
            "path": self.path,
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "error": self.error,
        }


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: Optional[int], body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body[:2000]
        super().__init__(f"{method} {path}: HTTP {status or 'network'} {self.body}")


class ControlClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        eval_token: str = "",
        internal_token: str = "",
        timeout: float = 30.0,
        insecure: bool = False,
        traffic_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.eval_token = eval_token
        self.internal_token = internal_token
        self.timeout = timeout
        self.context = ssl._create_unverified_context() if insecure else None
        self.traffic_path = traffic_path
        self.transfers: list[Transfer] = []

    def _headers(self, *, eval_import: bool = False, internal: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "mini-drop-github-pr-live-eval/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if eval_import and self.eval_token:
            headers["X-Evaluation-Import-Token"] = self.eval_token
        if internal and self.internal_token:
            headers["X-Internal-Token"] = self.internal_token
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any = None,
        phase: str,
        eval_import: bool = False,
        internal: bool = False,
        timeout: Optional[float] = None,
    ) -> Any:
        body = None if payload is None else json_bytes(payload)
        headers = self._headers(eval_import=eval_import, internal=internal)
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        started = time.monotonic()
        status: Optional[int] = None
        response_body = b""
        error: Optional[str] = None
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout, context=self.context) as response:
                status = int(getattr(response, "status", 200))
                response_body = response.read(2 * 1024 * 1024 + 1)
                if len(response_body) > 2 * 1024 * 1024:
                    response_body = response_body[: 2 * 1024 * 1024]
                    error = "response_truncated_at_2MiB"
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_body = exc.read(64 * 1024)
            error = f"http_{status}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            error = f"network:{type(exc).__name__}"
        elapsed = (time.monotonic() - started) * 1000
        transfer = Transfer(
            phase=phase,
            method=method,
            path=path,
            request_bytes=len(body or b""),
            response_bytes=len(response_body),
            status=status,
            elapsed_ms=elapsed,
            error=error,
        )
        self.transfers.append(transfer)
        if self.traffic_path:
            self.traffic_path.parent.mkdir(parents=True, exist_ok=True)
            with self.traffic_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(transfer.as_dict(), ensure_ascii=False) + "\n")
        if status is None or status < 200 or status >= 300:
            raise ApiError(method, path, status, response_body.decode("utf-8", "replace"))
        if not response_body:
            return None
        try:
            return json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(method, path, status, f"invalid_json:{type(exc).__name__}") from exc


def data_from_response(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def pack_projection(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Drop duplicated top-level raw fields while retaining exact citations."""
    records: list[dict[str, Any]] = []
    for item in pack.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        records.append({
            key: item.get(key)
            for key in ("evidence_id", "field_path", "projection_hash", "source_ref", "value", "synthetic")
            if key in item
        })
    projection: dict[str, Any] = {
        "schema": "mini-drop.github-pr.evaluation-projection.v1",
        "case_id": pack.get("case_id"),
        "pack_kind": pack.get("pack_kind"),
        "synthetic": bool(pack.get("synthetic")),
        "records": records,
    }
    # Runtime signals are useful as a compact index, but remain explicitly
    # marked synthetic by the pack and by every record.
    if pack.get("pack_kind") == "simulated_runtime":
        projection["signals"] = pack.get("signals") or []
        projection["run_metadata"] = pack.get("run_metadata") or {}
    return projection


def load_pack_set(pack_root: Path, case_id: str) -> dict[str, dict[str, Any]]:
    case_dir = pack_root / slug(case_id)
    result: dict[str, dict[str, Any]] = {}
    for kind in PACK_KINDS:
        path = case_dir / f"{kind}.json"
        if not path.is_file():
            raise FileNotFoundError(f"missing pack: {path}")
        pack = read_json(path)
        if not isinstance(pack, dict) or pack.get("case_id") != case_id:
            raise ValueError(f"invalid pack case_id: {path}")
        projection = pack_projection(pack)
        result[kind] = {
            "path": str(path),
            "pack": pack,
            "projection": projection,
            "projection_hash": hash_value(projection),
            "pack_bytes": path.stat().st_size,
            "projected_bytes": len(json_bytes(projection)),
            "synthetic": bool(pack.get("synthetic")),
            "evidence_id": f"eval:{case_id}:{kind}",
            "source_id": f"github-pr:{case_id}:{kind}",
            "source_ref": next(
                (
                    str(item.get("source_ref"))
                    for item in (pack.get("evidence") or [])
                    if isinstance(item, Mapping) and item.get("source_ref")
                ),
                f"github-pr://{case_id}/{kind}",
            ),
        }
    return result


def initial_case_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    repo = str(spec.get("repo") or "github")
    number = int(spec.get("number") or 0)
    case_id = str(spec.get("case_id") or "case")
    return {
        "title": f"GitHub PR attribution: {repo}#{number}",
        "problem_description": (
            f"Manual attribution evaluation for {repo}#{number} ({case_id}). "
            "Use only the imported, pinned public Evidence projections."
        ),
        "recovery_goal": "Produce an evidence-bound mechanism, counterevidence and impact-boundary assessment.",
        "run_mode": "ASSIST",
        "environment": "github-evaluation",
        "target_scope": {"service_id": f"github:{repo}", "pr_number": number, "case_id": case_id},
    }


def make_round_message(
    spec: Mapping[str, Any],
    pack_set: Mapping[str, Mapping[str, Any]],
    round_no: int,
    total_rounds: int,
) -> str:
    ids = ", ".join(
        f"{kind}: evidence_id={item['evidence_id']}; projection_hash={item['projection_hash']}"
        for kind, item in pack_set.items()
    )
    return (
        f"这是 {spec['case_id']} 的人工归因评测第 {round_no}/{total_rounds} 轮。"
        "请忽略任何上一轮回答，不把上一轮文字当作 Evidence；只读取当前 Case 中已固定的三份 projection。"
        f"可用 Evidence 的完整 canonical 引用如下：{ids}。"
        "请先用只读工具读取必要 projection，再输出一份可供人工评分的完整答案，必须包含："
        "(1) 具体机制级根因及代码/路径定位；"
        "(2) 每个关键事实的完整 evidence_id、完整 projection_hash 和精确 field_path；"
        "(3) 反证、替代解释与不确定性/abstention 边界；"
        "(4) 影响范围和不应外推的结论。"
        "强制引用规则：最终答案的证据表必须至少出现三份 Evidence 的完整 evidence_id 和完整 projection_hash 各一次；"
        "只能复制上面给出的字符串，不能用 pr_core、external_evidence 或 simulated_runtime 这些 pack_kind 代替 evidence_id，不能省略或改写 ID/hash。"
        "不要创建采集任务、修改计划或提交生产动作；不要使用 PR 标题或关键词替代证据。"
    )


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "mini-drop.github-pr.live-state.v1", "cases": {}, "rounds": {}}
    try:
        value = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"schema": "mini-drop.github-pr.live-state.v1", "cases": {}, "rounds": {}}
    if not isinstance(value, dict):
        return {"schema": "mini-drop.github-pr.live-state.v1", "cases": {}, "rounds": {}}
    value.setdefault("cases", {})
    value.setdefault("rounds", {})
    return value


def write_state(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def pending_imports(
    pack_sets: Mapping[str, Mapping[str, Mapping[str, Any]]],
    state: Mapping[str, Any],
) -> list[str]:
    """Return source-case/pack pairs that still need the one-time import.

    The import token is intentionally short-lived.  Once a successful import
    receipt is present in ``live-state.json``, later stability rounds should be
    able to run after the operator revokes that token.  We therefore decide
    whether the token is needed from the local, hash-pinned receipts rather
    than requiring it for every invocation.  A missing Case ID is treated as
    pending because the runner may need to create a new durable Case and bind
    fresh Evidence IDs.
    """
    cases = state.get("cases") if isinstance(state, Mapping) else None
    cases = cases if isinstance(cases, Mapping) else {}
    result: list[str] = []
    for source_case_id, pack_set in pack_sets.items():
        case_state = cases.get(source_case_id)
        control_case_id = (
            str(case_state.get("control_case_id") or "")
            if isinstance(case_state, Mapping)
            else ""
        )
        imports = case_state.get("imports") if isinstance(case_state, Mapping) else None
        imports = imports if isinstance(imports, Mapping) else {}
        if not control_case_id:
            result.extend(f"{source_case_id}:{kind}" for kind in pack_set)
            continue
        try:
            bound = bind_pack_set_to_case(
                pack_set,
                control_case_id=control_case_id,
                source_case_id=source_case_id,
            )
        except (TypeError, ValueError, KeyError):
            result.extend(f"{source_case_id}:{kind}" for kind in pack_set)
            continue
        for kind, item in bound.items():
            receipt = imports.get(kind)
            if not isinstance(receipt, Mapping):
                result.append(f"{source_case_id}:{kind}")
                continue
            if (
                str(receipt.get("evidence_id") or "") != str(item.get("evidence_id") or "")
                or str(receipt.get("projection_hash") or "") != str(item.get("projection_hash") or "")
            ):
                result.append(f"{source_case_id}:{kind}")
    return result


def case_data(payload: Any) -> dict[str, Any]:
    data = data_from_response(payload)
    if not isinstance(data, dict):
        raise ValueError("control response does not contain an object")
    return data


def ensure_case(client: ControlClient, spec: Mapping[str, Any], state: dict[str, Any]) -> tuple[str, bool]:
    case_id = str(spec["case_id"])
    existing = state["cases"].get(case_id) if isinstance(state.get("cases"), dict) else None
    if isinstance(existing, dict) and existing.get("control_case_id"):
        control_case_id = str(existing["control_case_id"])
        try:
            client.request("GET", f"/api/v1/cases/{urllib.parse.quote(control_case_id, safe='')}", phase="resume_case")
            return control_case_id, True
        except ApiError as exc:
            if exc.status not in {404, 410}:
                raise
    created = case_data(client.request(
        "POST", "/api/v1/cases", payload=initial_case_payload(spec), phase="create_case",
    ))
    control_case_id = str(created.get("case_id") or "")
    if not control_case_id:
        raise ValueError(f"control did not return case_id for {case_id}")
    state["cases"][case_id] = {
        "control_case_id": control_case_id,
        "created_at": utc_now(),
        "source_case_id": case_id,
    }
    return control_case_id, False


def bind_pack_set_to_case(
    pack_set: Mapping[str, Mapping[str, Any]],
    *,
    control_case_id: str,
    source_case_id: str,
) -> dict[str, dict[str, Any]]:
    """Bind local projections to the durable Case that owns their Evidence.

    The import route deliberately scopes IDs by Case (``eval:{case_id}:``).
    Preparation uses human-readable GitHub case IDs, while Control may assign
    a different durable ID when it creates a Case.  Rebinding also changes the
    projection's case identity and recomputes its server-compatible hash; all
    subsequent round messages and citations use this returned mapping.
    """
    bound: dict[str, dict[str, Any]] = {}
    for kind, raw_item in pack_set.items():
        item = dict(raw_item)
        projection = item.get("projection")
        if not isinstance(projection, Mapping):
            raise ValueError(f"invalid projection for {source_case_id}:{kind}")
        bound_projection = dict(projection)
        bound_projection["case_id"] = control_case_id
        bound_projection["source_case_id"] = source_case_id
        canonical_evidence_id = f"eval:{control_case_id}:{source_case_id}:{kind}"
        # The preparation pack records retain GitHub-local evidence IDs.  Once
        # imported, however, the server has exactly one canonical Evidence row
        # for this bounded projection. Make every citation in the expanded
        # content point at that row; retain source IDs/hashes only as provenance
        # so the model cannot accidentally submit an unverifiable local ID.
        records = bound_projection.get("records")
        if isinstance(records, list):
            normalized_records: list[Any] = []
            for raw_record in records:
                if not isinstance(raw_record, Mapping):
                    normalized_records.append(raw_record)
                    continue
                record = dict(raw_record)
                if record.get("evidence_id"):
                    record["source_evidence_id"] = record["evidence_id"]
                if record.get("projection_hash"):
                    record["source_projection_hash"] = record["projection_hash"]
                record["evidence_id"] = canonical_evidence_id
                # The aggregate projection hash is stored on the canonical
                # projection row. A per-record hash would fail claim binding.
                record.pop("projection_hash", None)
                normalized_records.append(record)
            bound_projection["records"] = normalized_records
        projection_hash = hash_value(bound_projection)
        item.update({
            "projection": bound_projection,
            "projection_hash": projection_hash,
            "projected_bytes": len(json_bytes(bound_projection)),
            "evidence_id": canonical_evidence_id,
            "bound_case_id": control_case_id,
            "source_case_id": source_case_id,
        })
        bound[str(kind)] = item
    return bound


def import_packs_once(
    client: ControlClient,
    control_case_id: str,
    pack_set: Mapping[str, Mapping[str, Any]],
    state: dict[str, Any],
    source_case_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    bound_pack_set = bind_pack_set_to_case(
        pack_set,
        control_case_id=control_case_id,
        source_case_id=source_case_id,
    )
    case_state = state["cases"].setdefault(source_case_id, {})
    imported = case_state.setdefault("imports", {})
    for kind, item in bound_pack_set.items():
        previous = imported.get(kind)
        if (
            isinstance(previous, dict)
            and previous.get("projection_hash") == item["projection_hash"]
            and previous.get("evidence_id") == item["evidence_id"]
        ):
            continue
        payload = {
            "evidence_id": item["evidence_id"],
            "pack_kind": kind,
            "source_id": item["source_id"],
            "source_ref": item["source_ref"],
            "projection": item["projection"],
            "projection_hash": item["projection_hash"],
            "content_hash": item["projection_hash"],
            "source_bytes": item["pack_bytes"],
            "synthetic": item["synthetic"],
        }
        result = data_from_response(client.request(
            "POST",
            f"/api/v1/cases/{urllib.parse.quote(control_case_id, safe='')}/evidence/import",
            payload=payload,
            phase="import_projection",
            eval_import=True,
        ))
        imported[kind] = {
            "evidence_id": item["evidence_id"],
            "projection_hash": item["projection_hash"],
            "projected_bytes": item["projected_bytes"],
            "control_case_id": control_case_id,
            "source_case_id": source_case_id,
            "imported_at": utc_now(),
            "server_result": redact(result),
        }
    case_state["imports"] = imported
    return bound_pack_set, {kind: dict(item) for kind, item in imported.items()}


def current_event_seq(client: ControlClient, control_case_id: str) -> int:
    try:
        data = data_from_response(client.request(
            "GET",
            f"/api/v1/cases/{urllib.parse.quote(control_case_id, safe='')}/events?latest=true",
            phase="event_cursor",
        ))
        high_water = data.get("last_event_seq") if isinstance(data, dict) else None
        if high_water is not None:
            return int(high_water or 0)
        items = data.get("items") if isinstance(data, dict) else []
        return max([int(item.get("case_event_seq") or 0) for item in items or []] or [0])
    except ApiError:
        return 0


def collect_round_events(
    client: ControlClient,
    control_case_id: str,
    turn_id: str,
    *,
    cursor: int,
    timeout: float,
    poll_interval: float,
) -> tuple[list[dict[str, Any]], int, str, Optional[str]]:
    deadline = time.monotonic() + timeout
    events: list[dict[str, Any]] = []
    visible_text = ""
    completed_at: Optional[str] = None
    while time.monotonic() < deadline:
        data = data_from_response(client.request(
            "GET",
            f"/api/v1/cases/{urllib.parse.quote(control_case_id, safe='')}/events?after_seq={cursor}&limit=200",
            phase="poll_events",
        ))
        rows = data.get("items") if isinstance(data, dict) else []
        for row in rows or []:
            seq = int(row.get("case_event_seq") or 0)
            cursor = max(cursor, seq)
            payload = row.get("payload") or {}
            trigger = str(payload.get("trigger_turn_id") or payload.get("turn_id") or "")
            if trigger and trigger != turn_id:
                continue
            event_type = str(row.get("event_type") or "")
            if trigger == turn_id or event_type in {"assistant.message", "turn.completed"}:
                events.append(redact(row))
            if trigger == turn_id and event_type == "assistant.message":
                visible_text = str(payload.get("content") or "")
            if trigger == turn_id and event_type == "turn.completed":
                completed_at = str(row.get("created_at") or utc_now())
        if visible_text and completed_at:
            return events, cursor, visible_text, completed_at
        time.sleep(max(0.05, poll_interval))
    return events, cursor, visible_text, completed_at


def runtime_events(client: ControlClient, control_case_id: str, turn_id: str) -> list[dict[str, Any]]:
    if not client.internal_token:
        return []
    try:
        data = data_from_response(client.request(
            "GET",
            f"/internal/runtime/v1/cases/{urllib.parse.quote(control_case_id, safe='')}/events?after_seq=0&limit=200",
            phase="runtime_events",
            internal=True,
        ))
    except ApiError:
        return []
    rows = data.get("items") if isinstance(data, dict) else []
    selected = []
    for row in rows or []:
        payload = row.get("payload") or {}
        if str(payload.get("trigger_turn_id") or "") == turn_id:
            selected.append(redact_runtime_event(row))
    return selected


def run_round(
    client: ControlClient,
    spec: Mapping[str, Any],
    pack_set: Mapping[str, Mapping[str, Any]],
    control_case_id: str,
    round_no: int,
    total_rounds: int,
    *,
    timeout: float,
    poll_interval: float,
) -> dict[str, Any]:
    before_seq = current_event_seq(client, control_case_id)
    command_id = f"github-pr-{spec['case_id']}-round-{round_no}"
    message = make_round_message(spec, pack_set, round_no, total_rounds)
    policy = {
        "side_effect_policy": "READ_ONLY",
        "execution_mode": "deny_write",
        "enabled_tools": list(READ_ONLY_TOOLS),
        "max_collection_requests": 1,
        "max_collection_duration_sec": 1,
    }
    payload = {
        "message": message,
        "intent": "explain",
        "execute_safe_tools": False,
        "max_tool_calls": 4,
        "requested_disposition": "ANSWER_ONLY",
        "client_command_id": command_id,
        "references": [],
        "runtime_policy": policy,
        "runtime_options": {
            "reasoning_effort": "low",
            "prompt_variant": "evidence_strict",
            # Do not let the Pi SDK replay prior stability-round messages to
            # the Provider.  Case/Evidence state remains durable in Control;
            # this only makes each human-scored round an independent prompt.
            "fresh_session": True,
        },
    }
    accepted_payload = data_from_response(client.request(
        "POST",
        f"/api/v1/cases/{urllib.parse.quote(control_case_id, safe='')}/agent/turn",
        payload=payload,
        phase="agent_turn",
    ))
    accepted = accepted_payload if isinstance(accepted_payload, dict) else {}
    turn_id = str(accepted.get("turn_id") or "")
    if not turn_id:
        return {
            "round_id": f"{spec['case_id']}:round-{round_no}",
            "case_id": spec["case_id"],
            "control_case_id": control_case_id,
            "round": round_no,
            "status": "blocked",
            "blocked_reason": "turn_id_missing",
            "accepted": redact(accepted),
        }
    events, last_seq, visible_text, completed_at = collect_round_events(
        client,
        control_case_id,
        turn_id,
        cursor=before_seq,
        timeout=timeout,
        poll_interval=poll_interval,
    )
    runtime = runtime_events(client, control_case_id, turn_id)
    model_attempts = []
    for row in runtime:
        attempt = (row.get("payload") or {}).get("model_attempt")
        if isinstance(attempt, dict):
            model_attempts.append(attempt)
    return {
        "schema": "mini-drop.github-pr.manual-round-result.v1",
        "round_id": f"{spec['case_id']}:round-{round_no}",
        "case_id": spec["case_id"],
        "control_case_id": control_case_id,
        "round": round_no,
        "status": "completed" if visible_text and completed_at else "timeout_or_no_visible_answer",
        "started_at": utc_now(),
        "completed_at": completed_at,
        "turn_id": turn_id,
        "client_command_id": command_id,
        "evidence_refs": [
            {
                "pack_kind": kind,
                "evidence_id": item["evidence_id"],
                "projection_hash": item["projection_hash"],
                "synthetic": item["synthetic"],
            }
            for kind, item in pack_set.items()
        ],
        "request_summary": {
            "message_bytes": len(message.encode("utf-8")),
            "intent": "explain",
            "execute_safe_tools": False,
            "requested_disposition": "ANSWER_ONLY",
            "fresh_session": True,
            "policy": policy,
            "tool_calls_allowed": list(READ_ONLY_TOOLS),
            "raw_pack_sent": False,
        },
        "assistant_visible_text": redact(visible_text),
        "case_events": events,
        "runtime_events": runtime,
        "model_attempts": redact(model_attempts),
        "manual_score": None,
        "manual_score_fields": {
            "mechanism_attribution_0_4": None,
            "evidence_citation_0_3": None,
            "counterevidence_uncertainty_0_2": None,
            "impact_boundary_0_1": None,
            "reviewer_notes": "",
        },
        "last_event_seq": last_seq,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_round_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(redact(row), ensure_ascii=False, sort_keys=True, default=str) + "\n")


def write_manual_ledger(path: Path, rows: Sequence[Mapping[str, Any]], total_rounds: int) -> None:
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(str(row.get("case_id") or ""), []).append(row)
    effective_rounds = max(1, int(total_rounds))
    round_mode = "单轮 smoke" if effective_rounds == 1 else f"{effective_rounds} 轮"
    stability_note = (
        "这是单轮 smoke；如需稳定性结论，请显式运行 `--rounds 3`。"
        if effective_rounds == 1
        else "请逐轮比较机制、引用和边界；不要用平均分掩盖单轮错误。"
    )
    lines = [
        f"# GitHub PR {round_mode}人工评分台账",
        "",
        "本文件只记录模型原文和人工评分位置；没有关键词、规则或程序自动评分。",
        "每轮满分 10 分：机制归因 0-4、证据引用 0-3、反证/不确定性 0-2、影响边界 0-1。",
        "请先阅读对应 `round-results.jsonl` 的 `assistant_visible_text`、Evidence 引用和 runtime events，再填写分数。",
        stability_note,
        "",
        "| PR | 轮次 | 状态 | 机制(0-4) | 证据(0-3) | 反证/不确定性(0-2) | 边界(0-1) | 合计 | 人工备注 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for case_id in sorted(by_case):
        for row in sorted(by_case[case_id], key=lambda item: int(item.get("round") or 0)):
            lines.append(
                f"| {case_id} | {row.get('round')} | {row.get('status')} |  |  |  |  |  |  |"
            )
    lines.extend([
        "",
        "## 稳定性记录" if effective_rounds > 1 else "## Smoke 记录",
        "",
        "如果执行多轮，另外判断同一 PR 是否在机制、引用有效性和边界判断上保持一致；不以平均分掩盖某一轮的错误。",
        "",
    ])
    for case_id in sorted(by_case):
        if effective_rounds == 1:
            lines.append(f"- `{case_id}`：Smoke 备注：____")
        else:
            lines.append(f"- `{case_id}`：稳定性：____；差异轮次/原因：____")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_blocked_live_report(
    output_dir: Path,
    specs: Sequence[Mapping[str, Any]],
    pack_sets: Mapping[str, Mapping[str, Mapping[str, Any]]],
    reasons: Sequence[str],
    *,
    rounds: int,
    traffic: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Persist one explicit blocked row per requested PR/round.

    A missing token or unavailable runtime is a preflight result, not a model
    answer.  Keeping rows in the same files used by a successful run makes the
    one-round smoke report auditable without fabricating assistant text or a
    score.
    """
    rows: list[dict[str, Any]] = []
    blocked = [str(item) for item in reasons if str(item)] or ["live_runtime_not_enabled"]
    for spec in specs:
        source_case_id = str(spec["case_id"])
        pack_set = pack_sets.get(source_case_id, {})
        prepared_refs = [
            {
                "pack_kind": kind,
                "evidence_id": item.get("evidence_id"),
                "projection_hash": item.get("projection_hash"),
                "synthetic": bool(item.get("synthetic")),
                "imported": False,
            }
            for kind, item in pack_set.items()
        ]
        for round_no in range(1, max(1, int(rounds)) + 1):
            rows.append({
                "schema": "mini-drop.github-pr.manual-round-result.v1",
                "round_id": f"{source_case_id}:round-{round_no}",
                "case_id": source_case_id,
                "round": round_no,
                "status": "blocked",
                "blocked_reasons": blocked,
                "prepared_evidence_refs": prepared_refs,
                "request_summary": {
                    "raw_pack_sent": False,
                    "model_turn_sent": False,
                },
                "assistant_visible_text": "",
                "model_attempts": None,
                "manual_score": None,
                "manual_score_fields": {
                    "mechanism_attribution_0_4": None,
                    "evidence_citation_0_3": None,
                    "counterevidence_uncertainty_0_2": None,
                    "impact_boundary_0_1": None,
                    "reviewer_notes": "",
                },
                "note": "未发送模型回合；这是运行前置条件阻塞，不是模型答案。",
            })
    write_round_jsonl(output_dir / "round-results.jsonl", rows)
    write_manual_ledger(output_dir / "manual-scoring.zh-CN.md", rows, rounds)
    summary = {
        "schema": "mini-drop.github-pr.live-summary.v1",
        "generated_at": utc_now(),
        "case_count": len(specs),
        "round_count": len(rows),
        "expected_round_count": len(specs) * max(1, int(rounds)),
        "completed_rounds": 0,
        "automatic_score": None,
        "manual_scoring_required": False,
        "real_ai_score": None,
        "blocked_reasons": blocked,
        "traffic": dict(traffic or {}),
        "notes": [
            "No model turn was sent because preflight prerequisites were unavailable.",
            "Prepared projection references are local-only and were not imported.",
            "No automatic score is calculated.",
        ],
    }
    write_json(output_dir / "summary.json", summary)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, help="Preparation report containing packs/.")
    parser.add_argument("--output-dir", help="Live report directory; defaults to <input-dir>/live.")
    parser.add_argument("--cases", help="Comma-separated case IDs or PR numbers; default all nine.")
    parser.add_argument("--rounds", type=int, default=1, help="Rounds per PR (default 1 smoke; formal 9x3 uses 3).")
    parser.add_argument("--control-url", default=os.getenv("MINI_DROP_CONTROL_URL", "http://127.0.0.1:8191"))
    parser.add_argument(
        "--pi-runtime-url",
        default=os.getenv("MINI_DROP_PI_RUNTIME_URL", "http://127.0.0.1:8899"),
        help="Pi sidecar URL used only for the health probe (default: MINI_DROP_PI_RUNTIME_URL or localhost:8899).",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP request timeout.")
    parser.add_argument("--turn-timeout", type=float, default=300.0, help="Maximum wait for one asynchronous turn.")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Event polling interval.")
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--eval-token-env", default="MINI_DROP_EVAL_IMPORT_TOKEN")
    parser.add_argument("--internal-token-env", default="MINI_DROP_PI_INTERNAL_TOKEN")
    parser.add_argument(
        "--require-provider-config",
        action="store_true",
        help="Require a locally visible provider credential/catalog during preflight (normally the sidecar owns it).",
    )
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate verification only for a private test endpoint.")
    parser.add_argument("--prepare-only", action="store_true", help="Write projection-only manifest; do not contact Control.")
    parser.add_argument("--max-cases", type=int, help="Optional cap for a smoke run.")
    return parser


def selected_specs(raw: Optional[str]) -> list[dict[str, Any]]:
    if not raw:
        return [dict(item) for item in CASE_SPECS]
    wanted = {part.strip().lower() for part in raw.split(",") if part.strip()}
    selected = [dict(item) for item in CASE_SPECS if str(item.get("case_id", "")).lower() in wanted or str(item.get("number", "")).lower() in wanted]
    unknown = wanted - {str(item.get("case_id", "")).lower() for item in selected} - {str(item.get("number", "")).lower() for item in selected}
    if unknown:
        raise ValueError(f"unknown cases: {', '.join(sorted(unknown))}")
    return selected


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rounds < 1 or args.rounds > 10:
        raise SystemExit("--rounds must be between 1 and 10")
    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_dir / "live"
    output_dir.mkdir(parents=True, exist_ok=True)
    specs = selected_specs(args.cases)
    if args.max_cases:
        specs = specs[: max(1, args.max_cases)]

    pack_root = input_dir / "packs"
    all_packs: dict[str, dict[str, dict[str, Any]]] = {}
    projection_manifest: dict[str, Any] = {
        "schema": "mini-drop.github-pr.projection-only-manifest.v1",
        "generated_at": utc_now(),
        "raw_packs_sent_per_round": False,
        "cases": {},
    }
    for spec in specs:
        case_id = str(spec["case_id"])
        pack_set = load_pack_set(pack_root, case_id)
        all_packs[case_id] = pack_set
        projection_manifest["cases"][case_id] = {
            kind: {
                "evidence_id": item["evidence_id"],
                "projection_hash": item["projection_hash"],
                "pack_bytes": item["pack_bytes"],
                "projected_bytes": item["projected_bytes"],
                "synthetic": item["synthetic"],
            }
            for kind, item in pack_set.items()
        }
        case_projection_dir = output_dir / "projection-only" / slug(case_id)
        for kind, item in pack_set.items():
            write_json(case_projection_dir / f"{kind}.json", item["projection"])
    write_json(output_dir / "projection-manifest.json", projection_manifest)
    if args.prepare_only:
        print(json.dumps({"status": "prepared", "output_dir": str(output_dir), "case_count": len(specs)}, ensure_ascii=False))
        return 0

    api_key = os.getenv(args.api_key_env, "")
    eval_token = os.getenv(args.eval_token_env, "")
    internal_token = os.getenv(args.internal_token_env, "")
    state_path = output_dir / "live-state.json"
    state = read_state(state_path)
    traffic_path = output_dir / "traffic.jsonl"
    client = ControlClient(
        args.control_url,
        api_key=api_key,
        eval_token=eval_token,
        internal_token=internal_token,
        timeout=max(1.0, args.timeout),
        insecure=args.insecure,
        traffic_path=traffic_path,
    )
    sidecar_client = ControlClient(
        args.pi_runtime_url,
        api_key=api_key,
        internal_token=internal_token,
        timeout=min(max(1.0, args.timeout), 10.0),
        insecure=args.insecure,
        traffic_path=traffic_path,
    )
    pending_import_keys = pending_imports(all_packs, state)
    preflight: dict[str, Any] = {
        "schema": "mini-drop.github-pr.live-preflight.v1",
        "captured_at": utc_now(),
        "control_url": args.control_url,
        "pi_runtime_url": args.pi_runtime_url,
        "control": None,
        "sidecar": None,
        "key_configured": bool(api_key),
        "eval_import_token_configured": bool(eval_token),
        "eval_import_token_required": bool(pending_import_keys),
        "pending_imports": pending_import_keys,
        "internal_token_configured": bool(internal_token),
        "provider_catalog_configured": bool(
            os.getenv("MINI_DROP_PI_MODELS_PATH", "").strip()
            or os.getenv("MINI_DROP_PI_AUTH_PATH", "").strip()
        ),
        "provider_config_required": bool(
            args.require_provider_config
            or os.getenv("MINI_DROP_EVAL_REQUIRE_PROVIDER_KEY", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        ),
        "blocked_reasons": [],
    }
    # The import token is deliberately short-lived.  It is required for the
    # first run (or after a projection/hash changes), but not for later rounds
    # that reuse receipts already recorded in live-state.json.
    if pending_import_keys and not eval_token:
        preflight["blocked_reasons"].append("evaluation_import_token_missing")
    api_auth_enabled = os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    # /api/livez is intentionally unauthenticated, so a missing API key would
    # otherwise only appear after the first Case mutation. Surface it during
    # preflight without ever persisting the secret itself.
    if api_auth_enabled and not api_key:
        preflight["blocked_reasons"].append("control_api_key_missing")
    try:
        livez = client.request("GET", "/api/livez", phase="preflight_control")
        preflight["control"] = redact(livez)
    except ApiError as exc:
        preflight["control"] = {"status": "unreachable", "error": str(exc)}
        preflight["blocked_reasons"].append("control_plane_unavailable")
    try:
        sidecar = sidecar_client.request(
            "GET", "/internal/runtime/v1/health", phase="preflight_sidecar", internal=True,
        )
        preflight["sidecar"] = redact(sidecar)
    except ApiError as exc:
        preflight["sidecar"] = {"status": "unreachable", "error": str(exc)}
        preflight["blocked_reasons"].append("pi_runtime_unavailable")
    if not internal_token:
        preflight["blocked_reasons"].append("pi_internal_token_missing")
    provider_key_present = any(
        os.getenv(name, "").strip()
        for name in ("MINI_DROP_AI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    )
    preflight["provider_key_configured"] = provider_key_present
    if (
        preflight["provider_config_required"]
        and not provider_key_present
        and not preflight["provider_catalog_configured"]
    ):
        preflight["blocked_reasons"].append("provider_key_missing")
    write_json(output_dir / "preflight.json", preflight)
    if preflight["blocked_reasons"]:
        write_json(state_path, state)
        preflight_transfers = [*client.transfers, *sidecar_client.transfers]
        blocked_rows = write_blocked_live_report(
            output_dir,
            specs,
            all_packs,
            preflight["blocked_reasons"],
            rounds=args.rounds,
            traffic={
                "request_bytes": sum(item.request_bytes for item in preflight_transfers),
                "response_bytes": sum(item.response_bytes for item in preflight_transfers),
                "transfer_count": len(preflight_transfers),
                "control_request_bytes": sum(item.request_bytes for item in client.transfers),
                "control_response_bytes": sum(item.response_bytes for item in client.transfers),
                "sidecar_request_bytes": sum(item.request_bytes for item in sidecar_client.transfers),
                "sidecar_response_bytes": sum(item.response_bytes for item in sidecar_client.transfers),
            },
        )
        print(json.dumps({
            "status": "blocked",
            "reasons": preflight["blocked_reasons"],
            "case_count": len(specs),
            "round_count": len(blocked_rows),
            "report_dir": str(output_dir),
        }, ensure_ascii=False))
        return 2

    rows: list[dict[str, Any]] = []
    for spec in specs:
        source_case_id = str(spec["case_id"])
        try:
            control_case_id, reused = ensure_case(client, spec, state)
            bound_pack_set, imported = import_packs_once(
                client,
                control_case_id,
                all_packs[source_case_id],
                state,
                source_case_id,
            )
            # Keep the in-memory pack mapping aligned with the durable Case;
            # round prompts and evidence_refs must never use preparation IDs.
            all_packs[source_case_id] = bound_pack_set
            projection_manifest["cases"][source_case_id]["control_case_id"] = control_case_id
            projection_manifest["cases"][source_case_id]["bound"] = {
                kind: {
                    "evidence_id": item["evidence_id"],
                    "projection_hash": item["projection_hash"],
                    "projected_bytes": item["projected_bytes"],
                }
                for kind, item in bound_pack_set.items()
            }
            write_json(output_dir / "projection-manifest.json", projection_manifest)
            state["cases"][source_case_id]["control_case_id"] = control_case_id
            state["cases"][source_case_id]["reused"] = reused
            state["cases"][source_case_id]["imported"] = imported
            write_state(state_path, state)
            for round_no in range(1, args.rounds + 1):
                round_key = f"{source_case_id}:round-{round_no}"
                previous = state["rounds"].get(round_key)
                expected_ids = {item["evidence_id"] for item in bound_pack_set.values()}
                previous_ids = {
                    str(item.get("evidence_id") or "")
                    for item in (previous or {}).get("evidence_refs", [])
                    if isinstance(item, Mapping)
                } if isinstance(previous, Mapping) else set()
                if (
                    isinstance(previous, dict)
                    and previous.get("status") == "completed"
                    and previous.get("assistant_visible_text")
                    and expected_ids.issubset(previous_ids)
                ):
                    rows.append(previous)
                    continue
                try:
                    row = run_round(
                        client,
                        spec,
                        bound_pack_set,
                        control_case_id,
                        round_no,
                        args.rounds,
                        timeout=max(5.0, args.turn_timeout),
                        poll_interval=max(0.1, args.poll_interval),
                    )
                except ApiError as exc:
                    row = {
                        "schema": "mini-drop.github-pr.manual-round-result.v1",
                        "round_id": round_key,
                        "case_id": source_case_id,
                        "control_case_id": control_case_id,
                        "round": round_no,
                        "status": "blocked",
                        "blocked_reason": redact(str(exc)),
                        "manual_score": None,
                    }
                state["rounds"][round_key] = row
                rows.append(row)
                write_state(state_path, state)
        except (ApiError, OSError, ValueError, KeyError) as exc:
            for round_no in range(1, args.rounds + 1):
                rows.append({
                    "schema": "mini-drop.github-pr.manual-round-result.v1",
                    "round_id": f"{source_case_id}:round-{round_no}",
                    "case_id": source_case_id,
                    "round": round_no,
                    "status": "blocked",
                    "blocked_reason": redact(str(exc)),
                    "manual_score": None,
                })
            continue

    write_round_jsonl(output_dir / "round-results.jsonl", rows)
    write_manual_ledger(output_dir / "manual-scoring.zh-CN.md", rows, args.rounds)
    all_transfers = [*client.transfers, *sidecar_client.transfers]
    summary = {
        "schema": "mini-drop.github-pr.live-summary.v1",
        "generated_at": utc_now(),
        "case_count": len(specs),
        "round_count": len(rows),
        "expected_round_count": len(specs) * args.rounds,
        "completed_rounds": sum(1 for row in rows if row.get("status") == "completed"),
        "automatic_score": None,
        "manual_scoring_required": True,
        "traffic": {
            "request_bytes": sum(item.request_bytes for item in all_transfers),
            "response_bytes": sum(item.response_bytes for item in all_transfers),
            "transfer_count": len(all_transfers),
            "control_request_bytes": sum(item.request_bytes for item in client.transfers),
            "control_response_bytes": sum(item.response_bytes for item in client.transfers),
            "sidecar_request_bytes": sum(item.request_bytes for item in sidecar_client.transfers),
            "sidecar_response_bytes": sum(item.response_bytes for item in sidecar_client.transfers),
            "import_requests": sum(1 for item in all_transfers if item.phase == "import_projection"),
            "round_requests": sum(1 for item in all_transfers if item.phase == "agent_turn"),
        },
        "notes": [
            "Projection import is one-time per pack and is reused across all rounds.",
            "Raw GitHub packs are never sent in an agent turn.",
            "No collector, artifact upload, MCP query, or automatic score is performed by this runner.",
        ],
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if len(rows) == len(specs) * args.rounds else 2


if __name__ == "__main__":
    raise SystemExit(main())
