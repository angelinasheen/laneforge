"""User-facing errors raised by the query layer."""
from __future__ import annotations


class ValidationError(Exception):
    """Bad input. `message` is safe to show to the user as-is."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
