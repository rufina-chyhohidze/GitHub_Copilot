"""JSON logs make CLI events readable by both people and future tracing tools."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app")
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
