"""
backend/app/logging.py

Logging configuration for the FastAPI backend.
Provides structured console output readable in development and production.
When LOG_DIR is configured, also writes daily rotating log files named
app.log (current day) and app.log.YYYY-MM-DD (previous days, UTC).
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def configure_logging(level: str = "INFO", log_dir: str = "") -> None:
    """Configure root logger with console handler and optional daily file handler."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    # Avoid duplicate handlers if configure_logging is called more than once.
    root.handlers.clear()
    root.addHandler(console_handler)

    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=log_path / "app.log",
            when="midnight",
            backupCount=90,
            encoding="utf-8",
            utc=True,
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Quieten noisy third-party loggers.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)
