export const VISUAL_ARTIFACT_TYPES = new Set([
  "flamegraph_json",
  "flamegraph_svg",
  "java_flamegraph_html",
  "top_json",
  "ebpf_metrics",
  "memory_json",
  "sys_metrics",
  "continuous_flamegraph_json",
  "continuous_flamegraph_svg",
  "continuous_top_json",
]);

/** Older API responses omitted availability; treat those as usable. */
export function isArtifactAvailable(artifact) {
  return !artifact?.availability || artifact.availability === "available";
}

export function artifactText(value) {
  if (typeof value === "string") return value;
  return typeof value?.text === "string" ? value.text : "";
}

/**
 * async-profiler measures its canvas while a srcDoc iframe is still being
 * laid out. A percentage width can resolve to zero at that instant and the
 * bundled viewer then pins the canvas to 0px. Viewport units are available
 * during parsing, so this keeps the original viewer interactive and visible.
 */
export function prepareAsyncProfilerHtml(value) {
  const html = artifactText(value);
  if (!html || html.includes('data-mini-drop-profiler-layout="true"')) return html;
  const layoutStyle = '<style data-mini-drop-profiler-layout="true">#canvas{width:100vw!important;max-width:100%!important}</style>';
  return /<\/head>/i.test(html)
    ? html.replace(/<\/head>/i, `${layoutStyle}</head>`)
    : `${layoutStyle}${html}`;
}

export function unavailableVisualArtifacts(artifacts) {
  return (artifacts || []).filter(
    (item) => VISUAL_ARTIFACT_TYPES.has(item?.artifact_type) && !isArtifactAvailable(item),
  );
}
