export function parseTaskArtifactReference(value) {
  const match = String(value || "").match(/^task:([^:]+)(?::artifact:([^:]+))?$/);
  if (!match) return null;
  return {
    taskId: match[1],
    artifactType: match[2] || "",
  };
}

export function evidenceArtifactTarget(evidence = {}) {
  const item = evidence || {};
  return parseTaskArtifactReference(item.raw_artifact_ref)
    || parseTaskArtifactReference(item.derived_artifact_ref);
}

export function formatArtifactSize(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "未知大小";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
