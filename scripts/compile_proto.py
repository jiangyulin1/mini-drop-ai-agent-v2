"""Compile Mini-Drop protobuf contracts on Windows, Linux or macOS."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import grpc_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = PROJECT_ROOT / "proto"
OUTPUT_DIR = PROJECT_ROOT / "server" / "app" / "generated"
PROTO_FILES = [
    "common.proto",
    "init.proto",
    "healthcheck.proto",
    "hotmethod.proto",
    "control.proto",
]


def main() -> None:
    grpc_include = Path(grpc_tools.__file__).resolve().parent / "_proto"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"-I{grpc_include}",
        f"--python_out={OUTPUT_DIR}",
        f"--grpc_python_out={OUTPUT_DIR}",
        *PROTO_FILES,
    ]
    subprocess.run(command, check=True, cwd=PROTO_DIR)

    pattern = re.compile(
        r"^import ([a-zA-Z0-9_]+_pb2) as ([a-zA-Z0-9_]+)$",
        re.MULTILINE,
    )
    for path in OUTPUT_DIR.glob("*_pb2*.py"):
        content = path.read_text(encoding="utf-8")
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(pattern.sub(r"from . import \1 as \2", content))

    with (OUTPUT_DIR / "__init__.py").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            '"""gRPC 自动生成的 Python stub。由 scripts/compile_proto.py 生成，不要手动编辑。"""\n'
        )
    print(f"proto compilation passed: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
