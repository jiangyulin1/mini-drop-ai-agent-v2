"""Extract structured hotspot data from self-contained flamegraph SVG files."""

from __future__ import annotations

import html
import re
from collections import defaultdict


_TITLE_PATTERN = re.compile(
    r"<title>(.*?) \(([0-9][0-9,]*) samples?, ([0-9]+(?:\.[0-9]+)?)%\)</title>",
    re.IGNORECASE | re.DOTALL,
)
_RAW_ADDRESS = re.compile(r"^0x[0-9a-f]+(?:\s|$)", re.IGNORECASE)
_THREAD_WRAPPER = re.compile(
    r"^(?:_bootstrap|_bootstrap_inner|run) \(threading\.py:",
    re.IGNORECASE,
)


def _is_navigation_or_runtime_frame(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in {"all", "root"}
        or _RAW_ADDRESS.match(name) is not None
        or _THREAD_WRAPPER.match(name) is not None
    )


def extract_top_functions_from_svg(svg_text: str, limit: int = 20) -> list[dict]:
    """Return function-level TopN data embedded in a py-spy/flamegraph SVG.

    Flamegraph SVG titles use the stable form
    ``function (N samples, P%)``. The same frame may occur under several call
    paths, so entries with the same label are aggregated before sorting.
    """

    if not svg_text or limit <= 0:
        return []

    totals: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"samples": 0, "percent": 0.0},
    )
    for raw_name, raw_samples, raw_percent in _TITLE_PATTERN.findall(svg_text):
        name = " ".join(html.unescape(raw_name).split())
        if not name or _is_navigation_or_runtime_frame(name):
            continue
        try:
            samples = int(raw_samples.replace(",", ""))
            percent = float(raw_percent)
        except ValueError:
            continue
        totals[name]["samples"] = int(totals[name]["samples"]) + samples
        totals[name]["percent"] = float(totals[name]["percent"]) + percent

    ranked = sorted(
        totals.items(),
        key=lambda item: (-int(item[1]["samples"]), item[0]),
    )[:limit]
    return [
        {
            "name": name,
            "samples": int(values["samples"]),
            "percent": round(float(values["percent"]), 2),
            "source": "flamegraph_svg",
        }
        for name, values in ranked
    ]
