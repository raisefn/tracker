"""Sanitize sensitive data from log messages."""

import re

# Patterns that might appear in error messages or URLs
_PATTERNS = [
    # API keys in URLs (?api_key=..., ?key=..., ?token=...)
    (re.compile(r"([?&])(api_key|key|token|apikey|access_token)=[^&\s]+", re.I), r"\1\2=***"),
    # Authorization headers
    (re.compile(r"(Authorization:\s*(?:Bearer|Basic)\s+)\S+", re.I), r"\1***"),
    # Database URLs with passwords
    (re.compile(r"(postgresql(?:\+asyncpg)?://\w+:)[^@]+(@)", re.I), r"\1***\2"),
    # Redis URLs with passwords
    (re.compile(r"(redis://:\s*)[^@]+(@)", re.I), r"\1***\2"),
]


def sanitize(msg: str) -> str:
    """Remove sensitive tokens/passwords from a log message."""
    for pattern, replacement in _PATTERNS:
        msg = pattern.sub(replacement, msg)
    return msg
