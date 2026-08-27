from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str | None


def load_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        database_url=os.getenv("DATABASE_URL"),
    )