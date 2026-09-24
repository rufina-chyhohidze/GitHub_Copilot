from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import Settings


def make_engine(settings: Settings) -> Engine:
    return create_engine(
        settings.require_database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )
