from bulario_service.config import load_settings, normalize_database_url


def test_load_settings_uses_local_environment_by_default(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = load_settings()

    assert settings.app_env == "local"
    assert settings.database_url is None


def test_load_settings_reads_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@db:5432/intelireg",
    )

    settings = load_settings()

    assert settings.app_env == "test"
    assert (
        settings.database_url
        == "postgresql+psycopg://user:password@db:5432/intelireg"
    )


def test_normalize_database_url_upgrades_legacy_postgresql_scheme() -> None:
    assert (
        normalize_database_url("postgresql://user:password@db:5432/intelireg")
        == "postgresql+psycopg://user:password@db:5432/intelireg"
    )


def test_normalize_database_url_preserves_psycopg_scheme() -> None:
    database_url = "postgresql+psycopg://user:password@db:5432/intelireg"

    assert normalize_database_url(database_url) == database_url
