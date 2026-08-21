from pathlib import Path

import pytest

from scripts.check_repo_hygiene import contains_plaintext_sudo_stdin, text_violations


SUDO = "".join(("su", "do"))


@pytest.mark.parametrize(
    "command",
    [
        f'echo "embedded-password" | {SUDO} -S -E command',
        f"printf '%s\\n' 'embedded-password' | {SUDO} -S command",
        f"echo embedded-password | {SUDO} --prompt='' -S command",
    ],
)
def test_plaintext_pipe_to_sudo_is_rejected(command):
    assert contains_plaintext_sudo_stdin(command) is True
    violations = text_violations(Path("unsafe.sh"), command)
    assert violations == ["unsafe.sh: plaintext credential piped to sudo -S"]
    assert "embedded-password" not in violations[0]


@pytest.mark.parametrize(
    "command",
    [
        'printf \'%s\\n\' "$MINI_DROP_SUDO_PASSWORD" | sudo -S command',
        'printf \'%s\\n\' "${MINI_DROP_SUDO_PASSWORD}" | sudo -S command',
        'f"printf \'%s\\n\' {password} | sudo -S command"',
        'f"printf \'%s\\n\' {shlex.quote(password)} | sudo -S command"',
        'echo "not-a-password" | command-that-is-not-sudo -S',
    ],
)
def test_runtime_injected_sudo_password_is_allowed(command):
    assert contains_plaintext_sudo_stdin(command) is False
