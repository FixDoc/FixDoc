"""SQLite schema and explicit transactions for the derived index."""

import sqlite3
from contextlib import contextmanager

SCHEMA_VERSION = "3"
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS entries (
        id TEXT NOT NULL PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        content_hash TEXT NOT NULL,
        search_hash TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL,
        resource_type TEXT,
        title TEXT NOT NULL,
        occurrences INTEGER NOT NULL,
        confidence REAL,
        created TEXT,
        env_scope TEXT,
        match_keys TEXT,
        embedding BLOB NOT NULL
    )""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT NOT NULL PRIMARY KEY, value TEXT)",
)


def open_database(path, *, readonly=False):
    # mode=ro prevents stats from creating or modifying a database. as_uri
    # also escapes spaces, '?' and '#' in repository paths.
    database = f"{path.resolve().as_uri()}?mode=ro" if readonly else str(path.resolve())
    connection = sqlite3.connect(database, uri=readonly, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def transaction(connection, *, writer=False):
    # Explicit BEGIN keeps SELECT and DDL in the same transaction. Writers
    # reserve the database before checking the committed generation.
    with connection:
        connection.execute("BEGIN IMMEDIATE" if writer else "BEGIN")
        yield connection
