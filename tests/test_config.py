from pathlib import Path

import pytest

from bulario_service.config import (
    load_local_env,
    load_settings,
    normalize_database_url,
)


def test_load_settings_uses_local_environment_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BULARIO_STORAGE_ROOT", raising=False)

    settings = load_settings(env_file=tmp_path / ".env")

    assert settings.app_env == "local"
    assert settings.database_url is None
    assert settings.storage_root == Path("storage")


def test_load_settings_reads_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@db:5432/intelireg",
    )

    settings = load_settings(env_file=tmp_path / ".env")

    assert settings.app_env == "test"
    assert (
        settings.database_url
        == "postgresql+psycopg://user:password@db:5432/intelireg"
    )


def test_load_settings_reads_dotenv_when_environment_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\n"
        "DATABASE_URL="
        "postgresql+psycopg://intelireg:intelireg_local@localhost:5433/intelireg\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.app_env == "local"
    assert settings.database_url == (
        "postgresql+psycopg://intelireg:intelireg_local@localhost:5433/intelireg"
    )


def test_process_environment_has_precedence_over_dotenv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://ci:ci@db-ci:5432/intelireg",
    )

    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=local\n"
        "DATABASE_URL="
        "postgresql+psycopg://local:local@localhost:5433/intelireg\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.app_env == "ci"
    assert settings.database_url == (
        "postgresql+psycopg://ci:ci@db-ci:5432/intelireg"
    )


def test_load_local_env_supports_comments_blank_lines_export_and_quotes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "export APP_ENV='local'\n"
        'DATABASE_URL="postgresql+psycopg://user:pass@localhost:5433/intelireg"\n',
        encoding="utf-8",
    )

    assert load_local_env(env_file) is True
    assert (
        load_settings(env_file=env_file).database_url
        == "postgresql+psycopg://user:pass@localhost:5433/intelireg"
    )


def test_load_local_env_returns_false_when_file_is_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert load_local_env(tmp_path / ".env") is False


def test_load_local_env_rejects_invalid_entry(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("INVALID LINE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid .env entry"):
        load_local_env(env_file)


def test_load_local_env_rejects_invalid_key(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("1INVALID=value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid .env key"):
        load_local_env(env_file)


def test_normalize_database_url_upgrades_legacy_postgresql_scheme() -> None:
    assert (
        normalize_database_url("postgresql://user:password@db:5432/intelireg")
        == "postgresql+psycopg://user:password@db:5432/intelireg"
    )


def test_normalize_database_url_preserves_psycopg_scheme() -> None:
    database_url = "postgresql+psycopg://user:password@db:5432/intelireg"

    assert normalize_database_url(database_url) == database_url



def test_load_settings_reads_storage_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(
        "BULARIO_STORAGE_ROOT",
        "../intelireg-data/bulas",
    )

    settings = load_settings(env_file=tmp_path / ".env")

    assert settings.storage_root == Path(
        "../intelireg-data/bulas"
    )
