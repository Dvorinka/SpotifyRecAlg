"""
Migrations module.

Discovers migration classes from explicitly registered modules and applies
pending migrations in deterministic order.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
from types import ModuleType

from swingmusic.db.metadata import MigrationTable
from swingmusic.migrations.base import Migration

log = logging.getLogger(__name__)


DEFAULT_MIGRATION_MODULES = [
    "swingmusic.migrations.production_setup_migration",
]

OPTIONAL_MIGRATION_MODULES = [
    (
        "SWINGMUSIC_ENABLE_UPDATE_TRACKING_MIGRATIONS",
        "swingmusic.migrations.update_tracking_migration",
    ),
]


def get_all_migrations(module: ModuleType) -> list[type[Migration]]:
    """
    Extract all enabled migration classes from a module.
    """

    def predicate(obj):
        return (
            inspect.isclass(obj)
            and issubclass(obj, Migration)
            and obj.enabled
            and obj is not Migration
            and obj.__module__ == module.__name__
        )

    return [obj for _, obj in inspect.getmembers(module, predicate)]


def _load_migration_modules() -> list[ModuleType]:
    modules: list[ModuleType] = []

    for module_path in DEFAULT_MIGRATION_MODULES:
        modules.append(importlib.import_module(module_path))

    for flag, module_path in OPTIONAL_MIGRATION_MODULES:
        enabled = os.getenv(flag, "").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            continue

        try:
            modules.append(importlib.import_module(module_path))
        except Exception as error:
            log.exception(
                "Failed to import optional migration module %s: %s", module_path, error
            )

    return modules


def apply_migrations():
    """
    Applies pending migrations and records the migration index.
    """
    modules = _load_migration_modules()
    migrations = [
        migration for module in modules for migration in get_all_migrations(module)
    ]
    migrations.sort(key=lambda migration: migration.__name__)

    current_index = MigrationTable.get_version()
    if current_index < 0:
        current_index = 0

    if current_index > len(migrations):
        log.warning(
            "Migration index %s exceeds known migrations %s. Clamping index.",
            current_index,
            len(migrations),
        )
        current_index = len(migrations)

    to_apply = migrations[current_index:]
    for migration in to_apply:
        migration.migrate()
        log.info("Applied migration: %s", migration.__name__)

    MigrationTable.set_version(len(migrations))
