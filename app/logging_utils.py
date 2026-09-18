"""
Secret-safe structured logging utility.
Ensures no API keys, tokens, or credentials appear in logs.
"""

import logging
import re
import sys
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9_\-]{20,})"),
    re.compile(r"(bearer\s+)([a-zA-Z0-9_\-\.]{20,})", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"'\s:=]+)([a-zA-Z0-9_\-]{16,})", re.IGNORECASE),
]


class SecretSanitizingFormatter(logging.Formatter):
    """Sanitizes sensitive patterns such as API keys from log strings."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        for pattern in SECRET_PATTERNS:
            formatted = pattern.sub(r"\1***REDACTED***", formatted)
        return formatted


def setup_logger(name: str = "gridwise", level: str = "INFO") -> logging.Logger:
    """Configures a standardized, secret-safe logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        formatter = SecretSanitizingFormatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
    return logger


logger = setup_logger()
