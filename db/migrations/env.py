"""Alembic env — reads DATABASE_URL from environment.

Uses a sync driver for migrations even though the app is async, because
Alembic doesn't need (and works better without) the asyncpg event loop.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Convert asyncpg URL to psycopg2 for migrations.
url = os.getenv("DATABASE_URL", "postgresql://ollive:ollive@postgres:5432/ollivelogs")
url = url.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", url)

target_metadata = None  # raw SQL migrations — no SQLAlchemy models needed.


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
