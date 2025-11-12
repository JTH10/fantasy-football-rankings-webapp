# logging_setup.py
import logging
import sys
import json
import time


class JsonFormatter(logging.Formatter):
    """Render logs in structured JSON for easy reading in Render logs."""
    def format(self, record):
        log_entry = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)


def setup_logging(level=logging.INFO):
    """Sets up consistent logging for Flask, SQLAlchemy, and Gunicorn."""
    # Remove any default handlers Flask might add
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # Create a single stream handler for stdout (Render captures stdout)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    # Quiet noisy loggers if desired
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Structured logging initialized")
