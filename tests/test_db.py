import pytest

from bulario_service.config import Settings
import bulario_service.db as db_module


def test_database_engine_requires_database_url() -> None:
    settings = Settings(app_env="test", database_url=None)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db_module.create_database_engine(settings)


def test_database_engine_uses_configured_url(monkeypatch) -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+psycopg://user:password@db:5432/intelireg",
    )
    captured: dict[str, object] = {}
    expected_engine = object()

    def fake_create_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return expected_engine

    monkeypatch.setattr(db_module, "create_engine", fake_create_engine)

    engine = db_module.create_database_engine(settings)

    assert engine is expected_engine
    assert captured["url"] == settings.database_url
    assert captured["kwargs"] == {"pool_pre_ping": True}
