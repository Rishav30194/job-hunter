"""Alembic environment: wires our SQLAlchemy models and DATABASE_URL into the migration runner."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config.settings import settings
from src.db.models import Base

config = context.config

# Wire logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject DB URL from settings so it never lives in alembic.ini
config.set_main_option("sqlalchemy.url", settings.database_url)

# Expose our models' metadata so autogenerate can diff against the live schema
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection — emits SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
