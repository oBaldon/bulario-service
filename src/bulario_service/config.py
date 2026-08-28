from dataclasses import dataclass
import os
from pathlib import Path


DEFAULT_ENV_FILE = Path(".env")


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


def load_local_env(
    env_file: Path | str = DEFAULT_ENV_FILE,
) -> bool:
    """Load simple KEY=VALUE entries without overriding process variables."""
    path = Path(env_file)
    if not path.is_file():
        return False

    loaded_any = False
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            raise ValueError(
                f"invalid .env entry at {path}:{line_number}"
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = _parse_env_value(value.strip())

        if not key or not _is_valid_env_key(key):
            raise ValueError(
                f"invalid .env key at {path}:{line_number}"
            )

        if key not in os.environ:
            os.environ[key] = value
            loaded_any = True

    return loaded_any


def load_settings(
    *,
    env_file: Path | str = DEFAULT_ENV_FILE,
) -> Settings:
    load_local_env(env_file)

    return Settings(
        app_env=os.getenv("APP_ENV", "local"),
        database_url=normalize_database_url(os.getenv("DATABASE_URL")),
    )


def _parse_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_valid_env_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(character.isalnum() or character == "_" for character in key)
