"""
Module to setup Sqlite databases and tables.
Applies migrations.
"""

from sqlalchemy import create_engine

from swingmusic.db import create_all_tables
from swingmusic.db import libdata as _libdata_models  # noqa: F401
from swingmusic.db import production as _production_models  # noqa: F401
from swingmusic.db import spotify as _spotify_models  # noqa: F401
from swingmusic.db import userdata as _userdata_models  # noqa: F401
from swingmusic.db.engine import DbEngine
from swingmusic.migrations import apply_migrations
from swingmusic.settings import Paths


def run_migrations():
    """
    Run migrations and updates migration version.
    """
    apply_migrations()


def setup_sqlite():
    """
    Create Sqlite databases and tables.
    """
    DbEngine._engine = create_engine(
        f"sqlite+pysqlite:///{Paths().app_db_path}",
        echo=False,
        max_overflow=20,
        pool_size=10,
    )

    create_all_tables()
