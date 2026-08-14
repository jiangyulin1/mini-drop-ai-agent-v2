"""EvidenceAttachment: unify data entry onto ResourceRef + Attachment (E1).

Replaces the split between initial_task_ids / source_task_id /
target_scope.evidence_task_ids / source_collection_ids.  A Task, a Collection
batch or a conversation `@` reference becomes one tenant-scoped attachment row;
only ACCEPTED attachments feed the next diagnosis.

Statuses follow plan section 5.2:
PENDING_VALIDATION / ACCEPTED / PARTIAL / REJECTED_SCOPE / REJECTED_TIME /
REJECTED_QUALITY / EXCLUDED_BY_USER / SUPERSEDED
"""

from __future__ import annotations

from typing import Any, Optional

from server.app.diagnosis.reference_resolver import ReferenceResolver, ResourceRef

# 兼容旧前端：target_scope.evidence_task_ids 里的 Task 自动投影为 Attachment
LEGACY_EVIDENCE_TASK_IDS_KEY = "evidence_task_ids"
LEGACY_COLLECTION_IDS_KEY = "source_collection_ids"

ATTACHMENT_SOURCE_MAP = {
    "user_mention": "user_mention",
    "from_task": "from_task",
    "collection_batch": "collection_batch",
    "legacy_backfill": "legacy_backfill",
}


def _attachment_id(case_id: str, ref: ResourceRef) -> str:
    return f"attach-{case_id}-{ref.type}-{ref.id}"


class EvidenceAttachmentService:
    def __init__(self, repository: Any, resolver: ReferenceResolver | None = None):
        self._repo = repository
        self._resolver = resolver or ReferenceResolver(repository)

    # ── create / backfill ──────────────────────────────────────────────
    def attach_resources(
        self,
        case: dict[str, Any],
        tenant_id: str,
        references: list[ResourceRef],
        *,
        actor_id: str,
        purpose: str | None = None,
        source: str = "user_mention",
    ) -> list[dict[str, Any]]:
        case_id = case["case_id"]
        results: list[dict[str, Any]] = []
        for ref in references:
            ref = ref.model_copy(update={"source": source})
            resolved = self._resolver.resolve(ref, tenant_id, case=case)
            existing = self._find_attachment(case_id, tenant_id, ref.type, ref.id)
            if existing is not None and existing.get("status") in {
                "ACCEPTED", "PARTIAL", "PENDING_VALIDATION",
            }:
                results.append({
                    "ref": ref.model_dump(mode="json"),
                    "result": "DUPLICATE_SKIPPED",
                    "attachment_id": existing.get("attachment_id"),
                    "detail": "该资源已关联，跳过重复绑定",
                })
                continue
            status = "ACCEPTED" if resolved.eligible else "REJECTED_QUALITY"
            reason = None if resolved.eligible else resolved.reason_code
            attachment = self._repo.upsert_case_attachment(
                case_id=case_id,
                tenant_id=tenant_id,
                payload={
                    "attachment_id": _attachment_id(case_id, ref),
                    "resource_type": ref.type,
                    "resource_id": ref.id,
                    "resource_revision": ref.revision,
                    "label": resolved.label or ref.label or ref.id,
                    "source": source,
                    "purpose": purpose,
                    "attached_by": actor_id,
                    "status": status,
                    "scope_match": "MATCH" if resolved.eligible else "MISMATCH",
                    "time_match": "UNKNOWN",
                    "freshness": "UNKNOWN",
                    "quality": "COMPLETE" if resolved.eligible else "INCOMPLETE",
                    "evidence_ids": resolved.evidence_ids,
                    "rejection_reason": reason,
                    "supersedes": [],
                },
            )
            results.append({
                "ref": ref.model_dump(mode="json"),
                "result": status if resolved.eligible else "REJECTED",
                "attachment_id": attachment.get("attachment_id"),
                "detail": resolved.label,
                "rejection_reason": reason,
            })
            # 集合展开：每个成员 Task 也建立独立 Attachment，
            # active_task_ids 因此只需读取 task 类型附件，无需二次展开。
            if ref.type == "collection":
                for member_id in ref.member_task_ids:
                    member_ref = ResourceRef(type="task", id=str(member_id), source=source)
                    if self._find_attachment(case_id, tenant_id, "task", str(member_id)) is not None:
                        continue
                    member_resolved = self._resolver.resolve(member_ref, tenant_id, case=case)
                    member_attachment = self._repo.upsert_case_attachment(
                        case_id=case_id,
                        tenant_id=tenant_id,
                        payload={
                            "attachment_id": _attachment_id(case_id, member_ref),
                            "resource_type": "task",
                            "resource_id": str(member_id),
                            "resource_revision": None,
                            "label": member_resolved.label or str(member_id),
                            "source": source,
                            "purpose": purpose or "集合成员展开",
                            "attached_by": actor_id,
                            "status": "ACCEPTED" if member_resolved.eligible else "REJECTED_QUALITY",
                            "scope_match": "MATCH" if member_resolved.eligible else "MISMATCH",
                            "time_match": "UNKNOWN",
                            "freshness": "UNKNOWN",
                            "quality": "COMPLETE" if member_resolved.eligible else "INCOMPLETE",
                            "evidence_ids": member_resolved.evidence_ids,
                            "rejection_reason": None if member_resolved.eligible else member_resolved.reason_code,
                            "supersedes": [],
                        },
                    )
                    results.append({
                        "ref": member_ref.model_dump(mode="json"),
                        "result": "ACCEPTED" if member_resolved.eligible else "REJECTED",
                        "attachment_id": member_attachment.get("attachment_id"),
                        "detail": f"集合成员 {member_id}",
                        "rejection_reason": member_resolved.reason_code,
                    })
        return results

    def backfill_legacy_target_scope(
        self,
        case: dict[str, Any],
        tenant_id: str,
        *,
        actor_id: str,
    ) -> list[dict[str, Any]]:
        """投影 target_scope.evidence_task_ids / source_collection_ids 为 Attachment。

        兼容期写入规则（plan 5.2）：不再向 target_scope 写 evidence_task_ids；
        此处把旧字段一次性转成附件，随后诊断消费附件而非旧字段。
        """
        scope = case.get("target_scope") or {}
        evidence_task_ids = list(dict.fromkeys(scope.get(LEGACY_EVIDENCE_TASK_IDS_KEY) or []))
        collection_ids = list(dict.fromkeys(scope.get(LEGACY_COLLECTION_IDS_KEY) or []))
        refs: list[ResourceRef] = [
            ResourceRef(type="task", id=str(task_id), source="legacy_backfill")
            for task_id in evidence_task_ids
        ] + [
            ResourceRef(type="collection", id=str(cid), source="legacy_backfill")
            for cid in collection_ids
        ]
        if not refs:
            return []
        return self.attach_resources(
            case, tenant_id, refs, actor_id=actor_id,
            purpose="兼容迁移：target_scope 旧字段投影",
            source="legacy_backfill",
        )

    # ── read ───────────────────────────────────────────────────────────
    def list_attachments(self, case_id: str, tenant_id: str) -> list[dict[str, Any]]:
        return self._repo.list_case_attachments(case_id, tenant_id)

    def active_task_ids(self, case_id: str, tenant_id: str) -> list[str]:
        """诊断消费入口：ACCEPTED 的 task 类型附件（含 collection 展开成员）。"""
        task_ids: list[str] = []
        for attachment in self._repo.list_case_attachments(case_id, tenant_id):
            if attachment.get("status") not in {"ACCEPTED", "PARTIAL"}:
                continue
            resource_type = attachment.get("resource_ref", {}).get("type") or attachment.get("resource_type")
            if resource_type == "task":
                task_ids.append(str(attachment["resource_ref"]["id"]))
            elif resource_type == "collection":
                task_ids.extend(str(item) for item in (attachment.get("member_task_ids") or []))
        return list(dict.fromkeys(task_ids))

    def exclude(self, attachment_id: str, tenant_id: str, *, actor_id: str, reason: str) -> Optional[dict[str, Any]]:
        attachment = self._repo.get_case_attachment(attachment_id, tenant_id)
        if attachment is None:
            return None
        if attachment.get("status") in {"EXCLUDED_BY_USER", "SUPERSEDED"}:
            return attachment
        return self._repo.update_case_attachment(
            attachment_id,
            tenant_id,
            updates={
                "status": "EXCLUDED_BY_USER",
                "rejection_reason": reason or "excluded_by_user",
            },
        )

    def _find_attachment(
        self, case_id: str, tenant_id: str, resource_type: str, resource_id: str,
    ) -> Optional[dict[str, Any]]:
        for attachment in self._repo.list_case_attachments(case_id, tenant_id):
            ref = attachment.get("resource_ref") or {}
            if ref.get("type") == resource_type and str(ref.get("id")) == str(resource_id):
                return attachment
        return None
