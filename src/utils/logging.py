"""Structured logging setup."""

from __future__ import annotations

import logging
import sys

from rich.console import Console
from rich.logging import RichHandler


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True)],
    )
    logger = logging.getLogger("profit_bot")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
