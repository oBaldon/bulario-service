from sqlalchemy import Engine, create_engine

from bulario_service.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for database operations")

    return create_engine(settings.database_url, pool_pre_ping=True)
