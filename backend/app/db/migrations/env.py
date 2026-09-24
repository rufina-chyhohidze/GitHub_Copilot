"""Use the same validated database configuration as the application."""

from alembic import context

from app.config import Settings
from app.db.session import make_engine

settings = Settings()

if context.is_offline_mode():
    context.configure(url=settings.require_database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
