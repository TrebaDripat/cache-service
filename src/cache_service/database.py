"""SQLite storage for versioned transformations and complete payload documents."""

import sqlite3

from sqlalchemy import URL, Engine, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from cache_service.config import Settings


class Base(DeclarativeBase):
    pass


class Transformation(Base):
    __tablename__ = "transformations"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    result: Mapped[str] = mapped_column(Text)


class Payload(Base):
    __tablename__ = "payloads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    output: Mapped[str] = mapped_column(Text)


def create_database(settings: Settings) -> Engine:
    path = settings.database_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={
            "check_same_thread": False,
            "timeout": settings.database_timeout,
            # BEGIN IMMEDIATE is explicitly issued for the cache-fill transaction.
            "autocommit": sqlite3.LEGACY_TRANSACTION_CONTROL,
        },
    )
