from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str | None


def normalize_database_url(database_url: str | None) -> str | None:
    if database_url is None:
        return None

    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)

    return database_url


def load_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        database_url=normalize_database_url(os.getenv("DATABASE_URL")),
    )
