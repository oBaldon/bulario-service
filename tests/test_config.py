from bulario_service.config import load_settings


def test_load_settings_uses_local_environment_by_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = load_settings()

    assert settings.app_env == "local"
    assert settings.database_url is None


def test_load_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")

    settings = load_settings()

    assert settings.app_env == "test"
    assert settings.database_url == "postgresql://example"