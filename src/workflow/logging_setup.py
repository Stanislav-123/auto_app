from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Noisy libraries — keep errors/warnings only.
_QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "telegram",
    "telegram.ext",
    "urllib3",
    "asyncio",
)


def setup_logging(log_dir: Path | None = None) -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    formatter = logging.Formatter(log_format)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload.
    if root.handlers:
        _configure_quiet_loggers()
        return

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    log_file = os.getenv("LOG_FILE")
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if log_dir is None:
        log_dir = Path(os.getenv("LOG_DIR", "logs"))

    if os.getenv("LOG_TO_DIR", "1") == "1":
        log_dir.mkdir(parents=True, exist_ok=True)
        dir_handler = RotatingFileHandler(
            log_dir / "bot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        dir_handler.setFormatter(formatter)
        root.addHandler(dir_handler)

    _configure_quiet_loggers()


def _configure_quiet_loggers() -> None:
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Persist path chatter is not useful in normal ops.
    logging.getLogger("src.workflow.persist").setLevel(logging.WARNING)
    # Keep bot module quiet except explicit step logger / errors.
    logging.getLogger("__main__").setLevel(logging.WARNING)
    logging.getLogger("bot").setLevel(logging.WARNING)
