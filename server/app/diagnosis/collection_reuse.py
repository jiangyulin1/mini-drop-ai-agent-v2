"""Strict matching for reusing completed collection results.

Collection results are case-wide storage assets, but they are not implicitly
visible to every investigation branch.  A branch may reuse a completed result
only after the deterministic probe identity matches exactly.  The branch's
test/proof contract is recorded by the caller; this module only answers the
physical collection equivalence question.

The fingerprint deliberately includes target incarnation, normalized time
window, collector implementation version, and effective parameters.  A PID or
collector name alone is never enough: both can refer to a different process or
different observation.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


# The canonical form is part of the persisted identity.  Bumping this value
# intentionally makes pre-normalization candidates fail closed instead of
# silently treating them as equivalent to a request built with new rules.
REUSE_SCHEMA_VERSION = "collection-reuse.v2"
REUSABLE_REQUEST_STATUSES = frozenset({"COMPLETED"})
REUSABLE_EVIDENCE_LIFECYCLES = frozenset({"ACTIVE"})
REUSABLE_EVIDENCE_TRUST_STATES = frozenset({"TRUSTED", "UNREVIEWED", "LOW_TRUST"})


def _canonical(value: Any) -> Any:
    """Return a JSON-safe, order-independent representation."""
    if isinstance(value, dict):
        return {
            str(key): _canonical(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_canonical(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(values, key=lambda item: json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ))
        return values
    if isinstance(value, float):
        # Avoid fingerprints changing because a transport decoded 1 as 1.0.
        if not math.isfinite(value):
            return str(value)
        rounded = round(value, 9)
        return int(rounded) if rounded.is_integer() else rounded
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _canonical_timestamp(value: Any) -> Any:
    """Normalize equivalent timezone spellings without guessing naive time."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat(timespec="microseconds")
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds",
        ).replace("+00:00", "Z")
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Invalid timestamps remain distinguishable.  Validation at the
        # collection boundary decides whether they are executable.
        return text
    if parsed.tzinfo is None:
        # A naive timestamp has no safe timezone interpretation.  Preserve it
        # instead of making two different local clocks look equal.
        return parsed.isoformat(timespec="microseconds")
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds",
    ).replace("+00:00", "Z")


def canonical_time_window(value: dict[str, Any] | None) -> dict[str, Any]:
    """Canonicalize common window aliases while retaining conflicts.

    The aliases are representation aliases only; they do not widen a window.
    Conflicting aliases are retained under a deterministic marker so they
    cannot accidentally hash to a valid request.
    """
    if not isinstance(value, dict):
        return _canonical(value or {})
    aliases = {
        "window_start": "start", "window_end": "end",
        "from": "start", "to": "end", "timestamp": "observed_at",
    }
    normalized: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    for raw_key in sorted(value, key=lambda item: str(item)):
        key = aliases.get(str(raw_key), str(raw_key))
        item = value[raw_key]
        if key in {"start", "end", "observed_at"}:
            item = _canonical_timestamp(item)
        else:
            item = _canonical(item)
        if key in normalized and normalized[key] != item:
            conflicts.setdefault(key, [normalized[key]]).append(item)
        else:
            normalized[key] = item
    for key, values in conflicts.items():
        normalized[f"_conflicting_{key}"] = values
    return _canonical(normalized)


def canonical_parameter_values(
    parameters: dict[str, Any] | None,
    *,
    parameter_schema: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply declared defaults and representation aliases for probe identity.

    Defaults are taken only from the CollectorSpec schema.  No fuzzy string
    matching is performed, so a typo cannot turn into an equivalent probe.
    ``pid`` is a structural alias for ``target_pid`` because both names are
    already accepted by the target selector contract.
    """
    values = deepcopy(parameters or {})
    if not isinstance(values, dict):
        return _canonical(values)
    schema = parameter_schema or {}
    aliases = schema.get("x_aliases") or schema.get("aliases") or {}
    if not isinstance(aliases, dict):
        aliases = {}
    aliases = {"pid": "target_pid", **{
        str(source): str(destination) for source, destination in aliases.items()
    }}
    for source, destination in aliases.items():
        if source not in values:
            continue
        if destination in values and _canonical(values[destination]) != _canonical(values[source]):
            # Keep both values.  The executable validation layer will reject
            # the conflict rather than allowing a silent winner.
            values[f"_conflicting_{destination}"] = [
                values[destination], values[source],
            ]
        elif destination not in values:
            values[destination] = values[source]
        if source != destination:
            values.pop(source, None)
    properties = schema.get("properties") or {}
    if isinstance(properties, dict):
        for name, rule in properties.items():
            if name not in values and isinstance(rule, dict) and "default" in rule:
                values[name] = deepcopy(rule["default"])
    order_insensitive = {
        "listener_ports",
        *(
            str(item) for item in (schema.get("x_order_insensitive") or [])
            if isinstance(item, (str, int))
        ),
    }
    for name in order_insensitive:
        if isinstance(values.get(name), list):
            values[name] = sorted(
                values[name],
                key=lambda item: json.dumps(
                    _canonical(item), ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
            )
    if target and "target_pid" not in values:
        target_pid = target.get("target_pid") or target.get("pid")
        if target_pid is not None:
            values["target_pid"] = target_pid
    return _canonical(values)


def _target_identity(target: dict[str, Any] | None) -> dict[str, Any]:
    target = target or {}
    # Keep only identity-bearing fields.  Display labels and mutable health
    # fields must not turn an otherwise identical probe into a new identity.
    keys = (
        "agent_id", "target_pid", "pid", "hostname", "entity_id",
        "resource_incarnation", "boot_id", "process_start_time",
        "container_id", "namespace", "target_ref",
    )
    result: dict[str, Any] = {}
    for key in keys:
        value = target.get(key)
        if value is not None and str(value) != "":
            result[key] = value
    if "target_pid" not in result and "pid" in result:
        result["target_pid"] = result.pop("pid")
    elif "target_pid" in result:
        # ``pid`` is a selector alias, not a second identity dimension.  A
        # caller may send both spellings; retaining both would make the same
        # process hash differently depending on transport normalization.
        result.pop("pid", None)
    # target_ref and hostname are useful labels, but are not process
    # incarnation evidence.  If a stable entity/incarnation exists, keeping
    # either label in the physical identity creates needless mismatches after
    # a topology refresh or host rename.
    if any(result.get(key) for key in (
        "entity_id", "resource_incarnation", "boot_id", "process_start_time",
    )):
        result.pop("target_ref", None)
        result.pop("hostname", None)
    return _canonical(result)


def canonical_probe_key_identity(
    *,
    case_id: str,
    tenant_id: str,
    collector_id: str,
    collector_spec_version: str,
    collector_implementation_version: str = "",
    target: dict[str, Any] | None,
    parameters: dict[str, Any] | None,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Identity of the physical probe independent of window/authority rev."""
    return {
        "schema_version": REUSE_SCHEMA_VERSION,
        "case_id": str(case_id),
        "tenant_id": str(tenant_id),
        "collector_id": str(collector_id),
        "collector_spec_version": str(collector_spec_version),
        "collector_implementation_version": str(collector_implementation_version),
        "target": _target_identity(target),
        "parameters": canonical_parameter_values(
            parameters, parameter_schema=parameter_schema, target=target,
        ),
    }


def canonical_probe_identity(
    *,
    case_id: str,
    tenant_id: str,
    collector_id: str,
    collector_spec_version: str,
    collector_implementation_version: str = "",
    target: dict[str, Any] | None,
    parameters: dict[str, Any] | None,
    time_window: dict[str, Any] | None,
    scope_revision: int,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the exact physical observation identity.

    ``branch_id`` and the information goal are intentionally absent.  They
    describe why a branch wants an observation, not whether two observations
    are physically equivalent.  They must be checked by the proof/branch
    layer before this identity is used for reuse.
    """
    return {
        **canonical_probe_key_identity(
            case_id=case_id,
            tenant_id=tenant_id,
            collector_id=collector_id,
            collector_spec_version=collector_spec_version,
            collector_implementation_version=collector_implementation_version,
            target=target,
            parameters=parameters,
            parameter_schema=parameter_schema,
        ),
        "time_window": canonical_time_window(time_window or {}),
        "scope_revision": int(scope_revision),
    }


def probe_fingerprint(**kwargs: Any) -> str:
    identity = canonical_probe_identity(**kwargs)
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def probe_key_fingerprint(**kwargs: Any) -> str:
    """Hash the reusable physical-probe key without window/revision fields."""
    identity = canonical_probe_key_identity(**{
        key: value for key, value in kwargs.items()
        if key not in {"time_window", "scope_revision"}
    })
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def result_fingerprint(
    *,
    probe_fingerprint_value: str,
    projection_hash: str,
    content_hash: str | None = None,
    artifact_schema: str | None = None,
    parser_version: str | None = None,
    completeness: str = "COMPLETE",
) -> str:
    """Fingerprint a concrete result, not just the request to collect it."""
    payload = {
        "schema_version": REUSE_SCHEMA_VERSION,
        "probe_fingerprint": str(probe_fingerprint_value),
        "projection_hash": str(projection_hash),
        "content_hash": str(content_hash or ""),
        "artifact_schema": str(artifact_schema or ""),
        "parser_version": str(parser_version or ""),
        "completeness": str(completeness or "UNKNOWN").upper(),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_reuse_candidate(
    candidate: dict[str, Any],
    *,
    requested_probe_fingerprint: str,
    requested_result_fingerprint: str | None = None,
    allow_low_trust: bool = False,
) -> dict[str, Any]:
    """Return a fail-closed reuse decision for one stored candidate."""
    metadata = candidate.get("reuse_metadata") or candidate.get("validation_result") or {}
    reasons: list[str] = []
    if str(candidate.get("status") or "").upper() not in REUSABLE_REQUEST_STATUSES:
        reasons.append("REQUEST_NOT_COMPLETED")
    if str(metadata.get("probe_fingerprint") or "") != str(requested_probe_fingerprint):
        reasons.append("PROBE_FINGERPRINT_MISMATCH")
    candidate_result = str(metadata.get("result_fingerprint") or "")
    if not candidate_result:
        reasons.append("RESULT_FINGERPRINT_MISSING")
    elif requested_result_fingerprint and candidate_result != str(requested_result_fingerprint):
        reasons.append("RESULT_FINGERPRINT_MISMATCH")
    # A historical row without an explicit governance snapshot is not proof
    # that the Evidence is currently citable.  The supervisor supplies these
    # fields for native rows; standalone/legacy candidates fail closed.
    lifecycle = str(metadata.get("evidence_lifecycle_status") or "UNKNOWN").upper()
    if lifecycle not in REUSABLE_EVIDENCE_LIFECYCLES:
        reasons.append("EVIDENCE_NOT_ACTIVE")
    evidence_status = str(metadata.get("evidence_status") or "UNKNOWN").upper()
    if evidence_status not in {"ACTIVE", "LOW_TRUST"}:
        reasons.append("EVIDENCE_STATUS_NOT_REUSABLE")
    trust_state = str(
        metadata.get("review_trust_state")
        or metadata.get("evidence_review_trust_state")
        or "UNKNOWN"
    ).upper()
    # Review trust is an independent invalidation channel.  A stale producer
    # can leave lifecycle/status as ACTIVE while a human review has excluded
    # or superseded the evidence.  Accept only the documented states and fail
    # closed for new/unknown values.
    if trust_state not in REUSABLE_EVIDENCE_TRUST_STATES:
        reasons.append("EVIDENCE_TRUST_STATE_NOT_REUSABLE")
    if evidence_status == "LOW_TRUST" and trust_state != "LOW_TRUST":
        reasons.append("EVIDENCE_TRUST_STATE_CONFLICT")
    if trust_state == "LOW_TRUST" and not allow_low_trust:
        reasons.append("EVIDENCE_LOW_TRUST_REQUIRES_EXPLICIT_REVIEW")
    if bool(metadata.get("stale_for_current_revision")):
        reasons.append("EVIDENCE_STALE_FOR_CURRENT_REVISION")
    return {
        "reusable": not reasons,
        "reason_codes": reasons,
        "probe_fingerprint": requested_probe_fingerprint,
        "result_fingerprint": candidate_result,
        "trust_state": trust_state,
        "candidate_request_id": candidate.get("collection_request_id"),
        "candidate_task_id": candidate.get("task_id"),
    }


def normalize_probe_request(
    *,
    case_id: str,
    tenant_id: str,
    collector_id: str,
    collector_spec_version: str,
    collector_implementation_version: str = "",
    target: dict[str, Any] | None,
    parameters: dict[str, Any] | None,
    time_window: dict[str, Any] | None,
    scope_revision: int,
    parameter_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one auditable normalized request before reuse matching.

    The returned object is deliberately JSON-safe and can be persisted in a
    proposal or branch-local reuse decision.  It separates the physical key
    (target/collector/parameters) from authority/time fields so future
    eligibility policies can relax coverage without changing the hash schema.
    This function does not authorize a target or make a reuse decision.
    """
    key_identity = canonical_probe_key_identity(
        case_id=case_id,
        tenant_id=tenant_id,
        collector_id=collector_id,
        collector_spec_version=collector_spec_version,
        collector_implementation_version=collector_implementation_version,
        target=target,
        parameters=parameters,
        parameter_schema=parameter_schema,
    )
    identity = canonical_probe_identity(
        case_id=case_id,
        tenant_id=tenant_id,
        collector_id=collector_id,
        collector_spec_version=collector_spec_version,
        collector_implementation_version=collector_implementation_version,
        target=target,
        parameters=parameters,
        time_window=time_window,
        scope_revision=scope_revision,
        parameter_schema=parameter_schema,
    )
    return {
        "schema_version": REUSE_SCHEMA_VERSION,
        "probe_key_identity": key_identity,
        "probe_identity": identity,
        "probe_key": probe_key_fingerprint(
            case_id=case_id,
            tenant_id=tenant_id,
            collector_id=collector_id,
            collector_spec_version=collector_spec_version,
            collector_implementation_version=collector_implementation_version,
            target=target,
            parameters=parameters,
            parameter_schema=parameter_schema,
        ),
        "probe_fingerprint": probe_fingerprint(
            case_id=case_id,
            tenant_id=tenant_id,
            collector_id=collector_id,
            collector_spec_version=collector_spec_version,
            collector_implementation_version=collector_implementation_version,
            target=target,
            parameters=parameters,
            time_window=time_window,
            scope_revision=scope_revision,
            parameter_schema=parameter_schema,
        ),
        "normalized_target": identity["target"],
        "normalized_parameters": identity["parameters"],
        "normalized_time_window": identity["time_window"],
        "scope_revision": identity["scope_revision"],
    }


def _candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    """Read current and legacy candidate shapes without weakening checks."""
    metadata = dict(
        candidate.get("reuse_metadata")
        or candidate.get("validation_result")
        or {}
    )
    for key in (
        "probe_fingerprint", "result_fingerprint", "probe_key",
        "evidence_status", "evidence_lifecycle_status", "review_trust_state",
        "stale_for_current_revision",
    ):
        if key not in metadata and key in candidate:
            metadata[key] = candidate[key]
    return metadata


def check_reuse_constraints(
    request: dict[str, Any],
    candidate: dict[str, Any],
    *,
    allow_low_trust: bool = False,
) -> dict[str, Any]:
    """Run the deterministic hard gate for one candidate.

    This is intentionally independent from scoring.  A high score can never
    override an identity, lifecycle, projection, or review failure.  It also
    accepts the persisted ``reuse_metadata`` shape emitted by the Supervisor
    and the flatter shape used by selection callers.
    """
    metadata = _candidate_metadata(candidate)
    requested_probe = str(
        request.get("probe_fingerprint")
        or request.get("probe_identity", {}).get("probe_fingerprint")
        or ""
    )
    requested_result = request.get("result_fingerprint")
    candidate_probe_key = str(metadata.get("probe_key") or "")
    requested_probe_key = str(request.get("probe_key") or "")
    reasons: list[str] = []
    if requested_probe_key and candidate_probe_key and requested_probe_key != candidate_probe_key:
        reasons.append("PROBE_KEY_MISMATCH")
    decision = evaluate_reuse_candidate(
        {**candidate, "reuse_metadata": metadata},
        requested_probe_fingerprint=requested_probe,
        requested_result_fingerprint=(
            str(requested_result) if requested_result else None
        ),
        allow_low_trust=allow_low_trust,
    )
    reasons.extend(
        code for code in decision["reason_codes"] if code not in reasons
    )
    return {
        "accepted": not reasons,
        "hard_gate": "PASS" if not reasons else "FAIL",
        "reason_codes": reasons,
        "probe_fingerprint": requested_probe,
        "result_fingerprint": str(metadata.get("result_fingerprint") or ""),
        "candidate_request_id": candidate.get("collection_request_id"),
        "candidate_evidence_id": candidate.get("evidence_id"),
    }


def score_reuse_candidate(
    request: dict[str, Any],
    candidate: dict[str, Any],
    *,
    allow_low_trust: bool = False,
) -> dict[str, Any]:
    """Score only candidates that pass the hard gate.

    The score is a deterministic ordering hint, not proof.  Features are
    explicit so an operator can explain why one valid observation was chosen;
    callers should still require a tie/threshold policy before accepting it.
    """
    gate = check_reuse_constraints(
        request, candidate, allow_low_trust=allow_low_trust,
    )
    metadata = _candidate_metadata(candidate)
    trust = str(
        metadata.get("review_trust_state") or candidate.get("trust_state") or "UNKNOWN"
    ).upper()
    freshness = str(
        metadata.get("freshness") or candidate.get("freshness") or "UNKNOWN"
    ).upper()
    completeness = str(
        metadata.get("completeness") or candidate.get("completeness") or "UNKNOWN"
    ).upper()
    trust_score = {"TRUSTED": 1.0, "UNREVIEWED": 0.8, "LOW_TRUST": 0.4}.get(trust, 0.0)
    freshness_score = {
        "FRESH": 1.0, "CURRENT": 1.0, "CURRENT_WINDOW": 1.0,
        "HISTORICAL": 0.65, "STALE": 0.0,
    }.get(freshness, 0.5 if gate["accepted"] else 0.0)
    completeness_score = {"COMPLETE": 1.0, "PARTIAL": 0.5, "UNKNOWN": 0.25}.get(
        completeness, 0.0,
    )
    score = (
        0.45 * trust_score
        + 0.30 * freshness_score
        + 0.25 * completeness_score
        if gate["accepted"] else 0.0
    )
    return {
        **gate,
        "score": round(score, 6),
        "score_features": {
            "trust": trust_score,
            "freshness": freshness_score,
            "completeness": completeness_score,
        },
    }


def select_reuse_candidate(
    request: dict[str, Any],
    candidates: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    allow_low_trust: bool = False,
    min_score: float = 0.0,
    tie_delta: float = 0.03,
) -> dict[str, Any]:
    """Select a candidate only after hard-gating and deterministic scoring."""
    scored = [
        score_reuse_candidate(request, candidate, allow_low_trust=allow_low_trust)
        | {"candidate": candidate}
        for candidate in candidates
    ]
    eligible = [
        item for item in scored
        if item["accepted"] and float(item["score"]) >= float(min_score)
    ]
    eligible.sort(key=lambda item: (
        -float(item["score"]),
        str(item["candidate"].get("collection_request_id") or ""),
        str(item["candidate"].get("evidence_id") or ""),
        str(item["candidate"].get("projection_id") or ""),
    ))
    if not eligible:
        return {
            "decision": "RECOLLECT_REQUIRED",
            "selected": None,
            "candidates": scored,
            "reason_codes": sorted({
                str(code) for item in scored for code in item["reason_codes"]
            }) or ["REUSE_CANDIDATE_NOT_ELIGIBLE"],
        }
    if len(eligible) > 1 and (
        float(eligible[0]["score"]) - float(eligible[1]["score"])
    ) < max(0.0, float(tie_delta)):
        return {
            "decision": "REUSE_AMBIGUOUS",
            "selected": None,
            "candidates": scored,
            "reason_codes": ["REUSE_AMBIGUOUS"],
        }
    return {
        "decision": "REUSED",
        "selected": eligible[0],
        "candidates": scored,
        "reason_codes": [],
    }
