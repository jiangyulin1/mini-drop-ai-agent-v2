"""Fail CI when release artifacts accidentally include common secret files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


PRIVATE_KEY_MARKER = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
FORBIDDEN_TRACKED_NAMES = {".env", "id_rsa", "id_ed25519"}
SUDO_STDIN_PIPE = re.compile(
    r"\b(?:echo|printf)\b"
    r"(?P<input>(?:[^|\r\n]|\\\r?\n){1,500}?)"
    r"\|\s*sudo\b(?P<sudo_args>[^\r\n]*)",
    re.IGNORECASE,
)
SUDO_PASSWORD_OPTION = re.compile(r"(?<!\S)-S(?=\s|$)")
DYNAMIC_SHELL_INPUT = re.compile(
    r"\$(?:\{|\(|[A-Za-z_])|\{[A-Za-z_][^{}\r\n]*\}",
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        Path(item.decode("utf-8"))
        for item in result.stdout.split(b"\0")
        if item
    ]


def contains_plaintext_sudo_stdin(content: str) -> bool:
    """Detect credentials embedded in a command and piped into ``sudo -S``."""
    for match in SUDO_STDIN_PIPE.finditer(content):
        if not SUDO_PASSWORD_OPTION.search(match.group("sudo_args")):
            continue
        if not DYNAMIC_SHELL_INPUT.search(match.group("input")):
            return True
    return False


def text_violations(path: Path, content: str) -> list[str]:
    violations: list[str] = []
    if PRIVATE_KEY_MARKER.search(content):
        violations.append(f"{path}: private key material")
    if contains_plaintext_sudo_stdin(content):
        violations.append(f"{path}: plaintext credential piped to sudo -S")
    return violations


def main() -> int:
    violations: list[str] = []
    for path in tracked_files():
        if path.name in FORBIDDEN_TRACKED_NAMES:
            violations.append(f"{path}: forbidden tracked secret filename")
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        violations.extend(text_violations(path, content))

    if violations:
        print("Repository hygiene check failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
