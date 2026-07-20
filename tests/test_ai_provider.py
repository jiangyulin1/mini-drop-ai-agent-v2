"""AI provider configuration tests."""

from server.app.ai_provider import _chat_url, get_ai_settings, is_feature_enabled


def test_ai_defaults_use_current_deepseek_flash(monkeypatch):
    for name in (
        "MINI_DROP_AI_PROVIDER",
        "DEEPSEEK_PROVIDER",
        "MINI_DROP_AI_BASE_URL",
        "DEEPSEEK_API_BASE",
        "MINI_DROP_AI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MINI_DROP_AI_MODEL",
        "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = get_ai_settings()
    assert settings.provider == "deepseek"
    assert settings.base_url == "https://api.deepseek.com"
    assert settings.model == "deepseek-v4-flash"
    assert _chat_url(settings.base_url) == "https://api.deepseek.com/v1/chat/completions"


def test_ai_mode_none_disables_all(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    settings = get_ai_settings()
    assert settings.nlp_enabled is False
    assert settings.rca_enabled is False
    assert settings.summarize_enabled is False
    assert is_feature_enabled("nlp") is False


def test_ai_mode_nlp_only(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "nlp-only")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    assert is_feature_enabled("nlp") is True
    assert is_feature_enabled("rca") is False
    assert is_feature_enabled("summarize") is False


def test_ai_custom_provider_env(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_PROVIDER", "openai-compatible")
    monkeypatch.setenv("MINI_DROP_AI_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "key")
    monkeypatch.setenv("MINI_DROP_AI_MODEL", "custom-model")
    settings = get_ai_settings()
    assert settings.provider == "openai-compatible"
    assert settings.base_url == "https://llm.example.com/v1"
    assert settings.model == "custom-model"
