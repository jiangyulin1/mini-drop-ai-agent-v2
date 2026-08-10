"""Validate Mini-Drop diagnosis testsets against schema and runtime contracts."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jsonschema import Draft7Validator

from server.app.task_kinds import TASK_KINDS


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    schema_path = root / "manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    task_kinds = {item["key"] for item in TASK_KINDS}
    cases = sorted((root / "real").glob("*/cases/*.json"))
    if not cases:
        return ["未发现 testsets/real/*/cases/*.json"]

    seen: set[str] = set()
    for case_path in cases:
        relative = case_path.relative_to(root)
        try:
            case = json.loads(case_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: JSON 无法读取: {exc}")
            continue
        for error in sorted(validator.iter_errors(case), key=lambda item: list(item.path)):
            path = ".".join(str(item) for item in error.path) or "<root>"
            errors.append(f"{relative}:{path}: {error.message}")

        case_id = str(case.get("case_id") or "")
        if case_id in seen:
            errors.append(f"{relative}: case_id 重复: {case_id}")
        seen.add(case_id)
        if case_path.stem != case_id:
            errors.append(f"{relative}: 文件名必须与 case_id 一致")

        system_root = case_path.parent.parent
        fault_type = (case.get("fault") or {}).get("type")
        fault_dir = system_root / "faults" / f"fault-{fault_type}"
        for script_name in ("inject.sh", "revert.sh"):
            script = fault_dir / script_name
            if not script.is_file():
                errors.append(f"{relative}: 缺少故障适配器 {script.relative_to(root)}")
            else:
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True,
                )
                if result.returncode:
                    errors.append(f"{relative}: {script_name} shell 语法错误: {result.stderr.strip()}")

        collectors = (case.get("capture") or {}).get("collectors") or []
        unknown = sorted(set(collectors) - task_kinds)
        if unknown:
            errors.append(f"{relative}: 未注册采集器: {', '.join(unknown)}")

        workload = (case.get("trigger") or {}).get("workload_script")
        if workload and not (system_root / workload).is_file():
            errors.append(f"{relative}: workload_script 不存在: {workload}")

        execution = case.get("execution") or {}
        for field in ("preflight_script", "runner_script"):
            script_path = execution.get(field)
            if not script_path:
                continue
            script = system_root / script_path
            if not script.is_file():
                errors.append(f"{relative}: {field} 不存在: {script_path}")
            else:
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True,
                )
                if result.returncode:
                    errors.append(f"{relative}: {field} shell 语法错误: {result.stderr.strip()}")

        if case.get("status") in {"verified_vm", "regression"}:
            gt_path = system_root / "ground_truth" / f"{case_id}.json"
            if not gt_path.is_file():
                errors.append(f"{relative}: 已验证案例缺少 ground truth: {gt_path.relative_to(root)}")
            score_path = system_root / "runs" / case_id / "score.json"
            if not score_path.is_file():
                errors.append(f"{relative}: 已验证案例缺少评分报告: {score_path.relative_to(root)}")
            else:
                try:
                    score = json.loads(score_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"{relative}: 评分报告无法读取: {exc}")
                else:
                    if score.get("case_id") != case_id:
                        errors.append(f"{relative}: 评分报告 case_id 不匹配")
                    if score.get("eligible_verified_vm") is not True:
                        errors.append(f"{relative}: 评分报告未通过 verified_vm 门禁")
                    repetitions = int(
                        (case.get("performance_requirements") or {}).get("repetitions", 0),
                    )
                    if int(score.get("eligible_repetitions", 0)) < repetitions:
                        errors.append(f"{relative}: 评分报告有效重复次数不足")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default="testsets", type=Path)
    args = parser.parse_args()
    errors = validate(args.root.resolve())
    if errors:
        print(f"Testset validation failed ({len(errors)} errors):")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(2)
    print("Testset validation passed.")


if __name__ == "__main__":
    main()
