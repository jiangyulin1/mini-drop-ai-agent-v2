import json
from pathlib import Path

import pytest

from scripts.aggregate_collector_agent_eval import aggregate, wilson
from scripts.capture_live_collector_suite import (
    SCENARIOS,
    _actions,
    build,
    canonical_hash,
)


def test_canonical_hash_is_order_independent():
    assert canonical_hash({"a": 1, "b": 2}) == canonical_hash({"b": 2, "a": 1})


def test_build_requires_verified_cleanup(tmp_path: Path):
    paths = []
    for name in SCENARIOS:
        path = tmp_path / f"{name}.json"
        path.write_text(
            json.dumps(
                {"scenario": name, "capture_integrity": {"cleanup_verified": False}}
            )
        )
        paths.append(path)
    with pytest.raises(ValueError, match="cleanups"):
        build(paths, tmp_path / "suite")


def test_wilson_and_empty_aggregate():
    interval = wilson(3, 3)
    assert interval is not None and interval[0] < 1 and interval[1] == 1
    result = aggregate([], {})
    assert result["arms"] == {}
    assert result["paired"] == {}


def test_equivalent_collection_paths_are_preserved():
    actions = _actions(
        [["sys_metrics", "log_scan"], ["sys_metrics", "connection_probe"]], True
    )
    assert actions["after:sys_metrics"] == [["log_scan"], ["connection_probe"]]
    assert actions["after:log_scan,sys_metrics"] == [["ABSTAIN"]]
