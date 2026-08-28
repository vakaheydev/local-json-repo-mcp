from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOGGER_NAME = "local-json-repo-mcp"


def setup_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """Configure file + stderr logging without writing anything to stdout.

    stdout is reserved for the MCP stdio transport, so this module deliberately
    logs only to stderr and to a rotating log file.

    Environment variables:
        MCP_LOG_LEVEL   DEBUG/INFO/WARNING/ERROR (default: INFO)
        MCP_LOG_DIR     log directory (default: ./logs next to this file)
        MCP_LOG_FILE    log filename (default: mcp.log)
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    level_name = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = Path(
        os.getenv(
            "MCP_LOG_DIR",
            str(Path(__file__).resolve().parent / "logs"),
        )
    ).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / os.getenv("MCP_LOG_FILE", "mcp.log")

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.propagate = False
    logger.addHandler(file_handler)
    logger.addHandler(stderr_handler)

    logger.debug("Logger initialized: file=%s level=%s", log_file, level_name)
    return logger


logger = setup_logger()
