const TITLE_PATTERN = /^(.*) \(([0-9][0-9,]*) samples?, ([0-9]+(?:\.[0-9]+)?)%\)$/i;
const RAW_ADDRESS = /^0x[0-9a-f]+(?:\s|$)/i;
const THREAD_WRAPPER = /^(?:_bootstrap|_bootstrap_inner|run) \(threading\.py:/i;

function isNavigationOrRuntimeFrame(name) {
  return ["all", "root"].includes(name.toLowerCase())
    || RAW_ADDRESS.test(name)
    || THREAD_WRAPPER.test(name);
}

export function extractTopFunctionsFromSvg(svgText, limit = 20) {
  if (!svgText || limit <= 0 || typeof DOMParser === "undefined") return [];

  const documentNode = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const totals = new Map();
  documentNode.querySelectorAll("title").forEach((node) => {
    const match = (node.textContent || "").trim().match(TITLE_PATTERN);
    if (!match) return;
    const name = match[1].replace(/\s+/g, " ").trim();
    const samples = Number(match[2].replaceAll(",", ""));
    const percent = Number(match[3]);
    if (
      !name
      || isNavigationOrRuntimeFrame(name)
      || !Number.isFinite(samples)
      || !Number.isFinite(percent)
    ) return;
    const previous = totals.get(name) || { samples: 0, percent: 0 };
    totals.set(name, {
      samples: previous.samples + samples,
      percent: previous.percent + percent,
    });
  });

  return [...totals.entries()]
    .sort(([leftName, left], [rightName, right]) => (
      right.samples - left.samples || leftName.localeCompare(rightName)
    ))
    .slice(0, limit)
    .map(([name, values]) => ({
      name,
      samples: values.samples,
      percent: Math.round(values.percent * 100) / 100,
      source: "flamegraph_svg",
    }));
}

function percentValue(value) {
  return Number(String(value || "").replace("%", ""));
}

export function extractFlamegraphTreeFromSvg(svgText) {
  if (!svgText || typeof DOMParser === "undefined") return null;

  const documentNode = new DOMParser().parseFromString(svgText, "image/svg+xml");
  const frames = [];
  documentNode.querySelectorAll("g").forEach((group) => {
    const title = group.querySelector(":scope > title")?.textContent?.trim() || "";
    const rect = group.querySelector(":scope > rect");
    const match = title.match(TITLE_PATTERN);
    if (!match || !rect) return;
    const frame = {
      name: match[1].replace(/\s+/g, " ").trim(),
      value: Number(match[2].replaceAll(",", "")),
      x: percentValue(rect.getAttribute("x")),
      y: Number(rect.getAttribute("y")),
      width: percentValue(rect.getAttribute("width")),
      children: [],
    };
    if (
      !frame.name
      || !Number.isFinite(frame.value)
      || !Number.isFinite(frame.x)
      || !Number.isFinite(frame.y)
      || !Number.isFinite(frame.width)
    ) return;
    frames.push(frame);
  });
  if (frames.length === 0) return null;

  frames.sort((left, right) => (
    left.y - right.y || left.x - right.x || right.width - left.width
  ));
  const root = frames.reduce((best, frame) => (
    !best || frame.y < best.y || (frame.y === best.y && frame.width > best.width)
      ? frame
      : best
  ), null);
  const attached = new Set([root]);
  const epsilon = 0.08;

  frames.forEach((frame) => {
    if (frame === root) return;
    const frameRight = frame.x + frame.width;
    const parent = frames
      .filter((candidate) => (
        candidate !== frame
        && candidate.y < frame.y
        && candidate.x <= frame.x + epsilon
        && candidate.x + candidate.width >= frameRight - epsilon
      ))
      .sort((left, right) => right.y - left.y || left.width - right.width)[0];
    if (!parent) return;
    parent.children.push(frame);
    attached.add(frame);
  });

  const clean = (frame) => {
    const node = { name: frame.name, value: frame.value };
    const children = frame.children
      .filter((child) => attached.has(child))
      .sort((left, right) => left.x - right.x)
      .map(clean);
    if (children.length > 0) node.children = children;
    return node;
  };
  return clean(root);
}
