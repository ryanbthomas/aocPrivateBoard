from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import ConnectionPoolEntry

from app.config import Settings


def create_database_engine(database_url: str | None = None) -> Engine:
    url = database_url or Settings().database_url
    engine = create_engine(url)

    if engine.dialect.name == "sqlite":
        database = engine.url.database
        if database and database != ":memory:":
            Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(
            dbapi_connection: SQLiteConnection, connection_record: ConnectionPoolEntry
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine
