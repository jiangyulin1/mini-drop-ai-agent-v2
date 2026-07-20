"""Tests for the secure DeepSeek configuration helper."""

from __future__ import annotations

import importlib.util
import os
import stat
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "deploy" / "scripts" / "configure_ai_provider.py"
SPEC = importlib.util.spec_from_file_location("configure_ai_provider", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_normalize_model_alias():
    assert MODULE.normalize_model("deepseek_v4_flash") == "deepseek-v4-flash"
    assert MODULE.normalize_model("v4-flash") == "deepseek-v4-flash"


def test_resolve_key_from_source_env_without_printing(tmp_path, monkeypatch):
    for name in MODULE.KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "source.env"
    source.write_text("MINI_DROP_AI_API_KEY=secret-value\n", encoding="utf-8")
    assert MODULE.resolve_api_key(source, prompt=False) == "secret-value"


def test_force_prompt_does_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "must-not-be-read")
    monkeypatch.setattr(MODULE.sys.stdin, "isatty", lambda: False)
    assert MODULE.resolve_api_key(None, force_prompt=True) == ""


def test_update_env_file_preserves_other_values_and_restricts_mode(tmp_path):
    target = tmp_path / "control.env"
    target.write_text("KEEP_ME=yes\nMINI_DROP_AI_ENABLED=off\n", encoding="utf-8")
    MODULE.update_env_file(
        target,
        {
            "MINI_DROP_AI_ENABLED": "full",
            "MINI_DROP_AI_API_KEY": "secret-value",
        },
    )
    content = target.read_text(encoding="utf-8")
    assert "KEEP_ME=yes" in content
    assert "MINI_DROP_AI_ENABLED=full" in content
    assert "MINI_DROP_AI_API_KEY=secret-value" in content
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0


def test_provider_urls():
    assert MODULE.models_url("https://api.deepseek.com") == "https://api.deepseek.com/v1/models"
    assert MODULE.chat_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"
