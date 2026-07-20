#!/usr/bin/env python3
"""Safely configure and verify Mini-Drop's DeepSeek provider.

The API key is read from a source env file, an environment variable, or an
interactive hidden prompt. It is never accepted as a command-line argument and
is never printed. The target env file is replaced atomically.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
KEY_NAMES = (
    "MINI_DROP_AI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_V4_FLASH_API_KEY",
)

AI_VALUES = {
    "MINI_DROP_AI_ENABLED": "full",
    "MINI_DROP_AI_PROVIDER": "deepseek",
    "MINI_DROP_AI_BASE_URL": DEFAULT_BASE_URL,
    "MINI_DROP_AI_MODEL": DEFAULT_MODEL,
    "MINI_DROP_NLP_ENABLED": "true",
    "MINI_DROP_RCA_ENABLED": "true",
    "MINI_DROP_SUMMARIZE_ENABLED": "true",
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def resolve_api_key(
    source_env: Path | None,
    *,
    prompt: bool = True,
    force_prompt: bool = False,
) -> str:
    if force_prompt:
        if not sys.stdin.isatty():
            return ""
        return getpass.getpass("DeepSeek API key (input hidden): ").strip()
    source_values = parse_env(source_env) if source_env else {}
    for name in KEY_NAMES:
        value = os.getenv(name, "").strip() or source_values.get(name, "").strip()
        if value:
            return value
    if prompt and sys.stdin.isatty():
        return getpass.getpass("DeepSeek API key: ").strip()
    return ""


def normalize_model(model: str) -> str:
    normalized = model.strip().lower().replace("_", "-")
    if normalized in {"deepseek-v4-flash", "v4-flash"}:
        return DEFAULT_MODEL
    return model.strip()


def chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def verify_provider(api_key: str, base_url: str, model: str, timeout: int) -> None:
    request = urllib.request.Request(
        models_url(base_url),
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"DeepSeek verification failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DeepSeek verification failed: {type(exc).__name__}") from exc

    model_ids = {item.get("id") for item in payload.get("data", []) if isinstance(item, dict)}
    if model not in model_ids:
        raise RuntimeError(f"configured model is not available: {model}")


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    remaining = dict(updates)
    output: list[str] = []
    for raw in original.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name in remaining:
                output.append(f"{name}={remaining.pop(name)}")
                continue
        output.append(raw)
    if output and output[-1] != "":
        output.append("")
    output.extend(f"{name}={value}" for name, value in remaining.items())
    content = "\n".join(output).rstrip() + "\n"

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-env", type=Path, required=True, help="env file used by Mini-Drop Server")
    parser.add_argument("--source-env", type=Path, help="optional env file containing the API key")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--skip-verify", action="store_true", help="write without calling the provider")
    parser.add_argument("--no-prompt", action="store_true", help="fail instead of asking for a hidden key")
    parser.add_argument(
        "--prompt-key",
        action="store_true",
        help="always request the key with hidden terminal input; ignore key environment variables",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = args.base_url.strip().rstrip("/")
    model = normalize_model(args.model)
    api_key = resolve_api_key(
        args.source_env,
        prompt=not args.no_prompt,
        force_prompt=args.prompt_key,
    )
    if not api_key:
        print(
            "No API key found. Set MINI_DROP_AI_API_KEY, DEEPSEEK_API_KEY, "
            "or DEEPSEEK_V4_FLASH_API_KEY.",
            file=sys.stderr,
        )
        return 2

    if not args.skip_verify:
        verify_provider(api_key, base_url, model, args.timeout)

    updates = dict(AI_VALUES)
    updates["MINI_DROP_AI_BASE_URL"] = base_url
    updates["MINI_DROP_AI_MODEL"] = model
    updates["MINI_DROP_AI_API_KEY"] = api_key
    update_env_file(args.target_env, updates)
    print(f"Configured DeepSeek provider: base_url={base_url}, model={model}, key=present")
    print(f"Chat endpoint: {chat_url(base_url)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
