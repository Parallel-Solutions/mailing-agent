from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.utils.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    database_name = unquote((parsed.path or "").lstrip("/"))
    if not database_name:
        raise ValueError("DATABASE_URL must include a database name.")
    return database_name


def _admin_database_url(database_url: str, *, admin_database: str = "postgres") -> str:
    parsed = urlparse(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    normalized_path = f"/{admin_database.lstrip('/')}"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            normalized_path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


def ensure_database_exists() -> None:
    database_url = settings.database_url
    database_name = _database_name_from_url(database_url)
    admin_engine = create_engine(
        _admin_database_url(database_url),
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": database_name},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()


def check_db_connection() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def init_db() -> None:
    """Ensure target database exists and apply Alembic migrations to head."""
    ensure_database_exists()
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_cfg, "head")
