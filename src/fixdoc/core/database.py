"""SQLAlchemy Core schema and synchronous SQLite transaction setup."""

from sqlalchemy import (
    JSON,
    URL,
    Column,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.pool import NullPool

SCHEMA_VERSION = "3"
metadata = MetaData()
entries = Table(
    "entries",
    metadata,
    Column("id", Text, primary_key=True),
    Column("path", Text, nullable=False, unique=True),
    Column("content_hash", Text, nullable=False),
    Column("search_hash", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("resource_type", Text),
    Column("title", Text, nullable=False),
    Column("occurrences", Integer, nullable=False),
    Column("confidence", Float),
    Column("created", Text),
    Column("env_scope", JSON),
    Column("match_keys", JSON),
    Column("embedding", LargeBinary, nullable=False),
)
meta = Table("meta", metadata, Column("key", Text, primary_key=True), Column("value", Text))


def open_engine(path, *, readonly=False):
    # mode=ro prevents stats from creating or modifying a database. as_uri
    # also escapes spaces, '?' and '#' in repository paths.
    url = URL.create(
        "sqlite+pysqlite",
        database=path.resolve().as_uri() if readonly else str(path.resolve()),
        query={"mode": "ro", "uri": "true"} if readonly else {},
    )
    engine = create_engine(url, connect_args={"timeout": 30}, poolclass=NullPool)

    @event.listens_for(engine, "connect")
    def configure(dbapi_connection, connection_record):
        # Explicit BEGIN makes SELECT and DDL transactional on Python 3.10+
        # too; sqlite3's legacy mode otherwise autocommits schema changes.
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin(connection):
        # Reserve the sole writer before checking the generation. Readers
        # hold short snapshots; embedding never runs inside a transaction.
        writer = connection.get_execution_options().get("writer")
        connection.exec_driver_sql("BEGIN IMMEDIATE" if writer else "BEGIN")

    return engine
